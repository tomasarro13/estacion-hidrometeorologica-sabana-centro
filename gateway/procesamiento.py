"""
procesamiento.py · Procesamiento local del Gateway.

Dos responsabilidades, en este orden:

  1. validar()   Defensa en profundidad. El nodo ya validó sus lecturas
                 (QARTOD), pero el Gateway no confía en lo que llega por
                 la red: comprueba forma, tipos y rangos físicos antes de
                 aceptar un mensaje.

  2. Agregador   Reduce la telemetría de 2 s a una fila por ventana
                 (5 min por defecto): media, extremos, dispersión,
                 pérdidas y tendencia. Es lo que se envía a la nube.

Por qué se agrega aquí y no en la nube: la plataforma gratuita admite
4 000 datos al día. El nodo produce 43 200 mensajes diarios con 9
variables cada uno (388 800 datos). La ventana de 5 min los reduce a
288 x 10 = 2 880 datos/día sin perder la información que sirve para
decidir, y el dato crudo completo queda en SQLite.
"""

import math

ALERTAS = ("VERDE", "AMARILLA", "ROJA", "FALLA")
GRAVEDAD = {"VERDE": 0, "AMARILLA": 1, "ROJA": 2, "FALLA": 3}
QC_VALIDOS = (1, 2, 3, 4, 9)
QC_GRAVEDAD = {1: 0, 2: 1, 3: 2, 4: 3, 9: 4}

# Bits de la máscara de calidad del firmware (config.h)
Q_DHT_FALLA = 0x10
Q_BMP_FALLA = 0x20

# Rangos físicos. Deliberadamente más anchos que los del nodo: aquí no
# se juzga la calidad de la medición (eso lo hizo QARTOD en el ESP32),
# solo se rechaza lo que no puede ser un dato de este sistema.
RANGOS = {
    "nivel":   (-5.0, 500.0),     # cm
    "temp":    (-20.0, 70.0),     # °C
    "hr":      (0.0, 100.0),      # %
    "presion": (500.0, 1100.0),   # hPa
    "luz":     (0.0, 100.0),      # %
    "caudal":  (0.0, 1.0e5),      # L/min
    "evap":    (0.0, 30.0),       # mm/día
}
OBLIGATORIOS = ("id", "seq", "qc", "cal", "alerta")


def _es_numero(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validar(carga):
    """Devuelve (True, None) o (False, motivo)."""
    if not isinstance(carga, dict):
        return False, "la carga no es un objeto JSON"
    for campo in OBLIGATORIOS:
        if campo not in carga:
            return False, "falta el campo '%s'" % campo
    if not isinstance(carga["id"], str) or not carga["id"] or len(carga["id"]) > 32:
        return False, "id de nodo invalido"
    if not isinstance(carga["seq"], int) or isinstance(carga["seq"], bool) or carga["seq"] < 0:
        return False, "seq invalido"
    if carga["qc"] not in QC_VALIDOS:
        return False, "bandera qc fuera del esquema QARTOD"
    if not isinstance(carga["cal"], int) or not 0 <= carga["cal"] <= 255:
        return False, "mascara de calidad invalida"
    if carga["alerta"] not in ALERTAS:
        return False, "estado de alerta desconocido"
    for campo, (lo, hi) in RANGOS.items():
        v = carga.get(campo)
        if v is None:                      # ausente o null: lectura faltante
            continue
        if not _es_numero(v):
            return False, "'%s' no es numerico" % campo
        if not lo <= v <= hi:
            return False, "'%s' fuera de rango fisico (%s)" % (campo, v)
    if "ts" in carga and not _es_numero(carga["ts"]):
        return False, "ts invalido"
    return True, None


def _media(xs):
    return sum(xs) / len(xs) if xs else None


def _desv(xs):
    if len(xs) < 2:
        return None
    m = _media(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def pendiente(ts, ys):
    """Pendiente por mínimos cuadrados (unidades de y por segundo)."""
    n = len(ts)
    if n < 3:
        return None
    mt, my = _media(ts), _media(ys)
    sxx = sum((t - mt) ** 2 for t in ts)
    if sxx == 0:
        return None
    return sum((t - mt) * (y - my) for t, y in zip(ts, ys)) / sxx


class _Ventana:
    __slots__ = ("ts_ini", "muestras", "seq_ini", "seq_fin", "reinicios")

    def __init__(self, ts_ini):
        self.ts_ini = ts_ini
        self.muestras = []
        self.seq_ini = None
        self.seq_fin = None
        self.reinicios = 0


class Agregador:
    """Ventanas de tiempo fijo por nodo, guiadas por el tiempo del dato.

    La ventana se cierra cuando llega una muestra que ya pertenece a la
    siguiente, no cuando lo dice el reloj del Gateway. Así el resultado
    es el mismo si los datos llegan en vivo o si se reprocesan.
    """

    def __init__(self, ventana_s=300):
        self.ventana_s = ventana_s
        self._abiertas = {}          # nodo -> _Ventana
        self._ultimo_seq = {}        # nodo -> último seq visto

    def sembrar(self, ultimos):
        """Carga el último consecutivo conocido de cada nodo (dict)."""
        self._ultimo_seq.update(ultimos)

    def agregar(self, lectura):
        """Recibe una lectura ya validada. Devuelve (agregado_cerrado o
        None, reinicio_detectado)."""
        nodo, ts = lectura["nodo"], lectura["ts"]
        ini = math.floor(ts / self.ventana_s) * self.ventana_s
        cerrado = None
        v = self._abiertas.get(nodo)
        if v is not None and ini > v.ts_ini:
            cerrado = self._cerrar(nodo, v)
            v = None
        if v is None:
            v = _Ventana(ini)
            self._abiertas[nodo] = v

        # Un consecutivo que retrocede significa que el nodo se reinició.
        reinicio = False
        previo = self._ultimo_seq.get(nodo)
        if previo is not None and lectura["seq"] <= previo:
            reinicio = True
            v.reinicios += 1
            v.seq_ini = None             # reinicia la cuenta de pérdidas
        self._ultimo_seq[nodo] = lectura["seq"]

        if v.seq_ini is None:
            if not reinicio and previo is not None and not v.muestras:
                # Ventana nueva: se cuenta desde el consecutivo siguiente al
                # último visto, para no perder de vista un hueco que caiga
                # justo en el borde entre dos ventanas.
                v.seq_ini = previo + 1
            else:
                # Primer mensaje del nodo, o reinicio a mitad de ventana:
                # las muestras previas al reinicio ya quedaron contadas.
                v.seq_ini = lectura["seq"] - len(v.muestras)
        v.seq_fin = lectura["seq"]
        v.muestras.append(lectura)
        return cerrado, reinicio

    def cerrar_todo(self):
        """Cierra las ventanas abiertas (apagado ordenado del Gateway)."""
        out = [self._cerrar(n, v) for n, v in self._abiertas.items() if v.muestras]
        self._abiertas.clear()
        return out

    def _cerrar(self, nodo, v):
        ms = v.muestras
        n = len(ms)
        esperadas = (v.seq_fin - v.seq_ini + 1) if v.seq_ini is not None else n
        perdidas = max(0, esperadas - n)

        # Nivel: solo lecturas que QARTOD marcó como buenas (qc = 1).
        # "Dudoso se muestra, no se usa en cálculos."
        buenas = [m for m in ms if m["qc"] == 1 and m.get("nivel") is not None]
        niveles = [m["nivel"] for m in buenas]
        sin_dht = [m for m in ms if not m["cal"] & Q_DHT_FALLA]
        sin_bmp = [m for m in ms if not m["cal"] & Q_BMP_FALLA]

        def med(lista, campo):
            return _media([m[campo] for m in lista if m.get(campo) is not None])

        b = pendiente([m["ts"] for m in buenas], niveles)      # cm/s
        tasa = None if b is None else b * 10.0 * 3600.0        # mm/h

        def r(x, d=3):
            return None if x is None else round(x, d)

        return {
            "nodo": nodo,
            "ts_ini": v.ts_ini,
            "ts_fin": v.ts_ini + self.ventana_s,
            "n": n,
            "n_validas": len(buenas),
            "perdidas": perdidas,
            "nivel_med": r(_media(niveles)),
            "nivel_min": r(min(niveles)) if niveles else None,
            "nivel_max": r(max(niveles)) if niveles else None,
            "nivel_std": r(_desv(niveles)),
            "temp_med": r(med(sin_dht, "temp"), 2),
            "hr_med": r(med(sin_dht, "hr"), 2),
            "presion_med": r(med(sin_bmp, "presion"), 2),
            "luz_med": r(med(ms, "luz"), 2),
            "caudal_med": r(med(buenas, "caudal")),
            "evap_med": r(med(sin_dht, "evap")),
            "tasa_mmh": r(tasa, 2),
            "qc_peor": max((m["qc"] for m in ms), key=lambda q: QC_GRAVEDAD[q]),
            "alerta": max((m["alerta"] for m in ms), key=lambda a: GRAVEDAD[a]),
        }
