// =====================================================================
//  Estación hidrometeorológica — Challenge #3 IoT
//
//  Core 0 : tarea de medición (periodo fijo) + ISR del Echo
//  Core 1 : loop() con servidor web embebido, LCD y semáforo de alarma
//  Core 1 : tareaMqtt(), hilo propio de transmisión hacia el Gateway
//
//  Todo lo del Challenge #2 se conserva sin cambios de comportamiento:
//  el nodo sigue midiendo, decidiendo y alarmando aunque no exista
//  broker. El Challenge #3 AGREGA un cliente MQTT que:
//    - publica cada muestra en  sabana/<nodo>/telemetria
//    - publica cada transición en sabana/<nodo>/alerta   (retenido)
//    - anuncia su presencia en    sabana/<nodo>/estado    (retenido + LWT)
//    - recibe comandos en         sabana/<nodo>/cmd/#
//    - confirma su ejecución en   sabana/<nodo>/ack
//
//  La transmisión corre en una tarea independiente del hilo principal
//  (restricción técnica 1 del enunciado): si el broker no responde, la
//  que espera es esa tarea, nunca la medición ni el tablero local.
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
#include <PubSubClient.h>   // Nick O'Leary, MQTT 3.1.1
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
  uint32_t seq;        // consecutivo: el Gateway detecta huecos con él
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
QueueHandle_t colaMqtt;                  // FIFO hacia la tarea de transmisión

// ---- Cliente MQTT (lo usa ÚNICAMENTE tareaMqtt: PubSubClient no es
//      seguro entre hilos, así que nadie más lo toca) ----
WiFiClient   clienteRed;
PubSubClient mqtt(clienteRed);
String mqttHost, mqttUser, mqttPass;      // copia editable desde el tablero
char mqHost[64], mqUser[32], mqPass[48];  // copia privada de tareaMqtt
volatile bool mqttReconfig = true;        // pide a tareaMqtt recargar la copia
portMUX_TYPE muxMqttCfg = portMUX_INITIALIZER_UNLOCKED;
uint16_t mqttPuerto = MQTT_PUERTO_DEF;
volatile bool     mqttConectado  = false;
volatile uint32_t mqttPublicados = 0;    // mensajes entregados al broker
volatile uint32_t mqttDescartes  = 0;    // muestras que no cupieron en la cola
char topicoTelemetria[48], topicoAlerta[48], topicoEstado[48],
     topicoAck[48], topicoCmd[48];
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
portMUX_TYPE muxSilencio = portMUX_INITIALIZER_UNLOCKED;
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
    static uint32_t consecutivo = 0;
    m.seq = ++consecutivo;

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

    // ---- Entregar a la tarea de transmisión MQTT ----
    // Nunca se espera: si la cola está llena (broker caído) se descarta
    // la muestra MÁS ANTIGUA. La medición no se frena por la red.
    if (xQueueSend(colaMqtt, &m, 0) != pdTRUE) {
      Muestra vieja;
      xQueueReceive(colaMqtt, &vieja, 0);
      xQueueSend(colaMqtt, &m, 0);
      mqttDescartes++;
    }

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
//  Servidor web embebido — tablero LOCAL (se conserva del Challenge #2)
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
  char buf[700];
  snprintf(buf, sizeof(buf),
    "{\"t\":%lu,\"nivel\":%.2f,\"temp\":%.1f,\"hr\":%.1f,\"presion\":%.1f,"
    "\"luz\":%.1f,\"caudal\":%.3f,\"evap\":%.2f,\"evapHarg\":%.2f,"
    "\"tempBmp\":%.1f,\"dispersion\":%.2f,\"calidad\":%u,\"qc\":%u,"
    "\"alerta\":\"%s\",\"uAmarillo\":%.1f,\"uRojo\":%.1f,"
    "\"tmin\":%.1f,\"tmax\":%.1f,"
    "\"alarmas10min\":%.1f,\"alarmasPico\":%u,\"alarmasTotal\":%u,"
    "\"bmpOk\":%d,\"lcdOk\":%d,\"silenciada\":%d,"
    "\"mqtt\":%d,\"mqttPub\":%lu,\"mqttDesc\":%lu,"
    "\"uptime\":%lu}",
    (unsigned long)m.t, m.nivel, m.temp, m.hr, m.presion, m.luz,
    m.caudal, m.evap, m.evapHarg, m.tempBmp, m.dispersion,
    m.calidad, m.qcFlag, est[alertaActual],
    umbralAmarillo, umbralRojo, tempMin, tempMax,
    alarmasPor10min, picoPor10min, alarmasTotales,
    bmpOk ? 1 : 0, lcdOk ? 1 : 0, silenciada ? 1 : 0,
    mqttConectado ? 1 : 0, (unsigned long)mqttPublicados,
    (unsigned long)mqttDescartes,
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
// Broker sin recompilar:    /api/calibrar?mqtt=192.168.1.50&mqttuser=nodo01&mqttpass=xxxx
// (el cambio de broker se aplica en la siguiente reconexión de tareaMqtt)
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
  if (server.hasArg("mqtt")) {
    mqttHost = server.arg("mqtt");
    nvs.putString("mqHost", mqttHost);
  }
  if (server.hasArg("mqttuser")) {
    mqttUser = server.arg("mqttuser");
    nvs.putString("mqUser", mqttUser);
  }
  if (server.hasArg("mqttpass")) {
    mqttPass = server.arg("mqttpass");
    nvs.putString("mqPass", mqttPass);
  }
  nvs.end();
  if (server.hasArg("mqtt") || server.hasArg("mqttuser") || server.hasArg("mqttpass"))
    mqttReconfig = true;                 // tareaMqtt reconecta con los datos nuevos
  char buf[200];
  snprintf(buf, sizeof(buf),
    "{\"altura\":%.2f,\"amarillo\":%.2f,\"rojo\":%.2f,\"mqtt\":\"%s\"}",
    alturaSensor, umbralAmarillo, umbralRojo, mqttHost.c_str());
  server.send(200, "application/json", buf);
}

// Desactivación de la alarma física desde el tablero.
//   POST/GET /api/silenciar          -> silencia la señal sonora
//   POST/GET /api/silenciar?on=1     -> la reactiva
// Requisito del enunciado: las autoridades deben poder desactivar
// alarmas físicas desde el tablero de control.
//
// La misma función atiende las dos vías de entrada: el tablero local
// (HTTP) y el tablero global (MQTT, desde Ubidots a través del Gateway).
// Las salvaguardas de ISA-18.2 viven en actualizarAlarma() y por tanto
// aplican igual sin importar por dónde llegó la orden.
uint32_t aplicarSilencio(bool reactivar, const char *origen) {
  portENTER_CRITICAL(&muxSilencio);
  if (reactivar) {
    silenciada = false;
  } else {
    silenciada = true;
    tSilencio = millis();
    alertaSilenciada = alertaActual;
  }
  bool sil = silenciada;
  uint32_t t0 = tSilencio;
  portEXIT_CRITICAL(&muxSilencio);
  Serial.printf("[%s] Alarma %s por el operador\n", origen,
                sil ? "SILENCIADA" : "REACTIVADA");
  return sil ? (SILENCIO_MAX_MS - (millis() - t0)) / 1000 : 0;
}

void rutaSilenciar() {
  if (!autenticado()) return;
  bool reactivar = server.hasArg("on") && server.arg("on") == "1";
  uint32_t restante = aplicarSilencio(reactivar, "TABLERO LOCAL");
  char buf[110];
  snprintf(buf, sizeof(buf),
    "{\"silenciada\":%d,\"restanteSeg\":%lu}",
    silenciada ? 1 : 0, (unsigned long)restante);
  server.send(200, "application/json", buf);
}

// =====================================================================
//  CLIENTE MQTT — Challenge #3
//
//  Contrato de tópicos (ver Wiki, C3 §4):
//    sabana/<nodo>/telemetria   ESP32 -> Gateway   QoS 0   cada 2 s
//    sabana/<nodo>/alerta       ESP32 -> Gateway   retenido, en cada cambio
//    sabana/<nodo>/estado       ESP32 -> Gateway   retenido + LWT
//    sabana/<nodo>/cmd/silenciar Gateway -> ESP32  QoS 1   "1" silencia, "0" reactiva
//    sabana/<nodo>/ack          ESP32 -> Gateway   confirmación del comando
//
//  El nodo no tiene reloj de pared: no fecha sus mensajes. Envía un
//  consecutivo (seq) y su tiempo de encendido (up); el Gateway pone la
//  marca de tiempo al recibir y usa seq para detectar pérdidas.
// =====================================================================
const char *NOMBRE_ALERTA[] = {"VERDE", "AMARILLA", "ROJA", "FALLA"};

// JSON no admite NaN: una lectura ausente viaja como null.
void numOnull(char *dst, size_t n, float v, uint8_t dec) {
  if (isnan(v) || isinf(v)) snprintf(dst, n, "null");
  else                      snprintf(dst, n, "%.*f", dec, v);
}

size_t construirTelemetria(char *buf, size_t n, const Muestra &m) {
  char nivel[12], caudal[12], evap[12];
  numOnull(nivel,  sizeof(nivel),  m.nivel,  2);
  numOnull(caudal, sizeof(caudal), m.caudal, 3);
  numOnull(evap,   sizeof(evap),   m.evap,   2);
  return snprintf(buf, n,
    "{\"id\":\"%s\",\"seq\":%lu,\"up\":%lu,"
    "\"nivel\":%s,\"temp\":%.1f,\"hr\":%.1f,\"presion\":%.1f,"
    "\"luz\":%.1f,\"caudal\":%s,\"evap\":%s,"
    "\"qc\":%u,\"cal\":%u,\"alerta\":\"%s\",\"sil\":%d,\"bmp\":%d,"
    "\"uA\":%.1f,\"uR\":%.1f}",
    NODO_ID, (unsigned long)m.seq, (unsigned long)(m.t / 1000),
    nivel, m.temp, m.hr, m.presion, m.luz, caudal, evap,
    m.qcFlag, m.calidad, NOMBRE_ALERTA[alertaActual],
    silenciada ? 1 : 0, bmpOk ? 1 : 0, umbralAmarillo, umbralRojo);
}

// Llega un comando desde el Gateway. Se ejecuta en el contexto de
// tareaMqtt (dentro de mqtt.loop()), por eso puede publicar el ack.
void alLlegarComando(char *topico, byte *carga, unsigned int largo) {
  char txt[16];
  unsigned int k = largo < sizeof(txt) - 1 ? largo : sizeof(txt) - 1;
  memcpy(txt, carga, k); txt[k] = 0;

  const char *accion = strrchr(topico, '/');
  accion = accion ? accion + 1 : topico;

  char ack[140];
  if (strcmp(accion, "silenciar") == 0) {
    // "1" silencia, "0" reactiva. Cualquier otra carga se rechaza:
    // un comando ambiguo sobre una alarma no se interpreta, se ignora.
    if (txt[0] != '0' && txt[0] != '1') {
      snprintf(ack, sizeof(ack),
        "{\"cmd\":\"silenciar\",\"ok\":0,\"error\":\"carga invalida\"}");
    } else {
      bool reactivar = (txt[0] == '0');
      uint32_t restante = aplicarSilencio(reactivar, "TABLERO GLOBAL");
      snprintf(ack, sizeof(ack),
        "{\"cmd\":\"silenciar\",\"ok\":1,\"silenciada\":%d,"
        "\"restanteSeg\":%lu,\"alerta\":\"%s\"}",
        silenciada ? 1 : 0, (unsigned long)restante,
        NOMBRE_ALERTA[alertaActual]);
    }
  } else {
    snprintf(ack, sizeof(ack),
      "{\"cmd\":\"%.20s\",\"ok\":0,\"error\":\"comando desconocido\"}", accion);
  }
  mqtt.publish(topicoAck, ack);
}

bool conectarMqtt() {
  if (mqHost[0] == 0) return false;
  // Hay red si el nodo está unido a la WLAN, o si alguien (la Raspberry)
  // está conectado al punto de acceso propio del nodo. Lo segundo es el
  // plan B cuando el router del sitio aísla a los clientes entre sí.
  if (WiFi.status() != WL_CONNECTED && WiFi.softAPgetStationNum() == 0) return false;
  mqtt.setServer(mqHost, mqttPuerto);
  // Última voluntad: si el nodo desaparece sin despedirse, el broker
  // publica "offline" por él. El Gateway lo registra como evento.
  bool ok = mqtt.connect(NODO_ID, mqUser, mqPass,
                         topicoEstado, 1, true, "offline");
  if (ok) {
    mqtt.publish(topicoEstado, "online", true);
    mqtt.subscribe(topicoCmd, 1);
    Serial.printf("[MQTT] Conectado a %s:%u\n", mqHost, mqttPuerto);
  } else {
    Serial.printf("[MQTT] Sin broker en %s (rc=%d). Reintento en %d s.\n",
                  mqHost, mqtt.state(), MQTT_REINTENTO_MS / 1000);
  }
  return ok;
}

// =====================================================================
//  TAREA DE TRANSMISIÓN — core 1, hilo independiente del principal
// =====================================================================
void tareaMqtt(void *param) {
  snprintf(topicoTelemetria, sizeof(topicoTelemetria), "%s/%s/telemetria", MQTT_BASE, NODO_ID);
  snprintf(topicoAlerta,     sizeof(topicoAlerta),     "%s/%s/alerta",     MQTT_BASE, NODO_ID);
  snprintf(topicoEstado,     sizeof(topicoEstado),     "%s/%s/estado",     MQTT_BASE, NODO_ID);
  snprintf(topicoAck,        sizeof(topicoAck),        "%s/%s/ack",        MQTT_BASE, NODO_ID);
  snprintf(topicoCmd,        sizeof(topicoCmd),        "%s/%s/cmd/#",      MQTT_BASE, NODO_ID);

  mqtt.setBufferSize(MQTT_BUFFER);       // el valor por defecto (256) no alcanza
  mqtt.setKeepAlive(MQTT_KEEPALIVE_S);
  mqtt.setSocketTimeout(3);              // un broker mudo no cuelga la tarea más de 3 s
  mqtt.setCallback(alLlegarComando);

  uint32_t tIntento = 0;
  int8_t   alertaPublicada = -1;         // fuerza la primera publicación
  int8_t   silencioPublicado = -1;
  static char buf[MQTT_BUFFER];

  for (;;) {
    // ---- Cambio de broker pedido desde el tablero local ----
    // loop() y esta tarea comparten el core 1: la sección crítica
    // garantiza que las cadenas no cambien mientras se copian.
    if (mqttReconfig) {
      portENTER_CRITICAL(&muxMqttCfg);
      strlcpy(mqHost, mqttHost.c_str(), sizeof(mqHost));
      strlcpy(mqUser, mqttUser.c_str(), sizeof(mqUser));
      strlcpy(mqPass, mqttPass.c_str(), sizeof(mqPass));
      mqttReconfig = false;
      portEXIT_CRITICAL(&muxMqttCfg);
      if (mqtt.connected()) mqtt.disconnect();
      tIntento = 0;
    }

    if (!mqtt.connected()) {
      mqttConectado = false;
      if (tIntento == 0 || millis() - tIntento > MQTT_REINTENTO_MS) {
        tIntento = millis();
        if (conectarMqtt()) { alertaPublicada = -1; silencioPublicado = -1; }
      }
    } else {
      mqttConectado = true;
      mqtt.loop();                       // atiende comandos y keepalive

      // ---- Telemetría: vacía lo que haya dejado la tarea de medición ----
      Muestra m;
      while (xQueueReceive(colaMqtt, &m, 0) == pdTRUE) {
        size_t n = construirTelemetria(buf, sizeof(buf), m);
        if (n < sizeof(buf) && mqtt.publish(topicoTelemetria, buf)) mqttPublicados++;
        else break;                      // se cayó a mitad: se reintenta al reconectar
      }

      // ---- Alerta: solo en la transición, retenida ----
      // Retenida para que un Gateway que arranque después conozca el
      // estado vigente sin esperar al siguiente cambio.
      int8_t a = (int8_t)alertaActual, s = silenciada ? 1 : 0;
      if (a != alertaPublicada || s != silencioPublicado) {
        snprintf(buf, sizeof(buf),
          "{\"id\":\"%s\",\"alerta\":\"%s\",\"codigo\":%d,\"sil\":%d,\"up\":%lu}",
          NODO_ID, NOMBRE_ALERTA[a], a, s, (unsigned long)(millis() / 1000));
        if (mqtt.publish(topicoAlerta, buf, true)) {
          alertaPublicada = a; silencioPublicado = s;
        }
      }
    }
    vTaskDelay(pdMS_TO_TICKS(50));       // cede el núcleo a loop()
  }
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
  mqttHost = nvs.getString("mqHost", MQTT_HOST_DEF);
  mqttUser = nvs.getString("mqUser", MQTT_USER_DEF);
  mqttPass = nvs.getString("mqPass", MQTT_PASS_DEF);
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
  colaMqtt     = xQueueCreate(MQTT_COLA_MUESTRAS, sizeof(Muestra));
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

  // --- Tarea de transmisión MQTT, hilo propio en el core 1 ---
  // Prioridad 1, por debajo de la medición (2): transmitir nunca le
  // quita CPU a medir. Comparte núcleo con loop() y cede con vTaskDelay.
  xTaskCreatePinnedToCore(tareaMqtt, "mqtt", 8192, NULL, 1, NULL, 1);
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
