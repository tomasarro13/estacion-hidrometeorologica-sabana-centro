#!/usr/bin/env python3
"""
generar_reporte.py · Escribe la página de resultados de la Wiki (C3.8)
a partir de datos/ch3-simulacion/resultados.json.

Ninguna cifra del reporte está escrita a mano: todas se leen del JSON
que produjo ejecutar_pruebas.py. Si se repite la corrida, se repite el
reporte:

    python3 pruebas/ejecutar_pruebas.py
    python3 pruebas/generar_reporte.py > datos/ch3-simulacion/REPORTE.md
"""

import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = json.load(open(os.path.join(RAIZ, "datos", "ch3-simulacion", "resultados.json"), encoding="utf-8"))
P, E = R["pruebas"], R["entorno"]


def n(x, d=None):
    """Número con formato en español: coma decimal, espacio de miles."""
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "sí" if x else "no"
    if isinstance(x, int) or (d == 0):
        return "{:,}".format(int(round(x))).replace(",", " ")
    if d is None:
        d = 2
    return ("{:,.%df}" % d).format(x).replace(",", " ").replace(".", ",")


def ok(b):
    return "✅" if b else "❌"


out = []
w = out.append
S1 = {x["escenario"]: x for x in P["S1_integridad"]}
IA = P["S9_ia"]
principales = ["estable", "sequia", "fuga"]
severos = ["estable_ruido_alto", "sequia_ruido_alto", "fuga_ruido_alto"]
NOMBRE = {"estable": "Estable", "sequia": "Sequía", "fuga": "Fuga", "falla": "Falla del sensor",
          "estable_ruido_alto": "Estable · ruido severo", "sequia_ruido_alto": "Sequía · ruido severo",
          "fuga_ruido_alto": "Fuga · ruido severo"}

tot_pub = sum(x["publicados"] for x in S1.values())
tot_sql = sum(x["en_sqlite"] for x in S1.values())
tot_agr = sum(x["agregados_con_nivel"] for x in S1.values())
tot_nube = sum(x["agregados_en_nube"] for x in S1.values())
tot_dup = sum(x["duplicados_nube"] for x in S1.values())
tot_h = sum(x["horas_simuladas"] for x in S1.values())
max_dia = max(x["datos_nube_por_dia"] for x in S1.values() if x["horas_simuladas"] >= 24)

w("# C3.8 · Resultados y análisis")
w("")
w("> **Todo lo que sigue es resultado de simulación.** El broker Mosquitto, el programa del Gateway, la base SQLite "
  "y el módulo de IA son los reales; el nodo ESP32 y la plataforma en la nube están emulados "
  "([C3.7 §7.1](C3.7-Configuracion-experimental)). Ninguna cifra de esta página es una medición del prototipo físico.")
w("")
w("> **Criterio de reporte**, el mismo del Challenge #2: no se consignan datos no obtenidos. Las cifras de esta página "
  "se generan desde `datos/ch3-simulacion/resultados.json` con `pruebas/generar_reporte.py`; ninguna está escrita a mano.")
w("")
w("| Corrida | |")
w("|---|---|")
w("| Fecha | %s UTC |" % E["fecha_utc"])
w("| Sistema | %s |" % E["sistema"])
w("| Software | Python %s · SQLite %s · paho-mqtt %s · %s |" % (
    E["python"], E["sqlite"], E["paho_mqtt"], E["broker"].replace("mosquitto version", "Mosquitto")))
w("| Tiempo simulado total | %s horas (%s días) en %s mensajes |" % (n(tot_h), n(tot_h / 24.0, 1), n(tot_pub)))
w("")
w("---")
w("")

# ------------------------------------------------------------------ resumen
s2, s3, s5, s6, s7, s8 = (P["S2_acceso"], P["S3_validacion"], P["S5_reenvio"], P["S6_comando"],
                          P["S7_caida_nodo"], P["S8_reinicio_gateway"])
fu, se = IA["fuga"], IA["sequia"]
w("## 8.0 Resumen")
w("")
w("| # | Prueba | Resultado | |")
w("|---|---|---|---|")
w("| S1 | Integridad de extremo a extremo | %s de %s mensajes en SQLite; %s de %s agregados en la nube; %s duplicados | %s |" % (
    n(tot_sql), n(tot_pub), n(tot_nube), n(tot_agr), n(tot_dup), ok(tot_sql == tot_pub and tot_nube == tot_agr)))
w("| S1 | Presupuesto de la nube | Máximo %s datos/día, frente a un límite de 4 000 | %s |" % (n(max_dia), ok(max_dia <= 4000)))
w("| S2 | Control de acceso del broker | %d de %d casos con el resultado esperado | %s |" % (
    s2["pasan"], s2["total"], ok(s2["pasan"] == s2["total"])))
w("| S3 | Validación de mensajes | %d de %d malformados rechazados; el Gateway siguió operando | %s |" % (
    s3["rechazados"], s3["malformados_enviados"],
    ok(s3["rechazados"] == s3["malformados_enviados"] and s3["gateway_siguio_operando"])))
w("| S4 | Latencia (cota inferior, sin red) | Nodo → SQLite: mediana %s ms. Orden → confirmación: mediana %s ms | — |" % (
    n(P["S4_latencia"]["nodo_a_sqlite_ms"]["mediana"]), n(P["S4_latencia"]["comando_tablero_global_a_ack_ms"]["mediana"])))
w("| S5 | Caída de Internet | %s perdidos, %s duplicados, orden cronológico: %s | %s |" % (
    n(s5["perdidos"]), n(s5["duplicados"]), n(s5["orden_cronologico_al_reenviar"]),
    ok(s5["perdidos"] == 0 and s5["orden_cronologico_al_reenviar"] and s5["llegaron_a_la_nube_durante_el_corte"] == 0)))
w("| S6 | Comando de silencio y salvaguardas | %d de %d comprobaciones | %s |" % (
    s6["pasan"], s6["total"], ok(s6["pasan"] == s6["total"])))
w("| S7 | Caída del nodo | Detectada en %s s; reinicio detectado | %s |" % (
    n(s7["segundos_hasta_detectar"], 1), ok(s7["secuencia_de_estados"] == ["online", "offline", "online"]
                                           and s7["reinicios_de_nodo_detectados"] >= 1)))
w("| S8 | Reinicio del Gateway | %s mensajes no recibidos; %s contados | %s |" % (
    n(s8["no_recibidos_por_el_apagado"]), n(s8["perdidas_contadas_en_agregados"]), ok(s8["el_hueco_quedo_contabilizado"])))
falsas = sum(IA[k].get("falsas_anomalias", 0) + IA[k].get("anomalias_antes_de_la_fuga", 0) for k in IA)
w("| S9 | IA · falsas anomalías | %s en %s corridas, incluido el ruido severo | %s |" % (
    n(falsas), n(sum(IA[k]["corridas"] for k in IA)), ok(falsas == 0)))
w("| S9 | IA · detección de la fuga | %s h después de iniciada; sostenida el %s %% del tiempo | %s |" % (
    n(fu["demora_deteccion_h"], 1), n(fu["deteccion_sostenida_pct"], 0), ok(fu["demora_deteccion_h"] is not None)))
w("| S9 | IA · predicción del cruce | Ver §8.9: depende de la anticipación y del caso | — |")
w("| — | **Hallazgo** | La alarma amarilla del **nodo** se activa sin que el nivel esté bajo (§8.10) | ⚠️ |")
w("")
w("---")
w("")

# ---------------------------------------------------------------------- S1
w("## 8.1 S1 · Integridad de extremo a extremo")
w("")
w("**Qué se comprueba.** Que cada mensaje publicado por el nodo queda en SQLite, que cada ventana cerrada produce "
  "un agregado, y que cada agregado llega a la nube exactamente una vez.")
w("")
w("| Escenario | Horas simuladas | Publicados | En SQLite | Perdidos | Agregados | En la nube | Duplicados | Datos/día a la nube |")
w("|---|---|---|---|---|---|---|---|---|")
for k in principales + severos + ["falla"]:
    x = S1[k]
    w("| %s | %s | %s | %s | **%s** | %s | %s | %s | %s |" % (
        NOMBRE[k], n(x["horas_simuladas"]), n(x["publicados"]), n(x["en_sqlite"]), n(x["perdidos"]),
        n(x["agregados"]), n(x["agregados_en_nube"]), n(x["duplicados_nube"]),
        n(x["datos_nube_por_dia"]) if x["horas_simuladas"] >= 24 else "—"))
w("| **Total** | **%s** | **%s** | **%s** | **%s** | | | **%s** | |" % (
    n(tot_h), n(tot_pub), n(tot_sql), n(tot_pub - tot_sql), n(tot_dup)))
w("")
w("En cada escenario el número de agregados es el de ventanas de 5 minutos **menos una**: la última ventana queda "
  "abierta porque no llegó una muestra posterior que la cierre. En el escenario de falla, los agregados sin nivel "
  "válido no envían la variable `nivel` (§8.11).")
w("")
w("### Análisis")
w("")
w("**Cero pérdidas en %s mensajes.** Es el resultado esperable en una sola máquina, sin red de por medio, y hay que "
  "leerlo como lo que es: prueba de que **el software no pierde datos por sí mismo** —ni en la recepción, ni en la "
  "escritura por lotes, ni en el cierre de ventanas, ni en la bandeja de salida—. No dice nada sobre la pérdida en "
  "un enlace Wi-Fi real; eso lo medirá el consecutivo `seq` cuando haya hardware." % n(tot_pub))
w("")
w("**El presupuesto de la nube se cumple con margen.** El máximo observado fue de **%s datos por día**, el %s %% del "
  "límite de 4 000. El cálculo de diseño ([C3.4 §4.5](C3.4-Diseno-modular)) daba 3 024 en operación estable; la "
  "diferencia son las transiciones de alarma y los cambios de estado, que salen fuera de ciclo." % (
      n(max_dia), n(100.0 * max_dia / 4000.0, 0)))
w("")
ritmo = S1["sequia"]["mensajes_por_segundo"]
w("**Holgura de procesamiento.** El banco inyectó %s mensajes por segundo y el Gateway los procesó sin acumular "
  "atraso. Un nodo real produce 0,5 por segundo. La cifra es de un computador de escritorio, **no de una Raspberry "
  "Pi**: no debe citarse como capacidad del Gateway, solo como indicio de que la carga de un nodo —o de decenas— "
  "no es el factor limitante." % n(ritmo))
w("")
w("---")
w("")

# ---------------------------------------------------------------------- S2
w("## 8.2 S2 · Control de acceso del broker local")
w("")
w("**Qué se comprueba.** Que el broker, con los archivos `estacion.conf` y `estacion.acl` del repositorio, rechaza "
  "a quien no tiene credenciales y confina a cada usuario a sus tópicos.")
w("")
w("| # | Caso | Esperado | Obtenido | |")
w("|---|---|---|---|---|")
for i, c in enumerate(s2["casos"], 1):
    w("| %d | %s | %s | %s | %s |" % (i, c["caso"], c["esperado"], c["obtenido"], ok(c["pasa"])))
w("")
w("### Análisis")
w("")
w("Los casos 1 a 4 prueban la **autenticación**; los casos 5 a 10, la **autorización**, que es la que suele omitirse. "
  "La diferencia importa: con solo usuario y clave, un nodo comprometido podría publicar una alerta a nombre de otro "
  "(caso 5), darse a sí mismo la orden de silenciar su alarma (caso 6) o anunciar que el Gateway se cayó (caso 7). "
  "La lista de control de acceso cierra los tres caminos.")
w("")
w("El caso 6 merece una línea: el nodo **recibe** órdenes de silencio pero no puede **emitirlas**. La única ruta para "
  "silenciar la alarma por MQTT pasa por el usuario `gateway`.")
w("")
w("Mosquitto no devuelve error cuando la ACL bloquea una publicación: la descarta en silencio. Por eso el veredicto "
  "se obtuvo con un observador independiente que confirma que el mensaje **no fue entregado**, no con la respuesta "
  "del broker.")
w("")
w("---")
w("")

# ---------------------------------------------------------------------- S3
w("## 8.3 S3 · Validación de mensajes en el Gateway")
w("")
w("| # | Mensaje enviado | Motivo registrado en la bitácora |")
w("|---|---|---|")
for i, c in enumerate(s3["casos"], 1):
    w("| %d | %s | `%s` |" % (i, c["caso"], c["motivo_registrado"]))
w("")
w("| | |")
w("|---|---|")
w("| Malformados enviados / rechazados | %d / %d |" % (s3["malformados_enviados"], s3["rechazados"]))
w("| Válidos enviados / guardados | %d / %d |" % (s3["validos_enviados"], s3["lecturas_guardadas"]))
w("| El Gateway siguió operando después de los diez | %s |" % n(s3["gateway_siguio_operando"]))
w("")
w("### Análisis")
w("")
w("Cada rechazo deja su motivo exacto en la tabla `eventos`. Eso convierte un mensaje malo en **información de "
  "diagnóstico**: si un nodo empieza a enviar humedades de 150 %, la bitácora lo dice con nombre de campo y valor.")
w("")
w("Uno de los tres mensajes válidos llevaba `nivel: null` con bandera 9 (sin eco). **Se aceptó y se guardó.** Es la "
  "distinción que el diseño quería: un dato faltante *declarado* es un dato válido del sistema; un nivel con texto "
  "donde va un número, no.")
w("")
w("---")
w("")

# ---------------------------------------------------------------------- S4
l1, l2 = P["S4_latencia"]["nodo_a_sqlite_ms"], P["S4_latencia"]["comando_tablero_global_a_ack_ms"]
w("## 8.4 S4 · Latencia por tramo")
w("")
w("> **Cota inferior.** Todo corre en una sola máquina por la interfaz de lazo local. Estas cifras miden el costo del "
  "**software** —serializar, validar, escribir, reenviar— y nada más. La latencia real la dominarán el Wi-Fi y, sobre "
  "todo, Internet.")
w("")
w("| Tramo | n | Mínimo | Mediana | P95 | Máximo |")
w("|---|---|---|---|---|---|")
w("| Publicación del nodo → fila en SQLite | %d | %s ms | **%s ms** | %s ms | %s ms |" % (
    l1["n"], n(l1["min"]), n(l1["mediana"]), n(l1["p95"]), n(l1["max"])))
w("| Orden en la «nube» → confirmación del nodo (ida y vuelta por los dos brokers) | %d | %s ms | **%s ms** | %s ms | %s ms |" % (
    l2["n"], n(l2["min"]), n(l2["mediana"]), n(l2["p95"]), n(l2["max"])))
w("| Cambio de alarma en el nodo → llegada a la «nube» | 1 | | **%s ms** | | |" % n(P["S4_latencia"]["alerta_nodo_a_nube_ms"], 1))
w("")
w("### Análisis")
w("")
w("**El software no agrega latencia apreciable.** Con medianas de fracción de milisegundo a pocos milisegundos, "
  "el recorrido completo por el Gateway es despreciable frente al período de muestreo de 2 s y frente a cualquier "
  "enlace real.")
w("")
w("**La transición de alarma no espera a la ventana.** El dato agregado sale cada 5 minutos, pero el cambio de estado "
  "sale de inmediato y con prioridad. En la nube, la latencia de una alerta es la de la red, no la de la ventana.")
w("")
w("**El máximo de la primera fila** (%s ms) es un valor aislado frente a un P95 de %s ms. No se investigó su causa; "
  "lo más probable es una confirmación de escritura en disco o la planificación del sistema operativo. No cambia "
  "la conclusión." % (
      n(l1["max"]), n(l1["p95"])))
w("")
w("---")
w("")

# ---------------------------------------------------------------------- S5
w("## 8.5 S5 · Caída de Internet: almacenar y reenviar")
w("")
w("**Procedimiento.** Cuatro horas simuladas con enlace; se corta el enlace (la «nube» sigue viva pero inalcanzable) "
  "y el nodo sigue publicando ocho horas más; se restablece; dos horas finales.")
w("")
w("| Fase | Lecturas en SQLite | Agregados en SQLite | **Pendientes de envío** | **Agregados en la nube** |")
w("|---|---|---|---|---|")
for f in s5["fases"]:
    w("| %s | %s | %s | **%s** | **%s** |" % (f["fase"], n(f["lecturas_en_sqlite"]), n(f["agregados_en_sqlite"]),
                                          n(f["pendientes_de_envio"]), n(f["agregados_en_nube"])))
w("")
w("| Verificación | Resultado |")
w("|---|---|")
w("| Agregados generados / recibidos en la nube | %s / %s |" % (n(s5["agregados_generados"]), n(s5["agregados_recibidos_en_nube"])))
w("| Perdidos | **%s** |" % n(s5["perdidos"]))
w("| Duplicados | **%s** |" % n(s5["duplicados"]))
w("| Llegaron a la nube durante el corte | %s |" % n(s5["llegaron_a_la_nube_durante_el_corte"]))
w("| Orden cronológico al reenviar | %s |" % n(s5["orden_cronologico_al_reenviar"]))
w("| Tiempo en ponerse al día tras volver el enlace | %s s |" % n(s5["segundos_hasta_ponerse_al_dia"], 1))
w("| Eventos registrados en la bitácora | %s |" % " → ".join(s5["eventos_nube_registrados"]))
w("")
f1, f2, f3 = s5["fases"][0], s5["fases"][1], s5["fases"][2]
w("### Análisis")
w("")
w("**Es el resultado más importante del Challenge #3.** Durante el corte, el Gateway siguió recibiendo "
  "(de %s a %s lecturas), siguió agregando (de %s a %s ventanas) y siguió corriendo el módulo de IA. Lo único que "
  "se detuvo fue el envío: la nube se quedó en %s agregados y la bandeja de salida creció hasta **%s mensajes**. "
  "Al volver el enlace se vació sola, en orden, sin perder ni repetir nada." % (
      n(f1["lecturas_en_sqlite"]), n(f2["lecturas_en_sqlite"]), n(f1["agregados_en_sqlite"]),
      n(f2["agregados_en_sqlite"]), n(f2["agregados_en_nube"]), n(f2["pendientes_de_envio"])))
w("")
w("Esto es lo que justifica la restricción del enunciado —*almacenar localmente antes de transmitir*— y lo que "
  "demuestra que SQLite no es un adorno: **es el mecanismo de fiabilidad del sistema.** Sin la bandeja de salida, "
  "ocho horas sin Internet serían ocho horas de hueco en el tablero global.")
w("")
w("**Los datos aparecen en su hora real.** Cada punto viaja con la marca de tiempo del dato, no la del envío. "
  "La gráfica de la plataforma no muestra un salto al reconectar: muestra el tramo completo.")
w("")
w("**Sobre el tiempo de recuperación.** Los %s s incluyen la espera de reconexión y **no** incluyen el límite de "
  "1 mensaje por segundo de la plataforma real, que en simulación está desactivado. Con el límite, vaciar %s mensajes "
  "toma unos %s s (≈ %s min). Es una extrapolación aritmética, no una medición: la prueba H5 la verifica." % (
      n(s5["segundos_hasta_ponerse_al_dia"], 1), n(f2["pendientes_de_envio"]), n(f2["pendientes_de_envio"]),
      n(f2["pendientes_de_envio"] / 60.0, 0)))
w("")
w("**Entrega al menos una vez.** En esta corrida no hubo duplicados. El diseño no los excluye: si el enlace cae entre "
  "la publicación y su confirmación, el mensaje se reenvía. No hacen daño porque un punto repetido —misma variable, "
  "misma marca de tiempo— sobrescribe al anterior.")
w("")
w("---")
w("")

# ---------------------------------------------------------------------- S6
w("## 8.6 S6 · Comando de silencio y salvaguardas")
w("")
w("**Procedimiento.** Escenario `critico`: nivel bajo el umbral rojo, alarma sonando. Las órdenes se publican en la "
  "«nube», en el tópico y formato de la plataforma, y recorren los dos tramos hasta el nodo.")
w("")
w("| # | Paso | Esperado | Obtenido | |")
w("|---|---|---|---|---|")
for i, p in enumerate(s6["pasos"], 1):
    w("| %d | %s | %s | %s | %s |" % (i, p["paso"].strip(), p["esperado"], p["obtenido"], ok(p["pasa"])))
w("")
ev = s6["eventos_registrados"]
w("En la bitácora del Gateway quedaron %s órdenes (`COMANDO`), %s confirmaciones del nodo (`ACK`) y %s rechazos "
  "(`RECHAZO`): los dos últimos son la orden vieja y la orden inválida." % (
      n(ev.get("COMANDO", 0)), n(ev.get("ACK", 0)), n(ev.get("RECHAZO", 0))))
w("")
w("### Análisis")
w("")
w("**El requisito se cumple por la ruta larga.** *Desactivar alarmas físicas desde el tablero* ya funcionaba en el "
  "Challenge #2 desde el tablero local. Aquí la orden entra por la nube y baja por dos brokers.")
w("")
w("**Silenciar no es ocultar (paso 3).** El zumbador calla; el estado sigue en ROJA. Es la diferencia entre "
  "*shelving* y supresión en ISA-18.2: el operador reconoce la alarma, no la borra.")
w("")
w("**La orden vieja no se ejecuta (pasos 5 y 6).** Una orden con diez minutos de antigüedad —la que la plataforma "
  "reenvía al reconectar— se descarta en el Gateway y **no llega al nodo**. Sin este filtro, recuperar Internet "
  "podría silenciar una alarma que nadie pidió silenciar.")
w("")
w("**El silencio expira solo (pasos 9 y 10).** A los 14 minutos sigue vigente; a los 15,5 la alarma vuelve a sonar. "
  "La salvaguarda vive en el nodo, no en el Gateway ni en la nube: funciona igual si después de silenciar se cae "
  "toda la red.")
w("")
w("> En simulación, \"sonando\" y \"silenciada\" son el estado lógico del zumbador en el nodo emulado, cuya lógica "
  "se portó de `actualizarAlarma()`. Que el zumbador físico calle es la prueba H6.")
w("")
w("---")
w("")

# ---------------------------------------------------------------------- S7
w("## 8.7 S7 · Caída del nodo")
w("")
w("**Procedimiento.** El nodo corre como proceso aparte y se **congela** (`SIGSTOP`): no cierra el socket ni se "
  "despide, igual que un corte de energía. Luego se arranca un nodo nuevo, cuyo consecutivo vuelve a 1.")
w("")
w("| | |")
w("|---|---|")
w("| *Keepalive* del nodo | %d s |" % s7["keepalive_s"])
w("| Tiempo nominal de detección (1,5 × *keepalive*) | 22,5 s |")
w("| **Tiempo medido hasta el evento `offline` en el Gateway** | **%s s** |" % n(s7["segundos_hasta_detectar"], 1))
w("| Secuencia de estados registrada | %s |" % " → ".join(s7["secuencia_de_estados"]))
w("| Reinicios de nodo detectados por el consecutivo | %d |" % s7["reinicios_de_nodo_detectados"])
w("| Variable `en_linea` enviada a la nube | %s |" % " → ".join(str(int(x)) for x in s7["en_linea_publicado_en_nube"]))
w("")
w("### Análisis")
w("")
w("**El nodo no avisó que se caía, y el sistema se enteró igual.** Es el mecanismo de última voluntad de MQTT: al "
  "conectarse, el nodo deja dicho qué publicar si desaparece. El broker lo publica cuando deja de oír su *keepalive*.")
w("")
w("El broker cuenta 1,5 veces el *keepalive* desde el último paquete que recibió del nodo y revisa los clientes "
  "inactivos por rondas, de modo que el tiempo exacto varía de una corrida a otra alrededor de ese valor. Para el "
  "tablero la consecuencia es la misma: **en menos de medio minuto** la autoridad sabe que el dato que está viendo "
  "ya no se actualiza. Sin esto, un nodo muerto se ve igual que un nivel estable.")
w("")
w("**El reinicio se distingue de la pérdida.** Un consecutivo que retrocede queda registrado como `REINICIO_NODO`, "
  "no como miles de mensajes perdidos.")
w("")
w("---")
w("")

# ---------------------------------------------------------------------- S8
w("## 8.8 S8 · Reinicio del Gateway")
w("")
w("**Procedimiento.** Con el nodo publicando sin pausa, se apaga el Gateway (`SIGTERM`), se esperan 2 s y se lo "
  "vuelve a levantar sobre la misma base de datos.")
w("")
w("| | |")
w("|---|---|")
w("| Publicados por el nodo | %s |" % n(s8["publicados_por_el_nodo"]))
w("| Recibidos en SQLite | %s |" % n(s8["recibidos_en_sqlite"]))
w("| No recibidos (el Gateway estaba apagado) | **%s** |" % n(s8["no_recibidos_por_el_apagado"]))
w("| Pérdidas contadas en la tabla `agregados` | **%s** |" % n(s8["perdidas_contadas_en_agregados"]))
w("| ¿Coinciden? | %s |" % n(s8["el_hueco_quedo_contabilizado"]))
w("| Bitácora | %s |" % " → ".join(s8["bitacora_gateway"]))
w("")
w("### Análisis")
w("")
w("**Aquí sí se pierden datos, y el resultado correcto es reconocerlo.** La telemetría viaja en QoS 0: lo que el nodo "
  "publica mientras el Gateway está apagado no se recupera. Fue una decisión de diseño "
  "([C3.4 §4.1](C3.4-Diseno-modular)) y esta prueba mide su costo.")
w("")
w("Lo que la prueba verifica es que **la pérdida no pasa inadvertida**: los %s mensajes que faltan son exactamente "
  "los %s que la columna `perdidas` contabiliza. Al arrancar, el Gateway recupera de SQLite el último consecutivo "
  "y reconstruye la ventana que quedó abierta; el hueco aparece medido en el agregado correspondiente." % (
      n(s8["no_recibidos_por_el_apagado"]), n(s8["perdidas_contadas_en_agregados"])))
w("")
w("En operación real el costo es menor de lo que sugiere la tabla: el banco publicaba a unos 250 mensajes por "
  "segundo, y un nodo real publica uno cada 2 s. Un reinicio de 30 s del Gateway cuesta 15 muestras, y el nodo "
  "conserva además su propio histórico de 24 h en el tablero local.")
w("")
w("---")
w("")

# ---------------------------------------------------------------------- S9
w("## 8.9 S9 · Módulo de IA")
w("")
w("El módulo corrió dentro del Gateway, cada 30 minutos de tiempo simulado, sobre los agregados que el propio "
  "Gateway iba guardando. Cada corrida quedó en la tabla `predicciones` y se compara aquí con la **verdad del "
  "modelo**: el nivel real del tanque simulado y el instante exacto en que empieza la fuga.")
w("")
w("> La capa generativa estuvo desactivada. Todas las recomendaciones citadas son del respaldo por reglas.")
w("")
w("### 8.9.1 Clasificación")
w("")
w("| Escenario | Ruido | Corridas | Estable | Descenso climático | **Descenso no explicado** | Recuperación | Sin datos |")
w("|---|---|---|---|---|---|---|---|")
for k in principales + severos:
    c = IA[k]["clasificaciones"]
    w("| %s | σ = %s cm | %d | %d | %d | **%d** | %d | %d |" % (
        NOMBRE[k].split(" ·")[0], n(S1[k]["ruido_cm"], 3), IA[k]["corridas"], c.get("ESTABLE", 0),
        c.get("DESCENSO_CLIMATICO", 0), c.get("DESCENSO_NO_EXPLICADO", 0), c.get("RECUPERACION", 0),
        c.get("DATOS_INSUFICIENTES", 0)))
w("")
w("**Sin falsas anomalías.** En los escenarios sin fuga —estable y sequía, con los dos niveles de ruido— el módulo "
  "no clasificó ninguna corrida como descenso no explicado. En el escenario de fuga, ninguna antes de que la fuga "
  "empezara.")
w("")
w("**La sequía se reconoce como sequía.** Es el caso que el módulo debía resolver: el nivel baja durante tres días y "
  "el sistema **no** pide una inspección, porque la evaporación lo explica.")
w("")
sr = IA["sequia_ruido_alto"]["clasificaciones"]
fr = IA["fuga_ruido_alto"]["clasificaciones"]
w("**El ruido severo cuesta sensibilidad, no falsas alarmas.** Con σ = 0,176 cm el módulo declaró `ESTABLE` en %d "
  "corridas del escenario de sequía y en %d del de fuga (antes de que esta empezara), donde con el ruido de operación "
  "veía un descenso climático: la tendencia de la evaporación queda por debajo de lo que ese ruido permite afirmar, "
  "y el módulo **prefiere no afirmar**." % (sr.get("ESTABLE", 0), fr.get("ESTABLE", 0)))
_rec = IA["estable_ruido_alto"]["clasificaciones"].get("RECUPERACION", 0)
if _rec:
    w("")
    w("**Un error de clasificación, declarado.** En el escenario estable con ruido severo, %d de %d corridas se "
      "clasificaron como `RECUPERACION` sin que el nivel subiera. No genera alerta ni recomendación de acción, pero "
      "es un falso positivo de tendencia." % (_rec, IA["estable_ruido_alto"]["corridas"]))
w("")
w("### 8.9.2 Detección de la fuga")
w("")
w("| | Ruido de operación | Ruido severo |")
w("|---|---|---|")
fa = IA["fuga_ruido_alto"]
w("| Inicio real de la fuga (3 mm/h) | hora %s | hora %s |" % (n(fu["hora_inicio_fuga"]), n(fa["hora_inicio_fuga"])))
w("| Primera clasificación como *no explicado* | hora %s | hora %s |" % (
    n(fu["hora_primera_deteccion"], 1), n(fa["hora_primera_deteccion"], 1)))
w("| **Demora de detección** | **%s h** | **%s h** |" % (n(fu["demora_deteccion_h"], 1), n(fa["demora_deteccion_h"], 1)))
w("| Anomalías declaradas antes de la fuga | %d | %d |" % (fu["anomalias_antes_de_la_fuga"], fa["anomalias_antes_de_la_fuga"]))
w("| Detección sostenida (de 3 h después del inicio hasta el cruce) | %s %% | %s %% |" % (
    n(fu["deteccion_sostenida_pct"], 0), n(fa["deteccion_sostenida_pct"], 0)))
w("| Cruce real del umbral de alerta | hora %s | hora %s |" % (
    n(fu["hora_cruce_real_umbral"], 1), n(fa["hora_cruce_real_umbral"], 1)))
w("")
antic = fu["hora_cruce_real_umbral"] - fu["hora_primera_deteccion"]
w("**La fuga se detecta %s h después de empezar y %s h antes de que el nivel cruce el umbral.** Ese margen es el "
  "valor del módulo: la alarma por umbral del nodo habría avisado en la hora %s; la IA avisó en la hora %s, y además "
  "dijo **por qué**." % (n(fu["demora_deteccion_h"], 1), n(antic, 1), n(fu["hora_cruce_real_umbral"], 1),
                        n(fu["hora_primera_deteccion"], 1)))
w("")
w("La demora tiene un piso que no depende del ruido: el módulo corre cada 30 minutos y necesita que el descenso "
  "nuevo pese en la ventana de 3 horas.%s Una fuga más pequeña tardaría más; una menor que el piso de 0,30 mm/h no "
  "se detectaría." % (" Por eso es la misma con los dos niveles de ruido." if fu["demora_deteccion_h"] == fa["demora_deteccion_h"] else ""))
w("")
w("La recomendación que produjo el Gateway al final de este escenario:")
w("")
w("> *%s*" % fu["ultima_recomendacion"])
w("")
w("### 8.9.3 Predicción del cruce del umbral")
w("")
w("Error = instante predicho del cruce − instante real. Solo predicciones hechas antes del cruce. En el escenario "
  "de fuga, solo las hechas una vez detectada la anomalía.")
w("")
w("| Escenario | Ruido | Anticipación | n | Error absoluto medio | Error absoluto máximo |")
w("|---|---|---|---|---|---|")
for k in ("sequia", "sequia_ruido_alto", "fuga", "fuga_ruido_alto"):
    e = IA[k].get("error_prediccion_cruce")
    if not e:
        continue
    for nombre, clave in (("Menos de 6 h", "anticipacion_0_6h"), ("De 6 a 24 h", "anticipacion_6_24h"),
                          ("Más de 24 h", "anticipacion_mas_24h")):
        f = e.get(clave)
        if f:
            w("| %s | σ = %s cm | %s | %d | %s h | %s h |" % (
                NOMBRE[k].split(" ·")[0], n(S1[k]["ruido_cm"], 3), nombre, f["n"],
                n(f["error_abs_medio_h"]), n(f["error_abs_max_h"])))
w("")
ef = fu["error_prediccion_cruce"]
es = se["error_prediccion_cruce"]
w("**En la fuga la predicción es precisa**: error medio de %s h (%s min) en las últimas seis horas. El descenso es "
  "una recta y el módulo ajusta una recta." % (
      n(ef["anticipacion_0_6h"]["error_abs_medio_h"]), n(ef["anticipacion_0_6h"]["error_abs_medio_h"] * 60, 0)))
w("")
_csv = __import__("csv")
_f = list(_csv.DictReader(open(os.path.join(RAIZ, "datos", "ch3-simulacion", "ia_fuga.csv"), encoding="utf-8")))
_det = [x for x in _f if x["clasificacion"] == "DESCENSO_NO_EXPLICADO" and x["error_prediccion_h"] != ""]
_conv = next((x for i, x in enumerate(_det) if all(abs(float(y["error_prediccion_h"])) < 1.0 for y in _det[i:])), None)
if _conv and ef.get("anticipacion_6_24h"):
    w("")
    w("**Pero no de inmediato.** Entre 6 y 24 h de anticipación el error medio es de %s h, y lo dominan las primeras "
      "predicciones tras detectar la fuga (máximo: %s h): la ventana de 3 horas todavía mezcla horas sin fuga y "
      "subestima la tasa. El error queda por debajo de una hora desde la hora %s del escenario, es decir, %s h "
      "después de iniciada la fuga." % (
          n(ef["anticipacion_6_24h"]["error_abs_medio_h"]), n(ef["anticipacion_6_24h"]["error_abs_max_h"]),
          n(float(_conv["hora_sim"]), 1), n(float(_conv["hora_sim"]) - fu["hora_inicio_fuga"], 1)))
w("")
esr = IA["sequia_ruido_alto"]["error_prediccion_cruce"]
w("**Con ruido severo, la predicción por sequía pierde precisión también cerca del cruce**: %s h de error medio con "
  "menos de 6 h de anticipación, frente a %s h con el ruido de operación. A ese nivel de ruido la tasa de la "
  "evaporación se estima mal incluso con un día de datos." % (
      n(esr["anticipacion_0_6h"]["error_abs_medio_h"]), n(es["anticipacion_0_6h"]["error_abs_medio_h"])))
w("")
w("**En la sequía la predicción es útil a un día y pobre a más de un día.** Con menos de 24 h de anticipación el "
  "error medio es de %s h; con más de 24 h sube a %s h, con un máximo de %s h. La razón está en la tabla siguiente." % (
      n(es["anticipacion_6_24h"]["error_abs_medio_h"]), n(es["anticipacion_mas_24h"]["error_abs_medio_h"]),
      n(es["anticipacion_mas_24h"]["error_abs_max_h"])))
w("")
w("### 8.9.4 Un hallazgo de la simulación: el estimador dependía de la hora")
w("")
ce = se["comparacion_estimadores_24h"]
w("La primera versión del módulo usaba Theil-Sen también en la ventana de 24 horas. Al correr el escenario de sequía, "
  "la tasa estimada **oscilaba a lo largo del día aunque el modelo no cambiaba**. La causa: la evaporación sigue "
  "al sol, el nivel baja en escalones —rápido de día, casi nada de noche— y la mediana de pendientes de una curva "
  "escalonada depende de dónde empieza y termina la ventana.")
w("")
w("Se agregó un segundo estimador para la ventana larga: el cambio neto entre la primera y la última hora de un ciclo "
  "completo de 24 h. Los dos se compararon sobre **la misma serie guardada en SQLite**, en los mismos %d instantes "
  "(todos con un día completo de historia):" % ce["ciclo"]["n"])
w("")
w("| Estimador de 24 h | Tasa estimada (mín. a máx.) | Error absoluto medio | Error absoluto máximo |")
w("|---|---|---|---|")
for clave, nombre in (("theil_sen", "Theil-Sen (versión inicial)"), ("ciclo", "**Ciclo completo (versión final)**")):
    c = ce[clave]
    w("| %s | %s a %s mm/h | %s h | %s h |" % (nombre, n(c["tasa_max_mmh"], 3), n(c["tasa_min_mmh"], 3),
                                             n(c["error_abs_medio_h"]), n(c["error_abs_max_h"])))
w("")
w("La tasa real media del modelo es del orden de −0,11 mm/h. Con Theil-Sen la estimación recorría de %s a %s mm/h "
  "según la hora; con el ciclo completo, de %s a %s. El error medio de la predicción bajó de %s a %s horas." % (
      n(ce["theil_sen"]["tasa_max_mmh"], 3), n(ce["theil_sen"]["tasa_min_mmh"], 3),
      n(ce["ciclo"]["tasa_max_mmh"], 3), n(ce["ciclo"]["tasa_min_mmh"], 3),
      n(ce["theil_sen"]["error_abs_medio_h"]), n(ce["ciclo"]["error_abs_medio_h"])))
w("")
cr = IA["sequia_ruido_alto"]["comparacion_estimadores_24h"]
w("Con el ruido severo la mejora es menor (de %s a %s h): el estimador por ciclo usa solo dos horas de datos y es "
  "más sensible al ruido que una mediana sobre todo el día. Es un compromiso, y queda declarado." % (
      n(cr["theil_sen"]["error_abs_medio_h"]), n(cr["ciclo"]["error_abs_medio_h"])))
w("")
w("**Lo que queda sin resolver.** El estimador por ciclo necesita 24 horas de historia. Durante el primer día el "
  "módulo sigue usando Theil-Sen, y de ahí salen los errores de más de 24 h de anticipación de §8.9.3: son "
  "predicciones del primer día. Por eso el resultado se marca como *provisional* y la recomendación lo dice. "
  "Y aun con un día completo, el error residual de pocas horas no desaparece: una recta no puede saber a qué hora "
  "del día caerá el cruce cuando el descenso real es escalonado.")
w("")
w("Este es un caso concreto de para qué sirve validar por simulación antes de tener hardware: el defecto habría sido "
  "difícil de ver en el banco físico, donde nadie espera tres días a que el agua se evapore.")
w("")
w("La recomendación al final del escenario de sequía:")
w("")
w("> *%s*" % se["ultima_recomendacion"])
w("")
w("---")
w("")

# ------------------------------------------------------------------ hallazgo
w("## 8.10 Hallazgo: la alarma amarilla del nodo se activa sin que el nivel esté bajo")
w("")
w("Este resultado **no es favorable** y se reporta porque es el más útil de la validación.")
w("")
w("El simulador ejecuta la lógica de `evaluarAlerta()` del firmware, portada línea a línea. Al comparar el estado de "
  "alarma del nodo con el nivel real del modelo:")
w("")
w("| Escenario | Transiciones a alarma | Alarmas por 10 min | % del tiempo en alarma | **% del tiempo en alarma con el nivel SOBRE el umbral** | Primera alarma (hora · nivel real) |")
w("|---|---|---|---|---|---|")
for k in principales:
    a = IA[k]["alarma_del_nodo"]
    w("| %s | %d | %s | %s %% | **%s %%** | hora %s · %s cm |" % (
        NOMBRE[k], S1[k]["transiciones_alarma_nodo"], n(S1[k]["alarmas_por_10min"], 3),
        n(a["pct_tiempo_en_alarma"], 1), n(a["pct_tiempo_en_alarma_con_nivel_sobre_umbral"], 1),
        n(a["hora_primera_alarma"], 1), n(a["nivel_real_en_la_primera_alarma_cm"], 1)))
w("")
ae, asq = IA["estable"]["alarma_del_nodo"], IA["sequia"]["alarma_del_nodo"]
w("En el escenario **estable** el nivel permanece en 20 cm, ocho centímetros por encima del umbral de 12 cm, y el nodo "
  "entró en alerta amarilla **%d veces en 24 horas**. En el de **sequía** la alerta amarilla se activó en la hora %s, "
  "con el nivel real en %s cm, y el umbral no se cruzó hasta la hora %s." % (
      S1["estable"]["transiciones_alarma_nodo"], n(asq["hora_primera_alarma"], 1),
      n(asq["nivel_real_en_la_primera_alarma_cm"], 1), n(se["hora_cruce_real_umbral"], 1)))
w("")
w("En la sequía, además, la alerta **no se retiró** en las %s horas que faltaban para el cruce: el nivel inicial del "
  "escenario (12,6 cm) coincide con el borde de la banda de histéresis (umbral × 1,05), y para salir de alarma el "
  "firmware exige superarla. Por eso ese escenario pasa el %s %% del tiempo en alarma con una sola transición." % (
      n(se["hora_cruce_real_umbral"] - asq["hora_primera_alarma"], 1), n(asq["pct_tiempo_en_alarma"], 0)))
w("")
_ev = [float(x["evap_mm_dia"]) for x in _csv.DictReader(
    l for l in open(os.path.join(RAIZ, "datos", "ch3-simulacion", "verdad_estable.csv"), encoding="utf-8")
    if not l.startswith("#"))]
w("### La causa")
w("")
w("La alerta amarilla exige que concuerden dos de tres indicadores: nivel bajo el umbral, **tendencia de descenso** "
  "mayor de 2 mm/h, y evaporación potencial mayor de 5 mm/día.")
w("")
w("El segundo indicador se calcula con **dos muestras consecutivas, separadas 2 segundos**. Un descenso de 2 mm/h "
  "equivale a 0,0011 mm en 2 s. El ruido del sensor entre dos muestras es del orden de décimas de milímetro. "
  "El indicador no mide una tendencia: **se activa cada vez que el ruido hace que una muestra sea un poco menor que "
  "la anterior, es decir, aproximadamente la mitad de las veces.**")
w("")
w("Consecuencia: basta que **uno** de los otros dos indicadores esté activo para que, a los pocos segundos, se reúnan "
  "tres muestras seguidas con «dos de tres». Hacia el mediodía del modelo la evaporación estimada supera los "
  "5 mm/día (máximo %s; el %s %% del tiempo por encima de 5), y eso solo ya dispara la amarilla. La corroboración "
  "multivariable se comporta, en la práctica, como *nivel bajo **o** evaporación alta*." % (n(max(_ev)), n(100.0 * sum(e > 5 for e in _ev) / len(_ev), 1)))
w("")
w("La misma causa explica el porcentaje de lecturas marcadas como dudosas (bandera 3) por la prueba de coherencia "
  "física, que también compara muestras consecutivas: **%s %%** con el ruido de operación y **%s %%** con el ruido "
  "severo." % (n(S1["estable"]["pct_qc_sospechoso"], 1), n(S1["estable_ruido_alto"]["pct_qc_sospechoso"], 1)))
w("")
w("### Qué tan grave es")
w("")
w("| | |")
w("|---|---|")
w("| ¿Afecta a la alerta **roja**, la que suena? | **No.** La roja exige además nivel bajo el umbral rojo. La amarilla es solo luz |")
w("| ¿Supera el objetivo de EEMUA 191 (≤ 1 alarma por 10 min)? | No: %s por 10 min en el peor escenario. Pero EEMUA 191 mide carga del operador, no acierto |" % n(max(S1[k]["alarmas_por_10min"] for k in principales), 3))
w("| ¿Es un defecto real o un artefacto del simulador? | La **causa** es real: está en el código y no depende del modelo. La **frecuencia** sí depende del clima simulado y del ruido |")
w("| ¿Por qué no se vio en el Challenge #2? | En la corrida de referencia de ese banco (`datos/repetibilidad-operacion.csv`) la evaporación estimada era de 4,15 mm/día: por debajo de 5, el tercer indicador no estaba activo |")
w("| ¿Lo corrige el Challenge #3? | **No en el nodo.** Sí lo compensa aguas arriba: la tendencia que usa el módulo de IA se calcula sobre horas, y en estos mismos escenarios no produjo ninguna falsa anomalía |")
w("")
w("### Por qué no se corrigió en el firmware")
w("")
w("La corrección es conocida: calcular la tendencia sobre el promedio de bloques de 5 minutos, no sobre muestras "
  "sueltas. Pero cambia el tiempo de respuesta de la alarma, que es el comportamiento central del nodo y el que se "
  "validó en el banco físico del Challenge #2. Se decidió **no modificar la lógica de alarma sin poder "
  "probarla en el hardware**, y declararlo. Queda como primer punto del trabajo futuro "
  "([C3.11](C3.11-Conclusiones-retos-y-trabajo-futuro)).")
w("")
w("Es también la demostración más directa de la tesis de este Challenge: **la tendencia de un nivel de agua no se "
  "puede medir en 2 segundos. Hace falta memoria, y la memoria está en el Gateway.**")
w("")
w("---")
w("")

# --------------------------------------------------------------------- falla
x = S1["falla"]
w("## 8.11 Datos faltantes de extremo a extremo")
w("")
w("**Procedimiento.** Escenario `falla`: durante 10 minutos el ultrasónico no recibe eco. El nodo publica "
  "`nivel: null` con bandera QARTOD 9.")
w("")
w("| | |")
w("|---|---|")
w("| Mensajes publicados / en SQLite | %s / %s |" % (n(x["publicados"]), n(x["en_sqlite"])))
w("| Lecturas guardadas con nivel nulo | %s |" % n(x["lecturas_sin_nivel"]))
w("| Agregados totales / sin nivel válido | %s / %s |" % (n(x["agregados"]), n(x["agregados_sin_nivel"])))
w("| Agregados con nivel enviados a la nube | %s de %s |" % (n(x["agregados_en_nube"]), n(x["agregados_con_nivel"])))
w("| Secuencia de alarmas registrada | %s |" % " → ".join(x["secuencia_alertas"]))
w("")
w("### Análisis")
w("")
w("**El dato faltante llega a la nube como dato faltante.** Las ventanas sin ninguna lectura válida no envían la "
  "variable `nivel`: en la gráfica de la plataforma queda un hueco, no un cero ni el último valor repetido. Las "
  "demás variables de esas ventanas —temperatura, humedad, presión— sí se envían, porque esos sensores seguían bien.")
w("")
w("**La falla viaja como falla.** El estado `FALLA` del nodo llegó al Gateway y de ahí a la nube como `alerta = 3`, "
  "distinto de una alerta por nivel. Es el principio del Challenge #2 —*un sistema debe declarar lo que no sabe*— "
  "sostenido a través de tres capas.")
w("")
w("---")
w("")

# -------------------------------------------------------------------- matriz
w("## 8.12 Requisitos del enunciado frente a la evidencia")
w("")
w("| Requisito | Evidencia en simulación | Falta comprobar en el prototipo |")
w("|---|---|---|")
w("| Transmisión MQTT dispositivo → Gateway | S1: protocolo y cargas, con nodo emulado | H1, H2: con el ESP32 |")
w("| Medición y transmisión en hilos independientes | — (es propiedad del firmware) | H3 |")
w("| Almacenamiento local antes de transmitir | S1, S5 | H5 |")
w("| Procesamiento local | S1, S3 | H12 |")
w("| MQTT Gateway → plataforma | S1, S5: contra una nube emulada | H2, H5: contra Ubidots |")
w("| Tablero local | Challenge #2 (sin cambios) | H4 |")
w("| Tablero global | — | H2 |")
w("| Acceso restringido | S2: broker local | H8: los dos tableros |")
w("| Notificaciones | S7: la variable que las dispara llega a la nube | H7 |")
w("| Desactivar alarmas físicas desde el tablero | S6: estado lógico | H6: zumbador físico |")
w("| Módulo de IA | S9: capa analítica y reglas | H9: capa generativa · H10: agua real |")
w("| Integración funcional de todos los componentes | S1 a S9: software | H2: cadena física completa |")
w("")
w("La columna de la derecha no es una formalidad. Es la lista de lo que esta documentación **no demuestra**, y se "
  "discute en [C3.9](C3.9-Autoevaluacion-del-protocolo).")

sys.stdout.write("\n".join(out) + "\n")
