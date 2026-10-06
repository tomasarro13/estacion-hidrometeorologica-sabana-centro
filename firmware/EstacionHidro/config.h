#pragma once
// =====================================================================
//  Estación hidrometeorológica — Challenge #2 IoT
//  Configuración central. Todo lo ajustable vive aquí.
//
//  Segunda entrega: HC-SR04, DHT22, BMP280, LDR, LCD I2C, zumbador,
//  semáforo de 3 LED. Alimentación por power bank.
//  Panel solar: trabajo futuro, no implementado.
// =====================================================================

// ---------- Pines ----------
#define PIN_TRIG      5    // HC-SR04 Trig (salida)
#define PIN_ECHO     18    // HC-SR04 Echo — ¡SIEMPRE por divisor 1k/2k!
#define PIN_DHT       4    // DHT22 Data, con pull-up 10k a 3V3
#define PIN_LDR      34    // ADC1 obligatorio (el ADC2 muere con WiFi activo)
#define PIN_ZUMBADOR 14    // Base del 2N2222 a través de 1k

// Semáforo de estado. Cada LED con su resistencia de 220 ohm.
#define PIN_LED_VERDE    25
#define PIN_LED_AMARILLO 26
#define PIN_LED_ROJO     27

#define I2C_SDA      21
#define I2C_SCL      22
#define DIR_LCD      0x27  // Confirmar con el escáner del diagnóstico
#define DIR_BMP      0x76  // Confirmar con el escáner del diagnóstico

// ---------- Red ----------
#define WIFI_SSID_DEF  "EstacionIoT"
#define WIFI_PASS_DEF  "cambiaesto2026"
// Punto de acceso propio del nodo. Siempre activo: garantiza acceso al
// tablero aunque el router aisle clientes o no exista infraestructura.
// La clave debe tener 8 caracteres como mínimo.
#define AP_SSID        "EstacionIoT"
#define AP_PASS        "agua2026"

#define WEB_USER_DEF   "operador"
#define WEB_PASS_DEF   "cambiaesto"

// ---------- Tiempos ----------
#define PERIODO_MUESTREO_MS   2000   // Lo fija el DHT22: no bajar de 2000
#define PERIODO_HISTORICO_MS  300000 // 5 min por registro
#define PERIODO_LCD_MS        4000   // Rotación de pantallas
#define N_MEDIANA             5      // Disparos del ultrasónico por muestra
#define TIMEOUT_ECHO_US       40000  // ~6,8 m ida y vuelta

// ---------- Histórico ----------
#define N_HISTORICO   288            // 288 x 5 min = 24 h

// ===================================================================
//  MEDIR EN EL BANCO Y ESCRIBIR AQUÍ
// ===================================================================

// Distancia de la cara del sensor al FONDO SECO del recipiente.
// Medir con regla, recipiente vacío. Verificar: con el recipiente
// vacío, el sensor debe reportar esta misma distancia.
#define ALTURA_SENSOR_CM_DEF  40.0f   // <-- MEDIR

// Nivel de trabajo considerado "lleno" en el banco.
#define NIVEL_REFERENCIA_CM   20.0f   // <-- MEDIR

// Rango válido. El superior sale de: ALTURA_SENSOR - 2 (mínimo del
// HC-SR04) - 3 (margen). El inferior es 0: no existe nivel negativo.
#define NIVEL_MIN_CM     0.0f
#define NIVEL_MAX_CM     35.0f        // <-- RECALCULAR

// Salto máximo creíble entre muestras.
//   área de la lámina [cm2] ; caudal máx [cm3/min]
//   descenso máx [cm/min] = caudal / área
//   cambio en 2 s = descenso / 30
//   este umbral = 10 a 30 veces ese valor
#define NIVEL_TASA_MAX   3.0f         // <-- RECALCULAR

// ---------- Umbrales de alerta (Threshold Level Method) ----------
// Percentiles de excedencia de la serie base:
//   amarillo = Q80 = PERCENTIL(serie; 0,20)
//   rojo     = Q90 = PERCENTIL(serie; 0,10)
// Calcular tras la corrida nocturna y escribir en cm.
#define UMBRAL_AMARILLO_CM   12.0f    // <-- CALCULAR (Q80)
#define UMBRAL_ROJO_CM        8.0f    // <-- CALCULAR (Q90)

#define TASA_DESCENSO_MMH     2.0f    // mm/h sostenidos = tendencia
#define UMBRAL_EVAP_MMDIA     5.0f    // Evaporación potencial alta

// ---------- Diseño de la alarma (ISA-18.2 / EEMUA 191) ----------
#define MUESTRAS_PERSISTENCIA 3       // 3 x 2 s = on-delay de 6 s
#define HISTERESIS_PCT        0.05f   // deadband 5 %: supera el ruido medido
#define TIEMPO_DESACTIVA_MS   600000  // 10 min = 2 registros históricos

// Silenciamiento desde el tablero (ISA-18.2, "shelving"). La norma
// exige que el silencio NO sea indefinido: expira por tiempo, al
// desaparecer la condición, o si la alarma escala de nivel.
#define SILENCIO_MAX_MS   900000   // 15 min

// Durante la calibración conviene el silencio: el LED sigue avisando.
// PONER EN true ANTES DE GRABAR EL VIDEO.
#define ALARMA_SONORA_FALLA   false

// ---------- Cruce de temperaturas DHT22 / BMP280 ----------
// El BMP280 se autocalienta y lee por encima del ambiente. El desfase
// es sistemático: se mide una vez con el diagnóstico y se descuenta.
#define BMP_AUTOCALENT_C      3.0f    // <-- LEER DEL DIAGNÓSTICO
#define DISCREPANCIA_TEMP_C   3.0f    // tolerancia alrededor del desfase

// Sensor congelado: muestras consecutivas con dispersión nula entre
// los N disparos. Un HC-SR04 sano siempre tiembla.
#define FLATLINE_MUESTRAS     30      // 30 x 2 s = 60 s

// ---------- Curva de gasto: Q = a * (h - h0)^b ----------
// Ajustar con tres aforos volumétricos.
#define GASTO_A_DEF   0.045f
#define GASTO_B_DEF   1.50f
#define GASTO_H0_DEF  2.0f            // cm, umbral del orificio

// Presión asumida cuando el BMP280 no está disponible.
// Valor típico de la Sabana de Bogotá (2560 m s.n.m.).
#define PRESION_SUPUESTA_HPA  750.0f

// ---------- Sitio (Sabana Centro, ~4,9 N / 2560 m) ----------
#define RA_MJ_M2_DIA  38.0f   // Radiación extraterrestre, casi constante en el trópico
#define VIENTO_U2     2.0f    // m/s asumidos por FAO-56 al no medir viento
#define ALBEDO        0.23f

// ---------- Validación física ----------
#define TEMP_MIN_C     (-10.0f)
#define TEMP_MAX_C       60.0f
#define HR_MIN_PCT        0.0f
#define HR_MAX_PCT      100.0f
#define PRES_MIN_HPA    600.0f
#define PRES_MAX_HPA   1100.0f

// ---------- Banderas de calidad (bitmask) ----------
#define Q_OK            0x00
#define Q_NIVEL_RANGO   0x01
#define Q_NIVEL_TASA    0x02
#define Q_NIVEL_TIMEOUT 0x04
#define Q_NIVEL_PEGADO  0x08
#define Q_DHT_FALLA     0x10
#define Q_BMP_FALLA     0x20
#define Q_SOL_FUERTE    0x40  // LDR alto: la T del DHT puede estar sesgada
#define Q_INCOHERENTE   0x80  // Descenso no explicado por evaporación + caudal

// ---------- Bandera primaria QARTOD ----------
// Esquema del Manual for the Use of Real-Time Oceanographic Data
// Quality Control Flags (U.S. IOOS / QARTOD).
#define QC_PASS      1   // dato bueno
#define QC_NOT_EVAL  2   // no evaluado
#define QC_SUSPECT   3   // dudoso: se muestra, no se usa en cálculos
#define QC_FAIL      4   // malo: se descarta
#define QC_MISSING   9   // ausente
