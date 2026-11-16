# Estación hidrometeorológica para alerta temprana de desabastecimiento hídrico

**Challenge #3 · Internet de las Cosas · Sabana Centro, Cundinamarca · 2026-2**

Santiago Barrera · Tomás Ariza · Andrés Camilo Beltrán

---

## De un nodo a un ecosistema

El Challenge #2 entregó una estación **autónoma**: mide el nivel del agua y el clima que la evapora, valida sus lecturas, decide y hace sonar una alarma sin depender de nadie.

El Challenge #3 la convierte en el primer nodo de una cadena de cuatro capas, para resolver lo que un nodo solo no puede: **avisar a quien no está en el sitio, recordar, y anticipar.**

```
  DISPOSITIVO              GATEWAY                  NUBE               AUTORIDAD
  ESP32            MQTT    Raspberry Pi     MQTT    Ubidots
  7 variables    ───────►  Mosquitto      ───────►  Tablero global ──►  en cualquier
  fusión · QARTOD          SQLite                   Notificaciones      lugar de
  alarma física  ◄───────  Procesamiento  ◄───────  Interruptor         Colombia
  tablero local    orden   Módulo de IA     orden
```

| Capa | Qué aporta | Si falla |
|---|---|---|
| **Dispositivo** | Decide y alarma en 6 s, sin red | — |
| **Gateway** | Guarda todo, resume, predice, sostiene los datos sin Internet | El nodo sigue alarmando y sirviendo su tablero local |
| **Nube** | Llega a quien decide, esté donde esté; notifica | Los datos esperan en disco y se reenvían |

> **Agregar tres capas no puede hacer al sistema menos confiable que antes.** Ninguna función de seguridad se movió hacia arriba. El Gateway y la nube amplían lo que el nodo hace; no lo reemplazan.

---

## Estado de esta entrega

| | |
|---|---|
| ✅ **Escrito y en el repositorio** | Firmware con cliente MQTT · Gateway · base de datos · módulo de IA · simulador · banco de pruebas |
| ✅ **Ejecutado y medido en simulación** | Broker Mosquitto real, Gateway real, SQLite real y módulo de IA real, contra un nodo y una nube emulados. Nueve pruebas, todas las cifras en [C3.8](C3.8-Resultados-y-analisis) |
| ⏳ **Pendiente: prototipo físico** | Firmware nuevo en el ESP32, Raspberry Pi, cuenta de Ubidots, capa generativa de la IA. Doce pruebas definidas en [C3.7 §7.6](C3.7-Configuracion-experimental). **Se demuestran en la sustentación** |

Esta documentación reporta una **validación por simulación** y lo dice en cada lugar donde aparece un número. No contiene ninguna cifra presentada como medición del prototipo que no lo sea. Lo que la simulación no puede probar está en [C3.9](C3.9-Autoevaluacion-del-protocolo).

---

## Resultados principales (simulación)

| Qué se probó | Resultado en simulación | Detalle |
|---|---|---|
| Integridad nodo → SQLite → nube | **457 200 de 457 200** mensajes en SQLite; cero agregados perdidos o duplicados | [§8.1](C3.8-Resultados-y-analisis) |
| Presupuesto de la plataforma (4 000 datos/día) | Máximo **3 046 datos/día** | [§8.1](C3.8-Resultados-y-analisis) |
| Caída de Internet de 8 h | **0 perdidos, 0 duplicados**; la bandeja llegó a 118 mensajes y se vació sola | [§8.5](C3.8-Resultados-y-analisis) |
| Control de acceso del broker | **10 de 10** casos | [§8.2](C3.8-Resultados-y-analisis) |
| Silenciar la alarma desde el tablero global, con salvaguardas | **10 de 10** comprobaciones; una orden vieja no se ejecuta | [§8.6](C3.8-Resultados-y-analisis) |
| IA · falsas anomalías | **0** en 501 corridas | [§8.9](C3.8-Resultados-y-analisis) |
| IA · fuga de 3 mm/h | Detectada **1,0 h** después de empezar, **11,5 h** antes de cruzar el umbral | [§8.9](C3.8-Resultados-y-analisis) |
| IA · predicción del cruce por sequía, con un día de historia | Error medio de **2,23 h** | [§8.9](C3.8-Resultados-y-analisis) |
| ⚠️ Hallazgo sobre el nodo | La alerta amarilla se activa sin que el nivel esté bajo: **10 veces en 24 h** en el escenario estable | [§8.10](C3.8-Resultados-y-analisis) |

---

## El módulo de IA en una frase

**El nivel baja. ¿Racionar, o mandar una cuadrilla?**

Un descenso por evaporación y uno por fuga se ven igual en un medidor de nivel y piden decisiones opuestas. El módulo cruza horas de historia del nivel con la evaporación que el nodo estima, y entrega tres cosas: **cuánto tiempo queda** hasta el umbral, **si el clima explica** el descenso, y **qué hacer**.

Un método estadístico auditable hace la cuenta. Un modelo generativo, con respaldo por reglas, redacta la recomendación. Cada parte del problema recibió la herramienta que le corresponde: [C3.5](C3.5-Modulo-de-IA).

---

## Documentación del Challenge #3

| Numeral del enunciado | Página |
|---|---|
| **(1)** Resumen, motivación, justificación, estructura | [C3.1 · Resumen, motivación y justificación](C3.1-Resumen-motivacion-y-justificacion) |
| **(2)** Restricciones de diseño | [C3.2 · Restricciones de diseño](C3.2-Restricciones-de-diseno) |
| **(2)** Arquitectura · diagramas de bloques · UML de la solución | [C3.3 · Arquitectura](C3.3-Arquitectura) |
| **(2)** Desarrollo modular · criterios · UML por módulo · estándares | [C3.4 · Diseño modular](C3.4-Diseno-modular) |
| **(2)** Módulo de Inteligencia Artificial | [C3.5 · Módulo de IA](C3.5-Modulo-de-IA) |
| **(2)** Esquemático e implementación | [C3.6 · Implementación](C3.6-Implementacion) |
| **(2)** Modelo de negocio | [C3.10 · Modelo de negocio](C3.10-Modelo-de-negocio) |
| **(3)** Configuración experimental | [C3.7 · Configuración experimental](C3.7-Configuracion-experimental) |
| **(3)** Resultados y análisis | [C3.8 · Resultados y análisis](C3.8-Resultados-y-analisis) |
| **(4)** Autoevaluación del protocolo de pruebas | [C3.9 · Autoevaluación del protocolo](C3.9-Autoevaluacion-del-protocolo) |
| **(5)** Conclusiones, retos, trabajo futuro | [C3.11 · Conclusiones, retos y trabajo futuro](C3.11-Conclusiones-retos-y-trabajo-futuro) |
| **(5)** Referencias | [C3.15 · Referencias](C3.15-Referencias) |
| **(6)** Anexos · código documentado · **(7)** video | [C3.14 · Anexos](C3.14-Anexos) |
| Roles y contribuciones | [C3.12 · Roles y contribuciones](C3.12-Roles-y-contribuciones) |
| Declaración de uso de IA | [C3.13 · Uso de herramientas de IA](C3.13-Uso-de-herramientas-de-IA) |

## Documentación del Challenge #2 (el nodo)

El hardware, la medición, las pruebas QARTOD, la lógica de fusión, los umbrales, la alarma y el tablero local **no cambiaron**. Su documentación sigue vigente: [resumen del Challenge #2](Estación-hidrometeorológica-para-alerta-temprana-de-desabastecimiento-hídrico) y páginas 1 a 16 de la barra lateral.

---

## Equipo

| Integrante | Frente en el Challenge #3 |
|---|---|
| Santiago Barrera | Dispositivo e Inteligencia Artificial |
| Tomás Ariza | Gateway y datos |
| Andrés Camilo Beltrán | Nube, validación y documentación |

**Repositorio:** https://github.com/tomasarro13/estacion-hidrometeorologica-sabana-centro
**Entrega de la documentación:** 16 de noviembre de 2026 · **Sustentación:** 19 de noviembre de 2026
