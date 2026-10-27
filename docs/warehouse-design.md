# Warehouse model and scope

## Decisions

Build a local DuckDB warehouse from the supplied BIDS files. Export a versioned CSV handoff for downstream consumers. Website code and the final PostgreSQL schema are owned by the separate raphael-data-engineer repository.

DuckDB is already installed locally. This dataset fits a single analytical process. The local model lives in warehouse/model.sql and analytical definitions in warehouse/marts.sql. The data build has no dependency on the portfolio, a web API or a PostgreSQL server.

## Source inventory

The 2026-10-27 local profile found:

| Source | Grain | Scope |
| --- | --- | --- |
| participants.tsv | Participant | 272 participants; diagnosis, age, gender code, scanner and inventory flags |
| phenotype/*.tsv | Participant within one source table | 52 tables, each containing the same 272 IDs |
| phenotype/*.json | Variable definition / instrument | All 1,983 phenotype variable columns have dictionary entries |
| task-*_bold.json | Imaging task | Shared acquisition/task descriptions |
| sub-10159/{anat,dwi,func} | Local scan file | Seven NIfTI gzip files, plus sidecars |
| sub-10159/{beh,func}/*_events.tsv | Task event | Five files, 803 event rows |
| dataset_description.json | Dataset | Accession, license, DOI, authorship and BIDS version |

The derivatives directory is empty. There are no acquired dates or longitudinal visits modeled in these tables. This initial model uses the snapshot's participant-level observation grain and does not invent a date dimension, visit history or clinical outcomes.

The downloaded participant table contains CONTROL 130, SCHZ 50, BIPOLAR 49 and ADHD 43. These observed counts take precedence over recruitment counts in the narrative dataset description.

## Layers and lineage

1. **ucla_raw** keeps the source-file manifest, complete JSON documents, and each original TSV record as JSON with its file path and logical source-record number.
2. **ucla_core** conforms participant identities and variable dictionaries into dimensions and three separate facts.
3. **ucla_mart** holds cohort composition, scan coverage, phenotype completeness, selected source-score summaries and local event inventory.
4. **Portable handoff** exports raw/core table CSVs plus a checksummed manifest, relation/column/key metadata and a quality profile. It includes published row-level data.

Source-record numbers count logical TSV records, with the header as row 1; quoted multiline fields can span physical lines. SHA-256 hashes cover ingested JSON and TSV bytes. Image files have path/size provenance and a gzip-signature check, not full image hashes or NIfTI validation. bval/bvec files and image voxel arrays remain source assets outside this warehouse.

## Conformed dimensions

| Dimension | Natural key | Attributes |
| --- | --- | --- |
| dim_dataset | Source accession | Name, license, DOI, BIDS version |
| dim_participant | BIDS participant_id in this snapshot | Study group, age, original gender code, ghost annotation, dataset key |
| dim_instrument | Phenotype filename stem | Source name, dictionary name, term URL, source path |
| dim_measure | Instrument + source variable name | Description, categorical/derived flags, complete dictionary metadata |
| dim_task | BIDS task / scan-type code | Name, modality, shared task metadata |
| dim_scanner | Original scanner serial string | Source serial without lossy integer conversion |

Integer surrogate keys are assigned in sorted source order for reproducible rebuilds of this snapshot. They are not permanent IDs across changing source inventories; rebuild the whole snapshot together. Extending to multiple datasets or longitudinal visits requires widening the natural keys and fact grains.

~~~mermaid
erDiagram
    dim_dataset ||--o{ dim_participant : scopes
    dim_participant ||--o{ fact_phenotype_response : responds
    dim_instrument ||--o{ dim_measure : defines
    dim_measure ||--o{ fact_phenotype_response : describes
    dim_participant ||--o{ fact_scan_availability : has_inventory
    dim_task ||--o{ fact_scan_availability : classifies
    dim_scanner ||--o{ fact_scan_availability : identifies
    dim_participant ||--o{ fact_task_event : performs
    dim_task ||--o{ fact_task_event : classifies
~~~

## Fact grains and analytical rules

**fact_phenotype_response** contains one cell per participant and variable: 272 × 1,983 = 539,376 rows. Missing cells are retained so completeness has an explicit denominator. source_value remains unchanged; cleaned value_text and value_numeric are nullable. Codes with dictionary Levels are categorical and are never turned into numeric measures. Decimal-form TSV codes such as 1.0 can map to dictionary key "1" while retaining their original source value.

An empty string or n/a is missing. Zero is observed, including source scores of zero. Other text is preserved rather than silently recoded. Undocumented sentinel codes or out-of-range clinical scores are not cleaned by guesswork.

**fact_scan_availability** contains 272 × 10 = 2,720 rows. The ten scan types include anatomy and diffusion as well as functional tasks. dataset_listed is true only for the source flag "1". n/a means unlisted in this source inventory; it does not prove that a scan never occurred. local_file_present independently describes this filesystem. The source scanner serial and optional local sidecar metadata are retained.

**fact_task_event** contains one row per logical event record, keyed by source file and event index. Behavioral stop-signal training is separate from the in-scanner stop-signal task. Standard onset and duration are extracted where supplied; complete task-specific fields remain in the corresponding raw record. All 803 event rows in this local copy have standard onset values. If a later input lacks that field, the loader retains null and reports it rather than inferring another task's timing conventions. Reaction times are not harmonized across task-specific units.

Do not directly join phenotype responses, scan availability and events and then sum or count them. Aggregate each fact to participant, study group or task grain first. Dimension relationships and fact primary keys are enforced by database constraints.

## Analytical marts

| Mart | Grain | Interpretation |
| --- | --- | --- |
| cohort_summary | Diagnosis × source gender code × age band | Participants and age sum, allowing weighted recombination |
| scan_coverage | Diagnosis × scan type | Listed / participant counts and local file counts |
| assessment_coverage | Diagnosis × phenotype source | Any-data participants and observed / expected response cells |
| metric_summary | Diagnosis × explicitly selected source measure | Observed n, sum, mean and source min/max |
| event_inventory | Local task | Event count, participant count and standard-onset availability |

The selected metrics are asrs.asrs_score, hamilton.hamd_17 and bart.bart_meanadjustedpumps. Each is marked Derivative in its source dictionary. These are descriptive source summaries; neither their averages nor missingness establish a diagnosis, severity threshold or effect of a study group. Means are recombined from sums and observed n, never by averaging group means.

This is published open research data. The handoff includes original row-level records and derived observations for educational consumers.

## Build quality checks

The loader rejects duplicate participant IDs, malformed TSV records, missing dictionary entries, unknown participant references, phenotype files with a different participant set, unexpected inventory flags, unsupported event filenames and unmodeled local images. Numeric event timings must be finite when present. Facts reconcile to source counts before export.

The profile also reports missing response cells, unmapped category codes, events without a standard onset and local images absent from the source inventory. The current build has 127,792 missing response cells and 1,772 non-missing category values without dictionary labels. Some source values include text such as "Traceable" or X and codes absent from the dictionary. These are explicit source findings; they are not silently changed. Rebuilds construct temporary artifacts inside the output folder, then replace completed outputs. Source-validation failures leave the previous outputs available; publication of multiple files is not a transactional filesystem operation.

The fixture tests cover zero versus missing, decimal categorical codes, cohort identity failures, separate scan scopes, event timing gaps, CSV handoff checksums/roundtripping and preservation of the previous database after a failed refresh. They do not replace NIfTI inspection or clinical validation.

## Repository boundary

This project exports data and logical model metadata. The consumer owns the deployed database definition.

The final PostgreSQL tables, views, reader permissions, importer, website and API live in raphael-data-engineer/projects/ucla and related website files. Updating the local analytical model does not silently change that deployed schema: the importer verifies the handoff table/column contract.

The local pipeline does not provision hosted services, write remote databases or generate website files.
