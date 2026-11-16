# Challenge #2 · Internet de las Cosas · Sabana Centro, Cundinamarca

---

## Resumen general

Durante 2026, la confirmación oficial del inicio anticipado del Fenómeno de El Niño por parte del Gobierno Nacional y el IDEAM extremó las vulnerabilidades hidrológicas de la Sabana de Bogotá, y la CAR identificó formalmente 20 municipios de Cundinamarca en riesgo extremo por desabastecimiento hídrico [1], [2].

Este proyecto desarrolla un **prototipo funcional de bajo costo** que mide de forma continua el nivel de un cuerpo de agua junto con las variables meteorológicas que gobiernan su pérdida por evaporación, combina esas señales mediante una **lógica de fusión multivariable** y emite alertas tempranas en dos canales independientes: una **alarma física in situ** y un **tablero de control** servido desde un servidor web embebido en la propia WLAN, sin broker ni dependencia de Internet.

El dispositivo mide siete variables —seis instrumentadas y una derivada— con un costo de materiales inferior a $250.000 COP, y opera de forma autónoma: si la red cae, sigue midiendo, decidiendo y alarmando.

---

## La idea central del diseño

> **Un nodo que deja de funcionar cuando se cae la red no sirve para alertar de una emergencia.**

Esa premisa ordena todas las decisiones de arquitectura. La inteligencia vive en el dispositivo: valida sus propias lecturas, aplica la lógica de fusión, decide si hay alerta y acciona su alarma sin consultar a nadie. La red es un canal adicional de visualización, no un requisito de operación.

Es también la razón por la cual el enunciado prohíbe MQTT, y una restricción que el equipo asume como criterio de diseño y no como limitación: un broker exige una máquina encendida permanentemente, lo que introduce costo, consumo y un punto único de falla en un sistema cuya función es avisar cuando algo va mal.

---

## Variables monitoreadas

| Variable | Origen | Instrumento |
|---|---|---|
| Nivel del agua | Medida | HC-SR04 (ultrasónico, sin contacto) |
| Caudal | Derivada del nivel | Curva de gasto calibrada |
| Temperatura del aire | Medida | DHT22 |
| Humedad relativa | Medida | DHT22 |
| Presión atmosférica | Medida | BMP280 |
| Radiación solar | Estimada | LDR + Ångström-Prescott |
| **Evaporación potencial** | **Derivada** | **Penman-Monteith FAO-56** |

La evaporación potencial es la variable que justifica todo el sistema: no se mide con ningún sensor, se infiere del cruce de las otras cuatro. Es la expresión más clara del valor de un ecosistema IoT frente a un conjunto de sensores aislados.

---

## Estructura de la documentación

| Página | Contenido |
|---|---|
| **[1. Introducción](1.-Introduccion)** | Motivación, justificación, problema y pregunta guía |
| **[2. Restricciones de diseño](2.-Restricciones-de-diseno)** | Técnicas, económicas, regulatorias, de espacio, escalabilidad y temporales |
| **[3. Arquitectura](3.-Arquitectura)** | Diagramas de bloques de hardware y software, UML, esquemático |
| **[4. Diseño modular](4.-Diseno-modular)** | Criterios de diseño y estándares de ingeniería aplicados por módulo |
| **[5. Lógica de fusión y umbrales](5.-Logica-de-fusion-y-umbrales)** | Fundamentación normativa de cada parámetro |
| **[6. Implementación](6.-Implementacion)** | Hardware, firmware, carcasa, alimentación y despliegue |
| **[7. Banco de pruebas](7.-Banco-de-pruebas)** | Protocolo experimental completo |
| **[8. Resultados y análisis](8.-Resultados-y-analisis)** | Calibración, validación de alertas, robustez y desempeño |
| **[9. Autoevaluación del protocolo](9.-Autoevaluacion-del-protocolo)** | Crítica de la propia metodología de pruebas |
| **[10. Mejoras respecto al Challenge #1](10.-Mejoras-respecto-al-Challenge-1)** | Deficiencias corregidas y evidencia |
| **[11. Modelo de negocio](11.-Modelo-de-negocio)** | Viabilidad, costos y stakeholders |
| **[12. Conclusiones y trabajo futuro](12.-Conclusiones-y-trabajo-futuro)** | Retos enfrentados y siguientes pasos |
| **[13. Roles y contribuciones](13.-Roles-y-contribuciones)** | Aporte de cada integrante |
| **[14. Uso de herramientas de IA](14.-Uso-de-herramientas-de-IA)** | Declaración de transparencia |
| **[15. Anexos](15.-Anexos)** | Código fuente documentado, esquemáticos y material complementario |
| **[16. Referencias](16.-Referencias)** | Bibliografía en formato IEEE |

---

## Equipo

| Integrante | Rol |
|---|---|
| Santiago Barrera | Líder técnico · Firmware y concurrencia |
| Tomás Ariza | Líder técnico · Servidor web y tablero |
| Andrés Camilo Beltrán | Documentación, integración física y comunicación |

**Repositorio:** `github.com/tomasarro13/estacion-hidrometeorologica-sabana-centro`
**Sustentación:** 1 de octubre de 2026
