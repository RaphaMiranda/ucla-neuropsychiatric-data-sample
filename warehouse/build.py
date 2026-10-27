"""Build the local UCLA warehouse, quality profile and portable data handoff."""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
from uuid import uuid4

import duckdb


PROJECT = Path(__file__).resolve().parents[1]
SCAN_TYPES = {
    "bart": ("Balloon Analog Risk Task", "func"),
    "bht": ("Breath Hold", "func"),
    "dwi": ("Diffusion-weighted imaging", "dwi"),
    "pamenc": ("Paired Associates Memory: encoding", "func"),
    "pamret": ("Paired Associates Memory: retrieval", "func"),
    "rest": ("Resting state", "func"),
    "scap": ("Spatial working memory", "func"),
    "stopsignal": ("Stop signal", "func"),
    "T1w": ("T1-weighted anatomy", "anat"),
    "taskswitch": ("Task switching", "func"),
}
MISSING = {"", "n/a"}
NULL_TOKEN = "__UCLA_NULL__"
TABLES = (
    "ucla_raw.source_file", "ucla_raw.document", "ucla_raw.record",
    "ucla_core.dim_dataset", "ucla_core.dim_participant",
    "ucla_core.dim_instrument", "ucla_core.dim_measure",
    "ucla_core.dim_task", "ucla_core.dim_scanner",
    "ucla_core.fact_phenotype_response",
    "ucla_core.fact_scan_availability", "ucla_core.fact_task_event",
)
MARTS = ("cohort_summary", "scan_coverage", "assessment_coverage",
         "metric_summary", "event_inventory")


def json_text(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def clean(value):
    return None if value.strip().lower() in MISSING else value.strip()


def number(value):
    if value is None:
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def category_label(value, levels):
    if value in levels:
        return levels[value]
    numeric = number(value)
    # TSV category codes use 1.0 where the JSON dictionary uses 1.
    return levels.get(str(int(numeric))) if numeric is not None and numeric.is_integer() else None


def tsv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = reader.fieldnames
        if not fields or len(fields) != len(set(fields)):
            raise ValueError(f"{path.name}: missing or duplicate column names")
        rows = list(reader)
    if any(None in row or None in row.values() for row in rows):
        raise ValueError(f"{path.name}: row width does not match its header")
    return fields, rows


class Loader:
    def __init__(self, source, connection, work):
        self.source = source
        self.connection = connection
        self.work = work
        self.files = []
        self.documents = []
        self.records = []
        self.registered = set()

    def register(self, path, kind, rows=None, payload=None):
        relative = path.relative_to(self.source).as_posix()
        if relative not in self.registered:
            digest = None if kind == "image" else hashlib.sha256(path.read_bytes()).hexdigest()
            self.files.append((relative, kind, path.stat().st_size, digest,
                               len(rows) if rows is not None else None))
            if payload is not None:
                self.documents.append((relative, json_text(payload)))
            if rows is not None:
                self.records.extend((relative, index, json_text(row))
                                    for index, row in enumerate(rows, 2))
            self.registered.add(relative)
        return relative

    def document(self, path):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        self.register(path, "json", payload=payload)
        return payload

    def tabular(self, path):
        fields, rows = tsv_rows(path)
        self.register(path, "tsv", rows=rows)
        return fields, rows

    def insert(self, table, rows):
        path = self.work / (table + ".csv")
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            for row in rows:
                if any(isinstance(value, str) and value == NULL_TOKEN for value in row):
                    raise ValueError("Source value collides with the internal CSV null marker")
                writer.writerow(NULL_TOKEN if value is None else value for value in row)
        if path.stat().st_size:
            self.connection.execute(
                f"COPY {table} FROM ? (FORMAT CSV, NULL '{NULL_TOKEN}')", [str(path)])


def require_participant(row, participant_keys, path):
    participant_id = row.get("participant_id")
    if participant_id not in participant_keys:
        raise ValueError(f"{path.name}: missing or unknown participant_id")
    return participant_keys[participant_id]


def populate(loader):
    con, source = loader.connection, loader.source
    description = loader.document(source / "dataset_description.json")
    loader.insert("ucla_core.dim_dataset", [(
        1, description["OpenFMRIAccessionNumber"], description["Name"],
        description.get("License"), description.get("DatasetDOI"),
        description.get("BIDSVersion"),
    )])
    fields, participants = loader.tabular(source / "participants.tsv")
    required = {"participant_id", "diagnosis", "age", "gender", "ScannerSerialNumber", *SCAN_TYPES}
    if not required.issubset(fields) or not participants:
        raise ValueError("participants.tsv is empty or lacks required cohort/scan columns")
    participants.sort(key=lambda row: row["participant_id"])
    participant_keys = {}
    participant_rows = []
    for key, row in enumerate(participants, 1):
        participant_id = row["participant_id"]
        if not re.fullmatch(r"sub-[A-Za-z0-9]+", participant_id):
            raise ValueError("participants.tsv: invalid BIDS participant_id")
        if participant_id in participant_keys:
            raise ValueError("participants.tsv: duplicate participant_id")
        if not clean(row["diagnosis"]) or not clean(row["gender"]):
            raise ValueError("participants.tsv: missing diagnosis or gender")
        participant_keys[participant_id] = key
        age = int(row["age"])
        # shortcut: this demo uses LA5c age bands; revise them for another cohort.
        if not 21 <= age <= 50:
            raise ValueError("Participant age lies outside the LA5c demo bands (21-50)")
        participant_rows.append((key, 1, participant_id, row["diagnosis"], age,
                                 row["gender"], clean(row.get("ghost_NoGhost", ""))))
    loader.insert("ucla_core.dim_participant", participant_rows)

    instruments, measures = [], []
    phenotype_paths = sorted((source / "phenotype").glob("*.tsv"))
    if not phenotype_paths:
        raise ValueError("No phenotype TSV sources found")
    response_count = 0
    for instrument_key, path in enumerate(phenotype_paths, 1):
        columns, rows = loader.tabular(path)
        dictionary = loader.document(path.with_suffix(".json"))
        tool = dictionary.get("MeasurementToolMetadata", {})
        source_path = path.relative_to(source).as_posix()
        instruments.append((instrument_key, path.stem, tool.get("Description") or path.stem,
                            tool.get("TermURL") or None, source_path))
        loader.insert("ucla_core.dim_instrument", [instruments[-1]])
        first_measure = len(measures)
        measure_map = {}
        for variable in columns:
            if variable == "participant_id":
                continue
            if variable not in dictionary:
                raise ValueError(f"{path.name}: undocumented variable {variable}")
            metadata = dictionary[variable]
            measure_key = len(measures) + 1
            measure_map[variable] = (measure_key, metadata)
            measures.append((measure_key, instrument_key, variable,
                             metadata.get("Description"), bool(metadata.get("Levels")),
                             metadata.get("Derivative") is True, json_text(metadata)))
        loader.insert("ucla_core.dim_measure", measures[first_measure:])
        seen = set()
        responses = []
        for row_number, row in enumerate(rows, 2):
            participant_key = require_participant(row, participant_keys, path)
            if participant_key in seen:
                raise ValueError(f"{path.name}: duplicate participant_id")
            seen.add(participant_key)
            for variable, (measure_key, metadata) in measure_map.items():
                raw_value = row[variable]
                value = clean(raw_value)
                levels = metadata.get("Levels", {})
                responses.append((
                    participant_key, measure_key, raw_value, value,
                    None if levels else number(value),
                    category_label(value, levels) if value is not None else None,
                    value is None, source_path, row_number,
                ))
        if seen != set(participant_keys.values()):
            raise ValueError(f"{path.name}: cohort coverage differs from participants.tsv")
        response_count += len(responses)
        loader.insert("ucla_core.fact_phenotype_response", responses)

    event_paths = sorted(source.glob("sub-*/**/*_events.tsv"))
    task_codes = set(SCAN_TYPES)
    event_sources = []
    for path in event_paths:
        match = re.fullmatch(r"(sub-[A-Za-z0-9]+)_task-([A-Za-z0-9]+)_events.tsv", path.name)
        if match is None:
            raise ValueError(f"Unsupported event filename: {path.name}")
        participant_id, task = match.groups()
        if participant_id not in participant_keys:
            raise ValueError(f"{path.name}: unknown participant")
        _, rows = loader.tabular(path)
        task_codes.add(task)
        event_sources.append((path, participant_id, task, rows))
    task_keys = {task: key for key, task in enumerate(sorted(task_codes), 1)}
    tasks = []
    for task, key in task_keys.items():
        name, modality = SCAN_TYPES.get(task, (task, "beh"))
        metadata_path = source / f"task-{task}_bold.json"
        metadata = loader.document(metadata_path) if metadata_path.exists() else {}
        tasks.append((key, task, name, modality, json_text(metadata)))
    loader.insert("ucla_core.dim_task", tasks)
    serials = sorted({row["ScannerSerialNumber"] for row in participants
                      if clean(row["ScannerSerialNumber"]) is not None})
    scanner_keys = {serial: key for key, serial in enumerate(serials, 1)}
    loader.insert("ucla_core.dim_scanner", [(key, serial) for serial, key in scanner_keys.items()])

    scans, matched_images = [], set()
    for row in participants:
        participant_id = row["participant_id"]
        for task, (_, modality) in SCAN_TYPES.items():
            flag = row[task]
            if flag != "1" and clean(flag) is not None:
                raise ValueError(f"Unsupported scan inventory flag: {flag!r}")
            suffix = f"task-{task}_bold" if modality == "func" else task
            image = source / participant_id / modality / f"{participant_id}_{suffix}.nii.gz"
            metadata_path = image.with_name(image.name.removesuffix(".nii.gz") + ".json")
            metadata = loader.document(metadata_path) if metadata_path.exists() else None
            exists = image.is_file()
            if exists:
                with image.open("rb") as handle:
                    if handle.read(2) != b"\x1f\x8b":
                        raise ValueError(f"{image.name}: not a local gzip image (annex pointer?)")
                loader.register(image, "image")
                matched_images.add(image)
            scans.append((
                participant_keys[participant_id], task_keys[task],
                scanner_keys.get(row["ScannerSerialNumber"]), flag, flag == "1", exists,
                image.relative_to(source).as_posix() if exists else None,
                image.stat().st_size if exists else None,
                number(str(metadata["RepetitionTime"])) if metadata and "RepetitionTime" in metadata else None,
                json_text(metadata) if metadata is not None else None,
            ))
    if set(source.glob("sub-*/**/*.nii.gz")) != matched_images:
        raise ValueError("Unmodeled local scan files found; extend scan grains before loading")
    loader.insert("ucla_core.fact_scan_availability", scans)

    events = []
    for path, participant_id, task, rows in event_sources:
        behavior_metadata = path.with_name(path.name.replace("_events.tsv", "_beh.json"))
        if behavior_metadata.exists():
            loader.document(behavior_metadata)
        source_path = path.relative_to(source).as_posix()
        for index, row in enumerate(rows, 1):
            onset = clean(row.get("onset", ""))
            duration = clean(row.get("duration", ""))
            if (onset is not None and number(onset) is None
                    or duration is not None and number(duration) is None):
                raise ValueError(f"{path.name}: nonnumeric event timing")
            events.append((len(events) + 1, participant_keys[participant_id],
                           task_keys[task], index, number(onset), number(duration),
                           clean(row.get("trial_type", "")), source_path, index + 1))
    loader.insert("ucla_core.fact_task_event", events)
    loader.insert("ucla_raw.source_file", loader.files)
    loader.insert("ucla_raw.document", loader.documents)
    loader.insert("ucla_raw.record", loader.records)
    return {"participants": len(participants), "instruments": len(instruments),
            "variables": len(measures), "responses": response_count,
            "local_scans": len(matched_images),
            "local_scan_participants": len({row[0] for row in scans if row[5]}),
            "events": len(events),
            "local_event_participants": len({row[1] for row in events})}


def query_rows(con, query):
    cursor = con.execute(query)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def validate(con, counts):
    actual = con.execute("SELECT COUNT(*) FROM ucla_core.fact_phenotype_response").fetchone()[0]
    if actual != counts["responses"] or actual != counts["participants"] * counts["variables"]:
        raise ValueError("Response count does not reconcile to participants x variables")
    scan_count = con.execute("SELECT COUNT(*) FROM ucla_core.fact_scan_availability").fetchone()[0]
    if scan_count != counts["participants"] * len(SCAN_TYPES):
        raise ValueError("Scan inventory does not reconcile to participants x scan types")
    issues = {
        "missing_responses": con.execute(
            "SELECT COUNT(*) FROM ucla_core.fact_phenotype_response WHERE is_missing").fetchone()[0],
        "unmapped_category_values": con.execute("""
            SELECT COUNT(*) FROM ucla_core.fact_phenotype_response f
            JOIN ucla_core.dim_measure m USING (measure_key)
            WHERE m.is_categorical AND NOT f.is_missing AND f.category_label IS NULL
        """).fetchone()[0],
        "events_without_standard_onset": con.execute("""
            SELECT COUNT(*) FROM ucla_core.fact_task_event WHERE onset_seconds IS NULL
        """).fetchone()[0],
        "local_scans_not_listed": con.execute("""
            SELECT COUNT(*) FROM ucla_core.fact_scan_availability
            WHERE local_file_present AND NOT dataset_listed
        """).fetchone()[0],
    }
    return issues


def export_handoff(con, path, profile):
    path.mkdir()
    relations = []
    for name in (*TABLES, *(f"ucla_mart.{mart}" for mart in MARTS)):
        schema, table = name.split(".")
        columns = query_rows(con, f"DESCRIBE {name}")
        constraints = con.execute("""
            SELECT constraint_type, constraint_column_names,
                   referenced_table, referenced_column_names
            FROM duckdb_constraints()
            WHERE schema_name = ? AND table_name = ?
        """, [schema, table]).fetchall()
        primary_key = next((row[1] for row in constraints if row[0] == "PRIMARY KEY"), [])
        relation = {
            "name": name, "kind": "table" if name in TABLES else "view",
            "row_count": con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0],
            "columns": [{"name": row["column_name"], "type": row["column_type"],
                         "nullable": row["null"] == "YES", "primary_key": row["column_name"] in primary_key}
                        for row in columns],
            "foreign_keys": [{"columns": row[1], "table": f"{schema}.{row[2]}",
                             "referenced_columns": row[3]}
                            for row in constraints if row[0] == "FOREIGN KEY"],
        }
        if name in TABLES:
            target = path / (name + ".csv")
            con.execute(
                f"COPY {name} TO ? (FORMAT CSV, HEADER, NULL '\\N', FORCE_QUOTE *)",
                [str(target)])
            relation["file"] = target.name
            relation["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        relations.append(relation)
    manifest = {key: profile[key] for key in ("generated_at", "dataset", "counts", "quality")}
    manifest.update({"format_version": 1, "relations": relations})
    (path / "manifest.json").write_text(json_text(manifest) + "\n", encoding="utf-8")
    (path / "profile.json").write_text(json_text(profile) + "\n", encoding="utf-8")


def build(source=PROJECT, output=PROJECT / "build/warehouse"):
    source, output = Path(source).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    schema_sql = (PROJECT / "warehouse/model.sql").read_text(encoding="utf-8")
    marts_sql = (PROJECT / "warehouse/marts.sql").read_text(encoding="utf-8")
    # Build beside the destination so a failed refresh preserves the previous database.
    work = output / ("ucla-build-" + uuid4().hex)
    # Inherit directory permissions; private temp-directory ACLs fail in the Windows sandbox.
    work.mkdir()
    try:
        database = work / "ucla.duckdb"
        con = duckdb.connect(str(database))
        try:
            con.execute(schema_sql)
            counts = populate(Loader(source, con, work))
            issues = validate(con, counts)
            con.execute(marts_sql)
            dataset = query_rows(con, "SELECT * FROM ucla_core.dim_dataset")[0]
            profile = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "dataset": {key: value for key, value in dataset.items() if key != "dataset_key"},
                "counts": counts, "quality": issues,
                "marts": {name: query_rows(con, f"SELECT * FROM ucla_mart.{name} ORDER BY ALL")
                          for name in MARTS},
            }
            profile["sources"] = query_rows(con, "SELECT * FROM ucla_raw.source_file ORDER BY source_path")
            export_handoff(con, work / "handoff", profile)
        finally:
            con.close()
        (work / "profile.json").write_text(json_text(profile) + "\n", encoding="utf-8")
        # Publish only complete artifacts. No source data files are modified.
        for name in ("ucla.duckdb", "profile.json"):
            (work / name).replace(output / name)
        shutil.copytree(work / "handoff", output / "handoff", dirs_exist_ok=True)
    finally:
        shutil.rmtree(work)
    return profile


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=PROJECT)
    parser.add_argument("--output", type=Path, default=PROJECT / "build/warehouse")
    args = parser.parse_args()
    result = build(args.source, args.output)
    print(json_text({"counts": result["counts"], "quality": result["quality"]}))
