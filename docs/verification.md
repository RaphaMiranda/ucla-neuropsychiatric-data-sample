# Data-project verification: 2026-10-28

- Four local warehouse tests passed after separating website and PostgreSQL responsibilities.
- The full source build reconciled 272 participants, 52 phenotype tables, 1,983 variables, 539,376 response cells, 2,720 scan-inventory rows, seven local scans and 803 events.
- The portable handoff contains 12 raw/core table CSVs, metadata for five analytical views, a quality profile and a checksummed versioned manifest.
- Missing numeric values, observed zeroes, decimal category codes, multiline source strings, CSV checksums, cohort identity failures and preservation of a previous database after source validation fails are covered by tests.
- The default output is build/warehouse; the older build/ucla.duckdb remains available to an already-open database client.
- SHA-256 preservation checks cover 142 original dataset/metadata files. The preservation manifest is stored with the generated warehouse outputs.
- Website artifacts and the legacy PostgreSQL import were copied and verified in the portfolio repository before removal here.
- This project's build no longer writes HTML, CSS, JavaScript, website snapshots or PostgreSQL DDL.

Website, API and PostgreSQL verification now lives in raphael-data-engineer/docs/ucla/verification.md.

NIfTI voxel contents and clinical score validity were not evaluated.
