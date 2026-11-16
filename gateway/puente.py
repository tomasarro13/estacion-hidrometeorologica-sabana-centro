"""
puente.py · Puente MQTT entre el Gateway y la plataforma IoT en la nube.

Dos sentidos:

  SUBIDA   Un hilo propio vacía la bandeja de salida (tabla `salida` de
           SQLite) hacia la nube, en orden, respetando el límite de la
           plataforma (1 mensaje por segundo). Un mensaje solo se marca
           como enviado cuando el broker de la nube lo confirma (QoS 1).
           Sin Internet no pasa nada: las filas esperan en disco.

  BAJADA   Se suscribe a la variable de comando del tablero global. Al
           recibir una orden válida, la entrega al Gateway para que la
           reenvíe al nodo por el broker local.

Formato de la nube (Ubidots, API v1.6):
  publicar   /v1.6/devices/<dispositivo>      {"var": {"value": v, "timestamp": ms}}
  suscribir  /v1.6/devices/<dispositivo>/<variable>   {"value": v, "timestamp": ms, ...}
  usuario    = token de la cuenta ; clave vacía
"""

import json
import logging
import threading
import time

import paho.mqtt.client as mqtt

log = logging.getLogger("puente")


def crear_cliente(id_cliente):
    """Cliente paho compatible con las versiones 1.x (la de `apt` en
    Raspberry Pi OS) y 2.x (la de `pip`)."""
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=id_cliente)
    except AttributeError:
        return mqtt.Client(client_id=id_cliente)


def codigo(rc):
    """Código de resultado como entero en paho 1.x y 2.x."""
    return getattr(rc, "value", rc)


class PuenteNube:
    def __init__(self, almacen, host, puerto, token, dispositivo,
                 variable_comando="silenciar", al_comando=None,
                 intervalo_s=1.0, usar_tls=False, edad_max_comando_s=120,
                 id_cliente="gateway-sabana"):
        self.almacen = almacen
        self.host, self.puerto = host, puerto
        self.dispositivo = dispositivo
        self.topico_datos = "/v1.6/devices/%s" % dispositivo
        self.topico_comando = "/v1.6/devices/%s/%s" % (dispositivo, variable_comando)
        self.al_comando = al_comando
        self.intervalo_s = intervalo_s
        self.edad_max_comando_s = edad_max_comando_s
        self.conectado = threading.Event()
        self._parar = threading.Event()
        self._hay_trabajo = threading.Event()
        self._ts_ultimo_comando = 0.0
        self.enviados = 0

        self.cliente = crear_cliente(id_cliente)
        self.cliente.username_pw_set(token, "")
        if usar_tls:
            self.cliente.tls_set()          # CA del sistema; el token viaja cifrado
        self.cliente.on_connect = self._al_conectar
        self.cliente.on_disconnect = self._al_desconectar
        self.cliente.on_message = self._al_mensaje
        self.cliente.reconnect_delay_set(min_delay=1, max_delay=30)
        self._hilo = threading.Thread(target=self._vaciar, name="puente-subida", daemon=True)

    # ----------------------------------------------------------- ciclo de vida
    def iniciar(self):
        # connect_async + loop_start: paho reintenta solo, en su propio hilo.
        # Arrancar sin Internet no es un error, es un estado.
        self.cliente.connect_async(self.host, self.puerto, keepalive=60)
        self.cliente.loop_start()
        self._hilo.start()

    def detener(self):
        self._parar.set()
        self._hay_trabajo.set()
        self._hilo.join(timeout=5)
        self.cliente.loop_stop()
        try:
            self.cliente.disconnect()
        except Exception:
            pass

    # ------------------------------------------------------------- callbacks
    def _al_conectar(self, cliente, datos, banderas, rc, *resto):
        if codigo(rc) == 0:
            self.conectado.set()
            cliente.subscribe(self.topico_comando, qos=1)
            pend = self.almacen.pendientes_salida()
            log.info("Nube conectada (%s:%s). Pendientes por enviar: %d",
                     self.host, self.puerto, pend)
            self.almacen.registrar_evento("NUBE", {"estado": "conectada", "pendientes": pend})
            self._hay_trabajo.set()
        else:
            log.warning("La nube rechazo la conexion (rc=%s)", rc)

    def _al_desconectar(self, cliente, datos, *resto):
        if self.conectado.is_set():
            self.conectado.clear()
            log.warning("Nube desconectada. Los datos se acumulan en SQLite.")
            self.almacen.registrar_evento("NUBE", {"estado": "desconectada"})

    def _al_mensaje(self, cliente, datos, msg):
        """Orden desde el tablero global."""
        try:
            carga = json.loads(msg.payload.decode("utf-8"))
            valor = float(carga["value"] if isinstance(carga, dict) else carga)
            ts = float(carga.get("timestamp", 0)) / 1000.0 if isinstance(carga, dict) else 0.0
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            self.almacen.registrar_evento("RECHAZO", {"origen": "nube", "motivo": "comando ilegible"})
            return
        ahora = time.time()
        # Una orden vieja no se ejecuta. Al reconectar, la plataforma puede
        # reenviar el último valor de la variable: silenciar una alarma
        # por una orden de hace una hora sería exactamente el riesgo que
        # ISA-18.2 pide evitar.
        if ts and (ahora - ts > self.edad_max_comando_s or ts <= self._ts_ultimo_comando):
            self.almacen.registrar_evento(
                "RECHAZO", {"origen": "nube", "motivo": "comando caducado o repetido",
                            "edad_s": round(ahora - ts, 1)})
            log.info("Comando de la nube descartado por caducado (%.0f s)", ahora - ts)
            return
        if valor not in (0.0, 1.0):
            self.almacen.registrar_evento("RECHAZO", {"origen": "nube", "motivo": "valor de comando invalido"})
            return
        self._ts_ultimo_comando = ts or ahora
        if self.al_comando:
            self.al_comando(int(valor))

    # ------------------------------------------------------------------ subida
    def avisar(self):
        """Hay filas nuevas en la bandeja de salida."""
        self._hay_trabajo.set()

    def _vaciar(self):
        while not self._parar.is_set():
            if not self.conectado.is_set():
                self._parar.wait(0.5)
                continue
            fila = self.almacen.siguiente_salida()
            if fila is None:
                self._hay_trabajo.clear()
                self._hay_trabajo.wait(2.0)
                continue
            try:
                info = self.cliente.publish(fila["topico"], fila["carga"], qos=1)
                info.wait_for_publish(timeout=10)
                entregado = info.is_published()
            except Exception:               # se cayó la conexión a mitad del envío
                entregado = False
            if entregado:
                self.almacen.marcar_enviado(fila["id"])
                self.enviados += 1
                if self.intervalo_s > 0:
                    self._parar.wait(self.intervalo_s)
            else:
                # No se marca: se reintenta. Entrega "al menos una vez";
                # un duplicado en la nube se sobrescribe por tener la
                # misma marca de tiempo.
                self._parar.wait(1.0)


def carga_nube(valores, ts, contexto=None):
    """Arma el JSON de la nube. Omite las variables sin valor."""
    ms = int(ts * 1000)
    out = {}
    for var, v in valores.items():
        if v is None:
            continue
        punto = {"value": v, "timestamp": ms}
        if contexto and var in contexto:
            punto["context"] = contexto[var]
        out[var] = punto
    return out
