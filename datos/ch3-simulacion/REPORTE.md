# C3.8 · Resultados y análisis

> **Todo lo que sigue es resultado de simulación.** El broker Mosquitto, el programa del Gateway, la base SQLite y el módulo de IA son los reales; el nodo ESP32 y la plataforma en la nube están emulados ([C3.7 §7.1](C3.7-Configuracion-experimental)). Ninguna cifra de esta página es una medición del prototipo físico.

> **Criterio de reporte**, el mismo del Challenge #2: no se consignan datos no obtenidos. Las cifras de esta página se generan desde `datos/ch3-simulacion/resultados.json` con `pruebas/generar_reporte.py`; ninguna está escrita a mano.

| Corrida | |
|---|---|
| Fecha | 2026-11-16 16:34:55 UTC |
| Sistema | Linux 6.18.44-fc-v470 (x86_64) |
| Software | Python 3.13.18 · SQLite 3.53.1 · paho-mqtt 2.2.0 · Mosquitto 2.0.18 |
| Tiempo simulado total | 254 horas (10,6 días) en 457 200 mensajes |

---

## 8.0 Resumen

| # | Prueba | Resultado | |
|---|---|---|---|
| S1 | Integridad de extremo a extremo | 457 200 de 457 200 mensajes en SQLite; 3 039 de 3 039 agregados en la nube; 0 duplicados | ✅ |
| S1 | Presupuesto de la nube | Máximo 3 046 datos/día, frente a un límite de 4 000 | ✅ |
| S2 | Control de acceso del broker | 10 de 10 casos con el resultado esperado | ✅ |
| S3 | Validación de mensajes | 10 de 10 malformados rechazados; el Gateway siguió operando | ✅ |
| S4 | Latencia (cota inferior, sin red) | Nodo → SQLite: mediana 0,33 ms. Orden → confirmación: mediana 2,15 ms | — |
| S5 | Caída de Internet | 0 perdidos, 0 duplicados, orden cronológico: sí | ✅ |
| S6 | Comando de silencio y salvaguardas | 10 de 10 comprobaciones | ✅ |
| S7 | Caída del nodo | Detectada en 22,2 s; reinicio detectado | ✅ |
| S8 | Reinicio del Gateway | 828 mensajes no recibidos; 828 contados | ✅ |
| S9 | IA · falsas anomalías | 0 en 501 corridas, incluido el ruido severo | ✅ |
| S9 | IA · detección de la fuga | 1,0 h después de iniciada; sostenida el 100 % del tiempo | ✅ |
| S9 | IA · predicción del cruce | Ver §8.9: depende de la anticipación y del caso | — |
| — | **Hallazgo** | La alarma amarilla del **nodo** se activa sin que el nivel esté bajo (§8.10) | ⚠️ |

---

## 8.1 S1 · Integridad de extremo a extremo

**Qué se comprueba.** Que cada mensaje publicado por el nodo queda en SQLite, que cada ventana cerrada produce un agregado, y que cada agregado llega a la nube exactamente una vez.

| Escenario | Horas simuladas | Publicados | En SQLite | Perdidos | Agregados | En la nube | Duplicados | Datos/día a la nube |
|---|---|---|---|---|---|---|---|---|
| Estable | 24 | 43 200 | 43 200 | **0** | 287 | 287 | 0 | 3 007 |
| Sequía | 72 | 129 600 | 129 600 | **0** | 863 | 863 | 0 | 3 021 |
| Fuga | 30 | 54 000 | 54 000 | **0** | 359 | 359 | 0 | 3 046 |
| Estable · ruido severo | 24 | 43 200 | 43 200 | **0** | 287 | 287 | 0 | 3 007 |
| Sequía · ruido severo | 72 | 129 600 | 129 600 | **0** | 863 | 863 | 0 | 3 019 |
| Fuga · ruido severo | 30 | 54 000 | 54 000 | **0** | 359 | 359 | 0 | 3 042 |
| Falla del sensor | 2 | 3 600 | 3 600 | **0** | 23 | 21 | 0 | — |
| **Total** | **254** | **457 200** | **457 200** | **0** | | | **0** | |

En cada escenario el número de agregados es el de ventanas de 5 minutos **menos una**: la última ventana queda abierta porque no llegó una muestra posterior que la cierre. En el escenario de falla, los agregados sin nivel válido no envían la variable `nivel` (§8.11).

### Análisis

**Cero pérdidas en 457 200 mensajes.** Es el resultado esperable en una sola máquina, sin red de por medio, y hay que leerlo como lo que es: prueba de que **el software no pierde datos por sí mismo** —ni en la recepción, ni en la escritura por lotes, ni en el cierre de ventanas, ni en la bandeja de salida—. No dice nada sobre la pérdida en un enlace Wi-Fi real; eso lo medirá el consecutivo `seq` cuando haya hardware.

**El presupuesto de la nube se cumple con margen.** El máximo observado fue de **3 046 datos por día**, el 76 % del límite de 4 000. El cálculo de diseño ([C3.4 §4.5](C3.4-Diseno-modular)) daba 3 024 en operación estable; la diferencia son las transiciones de alarma y los cambios de estado, que salen fuera de ciclo.

**Holgura de procesamiento.** El banco inyectó 1 500 mensajes por segundo y el Gateway los procesó sin acumular atraso. Un nodo real produce 0,5 por segundo. La cifra es de un computador de escritorio, **no de una Raspberry Pi**: no debe citarse como capacidad del Gateway, solo como indicio de que la carga de un nodo —o de decenas— no es el factor limitante.

---

## 8.2 S2 · Control de acceso del broker local

**Qué se comprueba.** Que el broker, con los archivos `estacion.conf` y `estacion.acl` del repositorio, rechaza a quien no tiene credenciales y confina a cada usuario a sus tópicos.

| # | Caso | Esperado | Obtenido | |
|---|---|---|---|---|
| 1 | Conexión anónima (sin usuario) | rechazado | rechazado | ✅ |
| 2 | Usuario válido con clave incorrecta | rechazado | rechazado | ✅ |
| 3 | Usuario inexistente | rechazado | rechazado | ✅ |
| 4 | Usuario y clave correctos | aceptado | aceptado | ✅ |
| 5 | nodo01 publica en la rama de nodo02 | bloqueado | bloqueado | ✅ |
| 6 | nodo01 se envía un comando a sí mismo | bloqueado | bloqueado | ✅ |
| 7 | nodo01 suplanta el estado del Gateway | bloqueado | bloqueado | ✅ |
| 8 | nodo01 lee datos agregados de la red | bloqueado | bloqueado | ✅ |
| 9 | nodo01 recibe sus propios comandos | entregado | entregado | ✅ |
| 10 | usuario de solo lectura envía un comando | bloqueado | bloqueado | ✅ |

### Análisis

Los casos 1 a 4 prueban la **autenticación**; los casos 5 a 10, la **autorización**, que es la que suele omitirse. La diferencia importa: con solo usuario y clave, un nodo comprometido podría publicar una alerta a nombre de otro (caso 5), darse a sí mismo la orden de silenciar su alarma (caso 6) o anunciar que el Gateway se cayó (caso 7). La lista de control de acceso cierra los tres caminos.

El caso 6 merece una línea: el nodo **recibe** órdenes de silencio pero no puede **emitirlas**. La única ruta para silenciar la alarma por MQTT pasa por el usuario `gateway`.

Mosquitto no devuelve error cuando la ACL bloquea una publicación: la descarta en silencio. Por eso el veredicto se obtuvo con un observador independiente que confirma que el mensaje **no fue entregado**, no con la respuesta del broker.

---

## 8.3 S3 · Validación de mensajes en el Gateway

| # | Mensaje enviado | Motivo registrado en la bitácora |
|---|---|---|
| 1 | Texto que no es JSON | `JSON invalido` |
| 2 | JSON truncado | `JSON invalido` |
| 3 | JSON válido pero no es un objeto | `la carga no es un objeto JSON` |
| 4 | Falta el consecutivo (seq) | `falta el campo 'seq'` |
| 5 | Nivel no numérico | `'nivel' no es numerico` |
| 6 | Humedad fuera de rango físico (150 %) | `'hr' fuera de rango fisico (150.0)` |
| 7 | Bandera QARTOD inexistente (qc = 7) | `bandera qc fuera del esquema QARTOD` |
| 8 | Estado de alerta desconocido | `estado de alerta desconocido` |
| 9 | El id del mensaje no coincide con el tópico | `el id no coincide con el topico` |
| 10 | Consecutivo negativo | `seq invalido` |

| | |
|---|---|
| Malformados enviados / rechazados | 10 / 10 |
| Válidos enviados / guardados | 3 / 3 |
| El Gateway siguió operando después de los diez | sí |

### Análisis

Cada rechazo deja su motivo exacto en la tabla `eventos`. Eso convierte un mensaje malo en **información de diagnóstico**: si un nodo empieza a enviar humedades de 150 %, la bitácora lo dice con nombre de campo y valor.

Uno de los tres mensajes válidos llevaba `nivel: null` con bandera 9 (sin eco). **Se aceptó y se guardó.** Es la distinción que el diseño quería: un dato faltante *declarado* es un dato válido del sistema; un nivel con texto donde va un número, no.

---

## 8.4 S4 · Latencia por tramo

> **Cota inferior.** Todo corre en una sola máquina por la interfaz de lazo local. Estas cifras miden el costo del **software** —serializar, validar, escribir, reenviar— y nada más. La latencia real la dominarán el Wi-Fi y, sobre todo, Internet.

| Tramo | n | Mínimo | Mediana | P95 | Máximo |
|---|---|---|---|---|---|
| Publicación del nodo → fila en SQLite | 300 | 0,23 ms | **0,33 ms** | 0,46 ms | 24,33 ms |
| Orden en la «nube» → confirmación del nodo (ida y vuelta por los dos brokers) | 20 | 1,05 ms | **2,15 ms** | 3,12 ms | 3,12 ms |
| Cambio de alarma en el nodo → llegada a la «nube» | 1 | | **0,9 ms** | | |

### Análisis

**El software no agrega latencia apreciable.** Con medianas de fracción de milisegundo a pocos milisegundos, el recorrido completo por el Gateway es despreciable frente al período de muestreo de 2 s y frente a cualquier enlace real.

**La transición de alarma no espera a la ventana.** El dato agregado sale cada 5 minutos, pero el cambio de estado sale de inmediato y con prioridad. En la nube, la latencia de una alerta es la de la red, no la de la ventana.

**El máximo de la primera fila** (24,33 ms) es un valor aislado frente a un P95 de 0,46 ms. No se investigó su causa; lo más probable es una confirmación de escritura en disco o la planificación del sistema operativo. No cambia la conclusión.

---

## 8.5 S5 · Caída de Internet: almacenar y reenviar

**Procedimiento.** Cuatro horas simuladas con enlace; se corta el enlace (la «nube» sigue viva pero inalcanzable) y el nodo sigue publicando ocho horas más; se restablece; dos horas finales.

| Fase | Lecturas en SQLite | Agregados en SQLite | **Pendientes de envío** | **Agregados en la nube** |
|---|---|---|---|---|
| 1. Con Internet (4 h) | 7 200 | 47 | **0** | **47** |
| 2. Sin Internet (8 h más) | 21 600 | 143 | **118** | **47** |
| 3. Internet restablecido | 21 600 | 143 | **0** | **143** |
| 4. Operación normal (2 h más) | 25 200 | 167 | **0** | **167** |

| Verificación | Resultado |
|---|---|
| Agregados generados / recibidos en la nube | 167 / 167 |
| Perdidos | **0** |
| Duplicados | **0** |
| Llegaron a la nube durante el corte | 0 |
| Orden cronológico al reenviar | sí |
| Tiempo en ponerse al día tras volver el enlace | 2,4 s |
| Eventos registrados en la bitácora | conectada → desconectada → conectada → desconectada |

### Análisis

**Es el resultado más importante del Challenge #3.** Durante el corte, el Gateway siguió recibiendo (de 7 200 a 21 600 lecturas), siguió agregando (de 47 a 143 ventanas) y siguió corriendo el módulo de IA. Lo único que se detuvo fue el envío: la nube se quedó en 47 agregados y la bandeja de salida creció hasta **118 mensajes**. Al volver el enlace se vació sola, en orden, sin perder ni repetir nada.

Esto es lo que justifica la restricción del enunciado —*almacenar localmente antes de transmitir*— y lo que demuestra que SQLite no es un adorno: **es el mecanismo de fiabilidad del sistema.** Sin la bandeja de salida, ocho horas sin Internet serían ocho horas de hueco en el tablero global.

**Los datos aparecen en su hora real.** Cada punto viaja con la marca de tiempo del dato, no la del envío. La gráfica de la plataforma no muestra un salto al reconectar: muestra el tramo completo.

**Sobre el tiempo de recuperación.** Los 2,4 s incluyen la espera de reconexión y **no** incluyen el límite de 1 mensaje por segundo de la plataforma real, que en simulación está desactivado. Con el límite, vaciar 118 mensajes toma unos 118 s (≈ 2 min). Es una extrapolación aritmética, no una medición: la prueba H5 la verifica.

**Entrega al menos una vez.** En esta corrida no hubo duplicados. El diseño no los excluye: si el enlace cae entre la publicación y su confirmación, el mensaje se reenvía. No hacen daño porque un punto repetido —misma variable, misma marca de tiempo— sobrescribe al anterior.

---

## 8.6 S6 · Comando de silencio y salvaguardas

**Procedimiento.** Escenario `critico`: nivel bajo el umbral rojo, alarma sonando. Las órdenes se publican en la «nube», en el tópico y formato de la plataforma, y recorren los dos tramos hasta el nodo.

| # | Paso | Esperado | Obtenido | |
|---|---|---|---|---|
| 1 | Estado inicial: nivel bajo el umbral rojo | sonando | sonando | ✅ |
| 2 | Orden 'silenciar' desde el tablero global | silenciada | silenciada | ✅ |
| 3 | El LED/estado sigue en ROJA (silenciar no oculta la condición) | ROJA | ROJA | ✅ |
| 4 | Orden 'reactivar' desde el tablero global | sonando | sonando | ✅ |
| 5 | Orden con 10 min de antigüedad (reenviada al reconectar) | sonando | sonando | ✅ |
| 6 | ...el Gateway la descarta y no llega al nodo | no llega | no llega | ✅ |
| 7 | Orden con valor inválido (5) | no llega | no llega | ✅ |
| 8 | Silenciar de nuevo | silenciada | silenciada | ✅ |
| 9 | 14 min después: el silencio sigue vigente | silenciada | silenciada | ✅ |
| 10 | 15,5 min después: el silencio expiró solo | sonando | sonando | ✅ |

En la bitácora del Gateway quedaron 43 órdenes (`COMANDO`), 43 confirmaciones del nodo (`ACK`) y 2 rechazos (`RECHAZO`): los dos últimos son la orden vieja y la orden inválida.

### Análisis

**El requisito se cumple por la ruta larga.** *Desactivar alarmas físicas desde el tablero* ya funcionaba en el Challenge #2 desde el tablero local. Aquí la orden entra por la nube y baja por dos brokers.

**Silenciar no es ocultar (paso 3).** El zumbador calla; el estado sigue en ROJA. Es la diferencia entre *shelving* y supresión en ISA-18.2: el operador reconoce la alarma, no la borra.

**La orden vieja no se ejecuta (pasos 5 y 6).** Una orden con diez minutos de antigüedad —la que la plataforma reenvía al reconectar— se descarta en el Gateway y **no llega al nodo**. Sin este filtro, recuperar Internet podría silenciar una alarma que nadie pidió silenciar.

**El silencio expira solo (pasos 9 y 10).** A los 14 minutos sigue vigente; a los 15,5 la alarma vuelve a sonar. La salvaguarda vive en el nodo, no en el Gateway ni en la nube: funciona igual si después de silenciar se cae toda la red.

> En simulación, "sonando" y "silenciada" son el estado lógico del zumbador en el nodo emulado, cuya lógica se portó de `actualizarAlarma()`. Que el zumbador físico calle es la prueba H6.

---

## 8.7 S7 · Caída del nodo

**Procedimiento.** El nodo corre como proceso aparte y se **congela** (`SIGSTOP`): no cierra el socket ni se despide, igual que un corte de energía. Luego se arranca un nodo nuevo, cuyo consecutivo vuelve a 1.

| | |
|---|---|
| *Keepalive* del nodo | 15 s |
| Tiempo nominal de detección (1,5 × *keepalive*) | 22,5 s |
| **Tiempo medido hasta el evento `offline` en el Gateway** | **22,2 s** |
| Secuencia de estados registrada | online → offline → online |
| Reinicios de nodo detectados por el consecutivo | 1 |
| Variable `en_linea` enviada a la nube | 1 → 0 → 1 |

### Análisis

**El nodo no avisó que se caía, y el sistema se enteró igual.** Es el mecanismo de última voluntad de MQTT: al conectarse, el nodo deja dicho qué publicar si desaparece. El broker lo publica cuando deja de oír su *keepalive*.

El broker cuenta 1,5 veces el *keepalive* desde el último paquete que recibió del nodo y revisa los clientes inactivos por rondas, de modo que el tiempo exacto varía de una corrida a otra alrededor de ese valor. Para el tablero la consecuencia es la misma: **en menos de medio minuto** la autoridad sabe que el dato que está viendo ya no se actualiza. Sin esto, un nodo muerto se ve igual que un nivel estable.

**El reinicio se distingue de la pérdida.** Un consecutivo que retrocede queda registrado como `REINICIO_NODO`, no como miles de mensajes perdidos.

---

## 8.8 S8 · Reinicio del Gateway

**Procedimiento.** Con el nodo publicando sin pausa, se apaga el Gateway (`SIGTERM`), se esperan 2 s y se lo vuelve a levantar sobre la misma base de datos.

| | |
|---|---|
| Publicados por el nodo | 3 857 |
| Recibidos en SQLite | 3 029 |
| No recibidos (el Gateway estaba apagado) | **828** |
| Pérdidas contadas en la tabla `agregados` | **828** |
| ¿Coinciden? | sí |
| Bitácora | inicio → fin → inicio → fin |

### Análisis

**Aquí sí se pierden datos, y el resultado correcto es reconocerlo.** La telemetría viaja en QoS 0: lo que el nodo publica mientras el Gateway está apagado no se recupera. Fue una decisión de diseño ([C3.4 §4.1](C3.4-Diseno-modular)) y esta prueba mide su costo.

Lo que la prueba verifica es que **la pérdida no pasa inadvertida**: los 828 mensajes que faltan son exactamente los 828 que la columna `perdidas` contabiliza. Al arrancar, el Gateway recupera de SQLite el último consecutivo y reconstruye la ventana que quedó abierta; el hueco aparece medido en el agregado correspondiente.

En operación real el costo es menor de lo que sugiere la tabla: el banco publicaba a unos 250 mensajes por segundo, y un nodo real publica uno cada 2 s. Un reinicio de 30 s del Gateway cuesta 15 muestras, y el nodo conserva además su propio histórico de 24 h en el tablero local.

---

## 8.9 S9 · Módulo de IA

El módulo corrió dentro del Gateway, cada 30 minutos de tiempo simulado, sobre los agregados que el propio Gateway iba guardando. Cada corrida quedó en la tabla `predicciones` y se compara aquí con la **verdad del modelo**: el nivel real del tanque simulado y el instante exacto en que empieza la fuga.

> La capa generativa estuvo desactivada. Todas las recomendaciones citadas son del respaldo por reglas.

### 8.9.1 Clasificación

| Escenario | Ruido | Corridas | Estable | Descenso climático | **Descenso no explicado** | Recuperación | Sin datos |
|---|---|---|---|---|---|---|---|
| Estable | σ = 0,020 cm | 47 | 46 | 0 | **0** | 0 | 1 |
| Sequía | σ = 0,020 cm | 143 | 0 | 142 | **0** | 0 | 1 |
| Fuga | σ = 0,020 cm | 59 | 0 | 16 | **42** | 0 | 1 |
| Estable | σ = 0,176 cm | 47 | 45 | 0 | **0** | 1 | 1 |
| Sequía | σ = 0,176 cm | 143 | 7 | 135 | **0** | 0 | 1 |
| Fuga | σ = 0,176 cm | 59 | 7 | 9 | **42** | 0 | 1 |

**Sin falsas anomalías.** En los escenarios sin fuga —estable y sequía, con los dos niveles de ruido— el módulo no clasificó ninguna corrida como descenso no explicado. En el escenario de fuga, ninguna antes de que la fuga empezara.

**La sequía se reconoce como sequía.** Es el caso que el módulo debía resolver: el nivel baja durante tres días y el sistema **no** pide una inspección, porque la evaporación lo explica.

**El ruido severo cuesta sensibilidad, no falsas alarmas.** Con σ = 0,176 cm el módulo declaró `ESTABLE` en 7 corridas del escenario de sequía y en 7 del de fuga (antes de que esta empezara), donde con el ruido de operación veía un descenso climático: la tendencia de la evaporación queda por debajo de lo que ese ruido permite afirmar, y el módulo **prefiere no afirmar**.

**Un error de clasificación, declarado.** En el escenario estable con ruido severo, 1 de 47 corridas se clasificaron como `RECUPERACION` sin que el nivel subiera. No genera alerta ni recomendación de acción, pero es un falso positivo de tendencia.

### 8.9.2 Detección de la fuga

| | Ruido de operación | Ruido severo |
|---|---|---|
| Inicio real de la fuga (3 mm/h) | hora 8 | hora 8 |
| Primera clasificación como *no explicado* | hora 9,0 | hora 9,0 |
| **Demora de detección** | **1,0 h** | **1,0 h** |
| Anomalías declaradas antes de la fuga | 0 | 0 |
| Detección sostenida (de 3 h después del inicio hasta el cruce) | 100 % | 100 % |
| Cruce real del umbral de alerta | hora 20,5 | hora 20,5 |

**La fuga se detecta 1,0 h después de empezar y 11,5 h antes de que el nivel cruce el umbral.** Ese margen es el valor del módulo: la alarma por umbral del nodo habría avisado en la hora 20,5; la IA avisó en la hora 9,0, y además dijo **por qué**.

La demora tiene un piso que no depende del ruido: el módulo corre cada 30 minutos y necesita que el descenso nuevo pese en la ventana de 3 horas. Por eso es la misma con los dos niveles de ruido. Una fuga más pequeña tardaría más; una menor que el piso de 0,30 mm/h no se detectaría.

La recomendación que produjo el Gateway al final de este escenario:

> *El nivel baja 3.1 mm/h y la evaporación solo explica 0.1 mm/h: hay una pérdida de 3.0 mm/h que el clima no justifica. El nivel ya está bajo el umbral de alerta. Inspeccionar HOY el punto: fugas en la estructura, válvulas abiertas o captaciones no autorizadas.*

### 8.9.3 Predicción del cruce del umbral

Error = instante predicho del cruce − instante real. Solo predicciones hechas antes del cruce. En el escenario de fuga, solo las hechas una vez detectada la anomalía.

| Escenario | Ruido | Anticipación | n | Error absoluto medio | Error absoluto máximo |
|---|---|---|---|---|---|
| Sequía | σ = 0,020 cm | Menos de 6 h | 12 | 0,56 h | 1,32 h |
| Sequía | σ = 0,020 cm | De 6 a 24 h | 36 | 3,09 h | 5,32 h |
| Sequía | σ = 0,020 cm | Más de 24 h | 65 | 17,59 h | 64,48 h |
| Sequía | σ = 0,176 cm | Menos de 6 h | 12 | 7,45 h | 8,48 h |
| Sequía | σ = 0,176 cm | De 6 a 24 h | 36 | 4,13 h | 8,88 h |
| Sequía | σ = 0,176 cm | Más de 24 h | 58 | 15,79 h | 75,98 h |
| Fuga | σ = 0,020 cm | Menos de 6 h | 12 | 0,07 h | 0,12 h |
| Fuga | σ = 0,020 cm | De 6 a 24 h | 12 | 6,52 h | 64,78 h |
| Fuga | σ = 0,176 cm | Menos de 6 h | 12 | 0,24 h | 0,38 h |
| Fuga | σ = 0,176 cm | De 6 a 24 h | 12 | 4,50 h | 38,08 h |

**En la fuga la predicción es precisa**: error medio de 0,07 h (4 min) en las últimas seis horas. El descenso es una recta y el módulo ajusta una recta.


**Pero no de inmediato.** Entre 6 y 24 h de anticipación el error medio es de 6,52 h, y lo dominan las primeras predicciones tras detectar la fuga (máximo: 64,78 h): la ventana de 3 horas todavía mezcla horas sin fuga y subestima la tasa. El error queda por debajo de una hora desde la hora 10,5 del escenario, es decir, 2,5 h después de iniciada la fuga.

**Con ruido severo, la predicción por sequía pierde precisión también cerca del cruce**: 7,45 h de error medio con menos de 6 h de anticipación, frente a 0,56 h con el ruido de operación. A ese nivel de ruido la tasa de la evaporación se estima mal incluso con un día de datos.

**En la sequía la predicción es útil a un día y pobre a más de un día.** Con menos de 24 h de anticipación el error medio es de 3,09 h; con más de 24 h sube a 17,59 h, con un máximo de 64,48 h. La razón está en la tabla siguiente.

### 8.9.4 Un hallazgo de la simulación: el estimador dependía de la hora

La primera versión del módulo usaba Theil-Sen también en la ventana de 24 horas. Al correr el escenario de sequía, la tasa estimada **oscilaba a lo largo del día aunque el modelo no cambiaba**. La causa: la evaporación sigue al sol, el nivel baja en escalones —rápido de día, casi nada de noche— y la mediana de pendientes de una curva escalonada depende de dónde empieza y termina la ventana.

Se agregó un segundo estimador para la ventana larga: el cambio neto entre la primera y la última hora de un ciclo completo de 24 h. Los dos se compararon sobre **la misma serie guardada en SQLite**, en los mismos 67 instantes (todos con un día completo de historia):

| Estimador de 24 h | Tasa estimada (mín. a máx.) | Error absoluto medio | Error absoluto máximo |
|---|---|---|---|
| Theil-Sen (versión inicial) | -0,080 a -0,130 mm/h | 5,28 h | 9,46 h |
| **Ciclo completo (versión final)** | -0,106 a -0,114 mm/h | 2,23 h | 5,34 h |

La tasa real media del modelo es del orden de −0,11 mm/h. Con Theil-Sen la estimación recorría de -0,080 a -0,130 mm/h según la hora; con el ciclo completo, de -0,106 a -0,114. El error medio de la predicción bajó de 5,28 a 2,23 horas.

Con el ruido severo la mejora es menor (de 5,86 a 5,07 h): el estimador por ciclo usa solo dos horas de datos y es más sensible al ruido que una mediana sobre todo el día. Es un compromiso, y queda declarado.

**Lo que queda sin resolver.** El estimador por ciclo necesita 24 horas de historia. Durante el primer día el módulo sigue usando Theil-Sen, y de ahí salen los errores de más de 24 h de anticipación de §8.9.3: son predicciones del primer día. Por eso el resultado se marca como *provisional* y la recomendación lo dice. Y aun con un día completo, el error residual de pocas horas no desaparece: una recta no puede saber a qué hora del día caerá el cruce cuando el descenso real es escalonado.

Este es un caso concreto de para qué sirve validar por simulación antes de tener hardware: el defecto habría sido difícil de ver en el banco físico, donde nadie espera tres días a que el agua se evapore.

La recomendación al final del escenario de sequía:

> *El nivel baja 0.11 mm/h y la evaporación lo explica: es efecto del clima, no una pérdida. El nivel ya está bajo el umbral de alerta y llegaría al umbral crítico en unos 14 días. Preparar medidas de ahorro y avisar a los usuarios del sistema con anticipación.*

---

## 8.10 Hallazgo: la alarma amarilla del nodo se activa sin que el nivel esté bajo

Este resultado **no es favorable** y se reporta porque es el más útil de la validación.

El simulador ejecuta la lógica de `evaluarAlerta()` del firmware, portada línea a línea. Al comparar el estado de alarma del nodo con el nivel real del modelo:

| Escenario | Transiciones a alarma | Alarmas por 10 min | % del tiempo en alarma | **% del tiempo en alarma con el nivel SOBRE el umbral** | Primera alarma (hora · nivel real) |
|---|---|---|---|---|---|
| Estable | 10 | 0,069 | 6,9 % | **6,9 %** | hora 11,5 · 20,0 cm |
| Sequía | 1 | 0,002 | 84,0 % | **63,4 %** | hora 11,5 · 12,5 cm |
| Fuga | 11 | 0,061 | 36,9 % | **5,6 %** | hora 11,5 · 14,8 cm |

En el escenario **estable** el nivel permanece en 20 cm, ocho centímetros por encima del umbral de 12 cm, y el nodo entró en alerta amarilla **10 veces en 24 horas**. En el de **sequía** la alerta amarilla se activó en la hora 11,5, con el nivel real en 12,5 cm, y el umbral no se cruzó hasta la hora 57,1.

En la sequía, además, la alerta **no se retiró** en las 45,6 horas que faltaban para el cruce: el nivel inicial del escenario (12,6 cm) coincide con el borde de la banda de histéresis (umbral × 1,05), y para salir de alarma el firmware exige superarla. Por eso ese escenario pasa el 84 % del tiempo en alarma con una sola transición.

### La causa

La alerta amarilla exige que concuerden dos de tres indicadores: nivel bajo el umbral, **tendencia de descenso** mayor de 2 mm/h, y evaporación potencial mayor de 5 mm/día.

El segundo indicador se calcula con **dos muestras consecutivas, separadas 2 segundos**. Un descenso de 2 mm/h equivale a 0,0011 mm en 2 s. El ruido del sensor entre dos muestras es del orden de décimas de milímetro. El indicador no mide una tendencia: **se activa cada vez que el ruido hace que una muestra sea un poco menor que la anterior, es decir, aproximadamente la mitad de las veces.**

Consecuencia: basta que **uno** de los otros dos indicadores esté activo para que, a los pocos segundos, se reúnan tres muestras seguidas con «dos de tres». Hacia el mediodía del modelo la evaporación estimada supera los 5 mm/día (máximo 5,16; el 8,7 % del tiempo por encima de 5), y eso solo ya dispara la amarilla. La corroboración multivariable se comporta, en la práctica, como *nivel bajo **o** evaporación alta*.

La misma causa explica el porcentaje de lecturas marcadas como dudosas (bandera 3) por la prueba de coherencia física, que también compara muestras consecutivas: **2,7 %** con el ruido de operación y **41,4 %** con el ruido severo.

### Qué tan grave es

| | |
|---|---|
| ¿Afecta a la alerta **roja**, la que suena? | **No.** La roja exige además nivel bajo el umbral rojo. La amarilla es solo luz |
| ¿Supera el objetivo de EEMUA 191 (≤ 1 alarma por 10 min)? | No: 0,069 por 10 min en el peor escenario. Pero EEMUA 191 mide carga del operador, no acierto |
| ¿Es un defecto real o un artefacto del simulador? | La **causa** es real: está en el código y no depende del modelo. La **frecuencia** sí depende del clima simulado y del ruido |
| ¿Por qué no se vio en el Challenge #2? | En la corrida de referencia de ese banco (`datos/repetibilidad-operacion.csv`) la evaporación estimada era de 4,15 mm/día: por debajo de 5, el tercer indicador no estaba activo |
| ¿Lo corrige el Challenge #3? | **No en el nodo.** Sí lo compensa aguas arriba: la tendencia que usa el módulo de IA se calcula sobre horas, y en estos mismos escenarios no produjo ninguna falsa anomalía |

### Por qué no se corrigió en el firmware

La corrección es conocida: calcular la tendencia sobre el promedio de bloques de 5 minutos, no sobre muestras sueltas. Pero cambia el tiempo de respuesta de la alarma, que es el comportamiento central del nodo y el que se validó en el banco físico del Challenge #2. Se decidió **no modificar la lógica de alarma sin poder probarla en el hardware**, y declararlo. Queda como primer punto del trabajo futuro ([C3.11](C3.11-Conclusiones-retos-y-trabajo-futuro)).

Es también la demostración más directa de la tesis de este Challenge: **la tendencia de un nivel de agua no se puede medir en 2 segundos. Hace falta memoria, y la memoria está en el Gateway.**

---

## 8.11 Datos faltantes de extremo a extremo

**Procedimiento.** Escenario `falla`: durante 10 minutos el ultrasónico no recibe eco. El nodo publica `nivel: null` con bandera QARTOD 9.

| | |
|---|---|
| Mensajes publicados / en SQLite | 3 600 / 3 600 |
| Lecturas guardadas con nivel nulo | 300 |
| Agregados totales / sin nivel válido | 23 / 2 |
| Agregados con nivel enviados a la nube | 21 de 21 |
| Secuencia de alarmas registrada | VERDE → FALLA → VERDE |

### Análisis

**El dato faltante llega a la nube como dato faltante.** Las ventanas sin ninguna lectura válida no envían la variable `nivel`: en la gráfica de la plataforma queda un hueco, no un cero ni el último valor repetido. Las demás variables de esas ventanas —temperatura, humedad, presión— sí se envían, porque esos sensores seguían bien.

**La falla viaja como falla.** El estado `FALLA` del nodo llegó al Gateway y de ahí a la nube como `alerta = 3`, distinto de una alerta por nivel. Es el principio del Challenge #2 —*un sistema debe declarar lo que no sabe*— sostenido a través de tres capas.

---

## 8.12 Requisitos del enunciado frente a la evidencia

| Requisito | Evidencia en simulación | Falta comprobar en el prototipo |
|---|---|---|
| Transmisión MQTT dispositivo → Gateway | S1: protocolo y cargas, con nodo emulado | H1, H2: con el ESP32 |
| Medición y transmisión en hilos independientes | — (es propiedad del firmware) | H3 |
| Almacenamiento local antes de transmitir | S1, S5 | H5 |
| Procesamiento local | S1, S3 | H12 |
| MQTT Gateway → plataforma | S1, S5: contra una nube emulada | H2, H5: contra Ubidots |
| Tablero local | Challenge #2 (sin cambios) | H4 |
| Tablero global | — | H2 |
| Acceso restringido | S2: broker local | H8: los dos tableros |
| Notificaciones | S7: la variable que las dispara llega a la nube | H7 |
| Desactivar alarmas físicas desde el tablero | S6: estado lógico | H6: zumbador físico |
| Módulo de IA | S9: capa analítica y reglas | H9: capa generativa · H10: agua real |
| Integración funcional de todos los componentes | S1 a S9: software | H2: cadena física completa |

La columna de la derecha no es una formalidad. Es la lista de lo que esta documentación **no demuestra**, y se discute en [C3.9](C3.9-Autoevaluacion-del-protocolo).
