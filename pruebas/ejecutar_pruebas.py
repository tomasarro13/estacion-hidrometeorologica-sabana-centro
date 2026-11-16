#!/usr/bin/env python3
"""
ejecutar_pruebas.py · Validación por simulación del Challenge #3.

Levanta el ecosistema completo en una sola máquina y lo somete al
protocolo de pruebas de la Wiki (C3 §7):

    nodo simulado ──MQTT──> Mosquitto ──> gateway.py ──MQTT──> nube emulada
    (simulador.py)          (real)        (real, con SQLite     (segundo
                                           e IA reales)          Mosquitto)

QUÉ ES REAL: el broker Mosquitto con su configuración, usuarios y ACL;
el programa gateway.py completo; la base SQLite; el módulo de IA.
QUÉ ES EMULADO: el ESP32 (simulador.py) y la plataforma en la nube (un
segundo broker que recibe los mismos tópicos y cargas que Ubidots).

Cada cifra que este programa escribe sale de una ejecución; no hay
ningún valor escrito a mano. Para repetirlo:

    python3 pruebas/ejecutar_pruebas.py
    (requiere mosquitto y mosquitto_passwd en el PATH, o las variables
     MOSQUITTO_BIN y MOSQUITTO_PASSWD_BIN)

Resultados: datos/ch3-simulacion/
"""

import csv
import json
import os
import platform
import shutil
import signal
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "simulador"))

import paho.mqtt.client as mqtt                        # noqa: E402
import simulador as sim                                # noqa: E402

MOSQ = os.environ.get("MOSQUITTO_BIN", "mosquitto")
MOSQ_PASSWD = os.environ.get("MOSQUITTO_PASSWD_BIN", "mosquitto_passwd")
SALIDA = os.path.join(RAIZ, "datos", "ch3-simulacion")
P_LOCAL, P_NUBE, P_PROXY = 18830, 18840, 18841
CLAVES = {"nodo01": "clave-nodo-sim", "gateway": "clave-gw-sim", "monitor": "clave-mon-sim"}
DISPOSITIVO = "estacion-sabana"
T_NUBE = "/v1.6/devices/%s" % DISPOSITIVO
RITMO = float(os.environ.get("RITMO", "1500"))         # muestras/s de reloj


def cliente(id_, usuario=None, clave=None):
    try:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=id_)
    except AttributeError:
        c = mqtt.Client(client_id=id_)
    if usuario is not None:
        c.username_pw_set(usuario, clave)
    return c


def rc_int(rc):
    return getattr(rc, "value", rc)


class ProxyInternet:
    """Reenvía TCP hacia la nube emulada. cortar() simula la caída del
    enlace a Internet: la nube sigue viva, pero el Gateway no llega."""

    def __init__(self, puerto, destino):
        self.puerto, self.destino = puerto, destino
        self._conexiones, self._srv, self._activo = [], None, False
        self.restablecer()

    def restablecer(self):
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", self.puerto))
        self._srv.listen(8)
        self._activo = True
        threading.Thread(target=self._aceptar, args=(self._srv,), daemon=True).start()

    def cortar(self):
        self._activo = False
        for cerrar in (lambda: self._srv.shutdown(socket.SHUT_RDWR), self._srv.close):
            try:
                cerrar()                 # shutdown despierta al hilo que espera en accept()
            except OSError:
                pass
        for s in self._conexiones:
            try:
                s.shutdown(socket.SHUT_RDWR)
                s.close()
            except OSError:
                pass
        self._conexiones = []

    def _aceptar(self, srv):
        while self._activo:
            try:
                a, _ = srv.accept()
            except OSError:
                return
            if not self._activo or srv is not self._srv:
                a.close()
                return
            try:
                b = socket.create_connection(("127.0.0.1", self.destino))
            except OSError:
                a.close()
                continue
            self._conexiones += [a, b]
            threading.Thread(target=self._bombear, args=(a, b), daemon=True).start()
            threading.Thread(target=self._bombear, args=(b, a), daemon=True).start()

    @staticmethod
    def _bombear(o, d):
        try:
            while True:
                datos = o.recv(65536)
                if not datos:
                    break
                d.sendall(datos)
        except OSError:
            pass
        for s in (o, d):
            try:
                s.close()
            except OSError:
                pass


class Entorno:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="ch3sim-")
        self.procs = {}
        self.gw_n = 0
        self.bd = None
        self.nube_msgs = []          # (t_rx, carga dict)
        self.local_msgs = []         # (t_rx, topico, texto)
        self._brokers()
        self.proxy = ProxyInternet(P_PROXY, P_NUBE)
        self._observadores()

    # -------------------------------------------------------------- brokers
    def _brokers(self):
        pw = os.path.join(self.tmp, "estacion.passwd")
        for i, (u, k) in enumerate(CLAVES.items()):
            subprocess.run([MOSQ_PASSWD, "-b"] + (["-c"] if i == 0 else []) + [pw, u, k],
                           check=True, capture_output=True)
        acl = os.path.join(self.tmp, "estacion.acl")
        shutil.copy(os.path.join(RAIZ, "gateway", "mosquitto", "estacion.acl"), acl)
        os.chmod(self.tmp, 0o755)        # mosquitto suelta privilegios al arrancar
        os.chmod(pw, 0o644)
        os.chmod(acl, 0o644)
        # La configuración es la del repositorio; solo cambian puerto y rutas.
        conf = open(os.path.join(RAIZ, "gateway", "mosquitto", "estacion.conf"), encoding="utf-8").read()
        conf = (conf.replace("listener 1883 0.0.0.0", "listener %d 127.0.0.1" % P_LOCAL)
                    .replace("/etc/mosquitto/estacion.passwd", pw)
                    .replace("/etc/mosquitto/estacion.acl", acl)
                    .replace("persistence true", "persistence false")
                    .replace("persistence_location /var/lib/mosquitto/", ""))
        self._lanzar_broker("local", conf)
        self._lanzar_broker("nube", "listener %d 127.0.0.1\nallow_anonymous true\n" % P_NUBE)
        time.sleep(0.8)

    def _lanzar_broker(self, nombre, conf):
        ruta = os.path.join(self.tmp, "%s.conf" % nombre)
        open(ruta, "w", encoding="utf-8").write(conf)
        log = open(os.path.join(self.tmp, "%s.log" % nombre), "w")
        self.procs[nombre] = subprocess.Popen([MOSQ, "-c", ruta], stdout=log, stderr=log)

    def _observadores(self):
        self.obs_nube = cliente("observador-nube")
        self.obs_nube.on_connect = lambda c, d, f, rc, *r: c.subscribe("/v1.6/#", 1)
        self.obs_nube.on_message = self._msg_nube
        self.obs_nube.connect("127.0.0.1", P_NUBE)
        self.obs_nube.loop_start()
        self.obs_local = cliente("monitor", "monitor", CLAVES["monitor"])
        self.obs_local.on_connect = lambda c, d, f, rc, *r: c.subscribe("sabana/#", 1)
        self.obs_local.on_message = lambda c, d, m: (
            None if m.topic.endswith("/telemetria")
            else self.local_msgs.append((time.time(), m.topic, m.payload.decode("utf-8", "replace"))))
        self.obs_local.connect("127.0.0.1", P_LOCAL)
        self.obs_local.loop_start()
        time.sleep(0.5)

    def _msg_nube(self, c, d, m):
        if m.topic != T_NUBE:
            return
        try:
            self.nube_msgs.append((time.time(), json.loads(m.payload)))
        except ValueError:
            pass

    # -------------------------------------------------------------- gateway
    def iniciar_gateway(self, nuevo=True, cada_ventanas=6):
        if nuevo:
            self.gw_n += 1
            self.bd = os.path.join(self.tmp, "gw%d.db" % self.gw_n)
            self.nube_msgs.clear()
            self.local_msgs.clear()
            self._limpiar_retenidos()
        ini = os.path.join(self.tmp, "gateway.ini")
        open(ini, "w", encoding="utf-8").write(
            "[local]\nhost=127.0.0.1\npuerto=%d\nusuario=gateway\nclave=%s\n"
            "[nube]\nactiva=si\nhost=127.0.0.1\npuerto=%d\ntls=no\ntoken=TOKEN-SIMULADO\n"
            "dispositivo=%s\nintervalo_s=0\n"
            "[almacen]\nruta=%s\n[procesamiento]\nventana_s=300\n"
            "[ia]\ncada_ventanas=%d\ngenerativa=no\n"
            % (P_LOCAL, CLAVES["gateway"], P_PROXY, DISPOSITIVO, self.bd, cada_ventanas))
        log = open(os.path.join(self.tmp, "gateway%d.log" % self.gw_n), "a")
        marca = len(self.local_msgs)
        entorno = dict(os.environ)
        entorno.pop("GEMINI_API_KEY", None)
        self.procs["gw"] = subprocess.Popen(
            [sys.executable, os.path.join(RAIZ, "gateway", "gateway.py"), "--config", ini],
            stdout=log, stderr=log, env=entorno)
        self._esperar(lambda: any(t == "sabana/gateway/estado" and x == "online"
                                  for _, t, x in self.local_msgs[marca:]), 10, "arranque del gateway")
        time.sleep(0.3)

    def _limpiar_retenidos(self):
        c = cliente("limpieza", "gateway", CLAVES["gateway"])
        c.connect("127.0.0.1", P_LOCAL)
        c.loop_start()
        for t in ("agregado", "prediccion"):
            c.publish("sabana/nodo01/%s" % t, b"", qos=1, retain=True).wait_for_publish(3)
        c.loop_stop()
        c.disconnect()
        c = cliente("limpieza-n", "nodo01", CLAVES["nodo01"])
        c.connect("127.0.0.1", P_LOCAL)
        c.loop_start()
        for t in ("alerta", "estado"):
            c.publish("sabana/nodo01/%s" % t, b"", qos=1, retain=True).wait_for_publish(3)
        c.loop_stop()
        c.disconnect()
        time.sleep(0.2)
        self.local_msgs.clear()

    def detener_gateway(self):
        p = self.procs.pop("gw", None)
        if p:
            p.send_signal(signal.SIGTERM)
            p.wait(timeout=30)

    def sql(self, consulta, args=()):
        con = sqlite3.connect("file:%s?mode=ro" % self.bd, uri=True, timeout=10)
        try:
            return con.execute(consulta, args).fetchall()
        finally:
            con.close()

    def _esperar(self, cond, seg, que):
        fin = time.time() + seg
        while time.time() < fin:
            try:
                if cond():
                    return True
            except sqlite3.OperationalError:
                pass
            time.sleep(0.05)
        raise RuntimeError("Tiempo agotado esperando: %s" % que)

    def esperar_lecturas(self, n, seg=120):
        self._esperar(lambda: self.sql("SELECT COUNT(*) FROM lecturas")[0][0] >= n, seg,
                      "%d lecturas en SQLite" % n)

    def esperar_salida_vacia(self, seg=120):
        self._esperar(lambda: self.sql("SELECT COUNT(*) FROM salida WHERE enviado=0")[0][0] == 0,
                      seg, "bandeja de salida vacia")
        time.sleep(0.4)

    def nodo(self, escenario, **kw):
        return sim.NodoSimulado(escenario, "127.0.0.1", P_LOCAL, "nodo01", CLAVES["nodo01"], **kw)

    def cerrar(self):
        self.detener_gateway()
        for c in (self.obs_nube, self.obs_local):
            c.loop_stop()
        self.proxy.cortar()
        for p in self.procs.values():
            p.terminate()
        shutil.rmtree(self.tmp, ignore_errors=True)


# ======================================================================
#  Utilidades de reporte
# ======================================================================
R = {"pruebas": {}}


def guardar_csv(nombre, encabezado, filas):
    with open(os.path.join(SALIDA, nombre), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(encabezado)
        w.writerows(filas)


def resumen(xs):
    xs = sorted(xs)
    return {"n": len(xs), "min": round(xs[0], 2), "mediana": round(statistics.median(xs), 2),
            "p95": round(xs[min(len(xs) - 1, int(0.95 * len(xs)))], 2), "max": round(xs[-1], 2)}


def puntos_nube(env, variable):
    """[(t_rx, timestamp_s, valor)] de una variable recibida en la nube."""
    out = []
    for t_rx, carga in env.nube_msgs:
        if variable in carga:
            out.append((t_rx, carga[variable]["timestamp"] / 1000.0, carga[variable]["value"]))
    return out


def correr_escenario(env, nombre, ruido=0.02, horas=None, etiqueta=None):
    """Corre un escenario completo con un Gateway y una base nuevos."""
    etiqueta = etiqueta or nombre
    env.iniciar_gateway()
    ruta_verdad = os.path.join(SALIDA, "verdad_%s.csv" % etiqueta)
    with open(ruta_verdad, "w", encoding="utf-8") as f:
        f.write("ts,nivel_real_cm,nivel_medido_cm,evap_mm_dia,fuga_mm_h,alerta,qc\n")
        nodo = env.nodo(nombre, ruido_cm=ruido, verdad=f)
        t0 = time.time()
        n = nodo.correr(RITMO, horas=horas)
        dur = time.time() - t0
        env.esperar_lecturas(n)
        nodo.cerrar()
    env.esperar_salida_vacia()
    time.sleep(1.0)                               # deja terminar al hilo de IA
    env.esperar_salida_vacia()
    return nodo, n, dur, ruta_verdad


def leer_verdad(ruta, cada=150):
    """Verdad del modelo, submuestreada a una fila cada 5 min."""
    filas = []
    with open(ruta, encoding="utf-8") as f:
        for i, fila in enumerate(csv.DictReader(f)):
            filas.append(fila)
    return filas


def compactar_verdad(ruta):
    """Deja el CSV de verdad en una fila cada 5 min (el completo pesa MB)."""
    filas = leer_verdad(ruta)
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        f.write("# Estado real del MODELO de simulacion (no es una medicion). Una fila cada 5 min.\n")
        w = csv.DictWriter(f, fieldnames=list(filas[0].keys()))
        w.writeheader()
        w.writerows(filas[::150])
    return filas


# ======================================================================
#  S1 · Integridad de extremo a extremo  +  S9 · Módulo de IA
# ======================================================================
def instante_cruce(verdad, umbral):
    for fila in verdad:
        if float(fila["nivel_real_cm"]) < umbral:
            return float(fila["ts"])
    return None


def comparar_estimadores(env, cruce):
    """Sobre la MISMA serie guardada en SQLite, proyecta el cruce del
    umbral con los dos estimadores de la ventana de 24 h y compara el
    error. Solo instantes con un día completo de historia."""
    from ia import prediccion
    serie = env.sql("SELECT ts_fin, nivel_med, evap_med FROM agregados "
                    "WHERE nivel_med IS NOT NULL ORDER BY ts_fin")
    out = {}
    for metodo in ("theil_sen", "ciclo"):
        errores, tasas = [], []
        for i in range(0, len(serie), 6):
            ahora = serie[i][0]
            if ahora - serie[0][0] < 24 * 3600 or ahora >= cruce:
                continue
            ventana = [p for p in serie if ahora - 24 * 3600 < p[0] <= ahora]
            r = prediccion.analizar(ventana, sim.UMBRAL_AMARILLO_CM, sim.UMBRAL_ROJO_CM, metodo=metodo)
            if r["horas_amarillo"]:
                errores.append((ahora + r["horas_amarillo"] * 3600 - cruce) / 3600.0)
                tasas.append(r["tasa_mmh"])
        if errores:
            out[metodo] = {"n": len(errores),
                           "error_abs_medio_h": round(statistics.mean(abs(x) for x in errores), 2),
                           "error_abs_max_h": round(max(abs(x) for x in errores), 2),
                           "sesgo_medio_h": round(statistics.mean(errores), 2),
                           "tasa_min_mmh": min(tasas), "tasa_max_mmh": max(tasas)}
    return out


def prueba_escenarios(env):
    casos = [("estable", 0.02, "estable"), ("sequia", 0.02, "sequia"), ("fuga", 0.02, "fuga"),
             ("estable", 0.176, "estable_ruido_alto"), ("fuga", 0.176, "fuga_ruido_alto"),
             ("sequia", 0.176, "sequia_ruido_alto"), ("falla", 0.02, "falla")]
    integridad, ia = [], {}
    for nombre, ruido, etiqueta in casos:
        print("  escenario %-20s ..." % etiqueta, end=" ", flush=True)
        nodo, n, dur, ruta = correr_escenario(env, nombre, ruido, etiqueta=etiqueta)
        verdad = compactar_verdad(ruta)
        lect = env.sql("SELECT COUNT(*) FROM lecturas")[0][0]
        agr, perd, nval = env.sql("SELECT COUNT(*), SUM(perdidas), SUM(n) FROM agregados")[0]
        pend = env.sql("SELECT COUNT(*) FROM salida WHERE enviado=0")[0][0]
        enc = env.sql("SELECT COUNT(*) FROM salida")[0][0]
        niveles = puntos_nube(env, "nivel")
        ts_bd = {r[0] for r in env.sql("SELECT ts_fin FROM agregados WHERE nivel_med IS NOT NULL")}
        ts_nube = [p[1] for p in niveles]
        datos_nube = sum(len(c) for _, c in env.nube_msgs)
        horas = sim.ESCENARIOS[nombre]["horas"]
        qc = dict(env.sql("SELECT qc, COUNT(*) FROM lecturas GROUP BY qc"))
        alertas = env.sql("SELECT ts, detalle FROM eventos WHERE tipo='ALERTA' ORDER BY id")
        integridad.append({
            "escenario": etiqueta, "ruido_cm": ruido, "horas_simuladas": horas,
            "publicados": n, "en_sqlite": lect, "perdidos": n - lect,
            "ventanas_cerradas_esperadas": horas * 12 - 1, "agregados": agr, "perdidas_contadas": perd or 0,
            "mensajes_encolados": enc, "pendientes_al_final": pend,
            "mensajes_en_nube": len(env.nube_msgs),
            "agregados_con_nivel": len(ts_bd),
            "agregados_en_nube": len(set(ts_nube) & ts_bd), "duplicados_nube": len(ts_nube) - len(set(ts_nube)),
            "datos_nube": datos_nube, "datos_nube_por_dia": round(datos_nube * 24.0 / horas),
            "qc": {str(k): v for k, v in sorted(qc.items())},
            "lecturas_sin_nivel": env.sql("SELECT COUNT(*) FROM lecturas WHERE nivel IS NULL")[0][0],
            "agregados_sin_nivel": env.sql("SELECT COUNT(*) FROM agregados WHERE nivel_med IS NULL")[0][0],
            "secuencia_alertas": [json.loads(a[1])["alerta"] for a in alertas][:12],
            "pct_qc_sospechoso": round(100.0 * qc.get(3, 0) / lect, 2),
            "transiciones_alarma_nodo": nodo.transiciones,
            "alarmas_por_10min": round(nodo.transiciones / (horas * 6.0), 3),
            "eventos_alerta_gateway": len(alertas),
            "segundos_de_reloj": round(dur, 1), "mensajes_por_segundo": round(n / dur),
        })
        print("%d msgs, %d en SQLite, %d agregados, %d en nube" % (n, lect, agr, len(set(ts_nube) & ts_bd)))

        # ---- línea de tiempo del módulo de IA ----
        pred = env.sql("SELECT ts, clasificacion, tasa_mmh, evap_mmh, exceso_mmh, horas_amarillo, "
                       "horas_rojo, prioridad, recomendacion, origen_texto, detalle FROM predicciones ORDER BY ts")
        t0 = sim.T0_SIM
        cruce = instante_cruce(verdad, sim.UMBRAL_AMARILLO_CM)
        filas, errores = [], []
        for p in pred:
            h = (p[0] - t0) / 3600.0
            det = json.loads(p[10])
            err = None
            # En el escenario de fuga solo cuentan las predicciones hechas una
            # vez DETECTADA la anomalía: antes de la fuga no había nada que
            # predecir, y la demora en detectarla se reporta aparte.
            cuenta = (p[1] == "DESCENSO_NO_EXPLICADO") if sim.ESCENARIOS[nombre]["fuga_mmh"] else True
            if cruce and p[5] is not None and cuenta and p[0] < cruce and p[5] > 0:
                err = (p[0] + p[5] * 3600.0 - cruce) / 3600.0
                errores.append(((cruce - p[0]) / 3600.0, err, p[5]))
            filas.append([round(h, 2), p[1], p[2], p[3], p[4], p[5], p[6], p[7], det.get("ventana_h"),
                          det["corta"]["clasificacion"], det["larga"]["clasificacion"],
                          "" if err is None else round(err, 2), p[9], p[8]])
        guardar_csv("ia_%s.csv" % etiqueta,
                    ["hora_sim", "clasificacion", "tasa_mmh", "evap_mmh", "exceso_mmh", "horas_a_umbral",
                     "horas_a_rojo", "prioridad", "ventana_h", "clase_ventana_corta", "clase_ventana_larga",
                     "error_prediccion_h", "origen_texto", "recomendacion"], filas)
        clases = {}
        for f in filas:
            clases[f[1]] = clases.get(f[1], 0) + 1
        info = {"corridas": len(filas), "clasificaciones": clases,
                "hora_cruce_real_umbral": None if not cruce else round((cruce - t0) / 3600.0, 2)}
        e = sim.ESCENARIOS[nombre]
        if e["fuga_mmh"]:
            primera = next((f for f in filas if f[1] == "DESCENSO_NO_EXPLICADO" and f[0] >= e["fuga_h"]), None)
            falsas = sum(1 for f in filas if f[1] == "DESCENSO_NO_EXPLICADO" and f[0] < e["fuga_h"])
            info["hora_inicio_fuga"] = e["fuga_h"]
            info["hora_primera_deteccion"] = primera[0] if primera else None
            info["demora_deteccion_h"] = round(primera[0] - e["fuga_h"], 2) if primera else None
            info["anomalias_antes_de_la_fuga"] = falsas
            tras = [f for f in filas if f[0] >= e["fuga_h"] + 3.0 and (not cruce or f[0] * 3600 + t0 < cruce)]
            info["deteccion_sostenida_pct"] = round(
                100.0 * sum(1 for f in tras if f[1] == "DESCENSO_NO_EXPLICADO") / len(tras), 1) if tras else None
        else:
            info["falsas_anomalias"] = clases.get("DESCENSO_NO_EXPLICADO", 0)
        if errores:
            def franja(lo, hi):
                xs = [abs(er) for ant, er, _ in errores if lo <= ant < hi]
                return None if not xs else {"n": len(xs), "error_abs_medio_h": round(statistics.mean(xs), 2),
                                            "error_abs_max_h": round(max(xs), 2)}
            info["error_prediccion_cruce"] = {
                "n": len(errores),
                "error_abs_medio_h": round(statistics.mean(abs(er) for _, er, _ in errores), 2),
                "sesgo_medio_h": round(statistics.mean(er for _, er, _ in errores), 2),
                "anticipacion_0_6h": franja(0, 6), "anticipacion_6_24h": franja(6, 24),
                "anticipacion_mas_24h": franja(24, 1e9),
            }
        if not e["fuga_mmh"] and cruce:
            info["comparacion_estimadores_24h"] = comparar_estimadores(env, cruce)
        # Alarma del NODO frente a la verdad del modelo (una fila cada 5 min)
        muestras = verdad[::150]
        en_alarma = [f for f in muestras if f["alerta"] in ("AMARILLA", "ROJA")]
        primera = next((f for f in muestras if f["alerta"] != "VERDE"), None)
        info["alarma_del_nodo"] = {
            "pct_tiempo_en_alarma": round(100.0 * len(en_alarma) / len(muestras), 1),
            "pct_tiempo_en_alarma_con_nivel_sobre_umbral": round(100.0 * sum(
                1 for f in en_alarma if float(f["nivel_real_cm"]) >= sim.UMBRAL_AMARILLO_CM) / len(muestras), 1),
            "hora_primera_alarma": None if not primera else round((float(primera["ts"]) - t0) / 3600.0, 2),
            "nivel_real_en_la_primera_alarma_cm": None if not primera else round(float(primera["nivel_real_cm"]), 2),
        }
        ultima = filas[-1] if filas else None
        info["ultima_recomendacion"] = ultima[13] if ultima else None
        ia[etiqueta] = info
        env.detener_gateway()
    guardar_csv("integridad.csv", list(integridad[0].keys()),
                [[json.dumps(v) if isinstance(v, dict) else v for v in f.values()] for f in integridad])
    R["pruebas"]["S1_integridad"] = integridad
    R["pruebas"]["S9_ia"] = ia


# ======================================================================
#  S2 · Control de acceso del broker local
# ======================================================================
def _conecta(usuario, clave, id_="prueba-acceso"):
    res = {}
    c = cliente(id_, usuario, clave)
    c.on_connect = lambda cl, d, f, rc, *r: res.setdefault("rc", rc_int(rc))
    try:
        c.connect("127.0.0.1", P_LOCAL)
    except OSError as e:
        return None, "error de red: %s" % e
    c.loop_start()
    fin = time.time() + 3
    while "rc" not in res and time.time() < fin:
        time.sleep(0.02)
    return c, res.get("rc")


def prueba_acceso(env):
    env.iniciar_gateway()
    casos = []

    def caso(desc, esperado, obtenido):
        casos.append({"caso": desc, "esperado": esperado, "obtenido": obtenido,
                      "pasa": esperado == obtenido})

    def veredicto(rc):
        return "aceptado" if rc == 0 else "rechazado"

    for desc, u, k, esp in (("Conexión anónima (sin usuario)", None, None, "rechazado"),
                            ("Usuario válido con clave incorrecta", "nodo01", "clave-mala", "rechazado"),
                            ("Usuario inexistente", "intruso", "x", "rechazado"),
                            ("Usuario y clave correctos", "nodo01", CLAVES["nodo01"], "aceptado")):
        c, rc = _conecta(u, k)
        caso(desc, esp, veredicto(rc))
        c.loop_stop()
        c.disconnect()

    # ACL: el nodo no puede publicar en la rama de otro nodo ni dar comandos
    antes = len(env.local_msgs)
    c, _ = _conecta("nodo01", CLAVES["nodo01"], "nodo01-acl")
    c.publish("sabana/nodo02/alerta", '{"alerta":"ROJA"}', qos=1).wait_for_publish(3)
    c.publish("sabana/nodo01/cmd/silenciar", "1", qos=1).wait_for_publish(3)
    c.publish("sabana/gateway/estado", "offline", qos=1).wait_for_publish(3)
    time.sleep(0.6)
    nuevos = [t for _, t, _ in env.local_msgs[antes:]]
    caso("nodo01 publica en la rama de nodo02", "bloqueado",
         "bloqueado" if "sabana/nodo02/alerta" not in nuevos else "entregado")
    caso("nodo01 se envía un comando a sí mismo", "bloqueado",
         "bloqueado" if "sabana/nodo01/cmd/silenciar" not in nuevos else "entregado")
    caso("nodo01 suplanta el estado del Gateway", "bloqueado",
         "bloqueado" if "sabana/gateway/estado" not in nuevos else "entregado")
    # ACL: el nodo no puede leer la telemetría (ni la suya ni la de otros)
    vistos = []
    c.on_message = lambda cl, d, m: vistos.append(m.topic)
    c.subscribe("sabana/#", 1)
    time.sleep(0.3)
    g, _ = _conecta("gateway", CLAVES["gateway"], "gw-acl")
    g.publish("sabana/nodo01/agregado", '{"x":1}', qos=1).wait_for_publish(3)
    g.publish("sabana/nodo01/cmd/silenciar", "0", qos=1).wait_for_publish(3)
    time.sleep(0.6)
    caso("nodo01 lee datos agregados de la red", "bloqueado",
         "bloqueado" if "sabana/nodo01/agregado" not in vistos else "entregado")
    caso("nodo01 recibe sus propios comandos", "entregado",
         "entregado" if "sabana/nodo01/cmd/silenciar" in vistos else "bloqueado")
    # monitor: solo lectura
    antes = len(env.local_msgs)
    m, _ = _conecta("monitor", CLAVES["monitor"], "monitor-acl")
    m.publish("sabana/nodo01/cmd/silenciar", "1", qos=1).wait_for_publish(3)
    time.sleep(0.5)
    caso("usuario de solo lectura envía un comando", "bloqueado",
         "bloqueado" if not any(t == "sabana/nodo01/cmd/silenciar" and x == "1"
                                for _, t, x in env.local_msgs[antes:]) else "entregado")
    for x in (c, g, m):
        x.loop_stop()
        x.disconnect()
    env.detener_gateway()
    R["pruebas"]["S2_acceso"] = {"casos": casos, "pasan": sum(c["pasa"] for c in casos), "total": len(casos)}


# ======================================================================
#  S3 · Validación de mensajes en el Gateway
# ======================================================================
def prueba_validacion(env):
    env.iniciar_gateway()
    bueno = {"id": "nodo01", "seq": 1, "up": 2, "nivel": 18.5, "temp": 19.0, "hr": 60.0,
             "presion": 752.0, "luz": 40.0, "caudal": 3.0, "evap": 3.5, "qc": 1, "cal": 0,
             "alerta": "VERDE", "sil": 0, "bmp": 1, "uA": 12.0, "uR": 8.0}

    def variante(**cambios):
        d = dict(bueno)
        d.update(cambios)
        return json.dumps({k: v for k, v in d.items() if v != "__quitar__"})

    malos = [
        ("Texto que no es JSON", "esto no es json"),
        ("JSON truncado", '{"id":"nodo01","seq":5,'),
        ("JSON válido pero no es un objeto", "[1,2,3]"),
        ("Falta el consecutivo (seq)", variante(seq="__quitar__")),
        ("Nivel no numérico", variante(nivel="alto")),
        ("Humedad fuera de rango físico (150 %)", variante(hr=150.0)),
        ("Bandera QARTOD inexistente (qc = 7)", variante(qc=7)),
        ("Estado de alerta desconocido", variante(alerta="NARANJA")),
        ("El id del mensaje no coincide con el tópico", variante(id="nodo99")),
        ("Consecutivo negativo", variante(seq=-4)),
    ]
    c, _ = _conecta("nodo01", CLAVES["nodo01"], "nodo01")
    c.publish("sabana/nodo01/telemetria", json.dumps(bueno), qos=1).wait_for_publish(3)
    for _, carga in malos:
        c.publish("sabana/nodo01/telemetria", carga, qos=1).wait_for_publish(3)
    # un mensaje con nivel null (sin eco) SÍ es válido: es un dato faltante declarado
    c.publish("sabana/nodo01/telemetria", variante(seq=2, nivel=None, caudal=None, qc=9, cal=4,
                                                   alerta="FALLA"), qos=1).wait_for_publish(3)
    c.publish("sabana/nodo01/telemetria", variante(seq=3), qos=1).wait_for_publish(3)
    env.esperar_lecturas(3, 10)
    time.sleep(0.5)
    env.detener_gateway()
    rech = [json.loads(r[0])["motivo"] for r in
            env.sql("SELECT detalle FROM eventos WHERE tipo='RECHAZO' ORDER BY id")]
    casos = [{"caso": d, "motivo_registrado": rech[i] if i < len(rech) else None}
             for i, (d, _) in enumerate(malos)]
    R["pruebas"]["S3_validacion"] = {
        "malformados_enviados": len(malos), "rechazados": len(rech),
        "validos_enviados": 3, "lecturas_guardadas": env.sql("SELECT COUNT(*) FROM lecturas")[0][0],
        "gateway_siguio_operando": env.sql("SELECT COUNT(*) FROM lecturas WHERE seq=3")[0][0] == 1,
        "casos": casos}
    c.loop_stop()
    c.disconnect()


# ======================================================================
#  S4 · Latencia por tramo  +  S6 · Comando de bajada y salvaguardas
# ======================================================================
def prueba_latencia_y_comando(env):
    env.iniciar_gateway()
    nodo = env.nodo("critico")
    t_pub = {}

    def al_paso(i, carga):
        t_pub[carga["seq"]] = time.time()

    # --- telemetría: 300 muestras espaciadas 20 ms ---
    nodo.correr(ritmo=50, horas=300 * 2 / 3600.0, al_paso=al_paso)
    env.esperar_lecturas(300, 20)
    time.sleep(0.5)
    lat = [(ts_rx - t_pub[seq]) * 1000.0 for seq, ts_rx in env.sql("SELECT seq, ts_rx FROM lecturas")
           if seq in t_pub]
    guardar_csv("latencia_nodo_gateway.csv", ["latencia_ms"], [[round(x, 3)] for x in lat])

    # --- alerta: del cambio de estado en el nodo a su llegada a la nube ---
    cambio = [(t, json.loads(x)) for t, tp, x in env.local_msgs if tp == "sabana/nodo01/alerta" and x]
    roja_local = next((t for t, c in cambio if c["alerta"] == "ROJA"), None)
    roja_nube = next((t for t, c in env.nube_msgs if c.get("alerta", {}).get("value") == 2
                      and "silenciada" in c), None)
    assert nodo.alerta == sim.ROJA and nodo.suena(), "el escenario critico debe dejar la alarma sonando"

    # --- comando: tablero global -> nube -> Gateway -> broker local -> nodo -> ack ---
    cmd = cliente("tablero-global")
    cmd.connect("127.0.0.1", P_NUBE)
    cmd.loop_start()
    t_cmd = "/v1.6/devices/%s/silenciar" % DISPOSITIVO
    pasos, viajes = [], []

    def enviar(valor, edad_s=0.0):
        ms = int((time.time() - edad_s) * 1000)
        cmd.publish(t_cmd, json.dumps({"value": valor, "timestamp": ms, "context": {}}),
                    qos=1).wait_for_publish(3)

    def esperar_ack(n_previos, seg=3.0):
        fin = time.time() + seg
        while time.time() < fin:
            if len(nodo.acks) > n_previos:
                return True
            time.sleep(0.002)
        return False

    def paso(desc, esperado, obtenido):
        pasos.append({"paso": desc, "esperado": esperado, "obtenido": obtenido, "pasa": esperado == obtenido})

    def estado():
        return "silenciada" if not nodo.suena() else "sonando"

    paso("Estado inicial: nivel bajo el umbral rojo", "sonando", estado())
    for _ in range(20):                                   # 20 idas y vueltas para medir
        n0 = len(nodo.acks); t0 = time.time(); enviar(1.0)
        assert esperar_ack(n0); viajes.append((time.time() - t0) * 1000.0)
        n0 = len(nodo.acks); enviar(0.0); assert esperar_ack(n0)
        time.sleep(0.02)
    n0 = len(nodo.acks); enviar(1.0); esperar_ack(n0)
    paso("Orden 'silenciar' desde el tablero global", "silenciada", estado())
    paso("El LED/estado sigue en ROJA (silenciar no oculta la condición)", "ROJA", sim.NOMBRE[nodo.alerta])
    n0 = len(nodo.acks); enviar(0.0); esperar_ack(n0)
    paso("Orden 'reactivar' desde el tablero global", "sonando", estado())
    n0 = len(nodo.acks); enviar(1.0, edad_s=600); llego = esperar_ack(n0, 1.0)
    paso("Orden con 10 min de antigüedad (reenviada al reconectar)", "sonando", estado())
    paso("  ...el Gateway la descarta y no llega al nodo", "no llega", "llega" if llego else "no llega")
    n0 = len(nodo.acks); enviar(5.0); llego = esperar_ack(n0, 1.0)
    paso("Orden con valor inválido (5)", "no llega", "llega" if llego else "no llega")
    n0 = len(nodo.acks); enviar(1.0); esperar_ack(n0)
    paso("Silenciar de nuevo", "silenciada", estado())
    # Salvaguarda ISA-18.2: el silencio expira solo a los 15 min
    nodo.correr(ritmo=400, horas=14.0 / 60.0)
    paso("14 min después: el silencio sigue vigente", "silenciada", estado())
    nodo.correr(ritmo=400, horas=1.5 / 60.0)
    paso("15,5 min después: el silencio expiró solo", "sonando", estado())
    time.sleep(0.8)
    nodo.cerrar()
    env.esperar_salida_vacia()
    env.detener_gateway()
    cmd.loop_stop()
    ev = dict(env.sql("SELECT tipo, COUNT(*) FROM eventos GROUP BY tipo"))
    sil_nube = [c["silenciada"]["value"] for _, c in env.nube_msgs if "silenciada" in c]
    R["pruebas"]["S4_latencia"] = {
        "nota": "Todo corre en una sola máquina (interfaz de lazo local). Mide el costo del software, "
                "no el de la red Wi-Fi ni el de Internet: es una cota inferior.",
        "nodo_a_sqlite_ms": resumen(lat),
        "alerta_nodo_a_nube_ms": None if not (roja_local and roja_nube) else round((roja_nube - roja_local) * 1000.0, 1),
        "comando_tablero_global_a_ack_ms": resumen(viajes)}
    R["pruebas"]["S6_comando"] = {
        "pasos": pasos, "pasan": sum(p["pasa"] for p in pasos), "total": len(pasos),
        "eventos_registrados": ev, "confirmaciones_de_silencio_en_nube": len(sil_nube)}


# ======================================================================
#  S5 · Caída de Internet: almacenamiento y reenvío
# ======================================================================
def prueba_reenvio(env):
    env.iniciar_gateway()
    nodo = env.nodo("estable")
    fases = []

    def foto(fase):
        fases.append({"fase": fase,
                      "lecturas_en_sqlite": env.sql("SELECT COUNT(*) FROM lecturas")[0][0],
                      "agregados_en_sqlite": env.sql("SELECT COUNT(*) FROM agregados")[0][0],
                      "pendientes_de_envio": env.sql("SELECT COUNT(*) FROM salida WHERE enviado=0")[0][0],
                      "agregados_en_nube": len(puntos_nube(env, "nivel"))})

    n = nodo.correr(RITMO, horas=4)
    env.esperar_lecturas(n); env.esperar_salida_vacia(); foto("1. Con Internet (4 h)")
    env.proxy.cortar()
    time.sleep(1.0)
    n += nodo.correr(RITMO, horas=8)
    env.esperar_lecturas(n); time.sleep(1.5); foto("2. Sin Internet (8 h más)")
    en_nube_durante = len(puntos_nube(env, "nivel"))
    t_vuelve = time.time()
    env.proxy.restablecer()
    env.esperar_salida_vacia(60)
    t_vacia = time.time() - t_vuelve
    foto("3. Internet restablecido")
    n += nodo.correr(RITMO, horas=2)
    env.esperar_lecturas(n); nodo.cerrar(); env.esperar_salida_vacia(); foto("4. Operación normal (2 h más)")
    env.detener_gateway()
    pts = puntos_nube(env, "nivel")
    ts_nube = [p[1] for p in pts]
    ts_bd = sorted(r[0] for r in env.sql("SELECT ts_fin FROM agregados WHERE nivel_med IS NOT NULL"))
    tras = [p[1] for p in pts if p[0] >= t_vuelve][:96]
    ev = [json.loads(r[0])["estado"] for r in env.sql("SELECT detalle FROM eventos WHERE tipo='NUBE' ORDER BY id")]
    guardar_csv("reenvio_fases.csv", list(fases[0].keys()), [list(f.values()) for f in fases])
    R["pruebas"]["S5_reenvio"] = {
        "fases": fases,
        "agregados_generados": len(ts_bd), "agregados_recibidos_en_nube": len(set(ts_nube)),
        "perdidos": len(set(ts_bd) - set(ts_nube)), "duplicados": len(ts_nube) - len(set(ts_nube)),
        "llegaron_a_la_nube_durante_el_corte": en_nube_durante - fases[0]["agregados_en_nube"],
        "orden_cronologico_al_reenviar": tras == sorted(tras),
        "segundos_hasta_ponerse_al_dia": round(t_vacia, 2),
        "nota_tiempo": "Incluye la espera de reconexión (reintentos de 1 a 30 s). En simulación no se aplica "
                       "el límite de 1 mensaje/s de la plataforma real (intervalo_s = 0): con el límite real, "
                       "vaciar N mensajes toma N segundos.",
        "eventos_nube_registrados": ev}


# ======================================================================
#  S7 · Caída del nodo (LWT)  y  S8 · Reinicio del Gateway
# ======================================================================
def prueba_caidas(env):
    env.iniciar_gateway()
    # El nodo corre como proceso aparte para poder "desenchufarlo": SIGSTOP
    # lo congela sin cerrar el socket, igual que un corte de energía. El
    # broker no recibe despedida ni cierre TCP: solo deja de oír el keepalive.
    proc = subprocess.Popen(
        [sys.executable, os.path.join(RAIZ, "simulador", "simulador.py"), "--escenario", "estable",
         "--host", "127.0.0.1", "--puerto", str(P_LOCAL), "--clave", CLAVES["nodo01"], "--ritmo", "100"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    env.esperar_lecturas(500, 30)
    proc.send_signal(signal.SIGSTOP)
    t0 = time.time()
    time.sleep(0.5)
    n1 = env.sql("SELECT COUNT(*) FROM lecturas")[0][0]
    env._esperar(lambda: any(json.loads(r[0])["estado"] == "offline" for r in
                             env.sql("SELECT detalle FROM eventos WHERE tipo='ESTADO_NODO'")),
                 40, "ultima voluntad del nodo")
    demora = time.time() - t0
    proc.kill()
    proc.wait()
    # el nodo vuelve: su consecutivo arranca de nuevo en 1
    nodo2 = env.nodo("estable", semilla=8)
    n2 = nodo2.correr(500, horas=400 * 2 / 3600.0)
    env.esperar_lecturas(n1 + n2)
    time.sleep(0.5)
    estados = [json.loads(r[0])["estado"] for r in
               env.sql("SELECT detalle FROM eventos WHERE tipo='ESTADO_NODO' ORDER BY id")]
    reinicios = env.sql("SELECT COUNT(*) FROM eventos WHERE tipo='REINICIO_NODO'")[0][0]
    R["pruebas"]["S7_caida_nodo"] = {
        "keepalive_s": 15, "segundos_hasta_detectar": round(demora, 1),
        "minimo_teorico_s": 22.5, "secuencia_de_estados": estados,
        "reinicios_de_nodo_detectados": reinicios,
        "en_linea_publicado_en_nube": [c["en_linea"]["value"] for _, c in env.nube_msgs if "en_linea" in c]}

    # ---- S8: el Gateway se apaga y vuelve mientras el nodo sigue publicando ----
    env.detener_gateway()
    env.iniciar_gateway()
    cuenta = {"n": 0}
    nodo3 = nodo2
    parar = threading.Event()

    def publicar():
        while not parar.is_set():
            nodo3.paso()
            cuenta["n"] += 1
            time.sleep(0.004)

    base = nodo3.publicados
    h = threading.Thread(target=publicar)
    h.start()
    env._esperar(lambda: env.sql("SELECT COUNT(*) FROM lecturas")[0][0] >= 1500, 30, "1500 lecturas")
    antes = env.sql("SELECT COUNT(*) FROM lecturas")[0][0]
    env.detener_gateway()                       # SIGTERM: apagado ordenado
    time.sleep(2.0)
    env.iniciar_gateway(nuevo=False)            # misma base de datos
    env._esperar(lambda: env.sql("SELECT COUNT(*) FROM lecturas")[0][0] >= antes + 1500, 30, "reanudacion")
    parar.set()
    h.join()
    publicados = cuenta["n"]
    env.esperar_lecturas(1, 5)
    time.sleep(1.0)
    # se fuerza el cierre de la última ventana con una muestra de la siguiente
    nodo3.t = (nodo3.t // 300 + 1) * 300 + 2
    nodo3.paso(); publicados += 1
    time.sleep(1.0)
    recibidos = env.sql("SELECT COUNT(*) FROM lecturas")[0][0]
    nodo3.cerrar()
    env.detener_gateway()
    n_agr, perd = env.sql("SELECT SUM(n), SUM(perdidas) FROM agregados")[0]
    hueco = env.sql("SELECT MAX(perdidas) FROM agregados")[0][0]
    gw = [json.loads(r[0])["estado"] for r in env.sql("SELECT detalle FROM eventos WHERE tipo='GATEWAY' ORDER BY id")]
    R["pruebas"]["S8_reinicio_gateway"] = {
        "publicados_por_el_nodo": publicados, "recibidos_en_sqlite": recibidos,
        "no_recibidos_por_el_apagado": publicados - recibidos,
        "perdidas_contadas_en_agregados": perd, "mayor_hueco_en_una_ventana": hueco,
        "el_hueco_quedo_contabilizado": perd == publicados - recibidos,
        "muestras_en_agregados": n_agr,
        "bitacora_gateway": gw,
        "nota": "La telemetría viaja con QoS 0: lo publicado mientras el Gateway está apagado no se "
                "recupera. El diseño no lo oculta: el hueco queda medido por el consecutivo."}


# ======================================================================
def main():
    os.makedirs(SALIDA, exist_ok=True)
    for b in (MOSQ, MOSQ_PASSWD):
        if not shutil.which(b):
            raise SystemExit("No se encuentra '%s'. Instale mosquitto o defina MOSQUITTO_BIN." % b)
    ver = subprocess.run([MOSQ, "-h"], capture_output=True, text=True).stdout.splitlines()[0]
    R["entorno"] = {
        "fecha_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "sistema": "%s %s (%s)" % (platform.system(), platform.release(), platform.machine()),
        "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
        "paho_mqtt": __import__("paho.mqtt").mqtt.__version__, "broker": ver,
        "ritmo_muestras_por_s": RITMO, "ventana_s": 300,
        "tipo_de_validacion": "SIMULACION: nodo ESP32 emulado y nube emulada. "
                              "Broker, Gateway, SQLite e IA son los reales.",
    }
    env = Entorno()
    try:
        for nombre, f in (("S2 control de acceso", prueba_acceso),
                          ("S3 validacion de mensajes", prueba_validacion),
                          ("S4/S6 latencia y comando", prueba_latencia_y_comando),
                          ("S5 caida de Internet", prueba_reenvio),
                          ("S7/S8 caidas de nodo y Gateway", prueba_caidas),
                          ("S1/S9 escenarios e IA", prueba_escenarios)):
            print("== %s" % nombre, flush=True)
            f(env)
    finally:
        env.cerrar()
    with open(os.path.join(SALIDA, "resultados.json"), "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=2)
    print("\nResultados en %s" % SALIDA)


if __name__ == "__main__":
    main()
