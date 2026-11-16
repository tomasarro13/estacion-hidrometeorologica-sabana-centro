#!/usr/bin/env python3
"""
gateway.py · Gateway IoT del Challenge #3 (Raspberry Pi).

Recorrido de un dato:

  ESP32 --MQTT--> Mosquitto (local) --> [ este programa ] --MQTT--> nube
                                          |  validar
                                          |  guardar crudo en SQLite
                                          |  agregar por ventana
                                          |  módulo de IA
                                          |  bandeja de salida

Y de un comando, en sentido contrario:

  tablero global --MQTT--> [ este programa ] --MQTT--> Mosquitto --> ESP32

Hilos (ninguna función espera a otra):
  - red local    lo crea paho; recibe del broker local y llama a los manejadores
  - red nube     lo crea paho; mantiene la conexión con la plataforma
  - puente       vacía la bandeja de salida hacia la nube
  - ia           corre el análisis y, si aplica, la consulta al modelo
                 generativo (hasta 8 s) sin frenar la recepción
  - principal    vigila, depura la base y espera la señal de apagado

Uso:
  python3 gateway.py --config gateway.ini
"""

import argparse
import configparser
import json
import logging
import os
import queue
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from almacen import Almacen                                    # noqa: E402
from procesamiento import Agregador, validar, GRAVEDAD         # noqa: E402
from puente import PuenteNube, carga_nube, crear_cliente, codigo   # noqa: E402
from ia import prediccion, recomendacion                       # noqa: E402

log = logging.getLogger("gateway")

CODIGO_ALERTA = {"VERDE": 0, "AMARILLA": 1, "ROJA": 2, "FALLA": 3}


class Gateway:
    def __init__(self, cfg):
        self.cfg = cfg
        loc, nube, proc, ia = cfg["local"], cfg["nube"], cfg["procesamiento"], cfg["ia"]

        self.base = loc.get("base_topicos", "sabana")
        self.almacen = Almacen(cfg["almacen"].get("ruta", "datos/gateway.db"))
        self.agregador = Agregador(proc.getint("ventana_s", 300))
        # Tras un reinicio del Gateway se retoma el último consecutivo de
        # cada nodo: lo que no se recibió mientras estuvo apagado queda
        # contado como pérdida, no pasa inadvertido.
        ultimos = self.almacen.ultimos_consecutivos()
        # ...y se reconstruye la ventana que quedó abierta al apagarse, a
        # partir del dato crudo que ya estaba en disco.
        abiertas = self.almacen.lecturas_ventana_abierta(self.agregador.ventana_s)
        for lectura in abiertas:
            ultimos[lectura["nodo"]] = min(ultimos[lectura["nodo"]], lectura["seq"] - 1)
        self.agregador.sembrar(ultimos)
        for lectura in abiertas:
            self.agregador.agregar(lectura)
        self.ia_cada = ia.getint("cada_ventanas", 6)
        self.ia_larga_h = ia.getfloat("ventana_larga_h", 24.0)
        self.ia_corta_h = ia.getfloat("ventana_corta_h", 3.0)
        self.ia_generativa = ia.getboolean("generativa", True)
        self.lugar = ia.get("lugar", "Sabana Centro")
        self._ventanas = {}              # nodo -> ventanas cerradas desde la última IA
        self._umbrales = {}              # nodo -> (amarillo, rojo), los informa el nodo
        self._alerta = {}                # nodo -> última alerta registrada
        self._estado = {}                # nodo -> online / offline
        self._clase_ia = {}              # nodo -> última clasificación de la IA
        self._ts_dato = {}               # nodo -> instante del último dato
        self._cola_ia = queue.Queue()
        self._hilo_ia = threading.Thread(target=self._trabajador_ia, name="ia", daemon=True)
        self.recibidos = 0
        self.rechazados = 0

        # ---- nube ----
        self.puente = None
        if nube.getboolean("activa", True):
            token = os.environ.get("UBIDOTS_TOKEN") or nube.get("token", "")
            self.puente = PuenteNube(
                self.almacen, nube.get("host"), nube.getint("puerto", 1883), token,
                nube.get("dispositivo", "estacion-sabana"),
                variable_comando=nube.get("variable_comando", "silenciar"),
                al_comando=self._comando_desde_nube,
                intervalo_s=nube.getfloat("intervalo_s", 1.0),
                usar_tls=nube.getboolean("tls", False))
        self.nodo_comandos = nube.get("nodo_comandos", "nodo01")
        self.topico_nube = "/v1.6/devices/%s" % nube.get("dispositivo", "estacion-sabana")

        # ---- broker local ----
        self.local = crear_cliente(loc.get("id_cliente", "gateway"))
        clave = os.environ.get("MQTT_LOCAL_CLAVE") or loc.get("clave", "")
        if loc.get("usuario", ""):
            self.local.username_pw_set(loc.get("usuario"), clave)
        self.topico_estado_gw = "%s/gateway/estado" % self.base
        self.local.will_set(self.topico_estado_gw, "offline", qos=1, retain=True)
        self.local.on_connect = self._al_conectar_local
        self.local.on_message = self._al_mensaje_local
        self.local.reconnect_delay_set(min_delay=1, max_delay=15)
        self._host_local = loc.get("host", "localhost")
        self._puerto_local = loc.getint("puerto", 1883)
        self._parar = threading.Event()

    # ================================================================ arranque
    def iniciar(self):
        self.almacen.registrar_evento("GATEWAY", {"estado": "inicio"})
        self.local.connect_async(self._host_local, self._puerto_local, keepalive=30)
        self.local.loop_start()
        self._hilo_ia.start()
        if self.puente:
            self.puente.iniciar()

    def detener(self):
        # La ventana en curso NO se cierra a medias: su dato crudo ya está
        # en SQLite y se reconstruye completa en el siguiente arranque.
        log.info("Apagado ordenado.")
        self.local.unsubscribe(["%s/+/%s" % (self.base, t)
                                for t in ("telemetria", "alerta", "estado", "ack")])
        time.sleep(0.2)
        self._cola_ia.put(None)
        self._hilo_ia.join(timeout=15)
        self.local.publish(self.topico_estado_gw, "offline", qos=1, retain=True)
        time.sleep(0.2)
        self.local.loop_stop()
        self.local.disconnect()
        if self.puente:
            self.puente.detener()
        self.almacen.registrar_evento("GATEWAY", {"estado": "fin"})
        self.almacen.cerrar()

    def esperar(self):
        """Hilo principal: mantenimiento periódico hasta la señal de apagado."""
        t_depura = time.time()
        while not self._parar.wait(2):
            self.almacen.confirmar()         # nada queda sin confirmar más de 2 s
            if time.time() - t_depura > 6 * 3600:
                t_depura = time.time()
                a, b = self.almacen.depurar(self.cfg["almacen"].getint("dias_lecturas", 7))
                if a or b:
                    log.info("Retencion: %d lecturas y %d envios antiguos eliminados", a, b)

    def pedir_parada(self, *_):
        self._parar.set()

    # ============================================================ broker local
    def _al_conectar_local(self, cliente, datos, banderas, rc, *resto):
        if codigo(rc) != 0:
            log.error("El broker local rechazo la conexion (rc=%s)", rc)
            return
        log.info("Conectado al broker local %s:%d", self._host_local, self._puerto_local)
        # QoS 1 en la suscripción: el broker reintenta la entrega al Gateway.
        cliente.subscribe([("%s/+/telemetria" % self.base, 1),
                           ("%s/+/alerta" % self.base, 1),
                           ("%s/+/estado" % self.base, 1),
                           ("%s/+/ack" % self.base, 1)])
        cliente.publish(self.topico_estado_gw, "online", qos=1, retain=True)

    def _al_mensaje_local(self, cliente, datos, msg):
        partes = msg.topic.split("/")
        if len(partes) != 3 or partes[1] == "gateway":
            return
        nodo, tipo = partes[1], partes[2]
        try:
            texto = msg.payload.decode("utf-8")
            if tipo == "telemetria":
                self._telemetria(nodo, texto)
            elif tipo == "alerta":
                self._alerta_nodo(nodo, texto)
            elif tipo == "estado":
                self._estado_nodo(nodo, texto)
            elif tipo == "ack":
                self._ack(nodo, texto)
        except Exception:                 # un mensaje malo no tumba el Gateway
            log.exception("Error procesando %s", msg.topic)

    # -------------------------------------------------------------- telemetría
    def _telemetria(self, nodo, texto):
        ahora = time.time()
        try:
            carga = json.loads(texto)
        except ValueError:
            return self._rechazar(nodo, "JSON invalido")
        ok, motivo = validar(carga)
        if ok and carga["id"] != nodo:
            ok, motivo = False, "el id no coincide con el topico"
        if not ok:
            return self._rechazar(nodo, motivo)

        lectura = {
            "nodo": nodo,
            # El ESP32 no tiene reloj de pared: la marca de tiempo la pone
            # el Gateway al recibir. Si un nodo trae la suya (campo "ts"),
            # se respeta.
            "ts": float(carga.get("ts", ahora)),
            "ts_rx": ahora,
            "seq": carga["seq"], "up": carga.get("up"),
            "nivel": carga.get("nivel"), "temp": carga.get("temp"),
            "hr": carga.get("hr"), "presion": carga.get("presion"),
            "luz": carga.get("luz"), "caudal": carga.get("caudal"),
            "evap": carga.get("evap"),
            "qc": carga["qc"], "cal": carga["cal"],
            "alerta": carga["alerta"], "sil": int(carga.get("sil", 0)),
        }
        self._ts_dato[nodo] = lectura["ts"]
        self.almacen.guardar_lectura(lectura)            # 1) primero a disco
        self.recibidos += 1
        if "uA" in carga and "uR" in carga:
            self._umbrales[nodo] = (float(carga["uA"]), float(carga["uR"]))

        agregado, reinicio = self.agregador.agregar(lectura)   # 2) luego se procesa
        if reinicio:
            self.almacen.registrar_evento("REINICIO_NODO", {"seq": carga["seq"]},
                                          nodo=nodo, ts=lectura["ts"])
        if agregado:
            self._ventana_cerrada(agregado)

    def _rechazar(self, nodo, motivo):
        self.rechazados += 1
        self.almacen.registrar_evento("RECHAZO", {"origen": "nodo", "motivo": motivo}, nodo=nodo)
        log.warning("Mensaje de %s rechazado: %s", nodo, motivo)

    def _ventana_cerrada(self, a):
        nodo = a["nodo"]
        self.almacen.guardar_agregado(a)
        # Copia local para otros consumidores de la WLAN (retenida: quien
        # se conecte después recibe de inmediato el último agregado).
        self.local.publish("%s/%s/agregado" % (self.base, nodo),
                           json.dumps(a, separators=(",", ":")), qos=0, retain=True)
        self._a_la_nube(carga_nube({
            "nivel": a["nivel_med"], "caudal": a["caudal_med"],
            "temp": a["temp_med"], "hr": a["hr_med"], "presion": a["presion_med"],
            "luz": a["luz_med"], "evap": a["evap_med"], "tasa_mmh": a["tasa_mmh"],
            "alerta": CODIGO_ALERTA[a["alerta"]], "qc": a["qc_peor"],
        }, a["ts_fin"]))

        self._ventanas[nodo] = self._ventanas.get(nodo, 0) + 1
        if self._ventanas[nodo] >= self.ia_cada:
            self._ventanas[nodo] = 0
            self._cola_ia.put((nodo, a["ts_fin"]))       # la IA corre en su hilo

    # ------------------------------------------------------------------ IA
    def _trabajador_ia(self):
        while True:
            tarea = self._cola_ia.get()
            if tarea is None:
                return
            try:
                self._correr_ia(*tarea)
            except Exception:
                log.exception("Fallo el modulo de IA; el Gateway sigue operando")

    def _ahora(self, nodo):
        """Instante de referencia de un nodo: el de su último dato."""
        return self._ts_dato.get(nodo, time.time())

    def _correr_ia(self, nodo, ahora):
        if nodo not in self._umbrales:
            return
        ua, ur = self._umbrales[nodo]
        serie = self.almacen.serie_agregados(nodo, ahora - self.ia_larga_h * 3600, ahora)
        r = prediccion.analizar_doble(serie, ua, ur, ahora, self.ia_larga_h, self.ia_corta_h)
        texto, origen = recomendacion.recomendar(r, self.lugar, usar_modelo=self.ia_generativa)
        self.almacen.guardar_prediccion(nodo, ahora, r, texto, origen)
        resumen = {"ts": ahora, "clasificacion": r["clasificacion"], "prioridad": r["prioridad"],
                   "tasa_mmh": r["tasa_mmh"], "exceso_mmh": r["exceso_mmh"],
                   "horas_amarillo": r["horas_amarillo"], "horas_rojo": r["horas_rojo"],
                   "recomendacion": texto, "origen": origen}
        self.local.publish("%s/%s/prediccion" % (self.base, nodo),
                           json.dumps(resumen, ensure_ascii=False), qos=0, retain=True)
        anomalia = 1 if r["clasificacion"] == "DESCENSO_NO_EXPLICADO" else 0
        # Un cambio de clasificación sale con prioridad: es una alerta nueva.
        cambio = self._clase_ia.get(nodo) != r["clasificacion"]
        self._clase_ia[nodo] = r["clasificacion"]
        self._a_la_nube(carga_nube(
            {"anomalia": anomalia, "horas_umbral": r["horas_amarillo"],
             "exceso_mmh": r["exceso_mmh"]},
            ahora,
            contexto={"anomalia": {"clase": r["clasificacion"], "prioridad": r["prioridad"],
                                   "recomendacion": texto, "origen": origen}}),
            prioridad=1 if cambio and anomalia else 0)
        log.info("IA %s: %s (%s) · %s", nodo, r["clasificacion"], r["prioridad"], texto)

    # -------------------------------------------------- alertas, estado, acks
    def _alerta_nodo(self, nodo, texto):
        try:
            c = json.loads(texto)
            alerta, sil = c["alerta"], int(c.get("sil", 0))
        except (ValueError, KeyError, TypeError):
            return self._rechazar(nodo, "alerta ilegible")
        if alerta not in GRAVEDAD:
            return self._rechazar(nodo, "alerta desconocida")
        if self._alerta.get(nodo) == (alerta, sil):
            return                        # mensaje retenido repetido
        previa = self._alerta.get(nodo, (None, 0))[0]
        self._alerta[nodo] = (alerta, sil)
        ts = self._ahora(nodo)
        self.almacen.registrar_evento(
            "ALERTA", {"alerta": alerta, "previa": previa, "silenciada": sil},
            nodo=nodo, ts=ts)
        # Las transiciones no esperan al cierre de la ventana: salen ya y
        # con prioridad. Es lo que dispara la notificación en la nube.
        self._a_la_nube(carga_nube(
            {"alerta": CODIGO_ALERTA[alerta], "silenciada": sil}, ts,
            contexto={"alerta": {"estado": alerta}}), prioridad=1)
        log.info("ALERTA %s: %s -> %s%s", nodo, previa, alerta, " (silenciada)" if sil else "")

    def _estado_nodo(self, nodo, texto):
        estado = texto.strip()
        if estado not in ("online", "offline") or self._estado.get(nodo) == estado:
            return
        self._estado[nodo] = estado
        ts = self._ahora(nodo)
        self.almacen.registrar_evento("ESTADO_NODO", {"estado": estado}, nodo=nodo, ts=ts)
        self._a_la_nube(carga_nube({"en_linea": 1 if estado == "online" else 0}, ts),
                        prioridad=1)
        log.info("Nodo %s: %s", nodo, estado)

    def _ack(self, nodo, texto):
        self.almacen.registrar_evento("ACK", texto, nodo=nodo, ts=self._ahora(nodo))
        log.info("Confirmacion de %s: %s", nodo, texto)

    # ---------------------------------------------------------- comando: bajada
    def _comando_desde_nube(self, valor):
        """valor = 1 silencia la alarma física; 0 la reactiva."""
        nodo = self.nodo_comandos
        topico = "%s/%s/cmd/silenciar" % (self.base, nodo)
        self.almacen.registrar_evento("COMANDO", {"origen": "nube", "silenciar": valor},
                                      nodo=nodo, ts=self._ahora(nodo))
        # QoS 1 y sin retener: si el nodo no está, la orden se pierde a
        # propósito. Una orden de silencio no debe ejecutarse más tarde.
        self.local.publish(topico, str(valor), qos=1, retain=False)
        log.info("Comando de la nube -> %s = %s", topico, valor)

    def _a_la_nube(self, carga, prioridad=0):
        if not self.puente or not carga:
            return
        self.almacen.encolar_salida(self.topico_nube, carga, prioridad)
        self.puente.avisar()


def cargar_config(ruta):
    cfg = configparser.ConfigParser(interpolation=None)
    # El archivo se escribe en español: "si"/"no" además de yes/no, true/false.
    cfg.BOOLEAN_STATES = dict(cfg.BOOLEAN_STATES, si=True, **{"sí": True})
    for seccion in ("local", "nube", "almacen", "procesamiento", "ia"):
        cfg.add_section(seccion)
    if ruta:
        leidos = cfg.read(ruta, encoding="utf-8")
        if not leidos:
            raise SystemExit("No se pudo leer el archivo de configuracion: %s" % ruta)
    return cfg


def main():
    ap = argparse.ArgumentParser(description="Gateway IoT · Challenge #3")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "gateway.ini"))
    ap.add_argument("--verboso", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verboso else logging.INFO,
                        format="%(asctime)s %(name)-8s %(levelname)-7s %(message)s")
    gw = Gateway(cargar_config(args.config))
    signal.signal(signal.SIGTERM, gw.pedir_parada)
    signal.signal(signal.SIGINT, gw.pedir_parada)
    gw.iniciar()
    try:
        gw.esperar()
    finally:
        gw.detener()


if __name__ == "__main__":
    main()
