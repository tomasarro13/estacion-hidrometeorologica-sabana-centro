"""
almacen.py · Capa de persistencia del Gateway (SQLite).

Única puerta de entrada a la base de datos. El resto del Gateway no
escribe SQL: llama a los métodos de la clase Almacen. Eso permite que
varios hilos (recepción MQTT, puente a la nube, módulo de IA) compartan
la base sin pisarse: todas las operaciones pasan por un mismo candado.
"""

import json
import os
import sqlite3
import threading
import time

RUTA_ESQUEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "esquema.sql")

CAMPOS_LECTURA = ("nodo", "ts", "ts_rx", "seq", "up", "nivel", "temp", "hr",
                  "presion", "luz", "caudal", "evap", "qc", "cal", "alerta", "sil")

CAMPOS_AGREGADO = ("nodo", "ts_ini", "ts_fin", "n", "n_validas", "perdidas",
                   "nivel_med", "nivel_min", "nivel_max", "nivel_std",
                   "temp_med", "hr_med", "presion_med", "luz_med",
                   "caudal_med", "evap_med", "tasa_mmh", "qc_peor", "alerta")


class Almacen:
    """Base de datos local del Gateway."""

    def __init__(self, ruta):
        self.ruta = ruta
        self._candado = threading.RLock()
        carpeta = os.path.dirname(os.path.abspath(ruta))
        os.makedirs(carpeta, exist_ok=True)
        # check_same_thread=False: la conexión se comparte entre hilos,
        # pero SIEMPRE bajo self._candado.
        self._bd = sqlite3.connect(ruta, check_same_thread=False, timeout=10)
        self._bd.row_factory = sqlite3.Row
        with open(RUTA_ESQUEMA, encoding="utf-8") as f:
            self._bd.executescript(f.read())
        self._bd.commit()
        self._pendientes = 0            # inserciones sin confirmar
        self._t_commit = time.time()

    # ------------------------------------------------------------ lecturas
    def guardar_lectura(self, lectura):
        """Inserta una lectura cruda. Confirma en disco por lotes: a 0,5
        mensajes/s un commit por fila sería desgaste gratuito de la SD.
        El hilo principal del Gateway confirma cada 2 s: ante un apagón
        se arriesgan, como máximo, 2 s de dato crudo."""
        valores = [lectura.get(c) for c in CAMPOS_LECTURA]
        with self._candado:
            self._bd.execute(
                "INSERT INTO lecturas (%s) VALUES (%s)"
                % (",".join(CAMPOS_LECTURA), ",".join("?" * len(CAMPOS_LECTURA))),
                valores)
            self._pendientes += 1
            if self._pendientes >= 50 or time.time() - self._t_commit > 5:
                self._confirmar()

    def _confirmar(self):
        self._bd.commit()
        self._pendientes = 0
        self._t_commit = time.time()

    def confirmar(self):
        with self._candado:
            self._confirmar()

    def ultimos_consecutivos(self):
        """Último seq guardado de cada nodo: {nodo: seq}."""
        with self._candado:
            filas = self._bd.execute(
                "SELECT nodo, seq FROM lecturas WHERE id IN "
                "(SELECT MAX(id) FROM lecturas GROUP BY nodo)").fetchall()
        return {f[0]: f[1] for f in filas}

    def lecturas_ventana_abierta(self, ventana_s):
        """Lecturas de la última ventana de cada nodo que todavía no tiene
        agregado: es la que estaba en curso cuando el Gateway se apagó."""
        out = []
        with self._candado:
            nodos = self._bd.execute(
                "SELECT nodo, MAX(ts) FROM lecturas GROUP BY nodo").fetchall()
            for nodo, ts_max in nodos:
                ini = (ts_max // ventana_s) * ventana_s
                ya = self._bd.execute(
                    "SELECT 1 FROM agregados WHERE nodo = ? AND ts_ini = ?",
                    (nodo, ini)).fetchone()
                if ya:
                    continue
                filas = self._bd.execute(
                    "SELECT %s FROM lecturas WHERE nodo = ? AND ts >= ? ORDER BY id"
                    % ",".join(CAMPOS_LECTURA), (nodo, ini)).fetchall()
                out.extend(dict(f) for f in filas)
        return out

    def contar(self, tabla, donde="1=1", args=()):
        assert tabla in ("lecturas", "agregados", "eventos", "salida", "predicciones")
        with self._candado:
            return self._bd.execute(
                "SELECT COUNT(*) FROM %s WHERE %s" % (tabla, donde), args).fetchone()[0]

    # ----------------------------------------------------------- agregados
    def guardar_agregado(self, agregado):
        valores = [agregado.get(c) for c in CAMPOS_AGREGADO]
        with self._candado:
            self._bd.execute(
                "INSERT OR REPLACE INTO agregados (%s) VALUES (%s)"
                % (",".join(CAMPOS_AGREGADO), ",".join("?" * len(CAMPOS_AGREGADO))),
                valores)
            self._confirmar()

    def serie_agregados(self, nodo, desde_ts, hasta_ts=None):
        """Serie (ts, nivel, evap) que consume el módulo de IA. Solo
        ventanas con nivel válido: la IA no ve datos que QARTOD rechazó."""
        hasta_ts = hasta_ts if hasta_ts is not None else 1e18
        with self._candado:
            filas = self._bd.execute(
                "SELECT ts_fin, nivel_med, evap_med FROM agregados "
                "WHERE nodo = ? AND ts_fin > ? AND ts_fin <= ? "
                "AND nivel_med IS NOT NULL ORDER BY ts_fin",
                (nodo, desde_ts, hasta_ts)).fetchall()
        return [(f[0], f[1], f[2]) for f in filas]

    # ------------------------------------------------------------- eventos
    def registrar_evento(self, tipo, detalle, nodo=None, ts=None):
        if not isinstance(detalle, str):
            detalle = json.dumps(detalle, ensure_ascii=False)
        with self._candado:
            self._bd.execute(
                "INSERT INTO eventos (ts, nodo, tipo, detalle) VALUES (?,?,?,?)",
                (ts if ts is not None else time.time(), nodo, tipo, detalle))
            self._confirmar()

    def eventos(self, tipo=None):
        with self._candado:
            if tipo:
                filas = self._bd.execute(
                    "SELECT * FROM eventos WHERE tipo = ? ORDER BY id", (tipo,)).fetchall()
            else:
                filas = self._bd.execute("SELECT * FROM eventos ORDER BY id").fetchall()
        return [dict(f) for f in filas]

    # ------------------------------------------------ bandeja de salida
    def encolar_salida(self, topico, carga, prioridad=0):
        """Deja un mensaje listo para la nube. Se confirma de inmediato:
        desde este punto el dato sobrevive a un corte de red o de energía."""
        if not isinstance(carga, str):
            carga = json.dumps(carga, ensure_ascii=False, separators=(",", ":"))
        with self._candado:
            self._bd.execute(
                "INSERT INTO salida (ts_creado, topico, carga, prioridad) VALUES (?,?,?,?)",
                (time.time(), topico, carga, prioridad))
            self._confirmar()

    def siguiente_salida(self):
        """Mensaje pendiente más urgente: primero alertas, luego por orden
        de llegada (los datos salen en orden cronológico)."""
        with self._candado:
            f = self._bd.execute(
                "SELECT id, topico, carga, prioridad FROM salida "
                "WHERE enviado = 0 ORDER BY prioridad DESC, id LIMIT 1").fetchone()
        return dict(f) if f else None

    def marcar_enviado(self, id_salida):
        with self._candado:
            self._bd.execute(
                "UPDATE salida SET enviado = 1, ts_enviado = ? WHERE id = ?",
                (time.time(), id_salida))
            self._confirmar()

    def pendientes_salida(self):
        return self.contar("salida", "enviado = 0")

    # --------------------------------------------------------- predicciones
    def guardar_prediccion(self, nodo, ts, resultado, recomendacion, origen_texto):
        with self._candado:
            self._bd.execute(
                "INSERT INTO predicciones (ts, nodo, clasificacion, tasa_mmh, evap_mmh, "
                "exceso_mmh, horas_amarillo, horas_rojo, prioridad, recomendacion, "
                "origen_texto, detalle) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, nodo, resultado["clasificacion"], resultado.get("tasa_mmh"),
                 resultado.get("evap_mmh"), resultado.get("exceso_mmh"),
                 resultado.get("horas_amarillo"), resultado.get("horas_rojo"),
                 resultado.get("prioridad"), recomendacion, origen_texto,
                 json.dumps(resultado, ensure_ascii=False)))
            self._confirmar()

    # ------------------------------------------------------------ retención
    def depurar(self, dias_lecturas=7, dias_salida=3, ahora=None):
        """Política de retención. El dato crudo de 2 s se conserva una
        semana (unas 300 000 filas, ~35 MB); los agregados, los eventos y
        las predicciones no se borran: son el histórico de largo plazo."""
        ahora = ahora if ahora is not None else time.time()
        with self._candado:
            a = self._bd.execute("DELETE FROM lecturas WHERE ts < ?",
                                 (ahora - dias_lecturas * 86400,)).rowcount
            b = self._bd.execute("DELETE FROM salida WHERE enviado = 1 AND ts_enviado < ?",
                                 (ahora - dias_salida * 86400,)).rowcount
            self._confirmar()
        return a, b

    def consulta(self, sql, args=()):
        """Consulta de solo lectura para diagnóstico y pruebas."""
        with self._candado:
            return [dict(f) for f in self._bd.execute(sql, args).fetchall()]

    def cerrar(self):
        with self._candado:
            self._confirmar()
            self._bd.close()
