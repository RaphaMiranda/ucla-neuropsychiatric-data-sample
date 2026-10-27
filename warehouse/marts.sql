CREATE VIEW ucla_mart.cohort_summary AS
SELECT diagnosis, gender_source_code,
       CASE WHEN age < 30 THEN '21-29'
            WHEN age < 40 THEN '30-39' ELSE '40-50' END AS age_band,
       COUNT(*) AS participants, SUM(age) AS age_sum
FROM ucla_core.dim_participant
GROUP BY diagnosis, gender_source_code, age_band;

CREATE VIEW ucla_mart.scan_coverage AS
SELECT p.diagnosis, t.task_code, t.task_name, t.modality,
       COUNT(*) AS participants,
       SUM(CASE WHEN f.dataset_listed THEN 1 ELSE 0 END) AS dataset_listed,
       SUM(CASE WHEN f.local_file_present THEN 1 ELSE 0 END) AS local_files,
       100.0 * SUM(CASE WHEN f.dataset_listed THEN 1 ELSE 0 END)
             / NULLIF(COUNT(*), 0) AS listed_percent
FROM ucla_core.fact_scan_availability f
JOIN ucla_core.dim_participant p USING (participant_key)
JOIN ucla_core.dim_task t USING (task_key)
GROUP BY p.diagnosis, t.task_code, t.task_name, t.modality;

CREATE VIEW ucla_mart.assessment_coverage AS
SELECT p.diagnosis, i.instrument_code, i.instrument_name,
       COUNT(DISTINCT f.participant_key) AS participants,
       COUNT(DISTINCT CASE WHEN NOT f.is_missing THEN f.participant_key END)
           AS participants_with_data,
       COUNT(DISTINCT f.measure_key) AS variables,
       COUNT(*) AS expected_responses,
       SUM(CASE WHEN NOT f.is_missing THEN 1 ELSE 0 END) AS observed_responses,
       100.0 * SUM(CASE WHEN NOT f.is_missing THEN 1 ELSE 0 END)
             / NULLIF(COUNT(*), 0) AS observed_percent
FROM ucla_core.fact_phenotype_response f
JOIN ucla_core.dim_participant p USING (participant_key)
JOIN ucla_core.dim_measure m USING (measure_key)
JOIN ucla_core.dim_instrument i USING (instrument_key)
GROUP BY p.diagnosis, i.instrument_code, i.instrument_name;

-- Only named source-derived metrics are averaged; category codes are excluded.
CREATE VIEW ucla_mart.metric_summary AS
SELECT p.diagnosis, i.instrument_code, m.variable_name, m.description,
       COUNT(f.value_numeric) AS sample_size,
       SUM(f.value_numeric) AS value_sum,
       AVG(f.value_numeric) AS mean_value,
       MIN(f.value_numeric) AS min_value,
       MAX(f.value_numeric) AS max_value
FROM ucla_core.fact_phenotype_response f
JOIN ucla_core.dim_participant p USING (participant_key)
JOIN ucla_core.dim_measure m USING (measure_key)
JOIN ucla_core.dim_instrument i USING (instrument_key)
WHERE NOT m.is_categorical AND m.is_derived AND
      ((i.instrument_code = 'asrs' AND m.variable_name = 'asrs_score')
       OR (i.instrument_code = 'hamilton' AND m.variable_name = 'hamd_17')
       OR (i.instrument_code = 'bart' AND m.variable_name = 'bart_meanadjustedpumps'))
GROUP BY p.diagnosis, i.instrument_code, m.variable_name, m.description;

CREATE VIEW ucla_mart.event_inventory AS
SELECT t.task_code, t.task_name, t.modality,
       COUNT(*) AS events, COUNT(DISTINCT f.participant_key) AS local_participants,
       COUNT(f.onset_seconds) AS events_with_onset
FROM ucla_core.fact_task_event f
JOIN ucla_core.dim_task t USING (task_key)
GROUP BY t.task_code, t.task_name, t.modality;
