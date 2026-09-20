-- ============================================================
-- Flood Risk Prediction System — PostgreSQL Schema
-- Study area: Bayelsa, Rivers, Delta, Cross River, Akwa Ibom
-- ============================================================

CREATE TABLE IF NOT EXISTS states (
    state_id    SERIAL PRIMARY KEY,
    name        VARCHAR(50) UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS lgas (
    lga_id          SERIAL PRIMARY KEY,
    state_id        INTEGER NOT NULL REFERENCES states(state_id),
    name            VARCHAR(100) NOT NULL,
    centroid_lat    NUMERIC(9,6) NOT NULL,
    centroid_lon    NUMERIC(9,6) NOT NULL,
    -- static terrain / land-cover attributes (computed once from SRTM + WorldCover)
    elevation_mean_m    NUMERIC(8,2),
    slope_mean_deg      NUMERIC(6,3),
    wetland_pct          NUMERIC(5,2),
    built_up_pct         NUMERIC(5,2),
    cropland_pct          NUMERIC(5,2),
    water_bodies_pct      NUMERIC(5,2),
    UNIQUE(state_id, name)
);

-- ---------- Raw staging tables (one per data source, Day 1-2) ----------

CREATE TABLE IF NOT EXISTS nasa_power_raw (
    id              SERIAL PRIMARY KEY,
    lga_id          INTEGER NOT NULL REFERENCES lgas(lga_id),
    obs_date        DATE NOT NULL,
    rainfall_mm     NUMERIC(8,3),
    temp_mean_c     NUMERIC(6,2),
    humidity_pct    NUMERIC(5,2),
    UNIQUE(lga_id, obs_date)
);

CREATE TABLE IF NOT EXISTS open_meteo_raw (
    id              SERIAL PRIMARY KEY,
    lga_id          INTEGER NOT NULL REFERENCES lgas(lga_id),
    obs_date        DATE NOT NULL,
    humidity_pct    NUMERIC(5,2),
    rainfall_mm     NUMERIC(8,3),
    UNIQUE(lga_id, obs_date)
);

CREATE TABLE IF NOT EXISTS grdc_discharge_raw (
    id              SERIAL PRIMARY KEY,
    station_name    VARCHAR(100) NOT NULL,   -- e.g. 'Niger at Onitsha', 'Benue at Makurdi'
    obs_date        DATE NOT NULL,
    discharge_cumecs NUMERIC(10,2),
    UNIQUE(station_name, obs_date)
);

CREATE TABLE IF NOT EXISTS flood_events_raw (
    id              SERIAL PRIMARY KEY,
    lga_id          INTEGER REFERENCES lgas(lga_id),   -- nullable, mapped later
    event_date      DATE NOT NULL,
    source          VARCHAR(50) DEFAULT 'Dartmouth Flood Observatory',
    severity        VARCHAR(20),
    raw_location    VARCHAR(200)
);

-- ---------- Unified analysis table (Day 3 output) ----------

CREATE TABLE IF NOT EXISTS lga_monthly_features (
    id                          SERIAL PRIMARY KEY,
    lga_id                      INTEGER NOT NULL REFERENCES lgas(lga_id),
    year                        INTEGER NOT NULL,
    month                       INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),

    rainfall_mm                 NUMERIC(8,3),
    temp_mean_c                 NUMERIC(6,2),
    humidity_pct                NUMERIC(5,2),
    river_discharge_cumecs      NUMERIC(10,2),

    -- engineered features
    rainfall_anomaly_index      NUMERIC(8,4),
    antecedent_precip_index     NUMERIC(8,4),
    normalised_discharge_ratio  NUMERIC(8,4),
    terrain_vulnerability_score NUMERIC(8,4),
    peak_season_flag            BOOLEAN,

    -- target
    flood_risk_label            VARCHAR(10),  -- 'Low' | 'Moderate' | 'High'

    UNIQUE(lga_id, year, month)
);

-- ---------- Model output (Day 4) ----------

CREATE TABLE IF NOT EXISTS predictions (
    id                  SERIAL PRIMARY KEY,
    lga_id              INTEGER NOT NULL REFERENCES lgas(lga_id),
    year                INTEGER NOT NULL,
    month               INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    predicted_class     VARCHAR(10) NOT NULL,
    prob_low            NUMERIC(5,4),
    prob_moderate       NUMERIC(5,4),
    prob_high           NUMERIC(5,4),
    model_version       VARCHAR(50) DEFAULT 'xgboost_v1',
    created_at          TIMESTAMP DEFAULT NOW(),
    UNIQUE(lga_id, year, month, model_version)
);

-- ---------- Helpful indexes ----------
CREATE INDEX IF NOT EXISTS idx_nasa_lga_date ON nasa_power_raw(lga_id, obs_date);
CREATE INDEX IF NOT EXISTS idx_meteo_lga_date ON open_meteo_raw(lga_id, obs_date);
CREATE INDEX IF NOT EXISTS idx_features_lga_ym ON lga_monthly_features(lga_id, year, month);
CREATE INDEX IF NOT EXISTS idx_predictions_lga_ym ON predictions(lga_id, year, month);
