"""
recomendacion.py · Módulo de IA, capa de lenguaje (corre en el Gateway).

Convierte las cifras de prediccion.py en una recomendación que una
autoridad pueda leer y ejecutar. Tiene dos caminos:

  * GENERATIVO  Un modelo de lenguaje (API de Gemini) redacta la
                recomendación a partir de las cifras ya calculadas.
  * REGLAS      Plantillas fijas por caso. Es el respaldo: siempre
                responde, sin Internet y sin credenciales.

Reparto de responsabilidades (y por qué es el técnicamente pertinente):

  - El modelo generativo NO predice ni calcula. Los números los produce
    prediccion.py con un método estadístico auditable. A un modelo de
    lenguaje no se le confía aritmética sobre la que se va a racionar agua.
  - El modelo generativo SÍ redacta: traduce "tasa -3,1 mm/h, exceso
    2,9 mm/h, 5,2 h al umbral" a una instrucción para alguien que no
    es ingeniero, y la adapta al contexto.
  - Si la API no responde en 8 s, o no hay Internet, o no hay clave, se
    usa el texto por reglas. El sistema nunca se queda sin recomendación
    y el tablero indica cuál de los dos caminos la produjo.

La clave se lee de la variable de entorno GEMINI_API_KEY: no se escribe
en el código ni se sube al repositorio.
"""

import json
import os
import urllib.error
import urllib.request

MODELO_DEF = "gemini-2.5-flash"
URL_API = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"
TIEMPO_MAX_S = 8
LARGO_MAX = 380      # el texto viaja en el campo "context" de un dato en la nube


def _h(horas):
    """Horas en lenguaje natural."""
    if horas is None:
        return "un plazo que no se puede estimar"
    if horas <= 0:
        return "ya ocurrió"
    if horas < 1:
        return "menos de una hora"
    if horas < 48:
        return "unas %d horas" % round(horas)
    return "unos %d días" % round(horas / 24)


def por_reglas(r):
    """Recomendación determinista. Una plantilla por clasificación."""
    c = r["clasificacion"]
    ha, hr = r.get("horas_amarillo"), r.get("horas_rojo")
    if c == "DATOS_INSUFICIENTES":
        return ("Aún no hay historia suficiente para estimar una tendencia. "
                "Mantener la vigilancia con el valor actual y las alertas del nodo.")
    if c == "ESTABLE":
        if ha == 0:
            return ("El nivel está por debajo del umbral de alerta pero se mantiene estable. "
                    "Sostener las medidas de ahorro vigentes y revisar de nuevo en 6 horas.")
        return "Nivel estable, sin tendencia de descenso. No se requiere acción."
    if c == "RECUPERACION":
        return ("El nivel está subiendo (%.1f mm/h). Mantener las medidas vigentes "
                "hasta que supere el umbral de forma sostenida." % r["tasa_mmh"])
    descenso = -r["tasa_mmh"]
    if c == "DESCENSO_CLIMATICO":
        if ha == 0:
            cuando = "El nivel ya está bajo el umbral de alerta"
            if hr:
                cuando += " y llegaría al umbral crítico en %s" % _h(hr)
        else:
            cuando = "Al ritmo actual llegaría al umbral de alerta en %s" % _h(ha)
        nota = " (estimación provisional: aún no hay 24 h de historia)" if r.get("provisional") else ""
        return ("El nivel baja %.2f mm/h y la evaporación lo explica: es efecto del clima, "
                "no una pérdida. %s%s. Preparar medidas de ahorro y avisar a los usuarios "
                "del sistema con anticipación." % (descenso, cuando, nota))
    # DESCENSO_NO_EXPLICADO
    if ha == 0:
        cuando = "El nivel ya está bajo el umbral de alerta"
    else:
        cuando = "A este ritmo llegaría al umbral de alerta en %s" % _h(ha)
    return ("El nivel baja %.1f mm/h y la evaporación solo explica %.1f mm/h: hay una pérdida "
            "de %.1f mm/h que el clima no justifica. %s. Inspeccionar HOY el punto: "
            "fugas en la estructura, válvulas abiertas o captaciones no autorizadas."
            % (descenso, r["evap_mmh"], r["exceso_mmh"], cuando))


def construir_instruccion(r, lugar):
    """Texto que se envía al modelo generativo. Solo cifras ya calculadas."""
    cifras = {k: r.get(k) for k in (
        "clasificacion", "prioridad", "nivel_actual", "umbral_amarillo", "umbral_rojo",
        "tasa_mmh", "evap_mmh", "exceso_mmh", "horas_amarillo", "horas_amarillo_min",
        "horas_amarillo_max", "horas_rojo", "horas_historia")}
    return (
        "Eres el asistente de una estación de monitoreo de agua en %s, Cundinamarca, "
        "durante el Fenómeno de El Niño. Redacta UNA recomendación operativa para la "
        "autoridad local (acueducto veredal o alcaldía), en español claro, sin tecnicismos, "
        "en máximo 3 frases y 350 caracteres.\n\n"
        "Reglas estrictas:\n"
        "- Usa ÚNICAMENTE las cifras del análisis. No inventes ni recalcules números.\n"
        "- Niveles en cm; tasas en mm/h (negativa = el nivel baja); tiempos en horas.\n"
        "- DESCENSO_CLIMATICO: la evaporación explica el descenso (sequía). "
        "Recomienda ahorro y aviso anticipado.\n"
        "- DESCENSO_NO_EXPLICADO: el clima no explica el descenso. "
        "Recomienda inspección inmediata por fuga o captación no autorizada.\n"
        "- Di cuánto tiempo queda hasta el umbral de alerta si el dato existe.\n"
        "- No uses listas, encabezados ni saludos.\n\n"
        "Análisis:\n%s" % (lugar, json.dumps(cifras, ensure_ascii=False))
    )


def por_modelo(r, lugar, clave, modelo=MODELO_DEF):
    """Llama a la API de Gemini. Lanza excepción si algo falla."""
    cuerpo = {
        "contents": [{"parts": [{"text": construir_instruccion(r, lugar)}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 200},
    }
    pet = urllib.request.Request(
        URL_API % modelo,
        data=json.dumps(cuerpo).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": clave},
        method="POST")
    with urllib.request.urlopen(pet, timeout=TIEMPO_MAX_S) as resp:
        datos = json.loads(resp.read().decode("utf-8"))
    texto = datos["candidates"][0]["content"]["parts"][0]["text"].strip()
    if not texto:
        raise ValueError("respuesta vacia del modelo")
    return " ".join(texto.split())[:LARGO_MAX]


def recomendar(r, lugar="Sabana Centro", clave=None, modelo=None, usar_modelo=True):
    """
    Devuelve (texto, origen). origen es 'reglas' o el nombre del modelo.
    Nunca lanza excepción: ante cualquier falla del camino generativo,
    responde por reglas.
    """
    clave = clave or os.environ.get("GEMINI_API_KEY", "")
    modelo = modelo or os.environ.get("GEMINI_MODELO", MODELO_DEF)
    # Con datos insuficientes o nivel estable no hay nada que redactar:
    # no se gasta una llamada a la API.
    if (usar_modelo and clave
            and r["clasificacion"] not in ("DATOS_INSUFICIENTES", "ESTABLE")):
        try:
            return por_modelo(r, lugar, clave, modelo), modelo
        except (urllib.error.URLError, OSError, KeyError, IndexError,
                ValueError, TimeoutError):
            pass
    return por_reglas(r)[:LARGO_MAX], "reglas"
