-- Local analytical model. The portfolio repository owns deployed PostgreSQL DDL.
CREATE SCHEMA ucla_raw;
CREATE SCHEMA ucla_core;
CREATE SCHEMA ucla_mart;

CREATE TABLE ucla_raw.source_file (
    source_path VARCHAR PRIMARY KEY,
    file_kind VARCHAR NOT NULL,
    byte_count BIGINT NOT NULL,
    sha256 VARCHAR,
    row_count INTEGER
);

CREATE TABLE ucla_raw.document (
    source_path VARCHAR PRIMARY KEY REFERENCES ucla_raw.source_file(source_path),
    payload JSON NOT NULL
);

CREATE TABLE ucla_raw.record (
    source_path VARCHAR REFERENCES ucla_raw.source_file(source_path),
    row_number INTEGER,
    payload JSON NOT NULL,
    PRIMARY KEY (source_path, row_number)
);

CREATE TABLE ucla_core.dim_dataset (
    dataset_key INTEGER PRIMARY KEY,
    accession VARCHAR NOT NULL,
    dataset_name VARCHAR NOT NULL,
    license VARCHAR,
    dataset_doi VARCHAR,
    bids_version VARCHAR
);

CREATE TABLE ucla_core.dim_participant (
    participant_key INTEGER PRIMARY KEY,
    dataset_key INTEGER NOT NULL REFERENCES ucla_core.dim_dataset(dataset_key),
    participant_id VARCHAR NOT NULL UNIQUE,
    diagnosis VARCHAR NOT NULL,
    age INTEGER NOT NULL CHECK (age >= 0),
    gender_source_code VARCHAR NOT NULL,
    ghost_annotation VARCHAR
);

CREATE TABLE ucla_core.dim_instrument (
    instrument_key INTEGER PRIMARY KEY,
    instrument_code VARCHAR NOT NULL UNIQUE,
    instrument_name VARCHAR NOT NULL,
    term_url VARCHAR,
    source_path VARCHAR NOT NULL
);

CREATE TABLE ucla_core.dim_measure (
    measure_key INTEGER PRIMARY KEY,
    instrument_key INTEGER NOT NULL REFERENCES ucla_core.dim_instrument(instrument_key),
    variable_name VARCHAR NOT NULL,
    description VARCHAR,
    is_categorical BOOLEAN NOT NULL,
    is_derived BOOLEAN NOT NULL,
    metadata JSON NOT NULL,
    UNIQUE (instrument_key, variable_name)
);

CREATE TABLE ucla_core.dim_task (
    task_key INTEGER PRIMARY KEY,
    task_code VARCHAR NOT NULL UNIQUE,
    task_name VARCHAR NOT NULL,
    modality VARCHAR NOT NULL,
    metadata JSON NOT NULL
);

CREATE TABLE ucla_core.dim_scanner (
    scanner_key INTEGER PRIMARY KEY,
    serial_number VARCHAR NOT NULL UNIQUE
);

CREATE TABLE ucla_core.fact_phenotype_response (
    participant_key INTEGER REFERENCES ucla_core.dim_participant(participant_key),
    measure_key INTEGER REFERENCES ucla_core.dim_measure(measure_key),
    source_value VARCHAR NOT NULL,
    value_text VARCHAR,
    value_numeric DOUBLE PRECISION,
    category_label VARCHAR,
    is_missing BOOLEAN NOT NULL,
    source_path VARCHAR NOT NULL,
    source_row INTEGER NOT NULL,
    PRIMARY KEY (participant_key, measure_key),
    CHECK (NOT is_missing OR (value_text IS NULL AND value_numeric IS NULL))
);

CREATE TABLE ucla_core.fact_scan_availability (
    participant_key INTEGER REFERENCES ucla_core.dim_participant(participant_key),
    task_key INTEGER REFERENCES ucla_core.dim_task(task_key),
    scanner_key INTEGER REFERENCES ucla_core.dim_scanner(scanner_key),
    source_flag VARCHAR NOT NULL,
    dataset_listed BOOLEAN NOT NULL,
    local_file_present BOOLEAN NOT NULL,
    local_path VARCHAR,
    local_byte_count BIGINT,
    repetition_time_seconds DOUBLE PRECISION,
    scan_metadata JSON,
    PRIMARY KEY (participant_key, task_key),
    CHECK (NOT local_file_present OR local_path IS NOT NULL)
);

CREATE TABLE ucla_core.fact_task_event (
    event_key INTEGER PRIMARY KEY,
    participant_key INTEGER NOT NULL REFERENCES ucla_core.dim_participant(participant_key),
    task_key INTEGER NOT NULL REFERENCES ucla_core.dim_task(task_key),
    event_index INTEGER NOT NULL,
    onset_seconds DOUBLE PRECISION,
    duration_seconds DOUBLE PRECISION,
    trial_type VARCHAR,
    source_path VARCHAR NOT NULL,
    source_row INTEGER NOT NULL,
    UNIQUE (source_path, event_index)
);
