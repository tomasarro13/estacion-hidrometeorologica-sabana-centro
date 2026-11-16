# Estación hidrometeorológica para alerta temprana de desabastecimiento hídrico

**Challenge #3 · Internet de las Cosas · Sabana Centro, Cundinamarca · 2026-2**

> **Challenge #3: de un nodo a un ecosistema.** La estación del Challenge #2 es ahora el primer nodo de una cadena de cuatro capas: **ESP32 → Raspberry Pi (Mosquitto + SQLite + procesamiento + IA) → Ubidots → autoridad**, con MQTT en los dos tramos y un canal de órdenes en sentido contrario. Ver [Challenge #3](#challenge-3--ecosistema-iot) más abajo.
>
> **Estado:** software completo y **validado por simulación** (broker, Gateway, SQLite e IA reales; nodo y nube emulados). La integración sobre el hardware se demuestra en la sustentación.

## El nodo (Challenge #2)

Prototipo funcional de bajo costo que monitorea el nivel de un cuerpo de agua junto con las variables meteorológicas que gobiernan su pérdida por evaporación, combina esas señales mediante lógica de fusión multivariable y emite alertas tempranas por dos canales independientes: alarma física *in situ* y tablero de control servido desde un servidor web embebido en la WLAN local, sin broker ni dependencia de Internet.

---

## 📖 Documentación completa

### 👉 **[Ir a la Wiki](../../wiki)**

Toda la documentación técnica —diseño, estándares aplicados, banco de pruebas, resultados y análisis— está en la Wiki de este repositorio.

---

## La idea central

> Un nodo que deja de funcionar cuando se cae la red no sirve para alertar de una emergencia.

La inteligencia vive en el dispositivo: valida sus propias lecturas, aplica la lógica de fusión, decide si hay alerta y acciona su alarma sin consultar a nadie. La red es un canal adicional de visualización, no un requisito de operación.

Medir el nivel por sí solo no distingue dos situaciones muy distintas: que el agua baje **porque hace calor y se evapora** —sequía, decisión de racionamiento— o que baje **sin que el clima lo explique** —fuga, captación no autorizada o falla del sensor. Separar esos casos exige cruzar el nivel con las variables meteorológicas. Ese cruce es la razón de ser del sistema.

---

## Variables monitoreadas

| Variable | Origen | Instrumento |
|---|---|---|
| Nivel del agua | Medida | HC-SR04 (ultrasónico, sin contacto) |
| Caudal | Derivada | Curva de gasto calibrada |
| Temperatura del aire | Medida | DHT22 |
| Humedad relativa | Medida | DHT22 |
| Presión atmosférica | Medida | BMP280 |
| Radiación solar | Estimada | LDR + Ångström-Prescott |
| **Evaporación potencial** | **Derivada** | **Penman-Monteith FAO-56** |

La evaporación potencial no se mide con ningún sensor: se infiere del cruce de las otras cuatro. Es la expresión más clara del valor de un ecosistema IoT frente a un conjunto de sensores aislados.

---

## Estándares de ingeniería aplicados

| Dominio | Estándar | Aporte |
|---|---|---|
| Calidad del dato | **QARTOD** (U.S. IOOS/NOAA) | Jerarquía de pruebas y banderas de calidad |
| Gestión de alarmas | **ANSI/ISA-18.2** · **IEC 62682** | Racionalización, deadband y retardos |
| Gestión de alarmas | **EEMUA 191** | Indicadores cuantitativos de desempeño |
| Evapotranspiración | **FAO-56** Penman-Monteith | Modelo de referencia |
| Umbrales hidrológicos | **Threshold Level Method** | Percentiles de excedencia Q80 / Q90 |
| Fusión de sensores | **Durrant-Whyte** · modelo **JDL** | Taxonomía de la fusión aplicada |
| Instrumentación | **WMO No. 8** (Guía CIMO) | Exposición de sensores meteorológicos |

Ningún parámetro del firmware se escogió a ojo. Los trece valores críticos se derivan de hojas de datos, geometría del montaje, rangos normativos o consistencia interna del diseño. La justificación completa está en **[§5 de la Wiki](../../wiki/5.-Logica-de-fusion-y-umbrales)**.

---

## Challenge #3 · Ecosistema IoT

```
 DISPOSITIVO            GATEWAY                 NUBE              AUTORIDAD
 ESP32          MQTT    Raspberry Pi     MQTT   Ubidots
 7 variables  ───────►  Mosquitto      ───────► Tablero global ──► en cualquier
 fusión                 SQLite                  Notificaciones     lugar de
 alarma física ◄─────── Procesamiento  ◄─────── Interruptor        Colombia
 tablero local  orden   Módulo de IA    orden
```

| Capa | Qué hace | Carpeta |
|---|---|---|
| **Dispositivo** | Todo lo del Challenge #2 + cliente MQTT en un hilo propio | `firmware/EstacionHidroMQTT/` |
| **Gateway** | Recibe, valida, guarda en SQLite, agrega por ventanas de 5 min, reenvía a la nube tras caídas de Internet, baja las órdenes | `gateway/` |
| **Módulo de IA** | Predice el tiempo al umbral, distingue un descenso por evaporación de uno que el clima no explica, y redacta una recomendación | `ia/` |
| **Validación** | Nodo ESP32 emulado y banco de nueve pruebas | `simulador/` · `pruebas/` |

### Poner en marcha la Raspberry

Guía paso a paso, con una verificación al final de cada paso: **[`gateway/README.md`](gateway/README.md)**

### Repetir la validación por simulación

```bash
sudo apt install mosquitto python3-paho-mqtt      # o: pip install paho-mqtt
python3 pruebas/ejecutar_pruebas.py                # ~10 min · escribe datos/ch3-simulacion/
python3 pruebas/generar_reporte.py                 # la página de resultados de la Wiki
```

### Probar el Gateway sin el ESP32

```bash
python3 simulador/simulador.py --escenario fuga --clave 'CLAVE_DEL_NODO' --ritmo 200
```

Documentación completa del Challenge #3: páginas **C3.1 a C3.15** de la [Wiki](../../wiki).

---

## Contenido del repositorio

```
.
├── firmware/
│   ├── EstacionHidroMQTT/    Challenge #3 · firmware con cliente MQTT
│   ├── EstacionHidro/        Challenge #2 · firmware autónomo (sin MQTT)
│   │   ├── EstacionHidro.ino   Tareas, ISR, fusión, servidor web
│   │   ├── config.h            Parámetros ajustables documentados
│   │   └── pagina.h            Tablero embebido en PROGMEM
│   └── Diagnostico/          Sketch de verificación de hardware
├── gateway/                  Challenge #3 · Gateway (Raspberry Pi)
│   ├── gateway.py              Orquestador
│   ├── almacen.py              SQLite
│   ├── procesamiento.py        Validación y agregación
│   ├── puente.py               Subida a la nube y bajada de órdenes
│   ├── esquema.sql             Las cinco tablas
│   ├── mosquitto/              Configuración y ACL del broker
│   └── README.md               Puesta en marcha
├── ia/                       Challenge #3 · módulo de IA
├── simulador/                Challenge #3 · nodo ESP32 emulado
├── pruebas/                  Challenge #3 · banco de pruebas y reporte
├── imagenes/                 Fotografías del prototipo y capturas
├── datos/                    Mediciones del Ch2 y resultados de simulación del Ch3
├── docs/                     Material complementario
└── wiki/                     Copia de la Wiki (fuente markdown)
```

---

## Puesta en marcha

### Requisitos

- Arduino IDE 2.x con el core **ESP32 by Espressif Systems**
- Librerías: `DHT sensor library`, `Adafruit Unified Sensor`, `Adafruit BMP280 Library`, `LiquidCrystal I2C`

### 1 · Verificar el hardware

Sube `firmware/Diagnostico/` y abre el monitor serie a **115200**. Reporta el escaneo I²C, el estado de cada sensor, la dispersión del ultrasónico y los valores a trasladar a `config.h`.

### 2 · Configurar

En `firmware/EstacionHidro/config.h`:

```cpp
#define WIFI_SSID_DEF  "..."      // red local
#define WIFI_PASS_DEF  "..."
#define WEB_USER_DEF   "..."      // credenciales del tablero
#define WEB_PASS_DEF   "..."

#define DIR_LCD   0x27            // confirmar con el escáner
#define DIR_BMP   0x76

#define ALTURA_SENSOR_CM_DEF  40.0f   // medir con regla
#define NIVEL_MAX_CM          35.0f   // altura − 5
```

### 3 · Calibrar sin recompilar

```
http://IP-DEL-NODO/api/calibrar?altura=38.5
http://IP-DEL-NODO/api/calibrar?amarillo=12.4&rojo=9.8
```

Los valores se guardan en NVS y sobreviven al reinicio.

---

## Conexiones

| Señal | GPIO | Alimentación | Componente intermedio |
|---|---|---|---|
| HC-SR04 Trig | 5 | VIN (5 V) | — |
| HC-SR04 Echo | 18 | — | **Divisor 1 kΩ / 2 kΩ** |
| DHT22 Data | 4 | 3,3 V | Pull-up 10 kΩ a 3,3 V |
| LDR (AO) | 34 | 3,3 V | ADC1 obligatorio |
| BMP280 SDA/SCL | 21 / 22 | **3,3 V** | — |
| LCD SDA/SCL | 21 / 22 | VIN (5 V) | Adaptador PCF8574 |
| Zumbador | 14 | 5 V | 2N2222 + 1 kΩ + 1N4148 |
| LED verde / amarillo / rojo | 25 / 26 / 27 | — | 220 Ω |

> ⚠️ **Dos advertencias que ahorran componentes.** El pin Echo entrega 5 V y la entrada del ESP32 tolera 3,3 V: **sin el divisor se destruye la placa**. Y el BMP280 va a **3,3 V**, nunca a 5 V; muchos módulos no traen regulador y el chip se daña, dejando además el bus I²C en corto para todos los dispositivos.

---

## API del tablero

| Ruta | Función |
|---|---|
| `GET /` | Tablero de control |
| `GET /api/datos` | Valor actual de las 7 variables, estado y calidad |
| `GET /api/historico` | Hasta 288 registros de 24 h |
| `GET /api/calibrar` | Ajuste de altura y umbrales |
| `GET /api/silenciar` | **Desactivación remota de la alarma física** |

Todas requieren autenticación HTTP Basic.

---

## Equipo

| Integrante | Rol |
|---|---|
| **Santiago Barrera** | Dispositivo e Inteligencia Artificial |
| **Tomás Ariza** | Gateway y datos |
| **Andrés Camilo Beltrán** | Nube, validación y documentación |

---

## Trabajos relacionados

El diseño de conectividad de largo alcance para una red de varios nodos —LoRaWAN, gateway y broker MQTT, validado en Cisco Packet Tracer— se desarrolló en la Actividad de refuerzo 2.3, en un repositorio independiente. El Challenge #3 implementa el gateway y el broker sobre Wi-Fi.

---

## Licencia

Proyecto académico. Universidad de La Sabana, 2026.
