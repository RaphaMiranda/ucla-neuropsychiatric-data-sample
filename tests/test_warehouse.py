import csv
import hashlib
import json
from pathlib import Path
import shutil
import unittest
from uuid import uuid4

import duckdb

from warehouse.build import PROJECT, SCAN_TYPES, build, category_label


class WarehouseTest(unittest.TestCase):
    def setUp(self):
        self.work = PROJECT / ".tmp" / ("test-" + uuid4().hex)
        self.source = self.work / "source"
        self.output = self.work / "output"
        (self.source / "phenotype").mkdir(parents=True)
        (self.source / "dataset_description.json").write_text(json.dumps({
            "Name": "Fixture", "OpenFMRIAccessionNumber": "fixture", "License": "CC0",
        }), encoding="utf-8")
        fields = ["participant_id", "diagnosis", "age", "gender", "ScannerSerialNumber", *SCAN_TYPES]
        rows = [
            dict.fromkeys(fields, "n/a") | {"participant_id": "sub-01", "diagnosis": "CONTROL",
                "age": "30", "gender": "F", "ScannerSerialNumber": "35343.0", "rest": "1"},
            dict.fromkeys(fields, "n/a") | {"participant_id": "sub-02", "diagnosis": "ADHD",
                "age": "25", "gender": "M", "ScannerSerialNumber": "35426.0", "rest": "1"},
        ]
        self.write_tsv(self.source / "participants.tsv", fields, rows)
        self.phenotype = [
            {"participant_id": "sub-01", "asrs_score": "0", "coded": "1.0", "note": "line one\nline two\\N"},
            {"participant_id": "sub-02", "asrs_score": "n/a", "coded": "2", "note": "not collected"},
        ]
        self.write_phenotype()
        (self.source / "phenotype/asrs.json").write_text(json.dumps({
            "MeasurementToolMetadata": {"Description": "Fixture ASRS"},
            "asrs_score": {"Description": "Source score", "Derivative": True},
            "coded": {"Description": "Category", "Levels": {"1": "Yes", "2": "No"}},
            "note": {"Description": "Source text"},
        }), encoding="utf-8")
        event = self.source / "sub-01/func/sub-01_task-taskswitch_events.tsv"
        event.parent.mkdir(parents=True)
        self.write_tsv(event, ["CueOnset", "duration"], [{"CueOnset": "12", "duration": "2"}])

    def tearDown(self):
        self.assertEqual(self.work.parent, PROJECT / ".tmp")
        shutil.rmtree(self.work)

    @staticmethod
    def write_tsv(path, fields, rows):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)

    def write_phenotype(self):
        self.write_tsv(self.source / "phenotype/asrs.tsv",
                       ["participant_id", "asrs_score", "coded", "note"], self.phenotype)

    def test_build_reconciles_missing_zero_categories_and_event_scope(self):
        result = build(self.source, self.output)
        self.assertEqual(result["counts"]["responses"], 6)
        self.assertEqual(result["quality"]["missing_responses"], 1)
        self.assertEqual(result["quality"]["unmapped_category_values"], 0)
        self.assertEqual(result["quality"]["events_without_standard_onset"], 1)
        with duckdb.connect(str(self.output / "ucla.duckdb"), read_only=True) as con:
            self.assertEqual(con.execute("""
                SELECT value_numeric, is_missing FROM ucla_core.fact_phenotype_response
                WHERE source_value = '0'
            """).fetchone(), (0.0, False))
            self.assertEqual(con.execute("""
                SELECT category_label, value_numeric FROM ucla_core.fact_phenotype_response
                WHERE source_value = '1.0'
            """).fetchone(), ("Yes", None))
            self.assertEqual(con.execute("SELECT COUNT(*) FROM ucla_core.fact_scan_availability").fetchone()[0], 20)
            self.assertEqual(con.execute("""
                SELECT COUNT(*) FROM ucla_core.fact_scan_availability
                WHERE dataset_listed AND NOT local_file_present
            """).fetchone()[0], 2)
        handoff = self.output / "handoff"
        manifest = json.loads((handoff / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["format_version"], 1)
        response = next(row for row in manifest["relations"]
                        if row["name"] == "ucla_core.fact_phenotype_response")
        self.assertEqual(response["row_count"], 6)
        for relation in manifest["relations"]:
            if relation["kind"] == "table":
                path = handoff / relation["file"]
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), relation["sha256"])
        with duckdb.connect(":memory:") as con:
            rows = con.execute(
                "SELECT source_value FROM read_csv(?, all_varchar=true, nullstr='\\N')",
                [str(handoff / response["file"])]).fetchall()
        self.assertIn(("line one\nline two\\N",), rows)
        self.assertFalse((self.output / "site").exists())
        self.assertFalse((self.output / "ucla-postgres.sql").exists())

    def test_failed_refresh_preserves_previous_database(self):
        build(self.source, self.output)
        path = self.output / "ucla.duckdb"
        before = hashlib.sha256(path.read_bytes()).digest()
        self.phenotype[0]["participant_id"] = "sub-unknown"
        self.write_phenotype()
        with self.assertRaisesRegex(ValueError, "unknown participant"):
            build(self.source, self.output)
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_duplicate_participant_rejected(self):
        self.phenotype[1]["participant_id"] = "sub-01"
        self.write_phenotype()
        with self.assertRaisesRegex(ValueError, "duplicate participant"):
            build(self.source, self.output)

    def test_category_edges(self):
        self.assertEqual(category_label("0.0", {"0": "No"}), "No")
        self.assertIsNone(category_label("99.0", {"0": "No"}))


if __name__ == "__main__":
    unittest.main()
