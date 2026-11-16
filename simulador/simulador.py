#!/usr/bin/env python3
"""
simulador.py · Nodo ESP32 emulado para validar el ecosistema sin hardware.

Publica por MQTT exactamente los mismos tópicos y el mismo JSON que el
firmware `EstacionHidroMQTT`, y atiende los mismos comandos. Para el
Gateway es indistinguible de un nodo real, con una sola diferencia
declarada: agrega el campo "ts" (tiempo simulado) para poder comprimir
días de operación en minutos.

QUÉ ES REAL Y QUÉ ES MODELO
  Real   : el protocolo (tópicos, cargas, QoS, retención, LWT), la
           lógica de alarma (portada línea a línea de evaluarAlerta() y
           actualizarAlarma() del firmware) y la fórmula de evaporación
           (portada de evapPenman()).
  Modelo : la física del tanque y el clima. No son mediciones.
           - Clima: ciclo diario sinusoidal con valores típicos de la
             Sabana de Bogotá.
           - Tanque: el nivel baja por evaporación y, si el escenario
             lo indica, por una fuga de caudal constante.
           - Ruido del sensor: gaussiano. La desviación por defecto
             (0,02 cm) es la dispersión medida por el equipo en
             operación (datos/repetibilidad-operacion.csv).

Uso:
  python3 simulador.py --escenario fuga --ritmo 2000
  python3 simulador.py --escenario estable --tiempo-real      # 1 muestra cada 2 s
"""

import argparse
import json
import math
import random
import sys
import threading
import time

import paho.mqtt.client as mqtt

# ----------------------------------------------------------------------
#  Constantes copiadas de firmware/EstacionHidroMQTT/config.h
# ----------------------------------------------------------------------
PERIODO_S = 2.0
UMBRAL_AMARILLO_CM = 12.0
UMBRAL_ROJO_CM = 8.0
TASA_DESCENSO_MMH = 2.0
UMBRAL_EVAP_MMDIA = 5.0
MUESTRAS_PERSISTENCIA = 3
HISTERESIS_PCT = 0.05
TIEMPO_DESACTIVA_S = 600
SILENCIO_MAX_S = 900
NIVEL_MIN_CM, NIVEL_MAX_CM = 0.0, 35.0
NIVEL_TASA_MAX = 3.0
RA_MJ_M2_DIA, VIENTO_U2, ALBEDO = 38.0, 2.0, 0.23
GASTO_A, GASTO_B, GASTO_H0 = 0.045, 1.50, 2.0
Q_NIVEL_RANGO, Q_NIVEL_TASA, Q_NIVEL_TIMEOUT = 0x01, 0x02, 0x04
Q_SOL_FUERTE, Q_INCOHERENTE = 0x40, 0x80
VERDE, AMARILLA, ROJA, FALLA = 0, 1, 2, 3
NOMBRE = ("VERDE", "AMARILLA", "ROJA", "FALLA")

# Inicio fijo del tiempo simulado: 2026-11-10 00:00 hora de Colombia (UTC-5)
T0_SIM = 1794286800.0

ESCENARIOS = {
    # nombre: horas, nivel inicial, factor de evaporación real, fuga (mm/h, hora de inicio)
    "estable": dict(horas=24, nivel0=20.0, k_evap=0.0, fuga_mmh=0.0, fuga_h=0,
                    nota="Aporte y pérdidas en equilibrio: el nivel no cambia."),
    "sequia":  dict(horas=72, nivel0=12.6, k_evap=1.0, fuga_mmh=0.0, fuga_h=0,
                    nota="Sin aporte. El nivel baja solo por evaporación."),
    "fuga":    dict(horas=30, nivel0=16.0, k_evap=1.0, fuga_mmh=3.0, fuga_h=8,
                    nota="Evaporación + fuga de 3 mm/h que empieza a la hora 8."),
    "falla":   dict(horas=2, nivel0=20.0, k_evap=0.0, fuga_mmh=0.0, fuga_h=0,
                    falla_ini_h=1.0, falla_min=10,
                    nota="El ultrasónico pierde el eco durante 10 min."),
    "critico": dict(horas=1, nivel0=7.0, k_evap=0.0, fuga_mmh=0.0, fuga_h=0,
                    nota="Nivel bajo el umbral rojo: alarma sonora activa."),
}


# ----------------------------------------------------------------------
#  Modelo del clima (NO es una medición)
# ----------------------------------------------------------------------
def clima(t_sim, rng):
    """Devuelve (temp °C, hr %, presión hPa, luz %) para un instante."""
    hora = ((t_sim - T0_SIM) / 3600.0) % 24.0
    # Sol entre las 6 y las 18 h; máximo al mediodía.
    sol = max(0.0, math.sin(math.pi * (hora - 6.0) / 12.0))
    # Temperatura: mínima ~8 °C a las 5 h, máxima ~20 °C a las 14 h.
    temp = 14.0 + 6.0 * math.sin(2 * math.pi * (hora - 8.0) / 24.0) + rng.gauss(0, 0.05)
    hr = 85.0 - 32.0 * sol + rng.gauss(0, 0.3)
    presion = 752.0 + 1.2 * math.sin(2 * math.pi * hora / 12.0) + rng.gauss(0, 0.05)
    luz = min(100.0, max(0.0, 82.0 * sol + rng.gauss(0, 1.0))) if sol > 0 else 0.0
    return temp, min(100.0, max(0.0, hr)), presion, luz


# ----------------------------------------------------------------------
#  Portado del firmware
# ----------------------------------------------------------------------
def evap_penman(T, HR, presion_hpa, luz_pct):
    """Copia de evapPenman() + radiacionMJ() del firmware."""
    P = presion_hpa / 10.0
    es = 0.6108 * math.exp(17.27 * T / (T + 237.3))
    ea = es * HR / 100.0
    delta = 4098.0 * es / (T + 237.3) ** 2
    gamma = 0.000665 * P
    nN = min(1.0, max(0.0, luz_pct / 100.0))
    Rs = RA_MJ_M2_DIA * (0.25 + 0.50 * nN)
    Rn = (1.0 - ALBEDO) * Rs * 0.75
    num = 0.408 * delta * Rn + gamma * (900.0 / (T + 273.0)) * VIENTO_U2 * (es - ea)
    den = delta + gamma * (1.0 + 0.34 * VIENTO_U2)
    return max(0.0, num / den)


def caudal_lmin(nivel):
    return 0.0 if nivel <= GASTO_H0 else GASTO_A * (nivel - GASTO_H0) ** GASTO_B


def bandera_primaria(cal):
    if cal & Q_NIVEL_TIMEOUT:
        return 9
    if cal & Q_NIVEL_RANGO:
        return 4
    if cal & (Q_NIVEL_TASA | Q_INCOHERENTE | Q_SOL_FUERTE):
        return 3
    return 1


class NodoSimulado:
    def __init__(self, escenario, host, puerto, usuario, clave, nodo="nodo01",
                 base="sabana", semilla=7, ruido_cm=0.02, verdad=None):
        self.e = dict(ESCENARIOS[escenario])
        self.nombre_escenario = escenario
        self.nodo, self.base = nodo, base
        self.rng = random.Random(semilla)
        self.ruido_cm = ruido_cm
        self.t = T0_SIM
        self.nivel_real = self.e["nivel0"]
        self.seq = 0
        # --- estado de la alarma (mismas variables que el firmware) ---
        self.alerta = VERDE
        self.persistencia = 0
        self.t_desactiva = None
        self.silenciada = False
        self.t_silencio = 0.0
        self.alerta_silenciada = VERDE
        self.nivel_previo = None
        self._pub_alerta = None
        self.transiciones = 0
        self.publicados = 0
        self.acks = []
        self.verdad = verdad            # archivo CSV con la verdad del modelo
        self._candado = threading.Lock()

        self.t_ = lambda sufijo: "%s/%s/%s" % (self.base, self.nodo, sufijo)
        try:
            self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=nodo)
        except AttributeError:
            self.c = mqtt.Client(client_id=nodo)
        self.c.username_pw_set(usuario, clave)
        self.c.will_set(self.t_("estado"), "offline", qos=1, retain=True)
        self.c.on_message = self._al_comando
        self.c.on_connect = self._al_conectar
        self._listo = threading.Event()
        self.c.connect(host, puerto, keepalive=15)
        self.c.loop_start()
        if not self._listo.wait(10):
            raise SystemExit("El broker no acepto la conexion del nodo simulado")

    def _al_conectar(self, c, d, f, rc, *resto):
        if getattr(rc, "value", rc) == 0:
            c.publish(self.t_("estado"), "online", qos=1, retain=True)
            c.subscribe(self.t_("cmd/#"), qos=1)
            self._listo.set()

    # ----- comando: mismo comportamiento que alLlegarComando() -----
    def _al_comando(self, c, d, msg):
        txt = msg.payload.decode("utf-8", "replace")[:15]
        accion = msg.topic.rsplit("/", 1)[-1]
        with self._candado:
            if accion == "silenciar":
                if txt[:1] not in ("0", "1"):
                    ack = {"cmd": "silenciar", "ok": 0, "error": "carga invalida"}
                else:
                    if txt[0] == "0":
                        self.silenciada = False
                    else:
                        self.silenciada = True
                        self.t_silencio = self.t
                        self.alerta_silenciada = self.alerta
                    restante = int(SILENCIO_MAX_S - (self.t - self.t_silencio)) if self.silenciada else 0
                    ack = {"cmd": "silenciar", "ok": 1, "silenciada": int(self.silenciada),
                           "restanteSeg": restante, "alerta": NOMBRE[self.alerta]}
            else:
                ack = {"cmd": accion[:20], "ok": 0, "error": "comando desconocido"}
        self.acks.append(ack)
        c.publish(self.t_("ack"), json.dumps(ack, separators=(",", ":")))

    # ----- copia de evaluarAlerta() -----
    def _evaluar(self, nivel, evap, qc):
        if qc in (4, 9):
            return FALLA
        i1a, i1r = nivel < UMBRAL_AMARILLO_CM, nivel < UMBRAL_ROJO_CM
        tasa = 0.0
        if self.nivel_previo is not None:
            tasa = (self.nivel_previo - nivel) * 10.0 * 3600.0 / PERIODO_S
        i2 = tasa > TASA_DESCENSO_MMH
        i3 = evap > UMBRAL_EVAP_MMDIA
        conc = int(i1a) + int(i2) + int(i3)
        self.persistencia = min(255, self.persistencia + 1) if conc >= 2 else 0
        condicion = self.persistencia >= MUESTRAS_PERSISTENCIA
        if self.alerta not in (VERDE, FALLA):
            if nivel > UMBRAL_AMARILLO_CM * (1.0 + HISTERESIS_PCT):
                if self.t_desactiva is None:
                    self.t_desactiva = self.t
                if self.t - self.t_desactiva > TIEMPO_DESACTIVA_S:
                    self.t_desactiva = None
                    return VERDE
            else:
                self.t_desactiva = None
            return ROJA if i1r else self.alerta
        if condicion:
            return ROJA if i1r else AMARILLA
        return VERDE

    def suena(self):
        """Estado del zumbador, como en actualizarAlarma()."""
        return self.alerta == ROJA and not self.silenciada

    def paso(self):
        """Una muestra: avanza la física, mide, decide y publica."""
        e = self.e
        horas = (self.t - T0_SIM) / 3600.0
        temp, hr, presion, luz = clima(self.t, self.rng)
        evap = evap_penman(temp, hr, presion, luz)

        # --- física del tanque (modelo) ---
        perdida_mmh = e["k_evap"] * evap / 24.0
        fuga = e["fuga_mmh"] if (e["fuga_mmh"] and horas >= e["fuga_h"]) else 0.0
        self.nivel_real = max(0.0, self.nivel_real - (perdida_mmh + fuga) / 10.0 * PERIODO_S / 3600.0)

        # --- medición ---
        cal = 0
        en_falla = ("falla_ini_h" in e and
                    e["falla_ini_h"] <= horas < e["falla_ini_h"] + e["falla_min"] / 60.0)
        if en_falla:
            nivel, cal = None, Q_NIVEL_TIMEOUT
        else:
            nivel = round(self.nivel_real + self.rng.gauss(0, self.ruido_cm), 2)
            if not NIVEL_MIN_CM <= nivel <= NIVEL_MAX_CM:
                cal |= Q_NIVEL_RANGO
            if self.nivel_previo is not None:
                if abs(nivel - self.nivel_previo) > NIVEL_TASA_MAX:
                    cal |= Q_NIVEL_TASA
                obs = (self.nivel_previo - nivel) * 10.0
                esp = (evap / 24.0) * (PERIODO_S / 3600.0)
                if obs > 0.5 and obs > esp * 1.3 + 0.5:
                    cal |= Q_INCOHERENTE
        if luz > 85.0:
            cal |= Q_SOL_FUERTE
        qc = bandera_primaria(cal)

        with self._candado:
            previa = self.alerta
            self.alerta = self._evaluar(nivel if nivel is not None else float("nan"), evap, qc)
            self.nivel_previo = nivel
            if previa == VERDE and self.alerta != VERDE:
                self.transiciones += 1
            # expiración del silencio, como en actualizarAlarma()
            if self.silenciada and (self.t - self.t_silencio > SILENCIO_MAX_S
                                    or self.alerta == VERDE
                                    or self.alerta > self.alerta_silenciada):
                self.silenciada = False
            alerta, sil = self.alerta, int(self.silenciada)

        self.seq += 1
        carga = {
            "id": self.nodo, "seq": self.seq, "up": int(self.t - T0_SIM),
            "nivel": nivel, "temp": round(temp, 1), "hr": round(hr, 1),
            "presion": round(presion, 1), "luz": round(luz, 1),
            "caudal": None if nivel is None else round(caudal_lmin(nivel), 3),
            "evap": round(evap, 2), "qc": qc, "cal": cal,
            "alerta": NOMBRE[alerta], "sil": sil, "bmp": 1,
            "uA": UMBRAL_AMARILLO_CM, "uR": UMBRAL_ROJO_CM,
            "ts": round(self.t, 3),                 # solo el simulador lo envía
        }
        self.c.publish(self.t_("telemetria"), json.dumps(carga, separators=(",", ":")), qos=0)
        self.publicados += 1
        if (alerta, sil) != self._pub_alerta:
            self._pub_alerta = (alerta, sil)
            self.c.publish(self.t_("alerta"), json.dumps(
                {"id": self.nodo, "alerta": NOMBRE[alerta], "codigo": alerta, "sil": sil,
                 "up": int(self.t - T0_SIM)}, separators=(",", ":")), qos=0, retain=True)
        if self.verdad is not None:
            self.verdad.write("%.0f,%.5f,%s,%.3f,%.3f,%s,%d\n" % (
                self.t, self.nivel_real, "" if nivel is None else "%.2f" % nivel,
                evap, fuga, NOMBRE[alerta], qc))
        self.t += PERIODO_S
        return carga

    def correr(self, ritmo=2000.0, tiempo_real=False, horas=None, al_paso=None):
        """ritmo = muestras por segundo de reloj (ignorado en tiempo real)."""
        total = int(round((horas if horas is not None else self.e["horas"]) * 3600 / PERIODO_S))
        espera = PERIODO_S if tiempo_real else 1.0 / ritmo
        siguiente = time.perf_counter()
        for i in range(total):
            carga = self.paso()
            if al_paso:
                al_paso(i, carga)
            siguiente += espera
            falta = siguiente - time.perf_counter()
            if falta > 0:
                time.sleep(falta)
        return total

    def cerrar(self, abrupto=False):
        if abrupto:
            # Corta el socket sin enviar DISCONNECT: el broker debe
            # publicar la última voluntad ("offline").
            self.c.loop_stop()
            self.c.socket().close()
        else:
            time.sleep(0.3)
            self.c.publish(self.t_("estado"), "offline", qos=1, retain=True).wait_for_publish(5)
            self.c.loop_stop()
            self.c.disconnect()


def main():
    ap = argparse.ArgumentParser(description="Nodo ESP32 emulado · Challenge #3")
    ap.add_argument("--escenario", choices=sorted(ESCENARIOS), default="estable")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--puerto", type=int, default=1883)
    ap.add_argument("--usuario", default="nodo01")
    ap.add_argument("--clave", default="cambiaesto-nodo")
    ap.add_argument("--ritmo", type=float, default=2000.0, help="muestras por segundo de reloj")
    ap.add_argument("--tiempo-real", action="store_true", help="una muestra cada 2 s")
    ap.add_argument("--ruido", type=float, default=0.02, help="desviacion del nivel, cm")
    ap.add_argument("--semilla", type=int, default=7)
    ap.add_argument("--verdad", help="CSV donde guardar el estado real del modelo")
    a = ap.parse_args()
    f = open(a.verdad, "w", encoding="utf-8") if a.verdad else None
    if f:
        f.write("ts,nivel_real_cm,nivel_medido_cm,evap_mm_dia,fuga_mm_h,alerta,qc\n")
    nodo = NodoSimulado(a.escenario, a.host, a.puerto, a.usuario, a.clave,
                        semilla=a.semilla, ruido_cm=a.ruido, verdad=f)
    print("Escenario '%s': %s" % (a.escenario, ESCENARIOS[a.escenario]["nota"]))
    try:
        n = nodo.correr(a.ritmo, a.tiempo_real)
        print("Publicadas %d muestras, %d transiciones de alarma" % (n, nodo.transiciones))
    except KeyboardInterrupt:
        pass
    finally:
        nodo.cerrar()
        if f:
            f.close()


if __name__ == "__main__":
    sys.exit(main())
