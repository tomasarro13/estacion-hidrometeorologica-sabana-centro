"""
prediccion.py · Módulo de IA, capa analítica (corre en el Gateway).

Responde tres preguntas que la autoridad necesita y que el nodo, por sí
solo, no puede contestar porque no guarda historia suficiente:

  1. PREDICCIÓN     ¿Cuándo llegará el nivel al umbral de alerta si la
                    tendencia actual se mantiene?
  2. ANOMALÍA       ¿El descenso se explica por la evaporación (sequía)
                    o hay una pérdida que el clima no justifica (fuga,
                    captación no autorizada)?
  3. PRIORIDAD      ¿Qué tan urgente es actuar?

Método
------
* Tendencia de corto plazo: estimador de Theil-Sen (mediana de las
  pendientes entre todos los pares de puntos). Se prefiere a los mínimos
  cuadrados porque tolera hasta ~29 % de valores atípicos sin desviarse,
  y la señal de un ultrasónico de bajo costo los tiene.
* Tendencia diaria: cambio neto entre la primera y la última hora de una
  ventana de 24 h ("ciclo completo"). La evaporación sigue al sol: de día
  el nivel baja rápido y de noche casi no baja. Sobre una curva así la
  mediana de pendientes depende de la hora a la que se mire; el cambio
  neto en un ciclo completo no. (La simulación mostró ese sesgo y motivó
  este segundo estimador: ver Wiki C3.8.)
* Incertidumbre: error estándar de la pendiente a partir de los residuos.
  Una tendencia solo se declara si supera dos errores estándar.
* Balance hídrico: el descenso observado se compara con la evaporación
  potencial FAO-56 que el propio nodo reporta. Es la misma idea del
  Test 9 de QARTOD que el firmware aplica entre dos muestras de 2 s,
  pero sobre horas de historia, donde el ruido ya no domina.

Sin dependencias externas: solo biblioteca estándar de Python, para que
corra en cualquier Raspberry Pi sin instalar nada.
"""

import math

# ----------------------------------------------------------------- parámetros
MIN_PUNTOS = 6             # menos de 6 ventanas no sostienen una tendencia
MIN_HORAS = 0.5            # ni menos de media hora de historia
T_SIGNIFICATIVO = 2.0      # |pendiente| > 2 errores estándar (~95 %)
TOLERANCIA_EVAP = 0.30     # incertidumbre de la evaporación estimada (igual
                           # que en el firmware: viento asumido, radiación proxy)
PISO_EXCESO_MMH = 0.30     # exceso mínimo para hablar de anomalía: por debajo,
                           # la resolución del sensor no permite distinguirlo
PISO_TASA_MMH = 0.02       # 0,5 mm/día: una tendencia menor no se declara
HORAS_CICLO = 23.0         # historia mínima para usar el estimador diario
HORIZONTE_MAX_H = 24 * 30  # no se proyecta más allá de 30 días

CLASES = ("DATOS_INSUFICIENTES", "ESTABLE", "RECUPERACION",
          "DESCENSO_CLIMATICO", "DESCENSO_NO_EXPLICADO")


def _mediana(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def theil_sen(ts, ys):
    """Pendiente e intercepto robustos. Devuelve (pendiente, intercepto)."""
    pendientes = []
    n = len(ts)
    for i in range(n - 1):
        for j in range(i + 1, n):
            dt = ts[j] - ts[i]
            if dt > 0:
                pendientes.append((ys[j] - ys[i]) / dt)
    b = _mediana(pendientes)
    a = _mediana([y - b * t for t, y in zip(ts, ys)])
    return b, a


def error_pendiente(ts, ys, b, a):
    """Error estándar de la pendiente a partir de los residuos."""
    n = len(ts)
    mt = sum(ts) / n
    sxx = sum((t - mt) ** 2 for t in ts)
    if n < 3 or sxx == 0:
        return float("inf")
    sse = sum((y - (a + b * t)) ** 2 for t, y in zip(ts, ys))
    return math.sqrt(sse / (n - 2) / sxx)


def pendiente_ciclo(ts, ys, bloque_h=1.0):
    """
    Cambio neto entre la primera y la última hora de la ventana.
    ts en horas. Devuelve (pendiente, intercepto, error_estandar) o None.
    Se usan medianas por bloque: un valor atípico no mueve el resultado.
    """
    ini = [(t, y) for t, y in zip(ts, ys) if t <= ts[0] + bloque_h]
    fin = [(t, y) for t, y in zip(ts, ys) if t >= ts[-1] - bloque_h]
    if len(ini) < 3 or len(fin) < 3:
        return None
    t1 = sum(t for t, _ in ini) / len(ini)
    t2 = sum(t for t, _ in fin) / len(fin)
    if t2 - t1 <= 0:
        return None
    y1, y2 = _mediana([y for _, y in ini]), _mediana([y for _, y in fin])
    b = (y2 - y1) / (t2 - t1)

    def var(bloque):
        m = sum(y for _, y in bloque) / len(bloque)
        return sum((y - m) ** 2 for _, y in bloque) / (len(bloque) - 1)

    se = math.sqrt(var(ini) / len(ini) + var(fin) / len(fin)) / (t2 - t1)
    return b, y2 - b * t2, se


def _horas_hasta(nivel_actual, umbral, tasa_cm_h):
    """Horas hasta cruzar el umbral bajando a tasa_cm_h (negativa)."""
    if nivel_actual <= umbral:
        return 0.0
    if tasa_cm_h >= 0:
        return None
    h = (nivel_actual - umbral) / (-tasa_cm_h)
    return h if h <= HORIZONTE_MAX_H else None


def _prioridad(horas_amarillo, horas_rojo, clase):
    if horas_rojo is not None and horas_rojo <= 6:
        return "CRITICA"
    if horas_amarillo is not None and horas_amarillo <= 6:
        return "ALTA"
    if clase == "DESCENSO_NO_EXPLICADO":
        return "ALTA"
    if horas_amarillo is not None and horas_amarillo <= 72:
        return "MEDIA"
    return "BAJA"


def analizar(serie, umbral_amarillo, umbral_rojo, metodo="theil_sen"):
    """
    serie: lista de tuplas (ts_segundos, nivel_cm, evap_mm_dia) ordenada.
    metodo: "theil_sen" o "ciclo" (este último exige ~24 h de historia; si
            no las hay, se usa Theil-Sen y el resultado se marca provisional).
    Devuelve un diccionario con la clasificación y sus cifras.
    """
    serie = [(t, n, e) for t, n, e in serie if n is not None]
    out = {
        "clasificacion": "DATOS_INSUFICIENTES",
        "n_puntos": len(serie),
        "umbral_amarillo": umbral_amarillo,
        "umbral_rojo": umbral_rojo,
        "tasa_mmh": None, "tasa_error_mmh": None,
        "evap_mmh": None, "exceso_mmh": None,
        "nivel_actual": None,
        "horas_amarillo": None, "horas_amarillo_min": None, "horas_amarillo_max": None,
        "horas_rojo": None,
        "prioridad": "BAJA",
        "horas_historia": 0.0,
        "metodo": "theil_sen",
    }
    if len(serie) < MIN_PUNTOS:
        return out
    t0 = serie[0][0]
    ts = [(t - t0) / 3600.0 for t, _, _ in serie]          # horas
    ys = [n for _, n, _ in serie]                          # cm
    out["horas_historia"] = round(ts[-1], 2)
    if ts[-1] < MIN_HORAS:
        return out

    ciclo = pendiente_ciclo(ts, ys) if (metodo == "ciclo" and ts[-1] >= HORAS_CICLO) else None
    if ciclo:
        b, a, se = ciclo                                   # cm/h
        out["metodo"] = "ciclo"
    else:
        b, a = theil_sen(ts, ys)                           # cm/h
        se = error_pendiente(ts, ys, b, a)
    nivel_actual = a + b * ts[-1]                          # nivel suavizado hoy
    evaps = [e for _, _, e in serie if e is not None]
    evap_mmh = (sum(evaps) / len(evaps) / 24.0) if evaps else 0.0

    tasa_mmh = b * 10.0
    se_mmh = se * 10.0
    descenso = -tasa_mmh                                   # positivo = baja
    exceso = descenso - evap_mmh

    out.update({
        "tasa_mmh": round(tasa_mmh, 3),
        "tasa_error_mmh": round(se_mmh, 3),
        "evap_mmh": round(evap_mmh, 3),
        "exceso_mmh": round(exceso, 3),
        "nivel_actual": round(nivel_actual, 2),
    })

    significativa = abs(b) > T_SIGNIFICATIVO * se and abs(tasa_mmh) >= PISO_TASA_MMH
    if not significativa:
        clase = "ESTABLE"
    elif b > 0:
        clase = "RECUPERACION"
    else:
        # ¿El clima alcanza a explicar lo que baja? Se concede a la
        # evaporación su 30 % de incertidumbre y a la pendiente la suya.
        limite = evap_mmh * (1.0 + TOLERANCIA_EVAP) + T_SIGNIFICATIVO * se_mmh
        if descenso > limite and exceso > PISO_EXCESO_MMH:
            clase = "DESCENSO_NO_EXPLICADO"
        else:
            clase = "DESCENSO_CLIMATICO"
    out["clasificacion"] = clase

    # Proyección al umbral, con banda por la incertidumbre de la pendiente
    if clase in ("DESCENSO_CLIMATICO", "DESCENSO_NO_EXPLICADO"):
        ha = _horas_hasta(nivel_actual, umbral_amarillo, b)
        hr = _horas_hasta(nivel_actual, umbral_rojo, b)
        out["horas_amarillo"] = None if ha is None else round(ha, 1)
        out["horas_rojo"] = None if hr is None else round(hr, 1)
        rapido = _horas_hasta(nivel_actual, umbral_amarillo, b - T_SIGNIFICATIVO * se)
        lento = _horas_hasta(nivel_actual, umbral_amarillo, b + T_SIGNIFICATIVO * se)
        out["horas_amarillo_min"] = None if rapido is None else round(rapido, 1)
        out["horas_amarillo_max"] = None if lento is None else round(lento, 1)
    else:
        if nivel_actual <= umbral_amarillo:
            out["horas_amarillo"] = 0.0
        if nivel_actual <= umbral_rojo:
            out["horas_rojo"] = 0.0

    out["prioridad"] = _prioridad(out["horas_amarillo"], out["horas_rojo"], clase)
    return out


def analizar_doble(serie, umbral_amarillo, umbral_rojo, ahora,
                   ventana_larga_h=24.0, ventana_corta_h=3.0):
    """
    Dos miradas sobre la misma serie:

      * ventana LARGA (24 h): promedia el ciclo día/noche de la
        evaporación. Es la que sirve para proyectar cuándo se llega
        al umbral.
      * ventana CORTA (3 h): reacciona rápido. Es la que detecta una
        fuga o una captación que acaba de empezar.

    El resultado es el de la ventana larga, salvo que la corta vea un
    descenso no explicado: en ese caso manda la corta, porque una fuga
    reciente todavía no pesa en el promedio de 24 h.
    """
    larga = [p for p in serie if p[0] > ahora - ventana_larga_h * 3600]
    corta = [p for p in serie if p[0] > ahora - ventana_corta_h * 3600]
    r_larga = analizar(larga, umbral_amarillo, umbral_rojo, metodo="ciclo")
    r_corta = analizar(corta, umbral_amarillo, umbral_rojo)

    if r_corta["clasificacion"] == "DESCENSO_NO_EXPLICADO":
        r = dict(r_corta)
        r["ventana_h"] = ventana_corta_h
    elif r_larga["clasificacion"] != "DATOS_INSUFICIENTES":
        r = dict(r_larga)
        r["ventana_h"] = ventana_larga_h
    else:
        r = dict(r_corta)
        r["ventana_h"] = ventana_corta_h
    # Durante el primer día no hay un ciclo completo: la proyección es provisional.
    r["provisional"] = r["metodo"] != "ciclo" and r["clasificacion"] == "DESCENSO_CLIMATICO"
    campos = ("clasificacion", "tasa_mmh", "exceso_mmh", "n_puntos", "metodo")
    r["corta"] = {k: r_corta[k] for k in campos}
    r["larga"] = {k: r_larga[k] for k in campos}
    return r
