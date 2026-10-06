// =====================================================================
//  Estación hidrometeorológica — Challenge #2 IoT
//
//  Core 0 : tarea de medición (periodo fijo) + ISR del Echo
//  Core 1 : loop() con servidor web embebido, LCD y semáforo de alarma
//
//  Sin MQTT. El tablero es un servidor HTTP propio en la WLAN.
//
//  Marcos de referencia:
//   - Control de calidad del dato ....... QARTOD (U.S. IOOS/NOAA)
//   - Diseño de la alarma ............... ANSI/ISA-18.2 y EEMUA 191
//   - Umbrales de alerta ................ Threshold Level Method
//   - Evapotranspiración ................ FAO-56 Penman-Monteith
// =====================================================================
#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <Adafruit_BMP280.h>
#include <DHT.h>
#include <math.h>

#include "config.h"
#include "pagina.h"

// ---------------------------------------------------------------- Tipos
struct Muestra {
  uint32_t t;          // millis() de la muestra
  float nivel;         // cm sobre el fondo
  float temp;          // °C (DHT22)
  float hr;            // % HR
  float presion;       // hPa
  float luz;           // 0-100 % (índice relativo del LDR)
  float caudal;        // L/min (derivado del nivel)
  float evap;          // mm/día (Penman-Monteith FAO-56)
  float evapHarg;      // mm/día (Hargreaves, verificación cruzada)
  float tempBmp;       // °C del BMP280 (redundancia)
  float dispersion;    // cm, spread entre los N disparos
  uint8_t calidad;     // bitmask Q_*   : QUÉ prueba falló
  uint8_t qcFlag;      // bandera QARTOD: SI el dato sirve
};

struct Registro {      // Entrada del histórico (más compacta)
  uint32_t t;
  float nivel, temp, hr, presion, luz, caudal, evap;
  uint8_t qc;
};

// ---------------------------------------------------------------- Estado global
DHT dht(PIN_DHT, DHT22);
Adafruit_BMP280 bmp;
LiquidCrystal_I2C lcd(DIR_LCD, 16, 2);
WebServer server(80);
Preferences nvs;

QueueHandle_t colaMuestras;              // Tamaño 1, sobrescritura
SemaphoreHandle_t mutexHist;             // Protege el búfer circular

Registro historico[N_HISTORICO];
volatile uint16_t histIni = 0, histCant = 0;

// Variables compartidas con la ISR
volatile uint32_t echoInicio = 0;
volatile uint32_t echoDuracion = 0;
volatile bool     echoListo = false;
portMUX_TYPE muxEcho = portMUX_INITIALIZER_UNLOCKED;

// Configuración persistente
float alturaSensor = ALTURA_SENSOR_CM_DEF;
float umbralAmarillo = UMBRAL_AMARILLO_CM;
float umbralRojo     = UMBRAL_ROJO_CM;
float gastoA = GASTO_A_DEF, gastoB = GASTO_B_DEF, gastoH0 = GASTO_H0_DEF;
String wifiSsid, wifiPass, webUser, webPass;

// Estado de alarma
enum Alerta { VERDE = 0, AMARILLA = 1, ROJA = 2, FALLA = 3 };
volatile Alerta alertaActual = VERDE;

// Silenciamiento desde el tablero (ISA-18.2: "alarm shelving").
// Silencia la señal sonora SIN ocultar la condición: el semáforo, el
// LCD y el tablero siguen mostrando el estado real. Expira solo por
// tiempo o cuando la condición desaparece, nunca de forma indefinida.
volatile bool silenciada = false;
volatile uint32_t tSilencio = 0;
volatile Alerta alertaSilenciada = VERDE;
uint8_t contadorPersistencia = 0;
uint32_t tDesactivacion = 0;

// Indicador de desempeño de alarmas (EEMUA 191)
// Objetivo en operación estable: <= 1 alarma cada 10 min.
volatile uint16_t alarmasTotales = 0;
volatile uint16_t alarmasVentana = 0;
uint32_t tVentanaAlarmas = 0;
volatile float    alarmasPor10min = 0;
volatile uint16_t picoPor10min = 0;

// Presencia de los módulos I2C. Si uno no respondió al arrancar NUNCA
// se le vuelve a hablar: una operación I2C sobre un bus caído se queda
// esperando el timeout y atasca la tarea.
bool bmpOk = false;
bool lcdOk = false;

// Extremos del día
float tempMin = 999, tempMax = -999, nivelMin = 999, nivelMax = -999;

// =====================================================================
//  ISR del Echo: mide el ancho del pulso sin bloquear nada.
//  pulseIn() bloquearía hasta 30 ms y mataría la concurrencia.
// =====================================================================
void IRAM_ATTR isrEcho() {
  if (digitalRead(PIN_ECHO)) {
    echoInicio = micros();
  } else {
    portENTER_CRITICAL_ISR(&muxEcho);
    echoDuracion = micros() - echoInicio;
    echoListo = true;
    portEXIT_CRITICAL_ISR(&muxEcho);
  }
}

// =====================================================================
//  Nivel: un disparo con corrección de velocidad del sonido
//  v = 331,3 + 0,606*T  [m/s]. Fusión cooperativa: el ultrasónico
//  mide tiempo, no distancia; sin la temperatura del DHT22 no hay
//  distancia correcta.
// =====================================================================
float medirDistanciaCm(float tempAire, bool *ok) {
  portENTER_CRITICAL(&muxEcho);
  echoListo = false;
  portEXIT_CRITICAL(&muxEcho);

  digitalWrite(PIN_TRIG, LOW);  delayMicroseconds(4);
  digitalWrite(PIN_TRIG, HIGH); delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);

  uint32_t t0 = micros();
  while (micros() - t0 < TIMEOUT_ECHO_US) {
    bool listo;
    portENTER_CRITICAL(&muxEcho);
    listo = echoListo;
    portEXIT_CRITICAL(&muxEcho);
    if (listo) break;
    vTaskDelay(1);                       // cede CPU: no bloquea el core
  }

  uint32_t dur; bool listo;
  portENTER_CRITICAL(&muxEcho);
  dur = echoDuracion; listo = echoListo;
  portEXIT_CRITICAL(&muxEcho);

  if (!listo) { *ok = false; return 0; }
  *ok = true;

  float vCmUs = (331.3f + 0.606f * tempAire) / 10000.0f;
  return (dur * vCmUs) / 2.0f;
}

int cmpFloat(const void *a, const void *b) {
  float d = *(const float*)a - *(const float*)b;
  return (d > 0) - (d < 0);
}

// Mediana de N disparos (equivale al Test 6 Spike de QARTOD: elimina
// el pico antes de que entre al promedio). Devuelve también la
// dispersión, que es el indicador real de si el sensor sigue vivo.
float medirNivelCm(float tempAire, uint8_t *calidad, float *dispersion) {
  float v[N_MEDIANA]; uint8_t n = 0;
  for (uint8_t i = 0; i < N_MEDIANA; i++) {
    bool ok;
    float d = medirDistanciaCm(tempAire, &ok);
    if (ok) v[n++] = d;
    vTaskDelay(pdMS_TO_TICKS(60));       // ciclo de medición del HC-SR04
  }
  if (n == 0) { *calidad |= Q_NIVEL_TIMEOUT; *dispersion = 0; return NAN; }

  qsort(v, n, sizeof(float), cmpFloat);
  *dispersion = v[n - 1] - v[0];
  return alturaSensor - v[n / 2];        // nivel sobre el fondo
}

// =====================================================================
//  Bandera primaria QARTOD
//  Esquema del Manual for the Use of Real-Time Oceanographic Data
//  Quality Control Flags (U.S. IOOS). El bitmask dice QUÉ falló;
//  esta bandera dice SI el dato se usa.
// =====================================================================
uint8_t banderaPrimaria(uint8_t calidad) {
  if (calidad & Q_NIVEL_TIMEOUT) return QC_MISSING;          // Test 1 Timing/Gap
  if (calidad & (Q_NIVEL_RANGO                               // Test 4 Gross Range
               | Q_NIVEL_PEGADO                              // Test 8 Flat Line
               | Q_DHT_FALLA | Q_BMP_FALLA)) return QC_FAIL;
  if (calidad & (Q_NIVEL_TASA                                // Test 7 Rate of Change
               | Q_INCOHERENTE                               // Test 9 Multi-Variate
               | Q_SOL_FUERTE)) return QC_SUSPECT;
  return QC_PASS;
}

// =====================================================================
//  Variables derivadas
// =====================================================================
float caudalLmin(float nivelCm) {
  if (nivelCm <= gastoH0) return 0.0f;
  return gastoA * powf(nivelCm - gastoH0, gastoB);
}

// Radiación solar estimada desde el LDR (Ångström-Prescott).
// El LDR NO mide W/m2: aporta la fracción de insolación n/N.
// Limitación declarada en la hoja de trabajo.
float radiacionMJ(float luzPct) {
  float nN = constrain(luzPct / 100.0f, 0.0f, 1.0f);
  return RA_MJ_M2_DIA * (0.25f + 0.50f * nN);
}

// FAO-56 Penman-Monteith. Sin anemómetro: u2 = 2 m/s, como permite
// la norma cuando no se mide viento.
float evapPenman(float T, float HR, float presionHpa, float luzPct) {
  float P  = presionHpa / 10.0f;                       // kPa
  float es = 0.6108f * expf(17.27f * T / (T + 237.3f));
  float ea = es * HR / 100.0f;
  float delta = 4098.0f * es / powf(T + 237.3f, 2);
  float gamma = 0.000665f * P;
  float Rs = radiacionMJ(luzPct);
  float Rn = (1.0f - ALBEDO) * Rs * 0.75f;             // neta aproximada
  float num = 0.408f * delta * Rn
            + gamma * (900.0f / (T + 273.0f)) * VIENTO_U2 * (es - ea);
  float den = delta + gamma * (1.0f + 0.34f * VIENTO_U2);
  float eto = num / den;
  return eto < 0 ? 0 : eto;
}

// Hargreaves: solo necesita temperatura. Verificación cruzada de Penman.
float evapHargreaves(float Tmed, float Tmin, float Tmax) {
  float dT = Tmax - Tmin;
  if (dT <= 0) return 0.0f;
  float Ra_mm = RA_MJ_M2_DIA * 0.408f;
  float eto = 0.0023f * (Tmed + 17.8f) * sqrtf(dT) * Ra_mm;
  return eto < 0 ? 0 : eto;
}

// =====================================================================
//  Histórico en búfer circular (RAM). ~9 KB de los 320 KB disponibles.
// =====================================================================
void guardarHistorico(const Muestra &m) {
  if (xSemaphoreTake(mutexHist, pdMS_TO_TICKS(100)) != pdTRUE) return;
  uint16_t idx = (histIni + histCant) % N_HISTORICO;
  if (histCant == N_HISTORICO) {
    histIni = (histIni + 1) % N_HISTORICO;
    idx = (histIni + histCant - 1) % N_HISTORICO;
  } else {
    histCant++;
  }
  historico[idx] = { m.t, m.nivel, m.temp, m.hr, m.presion,
                     m.luz, m.caudal, m.evap, m.qcFlag };
  xSemaphoreGive(mutexHist);
}

// =====================================================================
//  Lógica de fusión y decisión de alarma
//
//  Marco de referencia:
//   - Validación por lectura: jerarquía de pruebas QARTOD
//     (U.S. IOOS, Manual for Real-Time QC of Water Level Data)
//   - Combinación de señales: corroboración multivariable, análoga al
//     Test 9 (Multi-Variate) de QARTOD. NO es votación por mayoría:
//     eso exigiría sensores redundantes midiendo la misma magnitud.
//     Aquí los tres indicadores miden cosas distintas y se usan como
//     evidencia concurrente para reducir falsos positivos.
//   - Diseño de la alarma: ANSI/ISA-18.2 y EEMUA 191
//   - Umbrales: Threshold Level Method, percentiles de excedencia
//     Q80 y Q90 de la serie base (Hisdal et al., 2004)
// =====================================================================
Alerta evaluarAlerta(const Muestra &m, float nivelPrevio, uint32_t dtMs) {

  // Un dato marcado como malo o ausente no puede sostener una decisión.
  if (m.qcFlag == QC_FAIL || m.qcFlag == QC_MISSING) return FALLA;

  // --- Indicador 1: nivel bajo umbral (TLM, percentiles Q80/Q90) ---
  bool i1Amarillo = m.nivel < umbralAmarillo;
  bool i1Rojo     = m.nivel < umbralRojo;

  // --- Indicador 2: tendencia de descenso sostenida (mm/h) ---
  float tasaMmH = 0;
  if (dtMs > 0 && !isnan(nivelPrevio))
    tasaMmH = (nivelPrevio - m.nivel) * 10.0f * 3600000.0f / dtMs;
  bool i2 = tasaMmH > TASA_DESCENSO_MMH;

  // --- Indicador 3: evaporación potencial elevada (FAO-56) ---
  bool i3 = m.evap > UMBRAL_EVAP_MMDIA;

  // --- Corroboración multivariable: al menos 2 de 3 indicadores
  //     independientes deben concordar (QARTOD Test 9).
  uint8_t concordancia = (i1Amarillo ? 1 : 0) + (i2 ? 1 : 0) + (i3 ? 1 : 0);

  // --- Persistencia (on-delay). ISA-18.2 / EEMUA 191 recomiendan un
  //     retardo mínimo del orden de 3 s para suprimir transitorios.
  //     3 muestras x 2 s = 6 s, el doble del mínimo.
  if (concordancia >= 2) {
    if (contadorPersistencia < 255) contadorPersistencia++;
  } else {
    contadorPersistencia = 0;
  }
  bool condicion = contadorPersistencia >= MUESTRAS_PERSISTENCIA;

  // --- Histéresis (deadband). ISA-18.2 / EEMUA 191 recomiendan 2-5 %
  //     del span, siempre MAYOR que el ruido de la señal. El ruido
  //     medido en banco (dispersión del HC-SR04) ronda 0,9 cm, que
  //     sobre la referencia equivale a ~5 %. La desactivación exige
  //     además 10 min sostenidos = dos registros históricos.
  if (alertaActual != VERDE && alertaActual != FALLA) {
    if (m.nivel > umbralAmarillo * (1.0f + HISTERESIS_PCT)) {
      if (tDesactivacion == 0) tDesactivacion = millis();
      if (millis() - tDesactivacion > TIEMPO_DESACTIVA_MS) {
        tDesactivacion = 0;
        return VERDE;
      }
    } else {
      tDesactivacion = 0;
    }
    return i1Rojo ? ROJA : alertaActual;
  }

  if (condicion) return i1Rojo ? ROJA : AMARILLA;
  return VERDE;
}

// =====================================================================
//  TAREA DE MEDICIÓN — core 0
// =====================================================================
void tareaMedicion(void *param) {
  TickType_t ultimo = xTaskGetTickCount();
  uint32_t tUltimoHist = 0;
  float nivelPrevio = NAN;
  uint8_t repetidos = 0;

  for (;;) {
    Muestra m = {};
    m.t = millis();
    m.calidad = Q_OK;

    // ---- DHT22 (marca el período: no admite menos de 2 s) ----
    float t = dht.readTemperature();
    float h = dht.readHumidity();
    if (isnan(t) || isnan(h) || t < TEMP_MIN_C || t > TEMP_MAX_C) {
      m.calidad |= Q_DHT_FALLA;
      t = isnan(t) ? 20.0f : t;          // respaldo para el ultrasónico
      h = isnan(h) ? 50.0f : h;
    }
    m.temp = t; m.hr = h;

    // ---- BMP280 (opcional: si no respondio al arrancar, no se toca) ----
    if (bmpOk) {
      float p = bmp.readPressure() / 100.0f;
      m.tempBmp = bmp.readTemperature();
      if (p < PRES_MIN_HPA || p > PRES_MAX_HPA) m.calidad |= Q_BMP_FALLA;
      m.presion = p;

      // Redundancia entre dos caminos físicos distintos (fusión competitiva).
      // El BMP280 se autocalienta: se descuenta el desfase sistemático
      // medido en banco y se evalúa solo la desviación alrededor de él.
      if (!(m.calidad & (Q_DHT_FALLA | Q_BMP_FALLA))) {
        float desvio = (m.tempBmp - m.temp) - BMP_AUTOCALENT_C;
        if (fabsf(desvio) > DISCREPANCIA_TEMP_C) m.calidad |= Q_DHT_FALLA;
      }
    } else {
      m.presion = PRESION_SUPUESTA_HPA;   // valor tipico de la Sabana
      m.tempBmp = m.temp;
      // Sin sensor NO se marca falla: se declara la ausencia en la bandera
      // QARTOD como "no evaluado", que es lo que corresponde.
    }

    // ---- LDR ----
    uint32_t acc = 0;
    for (uint8_t i = 0; i < 8; i++) acc += analogRead(PIN_LDR);
    m.luz = (acc / 8.0f) * 100.0f / 4095.0f;
    if (m.luz > 85.0f) m.calidad |= Q_SOL_FUERTE;

    // ---- Nivel (mediana, con corrección por temperatura) ----
    float dispersion = 0;
    m.nivel = medirNivelCm(m.temp, &m.calidad, &dispersion);
    m.dispersion = dispersion;

    if (!isnan(m.nivel)) {
      // Test 4 Gross Range: rango impuesto por la geometría del montaje
      if (m.nivel < NIVEL_MIN_CM || m.nivel > NIVEL_MAX_CM)
        m.calidad |= Q_NIVEL_RANGO;

      // Test 7 Rate of Change: umbral derivado del caudal máximo del banco
      if (!isnan(nivelPrevio) && fabsf(m.nivel - nivelPrevio) > NIVEL_TASA_MAX)
        m.calidad |= Q_NIVEL_TASA;

      // Test 8 Flat Line: un HC-SR04 sano siempre tiembla entre disparos.
      // Dispersión nula sostenida = salida que dejó de actualizarse.
      if (dispersion < 0.01f) {
        if (++repetidos > FLATLINE_MUESTRAS) m.calidad |= Q_NIVEL_PEGADO;
      } else repetidos = 0;
    }

    // ---- Extremos del día (alimentan Hargreaves) ----
    if (!(m.calidad & Q_DHT_FALLA)) {
      if (m.temp < tempMin) tempMin = m.temp;
      if (m.temp > tempMax) tempMax = m.temp;
    }
    if (!isnan(m.nivel) && !(m.calidad & Q_NIVEL_RANGO)) {
      if (m.nivel < nivelMin) nivelMin = m.nivel;
      if (m.nivel > nivelMax) nivelMax = m.nivel;
    }

    // ---- Derivadas (fusión complementaria: cuatro variables
    //      producen una quinta que nadie midió) ----
    m.caudal   = isnan(m.nivel) ? 0 : caudalLmin(m.nivel);
    m.evap     = evapPenman(m.temp, m.hr, m.presion, m.luz);
    m.evapHarg = evapHargreaves(m.temp, tempMin, tempMax);

    // ---- Coherencia física (QARTOD Test 9, Multi-Variate) ----
    // El descenso observado debe explicarse por la evaporación estimada.
    // La tolerancia del 30 % corresponde a la incertidumbre propia de
    // esa estimación: viento asumido y radiación por proxy.
    if (!isnan(m.nivel) && !isnan(nivelPrevio)) {
      float dtH = PERIODO_MUESTREO_MS / 3600000.0f;
      float descensoObs = (nivelPrevio - m.nivel) * 10.0f;          // mm
      float descensoEsp = (m.evap / 24.0f) * dtH;                   // mm
      if (descensoObs > 0.5f && descensoObs > descensoEsp * 1.3f + 0.5f)
        m.calidad |= Q_INCOHERENTE;
    }

    // ---- Bandera primaria QARTOD ----
    // Resume la validez de la medición de NIVEL, que es la variable
    // principal. La ausencia del BMP280 no la degrada: en QARTOD la
    // bandera es por variable, y la presión se reporta aparte como
    // "no evaluada" mediante el indicador bmpOk.
    m.qcFlag = banderaPrimaria(m.calidad);

    // ---- Alarma ----
    Alerta previa = alertaActual;
    alertaActual = evaluarAlerta(m, nivelPrevio, PERIODO_MUESTREO_MS);
    nivelPrevio = m.nivel;

    // EEMUA 191 cuenta eventos anunciados al operador, no muestras en
    // alarma: se contabiliza la TRANSICIÓN desde estado normal.
    if (previa == VERDE && alertaActual != VERDE) {
      alarmasTotales++;
      alarmasVentana++;
    }
    if (tVentanaAlarmas == 0) tVentanaAlarmas = millis();
    if (millis() - tVentanaAlarmas >= 600000) {          // ventana de 10 min
      alarmasPor10min = alarmasVentana;
      if (alarmasVentana > picoPor10min) picoPor10min = alarmasVentana;
      alarmasVentana = 0;
      tVentanaAlarmas = millis();
    }

    // ---- Diagnóstico por puerto serie ----
    Serial.printf("niv=%6.1f disp=%4.2f T=%4.1f HR=%4.1f p=%5.0f luz=%3.0f "
                  "evap=%4.2f qc=%u cal=0x%02X ",
                  m.nivel, m.dispersion, m.temp, m.hr, m.presion, m.luz,
                  m.evap, m.qcFlag, m.calidad);
    if (m.calidad & Q_NIVEL_RANGO)   Serial.print("[RANGO]");
    if (m.calidad & Q_NIVEL_TASA)    Serial.print("[TASA]");
    if (m.calidad & Q_NIVEL_TIMEOUT) Serial.print("[SIN_ECO]");
    if (m.calidad & Q_NIVEL_PEGADO)  Serial.print("[CONGELADO]");
    if (m.calidad & Q_DHT_FALLA)     Serial.print("[DHT]");
    if (m.calidad & Q_BMP_FALLA)     Serial.print("[BMP]");
    if (m.calidad & Q_SOL_FUERTE)    Serial.print("[SOL]");
    if (m.calidad & Q_INCOHERENTE)   Serial.print("[INCOHER]");
    if (!bmpOk)                      Serial.print("[sin BMP]");
    if (!lcdOk)                      Serial.print("[sin LCD]");
    Serial.println();

    // ---- Publicar a la tarea de servicio (cola de tamaño 1) ----
    xQueueOverwrite(colaMuestras, &m);

    // ---- Histórico cada 5 min ----
    if (millis() - tUltimoHist >= PERIODO_HISTORICO_MS || tUltimoHist == 0) {
      tUltimoHist = millis();
      guardarHistorico(m);
      nvs.begin("estacion", false);
      nvs.putFloat("tmin", tempMin);  nvs.putFloat("tmax", tempMax);
      nvs.putFloat("nmin", nivelMin); nvs.putFloat("nmax", nivelMax);
      nvs.end();
    }

    vTaskDelayUntil(&ultimo, pdMS_TO_TICKS(PERIODO_MUESTREO_MS));
  }
}

// =====================================================================
//  Servidor web — sin MQTT, todo embebido
// =====================================================================
bool autenticado() {
  if (!server.authenticate(webUser.c_str(), webPass.c_str())) {
    server.requestAuthentication();
    return false;
  }
  return true;
}

void rutaRaiz() {
  if (!autenticado()) return;
  server.send_P(200, "text/html", PAGINA_HTML);
}

void rutaDatos() {
  if (!autenticado()) return;
  Muestra m;
  if (xQueuePeek(colaMuestras, &m, 0) != pdTRUE) {
    server.send(503, "application/json", "{\"error\":\"sin datos\"}");
    return;
  }
  const char *est[] = {"VERDE", "AMARILLA", "ROJA", "FALLA"};
  char buf[620];
  snprintf(buf, sizeof(buf),
    "{\"t\":%lu,\"nivel\":%.2f,\"temp\":%.1f,\"hr\":%.1f,\"presion\":%.1f,"
    "\"luz\":%.1f,\"caudal\":%.3f,\"evap\":%.2f,\"evapHarg\":%.2f,"
    "\"tempBmp\":%.1f,\"dispersion\":%.2f,\"calidad\":%u,\"qc\":%u,"
    "\"alerta\":\"%s\",\"uAmarillo\":%.1f,\"uRojo\":%.1f,"
    "\"tmin\":%.1f,\"tmax\":%.1f,"
    "\"alarmas10min\":%.1f,\"alarmasPico\":%u,\"alarmasTotal\":%u,"
    "\"bmpOk\":%d,\"lcdOk\":%d,\"silenciada\":%d,"
    "\"uptime\":%lu}",
    (unsigned long)m.t, m.nivel, m.temp, m.hr, m.presion, m.luz,
    m.caudal, m.evap, m.evapHarg, m.tempBmp, m.dispersion,
    m.calidad, m.qcFlag, est[alertaActual],
    umbralAmarillo, umbralRojo, tempMin, tempMax,
    alarmasPor10min, picoPor10min, alarmasTotales,
    bmpOk ? 1 : 0, lcdOk ? 1 : 0, silenciada ? 1 : 0,
    (unsigned long)(millis() / 1000));
  server.send(200, "application/json", buf);
}

void rutaHistorico() {
  if (!autenticado()) return;
  if (xSemaphoreTake(mutexHist, pdMS_TO_TICKS(200)) != pdTRUE) {
    server.send(503, "application/json", "[]");
    return;
  }
  server.setContentLength(CONTENT_LENGTH_UNKNOWN);
  server.send(200, "application/json", "");
  server.sendContent("[");
  char buf[170];
  for (uint16_t i = 0; i < histCant; i++) {
    Registro &r = historico[(histIni + i) % N_HISTORICO];
    snprintf(buf, sizeof(buf),
      "%s{\"t\":%lu,\"n\":%.2f,\"T\":%.1f,\"h\":%.1f,\"p\":%.1f,"
      "\"l\":%.1f,\"q\":%.3f,\"e\":%.2f,\"qc\":%u}",
      i ? "," : "", (unsigned long)r.t, r.nivel, r.temp, r.hr, r.presion,
      r.luz, r.caudal, r.evap, r.qc);
    server.sendContent(buf);
  }
  server.sendContent("]");
  server.sendContent("");
  xSemaphoreGive(mutexHist);
}

// Calibración en caliente: /api/calibrar?altura=38.5&amarillo=12.4&rojo=9.8
void rutaCalibrar() {
  if (!autenticado()) return;
  nvs.begin("estacion", false);
  if (server.hasArg("altura")) {
    alturaSensor = server.arg("altura").toFloat();
    nvs.putFloat("altura", alturaSensor);
  }
  if (server.hasArg("amarillo")) {
    umbralAmarillo = server.arg("amarillo").toFloat();
    nvs.putFloat("uAmar", umbralAmarillo);
  }
  if (server.hasArg("rojo")) {
    umbralRojo = server.arg("rojo").toFloat();
    nvs.putFloat("uRojo", umbralRojo);
  }
  nvs.end();
  char buf[120];
  snprintf(buf, sizeof(buf),
    "{\"altura\":%.2f,\"amarillo\":%.2f,\"rojo\":%.2f}",
    alturaSensor, umbralAmarillo, umbralRojo);
  server.send(200, "application/json", buf);
}

// Desactivación de la alarma física desde el tablero.
//   POST/GET /api/silenciar          -> silencia la señal sonora
//   POST/GET /api/silenciar?on=1     -> la reactiva
// Requisito del enunciado: las autoridades deben poder desactivar
// alarmas físicas desde el tablero de control.
void rutaSilenciar() {
  if (!autenticado()) return;
  bool reactivar = server.hasArg("on") && server.arg("on") == "1";
  if (reactivar) {
    silenciada = false;
  } else {
    silenciada = true;
    tSilencio = millis();
    alertaSilenciada = alertaActual;
  }
  uint32_t restante = silenciada
      ? (SILENCIO_MAX_MS - (millis() - tSilencio)) / 1000 : 0;
  char buf[110];
  snprintf(buf, sizeof(buf),
    "{\"silenciada\":%d,\"restanteSeg\":%lu}",
    silenciada ? 1 : 0, (unsigned long)restante);
  server.send(200, "application/json", buf);
  Serial.printf("[TABLERO] Alarma %s por el operador\n",
                silenciada ? "SILENCIADA" : "REACTIVADA");
}

// =====================================================================
//  LCD y semáforo — corren en core 1, independientes de la red
// =====================================================================
void refrescarLcd(const Muestra &m) {
  if (!lcdOk) return;          // sin LCD no se toca el bus
  static uint8_t pantalla = 0;
  static uint32_t tUlt = 0;
  if (millis() - tUlt < PERIODO_LCD_MS) return;
  tUlt = millis();
  pantalla = (pantalla + 1) % 4;

  char l1[17], l2[17];
  switch (pantalla) {
    case 0:
      snprintf(l1, 17, "Nivel  %5.1f cm", m.nivel);
      snprintf(l2, 17, "Caudal %5.2f L/m", m.caudal);
      break;
    case 1:
      snprintf(l1, 17, "Temp   %5.1f C", m.temp);
      snprintf(l2, 17, "HR     %5.1f %%", m.hr);
      break;
    case 2:
      snprintf(l1, 17, "Presion %5.1f%s", m.presion, bmpOk ? "hPa" : "SUP");
      snprintf(l2, 17, "Luz     %5.1f %%", m.luz);
      break;
    default: {
      const char *est[] = {"VERDE", "AMARILLA", "ROJA", "FALLA SENS"};
      snprintf(l1, 17, "Evap   %5.2fmm/d", m.evap);
      snprintf(l2, 17, "%-9s%s Q%u %s%d", est[alertaActual],
               silenciada ? "*" : " ", m.qcFlag,
               WiFi.status() == WL_CONNECTED ? "W" : "A",
               WiFi.softAPgetStationNum());
      break;
    }
  }
  lcd.setCursor(0, 0); lcd.print("                ");
  lcd.setCursor(0, 0); lcd.print(l1);
  lcd.setCursor(0, 1); lcd.print("                ");
  lcd.setCursor(0, 1); lcd.print(l2);
}

// Semáforo de estado + zumbador.
// El amarillo NO suena: ISA-18.2 pide que cada alarma tenga una acción
// asociada. La alerta moderada es informativa; reservar el sonido para
// la severa evita la fatiga de alarma.
void actualizarAlarma() {
  static uint32_t tUlt = 0;
  static uint8_t paso = 0;
  uint32_t ahora = millis();

  bool verde = false, amarillo = false, rojo = false, zumba = false;

  // El silencio expira por tiempo, si la condición desaparece, o si
  // escala a un nivel más grave que el silenciado.
  if (silenciada) {
    if (millis() - tSilencio > SILENCIO_MAX_MS
        || alertaActual == VERDE
        || alertaActual > alertaSilenciada) {
      silenciada = false;
    }
  }

  switch (alertaActual) {

    case ROJA:                                   // tono continuo
      rojo = true;
      zumba = !silenciada;
      break;

    case AMARILLA:                               // solo luz
      amarillo = true;
      break;

    case FALLA:                                  // tres pulsos cortos
      if (ahora - tUlt > 150) { tUlt = ahora; paso = (paso + 1) % 8; }
      amarillo = (paso == 0 || paso == 2 || paso == 4);
      zumba = amarillo && ALARMA_SONORA_FALLA && !silenciada;
      break;

    default:                                     // VERDE
      verde = true;
      break;
  }

  digitalWrite(PIN_LED_VERDE,    verde);
  digitalWrite(PIN_LED_AMARILLO, amarillo);
  digitalWrite(PIN_LED_ROJO,     rojo);
  digitalWrite(PIN_ZUMBADOR,     zumba);
}

// =====================================================================
//  Arranque
// =====================================================================
void cargarConfig() {
  nvs.begin("estacion", true);
  alturaSensor   = nvs.getFloat("altura", ALTURA_SENSOR_CM_DEF);
  umbralAmarillo = nvs.getFloat("uAmar",  UMBRAL_AMARILLO_CM);
  umbralRojo     = nvs.getFloat("uRojo",  UMBRAL_ROJO_CM);
  gastoA  = nvs.getFloat("gA",  GASTO_A_DEF);
  gastoB  = nvs.getFloat("gB",  GASTO_B_DEF);
  gastoH0 = nvs.getFloat("gH0", GASTO_H0_DEF);
  tempMin = nvs.getFloat("tmin", 999);
  tempMax = nvs.getFloat("tmax", -999);
  wifiSsid = nvs.getString("ssid", WIFI_SSID_DEF);
  wifiPass = nvs.getString("pass", WIFI_PASS_DEF);
  webUser  = nvs.getString("wuser", WEB_USER_DEF);
  webPass  = nvs.getString("wpass", WEB_PASS_DEF);
  nvs.end();
}

// Comprueba si hay algo en una direccion del bus, sin bloquear.
bool i2cPresente(uint8_t dir) {
  Wire.beginTransmission(dir);
  return Wire.endTransmission() == 0;
}

void setup() {
  Serial.begin(115200);
  delay(300);

  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);
  pinMode(PIN_ZUMBADOR, OUTPUT);
  pinMode(PIN_LED_VERDE, OUTPUT);
  pinMode(PIN_LED_AMARILLO, OUTPUT);
  pinMode(PIN_LED_ROJO, OUTPUT);
  digitalWrite(PIN_TRIG, LOW);
  digitalWrite(PIN_ZUMBADOR, LOW);
  digitalWrite(PIN_LED_VERDE, LOW);
  digitalWrite(PIN_LED_AMARILLO, LOW);
  digitalWrite(PIN_LED_ROJO, LOW);
  analogSetPinAttenuation(PIN_LDR, ADC_11db);

  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setTimeOut(50);            // no quedarse colgado si el bus esta caido

  // ---- Deteccion de los modulos I2C ----
  Serial.println("\nBus I2C:");
  lcdOk = i2cPresente(DIR_LCD);
  bmpOk = i2cPresente(DIR_BMP);
  if (!bmpOk && i2cPresente(0x77)) {   // la otra direccion tipica del BMP
    Serial.println("  BMP280 hallado en 0x77 (config.h dice otra cosa)");
  }
  Serial.printf("  LCD  0x%02X : %s\n", DIR_LCD, lcdOk ? "OK" : "AUSENTE");
  Serial.printf("  BMP  0x%02X : %s\n", DIR_BMP, bmpOk ? "OK" : "AUSENTE");
  if (!lcdOk && !bmpOk)
    Serial.println("  >> NINGUN dispositivo I2C. Revisar SDA/SCL, 3V3 y GND comun.");

  if (lcdOk) {
    lcd.init(); lcd.backlight();
    lcd.print("Estacion IoT"); lcd.setCursor(0, 1); lcd.print("Iniciando...");
  }

  dht.begin();

  if (bmpOk) {
    bmpOk = bmp.begin(DIR_BMP);
    if (bmpOk) {
      bmp.setSampling(Adafruit_BMP280::MODE_NORMAL,
                      Adafruit_BMP280::SAMPLING_X2,
                      Adafruit_BMP280::SAMPLING_X16,
                      Adafruit_BMP280::FILTER_X16,
                      Adafruit_BMP280::STANDBY_MS_500);
    } else {
      Serial.println("  BMP280 responde pero begin() fallo: puede ser un BME280.");
    }
  }
  if (!bmpOk)
    Serial.println("[AVISO] Se opera SIN presion. Penman usa 750 hPa (Sabana).");

  cargarConfig();

  colaMuestras = xQueueCreate(1, sizeof(Muestra));
  mutexHist    = xSemaphoreCreateMutex();

  attachInterrupt(digitalPinToInterrupt(PIN_ECHO), isrEcho, CHANGE);

  // --- WLAN ---
  // Modo doble: el nodo levanta SIEMPRE su propio punto de acceso y,
  // además, intenta unirse a la red existente si hay credenciales.
  // El AP propio garantiza acceso al tablero aunque el router tenga
  // aislamiento de clientes o no haya infraestructura en el sitio.
  WiFi.mode(WIFI_AP_STA);

  WiFi.softAPConfig(IPAddress(192, 168, 4, 1),
                    IPAddress(192, 168, 4, 1),
                    IPAddress(255, 255, 255, 0));
  WiFi.softAP(AP_SSID, AP_PASS);
  Serial.println();
  Serial.println("=== Punto de acceso propio ===");
  Serial.printf("  Red     : %s\n", AP_SSID);
  Serial.printf("  Clave   : %s\n", AP_PASS);
  Serial.printf("  Tablero : http://%s\n", WiFi.softAPIP().toString().c_str());

  if (wifiSsid.length()) {
    WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
    uint8_t intentos = 0;
    while (WiFi.status() != WL_CONNECTED && intentos++ < 32) delay(250);
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n=== Tambien en la red existente ===");
    Serial.printf("  SSID    : %s\n", wifiSsid.c_str());
    Serial.printf("  IP      : %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("  Gateway : %s\n", WiFi.gatewayIP().toString().c_str());
    Serial.printf("  Senal   : %d dBm\n", WiFi.RSSI());
    Serial.println("  Si esta IP no responde al ping, el router tiene");
    Serial.println("  aislamiento de clientes: usar el AP propio.");
    if (lcdOk) {
      lcd.clear(); lcd.print(WiFi.localIP());
      lcd.setCursor(0, 1); lcd.print("AP 192.168.4.1");
    }
  } else {
    Serial.println("\nSin unirse a red externa. Solo AP propio.");
    if (lcdOk) {
      lcd.clear(); lcd.print("AP: EstacionIoT");
      lcd.setCursor(0, 1); lcd.print("192.168.4.1");
    }
  }
  delay(2500);

  server.on("/", rutaRaiz);
  server.on("/api/datos", rutaDatos);
  server.on("/api/historico", rutaHistorico);
  server.on("/api/calibrar", rutaCalibrar);
  server.on("/api/silenciar", rutaSilenciar);
  server.begin();

  // --- Tarea de medición anclada al core 0 ---
  xTaskCreatePinnedToCore(tareaMedicion, "medicion", 6144, NULL, 2, NULL, 0);
  // loop() vive en el core 1: servidor web, LCD y semáforo
}

void loop() {
  server.handleClient();

  Muestra m;
  if (xQueuePeek(colaMuestras, &m, 0) == pdTRUE) {
    refrescarLcd(m);
  }
  actualizarAlarma();

  // Reconexión no bloqueante
  static uint32_t tRecon = 0;
  if (wifiSsid.length() && WiFi.status() != WL_CONNECTED
      && millis() - tRecon > 20000) {
    tRecon = millis();
    WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());   // sin disconnect: no tumba el AP
  }

  delay(5);
}
