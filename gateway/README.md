# Gateway IoT · puesta en marcha en la Raspberry Pi

Guía paso a paso para dejar funcionando la cadena completa:

```
ESP32 ──MQTT──> Mosquitto ──> gateway.py ──MQTT──> Ubidots
                (Raspberry)   (SQLite + IA)
```

Tiempo estimado: 60 a 90 minutos. Cada paso termina con una **verificación**: no avanzar hasta que pase.

---

## 0. Antes de empezar

| Se necesita | Dónde se usa |
|---|---|
| Raspberry Pi con Raspberry Pi OS (Bookworm o posterior), SSH habilitado | Todo |
| La Raspberry y el ESP32 en **la misma WLAN** | Paso 5 |
| Token de la cuenta de Ubidots (*API Credentials → Default token*) | Paso 4 |
| (Opcional) Clave de la API de Gemini | Paso 4 |

Dejar la IP de la Raspberry **fija** (reserva DHCP en el router o `nmtui`). El ESP32 la necesita.

```bash
hostname -I        # anotar la IP, por ejemplo 192.168.1.50
```

---

## 1. Instalar

```bash
sudo apt update
sudo apt install -y mosquitto mosquitto-clients python3-paho-mqtt sqlite3 git

sudo mkdir -p /opt/estacion /etc/estacion /var/lib/estacion
sudo git clone https://github.com/tomasarro13/estacion-hidrometeorologica-sabana-centro /opt/estacion/repo
sudo ln -s /opt/estacion/repo/gateway /opt/estacion/gateway
sudo ln -s /opt/estacion/repo/ia /opt/estacion/ia
```

> `gateway.py` busca el módulo `ia` en la carpeta hermana de `gateway/`. Los dos enlaces la dejan donde la espera.

Usuario sin privilegios para el servicio:

```bash
sudo useradd --system --home /var/lib/estacion --shell /usr/sbin/nologin estacion
sudo chown -R estacion:estacion /var/lib/estacion
```

**Verificación:** `python3 -c "import paho.mqtt, sqlite3; print('ok')"` imprime `ok`.

---

## 2. Broker Mosquitto con acceso restringido

```bash
sudo cp /opt/estacion/gateway/mosquitto/estacion.conf /etc/mosquitto/conf.d/
sudo cp /opt/estacion/gateway/mosquitto/estacion.acl  /etc/mosquitto/

# Tres usuarios. Cada comando pide la clave dos veces. ANOTARLAS.
sudo mosquitto_passwd -c /etc/mosquitto/estacion.passwd nodo01     # la usa el ESP32
sudo mosquitto_passwd    /etc/mosquitto/estacion.passwd gateway    # la usa gateway.py
sudo mosquitto_passwd    /etc/mosquitto/estacion.passwd monitor    # para mosquitto_sub

sudo chown mosquitto:mosquitto /etc/mosquitto/estacion.passwd /etc/mosquitto/estacion.acl
sudo chmod 640 /etc/mosquitto/estacion.passwd
sudo systemctl restart mosquitto
sudo systemctl status mosquitto --no-pager | head -5
```

> Si Mosquitto no arranca, casi siempre es porque `/etc/mosquitto/mosquitto.conf` ya trae su propio `listener` o `persistence`. Ver el motivo con `sudo journalctl -u mosquitto -n 20` y comentar la línea duplicada.

**Verificación.** Dos terminales:

```bash
# Terminal A: escucha todo
mosquitto_sub -h localhost -u monitor -P 'CLAVE_MONITOR' -t 'sabana/#' -v

# Terminal B: sin credenciales debe FALLAR ("not authorised")
mosquitto_pub -h localhost -t sabana/nodo01/estado -m prueba

# Terminal B: con credenciales debe aparecer en la terminal A
mosquitto_pub -h localhost -u nodo01 -P 'CLAVE_NODO' -t sabana/nodo01/estado -m online
```

---

## 3. Ubidots

1. Crear el dispositivo con **API label** `estacion-sabana` (*Devices → + → Blank device*).
2. No hace falta crear las variables de datos: Ubidots las crea solas al recibir el primer mensaje.
3. Crear **a mano** la variable de comando: dentro del dispositivo, *Add variable → Raw*, API label `silenciar`.

Variables que el Gateway envía:

| Variable | Qué es | Cada cuánto |
|---|---|---|
| `nivel`, `caudal`, `temp`, `hr`, `presion`, `luz`, `evap` | Las 7 variables, promedio de la ventana | 5 min |
| `tasa_mmh` | Tendencia del nivel en la ventana | 5 min |
| `alerta` | 0 verde · 1 amarilla · 2 roja · 3 falla | 5 min **y** en cada cambio |
| `qc` | Peor bandera QARTOD de la ventana | 5 min |
| `silenciada` | 1 si la alarma sonora está silenciada | En cada cambio |
| `en_linea` | 1 / 0, presencia del nodo | En cada cambio |
| `anomalia` | 1 si el descenso no lo explica el clima. Su *context* trae la recomendación | 30 min |
| `horas_umbral` | Horas estimadas hasta el umbral de alerta | 30 min |
| `exceso_mmh` | Descenso no explicado por la evaporación | 30 min |

### Tablero global

| Widget | Variable | Para qué |
|---|---|---|
| *Tank* o *Gauge* | `nivel` | Valor actual |
| *Line chart* | `nivel`, `evap` | Histórico reciente |
| *Indicator* | `alerta` | Estado (colores por valor 0–3) |
| *Metric* | `horas_umbral` | Predicción de la IA |
| *Indicator* | `anomalia` | Detección de la IA |
| *Variables table* o *HTML canvas* | `anomalia` (context) | Texto de la recomendación |
| ***Switch*** | `silenciar` | **Desactivar la alarma física** |
| *Indicator* | `en_linea` | Presencia del nodo |

### Notificaciones

*Data → Events → + Create event*:

| Evento | Condición | Acción |
|---|---|---|
| Alerta roja | `alerta` ≥ 2 | Correo / Telegram / SMS |
| Descenso no explicado | `anomalia` = 1 | Correo: "posible fuga o captación" |
| Nodo fuera de línea | `en_linea` = 0 durante 5 min | Correo |

### Acceso restringido

El tablero es privado por defecto: solo se ve con la sesión de la cuenta. **No** generar enlace público. Para la prueba: abrir la URL del tablero en una ventana de incógnito y capturar la pantalla de inicio de sesión.

---

## 4. Configurar el Gateway

```bash
sudo cp /opt/estacion/gateway/gateway.ini.ejemplo /etc/estacion/gateway.ini
sudo nano /etc/estacion/gateway.ini          # revisar [almacen] ruta y [nube] dispositivo
```

Los secretos van en un archivo aparte, fuera del repositorio:

```bash
sudo tee /etc/estacion/secretos.env > /dev/null <<'EOF'
UBIDOTS_TOKEN=BBUS-xxxxxxxxxxxxxxxxxxxxxxxx
MQTT_LOCAL_CLAVE=la-clave-del-usuario-gateway
GEMINI_API_KEY=
EOF
sudo chown root:estacion /etc/estacion/secretos.env
sudo chmod 640 /etc/estacion/secretos.env
```

Primera corrida **a mano**, para ver los mensajes:

```bash
cd /opt/estacion/gateway
sudo -u estacion env $(sudo cat /etc/estacion/secretos.env | xargs) \
     python3 gateway.py --config /etc/estacion/gateway.ini
```

**Verificación.** Deben salir estas dos líneas:

```
gateway  INFO    Conectado al broker local localhost:1883
puente   INFO    Nube conectada (industrial.api.ubidots.com:8883). Pendientes por enviar: 0
```

| Si sale… | Es que… |
|---|---|
| `El broker local rechazo la conexion (rc=5)` | La clave de `gateway` no coincide con la de `mosquitto_passwd` |
| No aparece "Nube conectada" | Token errado, o la red bloquea el 8883 → en el `.ini`: `puerto = 1883`, `tls = no` |

---

## 5. Probar SIN el ESP32 (simulador)

Antes de tocar el hardware, comprobar que la Raspberry hace todo su trabajo. Desde otra terminal:

```bash
cd /opt/estacion/repo
python3 simulador/simulador.py --escenario fuga --clave 'CLAVE_NODO' --ritmo 200
```

Publica 30 horas simuladas en unos 5 minutos. **Verificación:**

```bash
sqlite3 /var/lib/estacion/gateway.db "SELECT COUNT(*) FROM lecturas; SELECT COUNT(*) FROM agregados;"
sqlite3 /var/lib/estacion/gateway.db "SELECT clasificacion, recomendacion FROM predicciones ORDER BY id DESC LIMIT 1;"
```

y en Ubidots deben aparecer las variables con datos.

> El simulador fecha sus datos el 10 de noviembre de 2026. Para no mezclarlos con los reales, **borrar la base y las variables de Ubidots** antes de conectar el ESP32: `sudo systemctl stop gateway-estacion; sudo rm /var/lib/estacion/gateway.db*`

---

## 6. Conectar el ESP32

1. En el IDE de Arduino instalar la librería **PubSubClient** (Nick O'Leary).
2. Abrir `firmware/EstacionHidroMQTT/EstacionHidroMQTT.ino`.
3. En `config.h` poner la WLAN, `MQTT_HOST_DEF` (IP de la Raspberry) y `MQTT_PASS_DEF` (clave de `nodo01`).
4. Grabar. El monitor serie debe mostrar:

```
[MQTT] Conectado a 192.168.1.50:1883
```

También se puede cambiar el broker sin recompilar, desde el navegador:
`http://IP_DEL_ESP32/api/calibrar?mqtt=192.168.1.50&mqttuser=nodo01&mqttpass=CLAVE_NODO`

**Verificación** (el Hito de la cadena completa):

```bash
mosquitto_sub -h localhost -u monitor -P 'CLAVE_MONITOR' -t 'sabana/#' -v
```

debe mostrar una línea de `sabana/nodo01/telemetria` cada 2 segundos.

| Si en el monitor serie sale… | Es que… |
|---|---|
| `Sin broker ... (rc=-2)` | No hay ruta: IP errada, o la WLAN aísla clientes entre sí |
| `(rc=5)` o `(rc=4)` | Usuario o clave de `nodo01` errados |
| `(rc=-4)` | El broker no respondió a tiempo: revisar el `listener` de Mosquitto |

**Plan B si la WLAN aísla a los clientes** (pasó en el Challenge #2): conectar la Raspberry por **Ethernet** al router, para tener Internet, y por **Wi-Fi al punto de acceso del propio ESP32** (`EstacionIoT`). La Raspberry recibe una IP `192.168.4.x`; anotarla con `hostname -I` y dársela al nodo: `http://192.168.4.1/api/calibrar?mqtt=192.168.4.2`. El firmware intenta el broker tanto si está unido a la WLAN como si hay alguien conectado a su punto de acceso.

---

## 7. Dejarlo como servicio

```bash
sudo cp /opt/estacion/gateway/gateway.service /etc/systemd/system/gateway-estacion.service
sudo systemctl daemon-reload
sudo systemctl enable --now gateway-estacion
journalctl -u gateway-estacion -f
```

**Verificación:** `sudo reboot`, esperar un minuto y comprobar que `systemctl is-active gateway-estacion` responde `active` sin haber tocado nada.

---

## 8. Comando de vuelta: silenciar la alarma desde Ubidots

1. Bajar el nivel del recipiente por debajo del umbral rojo hasta que suene el zumbador.
2. En el tablero de Ubidots, activar el interruptor `silenciar`.
3. El zumbador debe callar en uno o dos segundos. El LED rojo **sigue encendido**: silenciar no oculta la condición.

Seguir el recorrido de la orden:

```bash
journalctl -u gateway-estacion -f | grep -E "Comando|Confirmacion"
```

```
gateway  INFO  Comando de la nube -> sabana/nodo01/cmd/silenciar = 1
gateway  INFO  Confirmacion de nodo01: {"cmd":"silenciar","ok":1,"silenciada":1,"restanteSeg":900,"alerta":"ROJA"}
```

---

## 9. Las pruebas de la sustentación

| Prueba | Cómo se provoca | Qué debe verse |
|---|---|---|
| Caída de Internet | Desconectar el cable WAN del router (la WLAN sigue) | `sqlite3 … "SELECT COUNT(*) FROM salida WHERE enviado=0"` crece. Al reconectar vuelve a 0 y Ubidots muestra el tramo sin huecos |
| Caída del Gateway | `sudo systemctl stop gateway-estacion mosquitto` | El ESP32 sigue midiendo, alarmando y sirviendo su tablero local |
| Caída del nodo | Desenchufar el ESP32 | A los ~23 s: `Nodo nodo01: offline` en el log y `en_linea = 0` en Ubidots |
| Acceso local | `mosquitto_sub` sin usuario | `Connection Refused: not authorised` |
| Módulo de IA | Abrir la llave de desagüe del recipiente y esperar 30–40 min | `anomalia = 1` y la recomendación de inspección |

---

## Consultas útiles

```bash
BD=/var/lib/estacion/gateway.db

# Últimas lecturas crudas
sqlite3 -header -column $BD "SELECT datetime(ts,'unixepoch','localtime') hora, seq, nivel, temp, hr, evap, qc, alerta FROM lecturas ORDER BY id DESC LIMIT 10;"

# Agregados de la última hora
sqlite3 -header -column $BD "SELECT datetime(ts_fin,'unixepoch','localtime') hora, n, perdidas, nivel_med, nivel_std, tasa_mmh, alerta FROM agregados ORDER BY id DESC LIMIT 12;"

# Bitácora de eventos
sqlite3 -header -column $BD "SELECT datetime(ts,'unixepoch','localtime') hora, tipo, detalle FROM eventos ORDER BY id DESC LIMIT 20;"

# Lo que espera para salir a la nube
sqlite3 $BD "SELECT COUNT(*) FROM salida WHERE enviado=0;"

# Última predicción
sqlite3 -header -line $BD "SELECT * FROM predicciones ORDER BY id DESC LIMIT 1;"
```

## Archivos de esta carpeta

| Archivo | Qué hace |
|---|---|
| `gateway.py` | Programa principal: recibe, valida, guarda, agrega, llama a la IA, encola |
| `almacen.py` | Única puerta a SQLite |
| `procesamiento.py` | Validación de mensajes y agregación por ventanas |
| `puente.py` | Subida a la nube (bandeja de salida) y bajada de comandos |
| `esquema.sql` | Las cinco tablas |
| `gateway.ini.ejemplo` | Plantilla de configuración |
| `gateway.service` | Unidad de systemd |
| `mosquitto/` | Configuración y ACL del broker |
