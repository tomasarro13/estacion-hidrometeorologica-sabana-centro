// =====================================================================
//  DIAGNÓSTICO — Estación hidrometeorológica
//
//  Sketch independiente. Verifica cada sensor por separado, mide la
//  dispersión del ultrasónico y entrega los valores que hay que
//  trasladar a config.h del sketch principal.
//
//  Subir este archivo SOLO, en su propia carpeta llamada "Diagnostico".
//  Monitor serie a 115200.
// =====================================================================
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <Adafruit_BMP280.h>
#include <DHT.h>

// ---------- Ajustar si cambiaron los pines ----------
#define PIN_TRIG      5
#define PIN_ECHO     18
#define PIN_DHT       4
#define PIN_LDR      35
#define PIN_ZUMBADOR 25
#define PIN_LED      26
#define I2C_SDA      21
#define I2C_SCL      22

DHT dht(PIN_DHT, DHT22);
Adafruit_BMP280 bmp;
LiquidCrystal_I2C *lcd = nullptr;

uint8_t dirLcd = 0, dirBmp = 0;

// ---------------------------------------------------------------- utilidades
void titulo(const char *t) {
  Serial.println();
  Serial.println("======================================================");
  Serial.printf ("  %s\n", t);
  Serial.println("======================================================");
}

void veredicto(const char *quien, bool ok, const char *detalle) {
  Serial.printf("  [%s] %-12s  %s\n", ok ? " OK " : "FALLA", quien, detalle);
}

// ---------------------------------------------------------------- I2C
void escanearI2C() {
  titulo("1. Escaneo del bus I2C");
  uint8_t hallados = 0;
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      Serial.printf("  Dispositivo en 0x%02X", a);
      if (a == 0x27 || a == 0x3F) { Serial.print("  <- LCD");    dirLcd = a; }
      if (a == 0x76 || a == 0x77) { Serial.print("  <- BMP280"); dirBmp = a; }
      Serial.println();
      hallados++;
    }
  }
  if (!hallados) {
    Serial.println("  NADA EN EL BUS.");
    Serial.println("  Revisar: SDA en GPIO21, SCL en GPIO22, alimentacion y GND comun.");
  }
  Serial.printf("\n  >> config.h:  #define DIR_LCD  0x%02X\n", dirLcd ? dirLcd : 0x27);
  Serial.printf("  >> config.h:  #define DIR_BMP  0x%02X\n", dirBmp ? dirBmp : 0x76);
}

// ---------------------------------------------------------------- LCD
void probarLcd() {
  titulo("2. LCD 16x2");
  if (!dirLcd) { veredicto("LCD", false, "no aparecio en el bus I2C"); return; }
  lcd = new LiquidCrystal_I2C(dirLcd, 16, 2);
  lcd->init(); lcd->backlight();
  lcd->setCursor(0, 0); lcd->print("0123456789ABCDEF");
  lcd->setCursor(0, 1); lcd->print("Diagnostico  OK ");
  veredicto("LCD", true, "escribiendo patron de prueba");
  Serial.println("  Mirar la pantalla: deben verse 16 caracteres por linea,");
  Serial.println("  sin segmentos apagados. Si esta en blanco, girar el");
  Serial.println("  potenciometro azul del adaptador (contraste).");
}

// ---------------------------------------------------------------- DHT22
float probarDht(float *tOut, float *hOut) {
  titulo("3. DHT22  (temperatura y humedad)");
  float t = NAN, h = NAN;
  for (uint8_t i = 0; i < 3 && (isnan(t) || isnan(h)); i++) {
    t = dht.readTemperature();
    h = dht.readHumidity();
    if (isnan(t) || isnan(h)) { Serial.println("  reintentando..."); delay(2200); }
  }
  if (isnan(t) || isnan(h)) {
    veredicto("DHT22", false, "sin lectura valida");
    Serial.println("  Revisar: resistencia pull-up de 10k entre Data y 3V3,");
    Serial.println("  alimentacion a 3V3 y GND comun.");
    *tOut = NAN; *hOut = NAN;
    return NAN;
  }
  Serial.printf("  Temperatura : %.1f C\n", t);
  Serial.printf("  Humedad     : %.1f %%\n", h);
  bool rangoOk = (t > -10 && t < 60 && h >= 0 && h <= 100);
  veredicto("DHT22", rangoOk, rangoOk ? "valores en rango fisico" : "valores imposibles");
  Serial.println("\n  PRUEBA MANUAL: soplar sobre el sensor.");
  Serial.println("  La humedad debe subir de 90 % en segundos y bajar en ~1 min.");
  *tOut = t; *hOut = h;
  return t;
}

// ---------------------------------------------------------------- BMP280
void probarBmp(float tDht) {
  titulo("4. BMP280  (presion atmosferica)");
  if (!dirBmp) { veredicto("BMP280", false, "no aparecio en el bus I2C"); return; }
  if (!bmp.begin(dirBmp)) { veredicto("BMP280", false, "begin() fallo"); return; }

  bmp.setSampling(Adafruit_BMP280::MODE_NORMAL,
                  Adafruit_BMP280::SAMPLING_X2,
                  Adafruit_BMP280::SAMPLING_X16,
                  Adafruit_BMP280::FILTER_X16,
                  Adafruit_BMP280::STANDBY_MS_500);
  delay(300);

  float p  = bmp.readPressure() / 100.0f;
  float tb = bmp.readTemperature();
  Serial.printf("  Presion      : %.1f hPa\n", p);
  Serial.printf("  Temp BMP280  : %.1f C\n", tb);

  bool pOk = (p > 600 && p < 1100);
  veredicto("BMP280", pOk, pOk ? "presion en rango" : "presion fuera de rango");

  if (p > 980) {
    Serial.println("\n  AVISO: la presion parece de nivel del mar.");
    Serial.println("  En la Sabana de Bogota (2560 m) debe rondar 745-760 hPa.");
    Serial.println("  Si marca ~1013, la libreria esta convirtiendo a nivel del mar.");
  }

  // ---- Esto es lo que resuelve el falso "FALLA" ----
  if (!isnan(tDht)) {
    float delta = tb - tDht;
    Serial.println("\n  --- AUTOCALENTAMIENTO DEL BMP280 ---");
    Serial.printf("  Temp BMP - Temp DHT = %+.1f C\n", delta);
    Serial.println("  El BMP280 se autocalienta y suele leer por encima del");
    Serial.println("  ambiente. Si ademas va dentro de la caja, la diferencia");
    Serial.println("  crece. Esto NO es una falla: es un desfase sistematico.");
    Serial.printf("\n  >> config.h:  #define BMP_AUTOCALENT_C  %.1ff\n", delta);
    Serial.println("  (dejar el sistema estable 10 min antes de tomar este valor)");
  }
}

// ---------------------------------------------------------------- LDR
void probarLdr() {
  titulo("5. LDR  (luz)");
  analogSetPinAttenuation(PIN_LDR, ADC_11db);

  Serial.println("  Dejar el sensor como esta. Midiendo 3 s...");
  uint32_t acc = 0; uint16_t mn = 4095, mx = 0;
  for (uint8_t i = 0; i < 60; i++) {
    uint16_t v = analogRead(PIN_LDR);
    acc += v; if (v < mn) mn = v; if (v > mx) mx = v;
    delay(50);
  }
  uint16_t prom = acc / 60;
  Serial.printf("  Lectura cruda: media %u, min %u, max %u (rango 0-4095)\n", prom, mn, mx);
  Serial.printf("  Indice       : %.1f %%\n", prom * 100.0f / 4095.0f);

  bool vivo = (mx - mn) > 2 || (prom > 20 && prom < 4075);
  veredicto("LDR", vivo, vivo ? "responde" : "valor pegado en un extremo");

  if (prom < 20)  Serial.println("  AVISO: casi cero. Revisar el divisor resistivo.");
  if (prom > 4075) Serial.println("  AVISO: saturado. Revisar el divisor resistivo.");

  Serial.println("\n  PRUEBA MANUAL: tapar el sensor con la mano 3 s y destapar.");
  Serial.println("  Anotar el valor tapado y el valor a plena luz: esos dos");
  Serial.println("  extremos son la calibracion relativa del LDR.");
  Serial.println("  RECORDAR: el LDR debe estar en GPIO 32-39 (ADC1).");
  Serial.println("  En ADC2 deja de funcionar cuando se enciende el WiFi.");
}

// ---------------------------------------------------------------- HC-SR04
float disparo(float tAire) {
  digitalWrite(PIN_TRIG, LOW);  delayMicroseconds(4);
  digitalWrite(PIN_TRIG, HIGH); delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  uint32_t dur = pulseIn(PIN_ECHO, HIGH, 40000);   // bloqueante: solo en diagnostico
  if (dur == 0) return NAN;
  float vCmUs = (331.3f + 0.606f * tAire) / 10000.0f;
  return (dur * vCmUs) / 2.0f;
}

void probarUltrasonico(float tAire) {
  titulo("6. HC-SR04  (nivel)");
  if (isnan(tAire)) { tAire = 20.0f; Serial.println("  (sin DHT: asumiendo 20 C)"); }

  Serial.println("  Apuntar el sensor a una superficie fija y NO moverlo.");
  Serial.println("  Midiendo 20 disparos...\n");

  float v[20]; uint8_t n = 0, fallos = 0;
  for (uint8_t i = 0; i < 20; i++) {
    float d = disparo(tAire);
    if (isnan(d)) { fallos++; Serial.printf("  %2d: SIN ECO\n", i + 1); }
    else          { v[n++] = d; Serial.printf("  %2d: %6.2f cm\n", i + 1, d); }
    delay(70);
  }

  if (n < 2) {
    veredicto("HC-SR04", false, "sin ecos utiles");
    Serial.println("\n  Revisar en este orden:");
    Serial.println("   1. Divisor 1k/2k en Echo. Medir el nodo: debe dar ~3.3 V");
    Serial.println("   2. Alimentacion del sensor a 5V (VIN), no a 3V3");
    Serial.println("   3. GND comun entre sensor y ESP32");
    Serial.println("   4. Sensor perpendicular a la superficie, sin obstaculos");
    return;
  }

  float suma = 0, mn = v[0], mx = v[0];
  for (uint8_t i = 0; i < n; i++) { suma += v[i]; if (v[i] < mn) mn = v[i]; if (v[i] > mx) mx = v[i]; }
  float media = suma / n;
  float var = 0;
  for (uint8_t i = 0; i < n; i++) var += (v[i] - media) * (v[i] - media);
  float sd = sqrtf(var / n);
  float disp = mx - mn;

  Serial.println("\n  --- ESTADISTICA ---");
  Serial.printf("  Lecturas validas : %u de 20  (%u sin eco)\n", n, fallos);
  Serial.printf("  Media            : %.2f cm\n", media);
  Serial.printf("  Minimo / Maximo  : %.2f / %.2f cm\n", mn, mx);
  Serial.printf("  Dispersion       : %.2f cm\n", disp);
  Serial.printf("  Desv. estandar   : %.3f cm\n", sd);

  veredicto("HC-SR04", fallos <= 2, fallos <= 2 ? "ecos estables" : "demasiados fallos de eco");

  Serial.println("\n  --- LECTURA DE LOS RESULTADOS ---");
  if (disp < 0.01f) {
    Serial.println("  DISPERSION CERO. Un HC-SR04 sano siempre tiembla entre");
    Serial.println("  disparos. Dispersion exactamente cero sugiere salida");
    Serial.println("  congelada. Revisar el cableado del Echo.");
  } else if (disp > 3.0f) {
    Serial.println("  DISPERSION ALTA. Probables ecos de las paredes del");
    Serial.println("  recipiente. Centrar el sensor y verificar que este");
    Serial.println("  perfectamente vertical. El cono abre unos 15 grados.");
  } else {
    Serial.println("  Dispersion normal. Este numero es el RUIDO DE LA SENAL,");
    Serial.println("  y es el que justifica la histeresis de la alarma:");
    Serial.println("  el deadband debe ser mayor que este valor.");
  }

  Serial.println("\n  --- VALORES PARA config.h ---");
  Serial.println("  Medir con regla la distancia del sensor al FONDO SECO");
  Serial.println("  del recipiente y escribirla en ALTURA_SENSOR_CM_DEF.");
  Serial.printf("  Ahora mismo el sensor lee %.2f cm hasta la superficie.\n", media);
  Serial.printf("  Si el fondo seco esta a H cm, el nivel actual es H - %.2f\n", media);
}

// ---------------------------------------------------------------- salidas
void probarSalidas() {
  titulo("7. Zumbador y LED");
  Serial.println("  Tres pulsos cortos (patron de falla de sensor)...");
  for (uint8_t i = 0; i < 3; i++) {
    digitalWrite(PIN_ZUMBADOR, HIGH); digitalWrite(PIN_LED, HIGH); delay(150);
    digitalWrite(PIN_ZUMBADOR, LOW);  digitalWrite(PIN_LED, LOW);  delay(150);
  }
  delay(600);
  Serial.println("  Tono continuo 1 s (patron de nivel critico)...");
  digitalWrite(PIN_ZUMBADOR, HIGH); digitalWrite(PIN_LED, HIGH);
  delay(1000);
  digitalWrite(PIN_ZUMBADOR, LOW);  digitalWrite(PIN_LED, LOW);

  Serial.println("\n  Se escucharon los dos patrones?");
  Serial.println("  Si el ESP32 se reinicio al sonar: el zumbador esta");
  Serial.println("  conectado directo al GPIO. Debe ir por el transistor");
  Serial.println("  2N2222 con base a 1k y diodo 1N4148 en paralelo.");
}

// ---------------------------------------------------------------- resumen
void resumen() {
  titulo("RESUMEN — QUE LLEVAR A config.h");
  Serial.println();
  Serial.println("  MEDIR CON REGLA Y ESCRIBIR A MANO:");
  Serial.println("   ALTURA_SENSOR_CM_DEF   distancia sensor -> fondo seco");
  Serial.println("   NIVEL_REFERENCIA_CM    nivel 'lleno' del banco");
  Serial.println("   NIVEL_MAX_CM           = ALTURA_SENSOR - 2 - margen");
  Serial.println();
  Serial.println("  LEER DE ESTE DIAGNOSTICO:");
  Serial.println("   DIR_LCD, DIR_BMP       del escaneo I2C");
  Serial.println("   BMP_AUTOCALENT_C       del delta BMP-DHT");
  Serial.println("   Dispersion del HC-SR04 -> justifica la histeresis");
  Serial.println();
  Serial.println("  CALCULAR CON EL BANCO:");
  Serial.println("   NIVEL_TASA_MAX         caudal maximo / area de la lamina");
  Serial.println("   GASTO_A, GASTO_B       aforo volumetrico (3 puntos)");
  Serial.println();
  Serial.println("======================================================");
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  pinMode(PIN_TRIG, OUTPUT);  digitalWrite(PIN_TRIG, LOW);
  pinMode(PIN_ECHO, INPUT);
  pinMode(PIN_ZUMBADOR, OUTPUT); digitalWrite(PIN_ZUMBADOR, LOW);
  pinMode(PIN_LED, OUTPUT);      digitalWrite(PIN_LED, LOW);

  Wire.begin(I2C_SDA, I2C_SCL);
  dht.begin();
  delay(2200);                       // el DHT22 necesita este arranque

  Serial.println("\n\n#####################################################");
  Serial.println("#   DIAGNOSTICO DE LA ESTACION HIDROMETEOROLOGICA   #");
  Serial.println("#####################################################");

  float t = NAN, h = NAN;
  escanearI2C();
  probarLcd();
  probarDht(&t, &h);
  probarBmp(t);
  probarLdr();
  probarUltrasonico(t);
  probarSalidas();
  resumen();

  Serial.println("\nAhora el sketch repite solo la lectura de nivel cada 2 s");
  Serial.println("para que puedas calibrar contra la regla.\n");
  Serial.println("  distancia(cm)   nivel(cm, usando ALTURA=40)");
}

void loop() {
  float t = dht.readTemperature();
  if (isnan(t)) t = 20.0f;
  float d = disparo(t);
  if (isnan(d)) Serial.println("     SIN ECO");
  else          Serial.printf("     %6.2f          %6.2f\n", d, 40.0f - d);
  delay(2000);
}
