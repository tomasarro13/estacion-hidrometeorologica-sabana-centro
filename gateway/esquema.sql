-- =====================================================================
--  Gateway IoT · Challenge #3 · Esquema de la base de datos local
--  Motor: SQLite 3 (un archivo, sin servidor, incluido en Raspberry Pi OS)
--
--  Cinco tablas, una por responsabilidad:
--    lecturas      dato crudo, tal como llegó del nodo        (cada 2 s)
--    agregados     resultado del procesamiento local          (cada ventana)
--    eventos       bitácora: alertas, comandos, conexiones
--    salida        cola persistente hacia la nube (store-and-forward)
--    predicciones  resultados del módulo de IA
--
--  Todas las marcas de tiempo son época Unix en segundos (UTC).
-- =====================================================================

PRAGMA journal_mode = WAL;      -- lectores y escritor no se bloquean entre sí
PRAGMA synchronous  = NORMAL;   -- seguro ante corte de energía con WAL, sin
                                -- castigar la tarjeta SD con un fsync por fila

-- ---------------------------------------------------------------------
--  LECTURAS: una fila por mensaje de telemetría recibido.
--  Se guarda TODO lo que llega con formato correcto, incluso lo que el
--  nodo marcó como dudoso o malo (qc 3, 4, 9): descartar es decisión
--  del procesamiento, no del almacenamiento.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lecturas (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    nodo     TEXT    NOT NULL,
    ts       REAL    NOT NULL,   -- instante del dato
    ts_rx    REAL    NOT NULL,   -- instante de recepción en el Gateway
    seq      INTEGER NOT NULL,   -- consecutivo del nodo: detecta pérdidas
    up       INTEGER,            -- segundos desde el arranque del nodo
    nivel    REAL,               -- cm sobre el fondo (NULL = sin eco)
    temp     REAL,               -- °C
    hr       REAL,               -- %
    presion  REAL,               -- hPa
    luz      REAL,               -- 0-100 %
    caudal   REAL,               -- L/min (derivada)
    evap     REAL,               -- mm/día (derivada, FAO-56)
    qc       INTEGER NOT NULL,   -- bandera QARTOD: 1, 2, 3, 4, 9
    cal      INTEGER NOT NULL,   -- máscara de pruebas fallidas
    alerta   TEXT    NOT NULL,   -- VERDE | AMARILLA | ROJA | FALLA
    sil      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_lecturas_nodo_ts ON lecturas (nodo, ts);

-- ---------------------------------------------------------------------
--  AGREGADOS: una fila por ventana cerrada. Es lo que viaja a la nube
--  y lo que consulta el módulo de IA.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agregados (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nodo        TEXT    NOT NULL,
    ts_ini      REAL    NOT NULL,
    ts_fin      REAL    NOT NULL,
    n           INTEGER NOT NULL,   -- mensajes recibidos en la ventana
    n_validas   INTEGER NOT NULL,   -- con nivel utilizable (qc = 1)
    perdidas    INTEGER NOT NULL,   -- huecos en el consecutivo
    nivel_med   REAL,  nivel_min REAL,  nivel_max REAL,  nivel_std REAL,
    temp_med    REAL,  hr_med    REAL,  presion_med REAL, luz_med REAL,
    caudal_med  REAL,  evap_med  REAL,
    tasa_mmh    REAL,               -- pendiente del nivel en la ventana
    qc_peor     INTEGER NOT NULL,   -- peor bandera QARTOD observada
    alerta      TEXT    NOT NULL,   -- alerta más grave de la ventana
    UNIQUE (nodo, ts_ini)
);
CREATE INDEX IF NOT EXISTS ix_agregados_nodo_ts ON agregados (nodo, ts_ini);

-- ---------------------------------------------------------------------
--  EVENTOS: todo lo que no es una medición.
--  tipo: ALERTA | ESTADO_NODO | COMANDO | ACK | RECHAZO | REINICIO_NODO
--        | NUBE | GATEWAY
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS eventos (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL    NOT NULL,
    nodo     TEXT,
    tipo     TEXT    NOT NULL,
    detalle  TEXT
);
CREATE INDEX IF NOT EXISTS ix_eventos_ts ON eventos (ts);

-- ---------------------------------------------------------------------
--  SALIDA: bandeja de salida hacia la plataforma en la nube.
--  Nada se publica directamente: primero se escribe aquí y un hilo
--  aparte lo envía. Si no hay Internet las filas esperan con
--  enviado = 0; al volver la conexión salen en orden. Es el patrón
--  "transactional outbox": el dato existe en disco ANTES de intentarse
--  su envío, así que una caída de red o de energía no lo pierde.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS salida (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_creado   REAL    NOT NULL,
    topico      TEXT    NOT NULL,
    carga       TEXT    NOT NULL,     -- JSON listo para publicar
    prioridad   INTEGER NOT NULL DEFAULT 0,   -- 1 = alertas, salen primero
    enviado     INTEGER NOT NULL DEFAULT 0,
    ts_enviado  REAL
);
CREATE INDEX IF NOT EXISTS ix_salida_pend ON salida (enviado, prioridad DESC, id);

-- ---------------------------------------------------------------------
--  PREDICCIONES: una fila por corrida del módulo de IA.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS predicciones (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              REAL    NOT NULL,   -- instante del último dato usado
    nodo            TEXT    NOT NULL,
    clasificacion   TEXT    NOT NULL,
    tasa_mmh        REAL,               -- tendencia del nivel (negativa = baja)
    evap_mmh        REAL,               -- pérdida esperada por evaporación
    exceso_mmh      REAL,               -- descenso que el clima no explica
    horas_amarillo  REAL,               -- horas estimadas hasta el umbral Q80
    horas_rojo      REAL,               -- horas estimadas hasta el umbral Q90
    prioridad       TEXT,
    recomendacion   TEXT,
    origen_texto    TEXT,               -- 'reglas' o nombre del modelo generativo
    detalle         TEXT                -- JSON completo del análisis
);
CREATE INDEX IF NOT EXISTS ix_pred_nodo_ts ON predicciones (nodo, ts);
