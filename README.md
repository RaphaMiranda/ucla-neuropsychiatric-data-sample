# UCLA integrated research warehouse

Data engineering project for the UCLA Consortium for Neuropsychiatric Phenomics LA5c dataset, OpenNeuro **ds000030**.

This repository owns the dataset, local dimensional model, ingestion pipeline, lineage, data-quality checks and portable data exports.

**The website, SQL playground, query API, final PostgreSQL schema and PostgreSQL deployment tooling belong to the sibling raphael-data-engineer repository.** This build does not generate website assets or PostgreSQL DDL.

The original BIDS files and original README are preserved. This folder arrived without Git metadata; no repository was initialized.

## Run locally

Python 3.10+ and DuckDB are the only runtime requirements.

~~~powershell
python -m pip install -r requirements.txt
python warehouse/build.py
python -m unittest discover -s tests -v
~~~

The default output is **build/warehouse/**:

| Artifact | Purpose |
| --- | --- |
| ucla.duckdb | Source lineage, local dimensional model and analytical marts |
| profile.json | Reconciled counts, quality findings, aggregates and source manifest |
| handoff/manifest.json | Versioned relation/column/key contract and CSV checksums |
| handoff/*.csv | Full raw and modeled table data for downstream consumers |
| handoff/profile.json | Profile accompanying the exported data |

Use --source and --output to select other input/output directories. The new default output keeps the older build/ucla.duckdb available to any existing database client. Close clients before replacing a database on Windows.

Builds construct new artifacts before replacing completed outputs. Source-validation failures preserve the previous outputs. Publication of multiple files is not one filesystem transaction.

## Model

The supplied files contain **272 participants**, **52 phenotype tables**, **1,983 variables**, **539,376 response cells**, **2,720 scan-inventory rows**, and **803 local task-event rows**. Seven local scan files belong to one participant; the inventory describes the larger cohort.

| Fact | One row represents |
| --- | --- |
| ucla_core.fact_phenotype_response | One participant and one source variable, including missing responses |
| ucla_core.fact_scan_availability | One participant and one of ten scan types |
| ucla_core.fact_task_event | One event record within a source file |

Shared dimensions cover dataset, participant, instrument, measure, task and scanner.

- Local model: warehouse/model.sql.
- Local analytical definitions: warehouse/marts.sql.
- Build and validation: warehouse/build.py.
- Model rationale: [docs/warehouse-design.md](docs/warehouse-design.md).

Aggregate each fact to the intended common grain before joining facts. Joining response, scan and event rows directly inflates counts.

## Inspect the local warehouse

~~~powershell
@'
import duckdb
with duckdb.connect("build/warehouse/ucla.duckdb", read_only=True) as con:
    print(con.sql("""
        SELECT diagnosis, task_code, participants,
               dataset_listed, local_files, listed_percent
        FROM ucla_mart.scan_coverage
        ORDER BY diagnosis, task_code
    """))
'@ | python -
~~~

## Portable handoff

The handoff is a data contract, not a database deployment. It includes the 12 raw/core tables as UTF-8 CSV with headers, quoted non-null values and an unquoted backslash-N null marker. The manifest identifies each table, column order, logical types, primary/foreign keys, row count and SHA-256 checksum. Five analytical views are described as metadata.

The consuming portfolio project owns its final PostgreSQL DDL and verifies this contract before importing. Run its importer from that repository:

~~~powershell
npm run ucla:import -- ../UCLA_Neuropsychiatric_data/build/warehouse/handoff
~~~

Website and PostgreSQL setup instructions live in raphael-data-engineer/docs/ucla/README.md. Dataset/image binaries remain here and are not copied into PostgreSQL.

## Source fidelity and quality

Missing cells remain explicit, numeric zero remains observed, and categorical codes retain their source values and dictionary metadata. The current profile reports **127,792 missing response cells** and **1,772 categorical values without dictionary labels**.

The dataset is open research data. Raw source records and modeled participant-level data are included in the handoff for educational use.

Source metadata declares **CC0** and DOI **10.18112/openneuro.ds000030.v1.0.0**. The original dataset description requests citation of the [dataset publication](https://www.nature.com/articles/sdata2016110). The original README and dataset_description.json remain authoritative for acquisition notes and study context.
