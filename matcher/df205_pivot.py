"""Build the new DF205 actions array — backward-compatible all-staff pivot.

Strategy: keep existing tiles untouched; add 4 new tiles (load-crosswalk +
3 SQL tiles); modify sql-99-final to UNION 'Hour' rows (existing logic) with
'Detail' rows (per-associate, all-staff, with unified_employee_id from the
crosswalk).

This script prints the new actions array as JSON to stdout. It's invoked
once to generate the payload for update_dataflow; it doesn't talk to Domo
itself.
"""

from __future__ import annotations

import json
import sys


# --- Existing tiles (verbatim from the live dataflow) ----------------------

EXISTING_LOADS = [
    {"type":"LoadFromVault","id":"load-punches","name":"punches","settings":{"preferredDatabaseEntityType":"TEMP_VIEW"},"gui":{"x":48,"y":48,"color":None,"colorSource":None,"sampleJson":None},"previewRowLimit":10000,"propagateAi":False,"filterPolicy":"LEGACY","dataSourceId":"8e3dce28-1e8b-4cf3-a7f4-b95e06e4eaf6","sourceType":"AUTO","executeFlowWhenUpdated":False,"pseudoDataSource":False,"truncateTextColumns":False,"truncateRows":False,"onlyLoadNewVersions":False,"recentVersionCutoffMs":0,"tables":[{}]},
    {"type":"LoadFromVault","id":"load-svc-recv","name":"svc_received","settings":{"preferredDatabaseEntityType":"TEMP_VIEW"},"gui":{"x":144,"y":48,"color":None,"colorSource":None,"sampleJson":None},"previewRowLimit":10000,"propagateAi":False,"filterPolicy":"LEGACY","dataSourceId":"b8806ae0-6b04-4cb1-8896-02d0aa7ec3ef","sourceType":"AUTO","executeFlowWhenUpdated":False,"pseudoDataSource":False,"truncateTextColumns":False,"truncateRows":False,"onlyLoadNewVersions":False,"recentVersionCutoffMs":0,"tables":[{}]},
    {"type":"LoadFromVault","id":"load-med-del","name":"med_delivery","settings":{"preferredDatabaseEntityType":"TEMP_VIEW"},"gui":{"x":240,"y":48,"color":None,"colorSource":None,"sampleJson":None},"previewRowLimit":10000,"propagateAi":False,"filterPolicy":"LEGACY","dataSourceId":"224fd07c-773f-4163-a891-a74cd70ab423","sourceType":"AUTO","executeFlowWhenUpdated":False,"pseudoDataSource":False,"truncateTextColumns":False,"truncateRows":False,"onlyLoadNewVersions":False,"recentVersionCutoffMs":0,"tables":[{}]},
    {"type":"LoadFromVault","id":"load-trans","name":"trans","settings":{"preferredDatabaseEntityType":"TEMP_VIEW"},"gui":{"x":336,"y":48,"color":None,"colorSource":None,"sampleJson":None},"previewRowLimit":10000,"propagateAi":False,"filterPolicy":"LEGACY","dataSourceId":"a7b9785a-295e-43d2-9f2c-37c335422957","sourceType":"AUTO","executeFlowWhenUpdated":False,"pseudoDataSource":False,"truncateTextColumns":False,"truncateRows":False,"onlyLoadNewVersions":False,"recentVersionCutoffMs":0,"tables":[{}]},
    {"type":"LoadFromVault","id":"load-tt","name":"trans_types","settings":{"preferredDatabaseEntityType":"TEMP_VIEW"},"gui":{"x":432,"y":48,"color":None,"colorSource":None,"sampleJson":None},"previewRowLimit":10000,"propagateAi":False,"filterPolicy":"LEGACY","dataSourceId":"faa2d719-446b-4c48-ac19-506612769646","sourceType":"AUTO","executeFlowWhenUpdated":False,"pseudoDataSource":False,"truncateTextColumns":False,"truncateRows":False,"onlyLoadNewVersions":False,"recentVersionCutoffMs":0,"tables":[{}]},
    {"type":"LoadFromVault","id":"load-occ","name":"occupancy","settings":{"preferredDatabaseEntityType":"TEMP_VIEW"},"gui":{"x":528,"y":48,"color":None,"colorSource":None,"sampleJson":None},"previewRowLimit":10000,"propagateAi":False,"filterPolicy":"LEGACY","dataSourceId":"e616d17f-be36-494d-8b31-f45a0850dbd8","sourceType":"AUTO","executeFlowWhenUpdated":False,"pseudoDataSource":False,"truncateTextColumns":False,"truncateRows":False,"onlyLoadNewVersions":False,"recentVersionCutoffMs":0,"tables":[{}]},
    {"type":"LoadFromVault","id":"load-comm","name":"communities","settings":{"preferredDatabaseEntityType":"TEMP_VIEW"},"gui":{"x":624,"y":48,"color":None,"colorSource":None,"sampleJson":None},"previewRowLimit":10000,"propagateAi":False,"filterPolicy":"LEGACY","dataSourceId":"7570562b-d421-448c-85f7-b42e0967ab83","sourceType":"AUTO","executeFlowWhenUpdated":False,"pseudoDataSource":False,"truncateTextColumns":False,"truncateRows":False,"onlyLoadNewVersions":False,"recentVersionCutoffMs":0,"tables":[{}]},
    {"type":"LoadFromVault","id":"load-sched-plan","name":"svc_plan","settings":{"preferredDatabaseEntityType":"TEMP_VIEW"},"gui":{"x":720,"y":48,"color":None,"colorSource":None,"sampleJson":None},"previewRowLimit":10000,"propagateAi":False,"filterPolicy":"LEGACY","dataSourceId":"db86775a-be87-4253-98d1-8bf7ab2c6671","sourceType":"AUTO","executeFlowWhenUpdated":False,"pseudoDataSource":False,"truncateTextColumns":False,"truncateRows":False,"onlyLoadNewVersions":False,"recentVersionCutoffMs":0,"tables":[{}]},
]


def _sql_tile(tile_id, name, depends, gui_x, gui_y, statement):
    return {
        "type": "SQL",
        "id": tile_id,
        "name": name,
        "dependsOn": depends,
        "settings": {"sqlDialect": "MAGIC", "preferredDatabaseEntityType": "TEMP_VIEW"},
        "notes": [],
        "gui": {"x": gui_x, "y": gui_y, "color": None, "colorSource": None, "sampleJson": None},
        "inputs": depends,
        "statements": [statement],
        "columnSettings": {},
        "tables": [{}],
    }


SQL_01_RA_PUNCHES = """SELECT
  `Associate ID` AS associate_id,
  CAST(`Timecard Date` AS DATE) AS timecard_date,
  CAST(`Time In` AS TIMESTAMP) AS time_in,
  CAST(`Time Out` AS TIMESTAMP) AS time_out,
  MAX(`Community Name`) AS community_name,
  MAX(`Location Code`) AS location_code,
  SUM(COALESCE(CAST(`Hours` AS DOUBLE), 0)) AS total_hours,
  SUM(COALESCE(`Final Pay Rate`, 0)) AS total_pay
FROM `punches`
WHERE (
    `Department Simplified` = 'Resident Assistants Staff'
    OR (
      (`Department Simplified` IS NULL OR `Department Simplified` = '')
      AND (
        LOWER(`Timecard Worked Department Description`) LIKE '%resident assist%'
        OR LOWER(`Timecard Worked Department Description`) LIKE '%res assist%'
        OR LOWER(`Timecard Worked Department Description`) LIKE '%-ra%'
        OR LOWER(`Timecard Worked Department Description`) LIKE '%- ra%'
        OR LOWER(`Timecard Worked Department Description`) LIKE '% ra %'
      )
    )
  )
  AND `Time In` IS NOT NULL
  AND `Time Out` IS NOT NULL
  AND CAST(`Time In` AS TIMESTAMP) < CAST(`Time Out` AS TIMESTAMP)
  AND CAST(`Hours` AS DOUBLE) > 0
GROUP BY 1,2,3,4"""


SQL_02_RA_HOURLY = """WITH hours_dim AS (
  SELECT 0 AS h UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
  UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9
  UNION ALL SELECT 10 UNION ALL SELECT 11 UNION ALL SELECT 12 UNION ALL SELECT 13 UNION ALL SELECT 14
  UNION ALL SELECT 15 UNION ALL SELECT 16 UNION ALL SELECT 17 UNION ALL SELECT 18 UNION ALL SELECT 19
  UNION ALL SELECT 20 UNION ALL SELECT 21 UNION ALL SELECT 22 UNION ALL SELECT 23
),
legs AS (
  SELECT
    p.associate_id,
    p.community_name,
    CAST(p.time_in AS DATE) AS bucket_date,
    p.time_in AS leg_start,
    CASE WHEN CAST(p.time_out AS DATE) = CAST(p.time_in AS DATE)
         THEN p.time_out
         ELSE CAST(DATE_ADD(CAST(p.time_in AS DATE), 1) AS TIMESTAMP)
    END AS leg_end,
    p.total_hours, p.total_pay,
    (UNIX_TIMESTAMP(p.time_out) - UNIX_TIMESTAMP(p.time_in)) / 60.0 AS punch_minutes
  FROM `ra_punches` p
  UNION ALL
  SELECT
    p.associate_id,
    p.community_name,
    CAST(p.time_out AS DATE) AS bucket_date,
    CAST(DATE_ADD(CAST(p.time_in AS DATE), 1) AS TIMESTAMP) AS leg_start,
    p.time_out AS leg_end,
    p.total_hours, p.total_pay,
    (UNIX_TIMESTAMP(p.time_out) - UNIX_TIMESTAMP(p.time_in)) / 60.0 AS punch_minutes
  FROM `ra_punches` p
  WHERE CAST(p.time_out AS DATE) > CAST(p.time_in AS DATE)
)
SELECT
  associate_id,
  community_name,
  bucket_date,
  h.h AS hour_of_day,
  GREATEST(0,
    (UNIX_TIMESTAMP(LEAST(leg_end,
       CAST(CONCAT(CAST(bucket_date AS STRING), ' ', LPAD(CAST(h.h + 1 AS STRING), 2, '0'), ':00:00') AS TIMESTAMP)))
   - UNIX_TIMESTAMP(GREATEST(leg_start,
       CAST(CONCAT(CAST(bucket_date AS STRING), ' ', LPAD(CAST(h.h     AS STRING), 2, '0'), ':00:00') AS TIMESTAMP))))
  ) / 60.0 AS overlap_minutes,
  total_hours, total_pay,
  (UNIX_TIMESTAMP(leg_end) - UNIX_TIMESTAMP(leg_start)) / 60.0 AS leg_minutes,
  punch_minutes
FROM legs
CROSS JOIN hours_dim h
WHERE (UNIX_TIMESTAMP(LEAST(leg_end,
         CAST(CONCAT(CAST(bucket_date AS STRING), ' ', LPAD(CAST(h.h + 1 AS STRING), 2, '0'), ':00:00') AS TIMESTAMP)))
     - UNIX_TIMESTAMP(GREATEST(leg_start,
         CAST(CONCAT(CAST(bucket_date AS STRING), ' ', LPAD(CAST(h.h     AS STRING), 2, '0'), ':00:00') AS TIMESTAMP)))) > 0"""


SQL_03_RA_AGG = """SELECT
  community_name,
  bucket_date AS report_date,
  hour_of_day,
  SUM(overlap_minutes) / 60.0 AS ra_hours_worked,
  SUM(total_pay * (overlap_minutes / NULLIF(punch_minutes, 0))) AS ra_labor_cost,
  COUNT(DISTINCT associate_id) AS ra_associate_count
FROM `ra_hourly`
GROUP BY 1,2,3"""


SQL_10_SVC_BASE = """SELECT
  CAST(s.Community_ID AS STRING) AS community_id,
  CAST(s.Service_Date AS DATE) AS report_date,
  HOUR(CAST(s.Actual_Service_Start_Time AS TIMESTAMP)) AS hour_of_day,
  s.Minutes_of_Service_Actual AS svc_minutes,
  s.Amount_Billed AS svc_amount_billed,
  s.Res_Number,
  s.Employee_ID,
  LOWER(COALESCE(s.Covered, 'true')) AS covered_flag
FROM `svc_received` s
WHERE s.Minutes_of_Service_Actual > 0
  AND (s.Canceled_Code IS NULL OR s.Canceled_Code = '')
  AND s.Service_Date IS NOT NULL
  AND s.Actual_Service_Start_Time IS NOT NULL"""


SQL_11_SVC_AGG = """SELECT
  community_id,
  report_date,
  hour_of_day,
  SUM(svc_minutes) AS service_minutes,
  SUM(CASE WHEN covered_flag = 'true' THEN svc_minutes ELSE 0 END) AS covered_service_minutes,
  SUM(CASE WHEN covered_flag = 'false' THEN svc_minutes ELSE 0 END) AS uncovered_service_minutes,
  COUNT(*) AS service_count,
  SUM(CASE WHEN covered_flag = 'true' THEN 1 ELSE 0 END) AS covered_service_count,
  SUM(CASE WHEN covered_flag = 'false' THEN 1 ELSE 0 END) AS uncovered_service_count,
  SUM(svc_amount_billed) AS service_billed_hour,
  COUNT(DISTINCT Res_Number) AS service_residents
FROM `svc_base`
GROUP BY 1,2,3"""


SQL_20_MED_BASE = """SELECT
  CAST(m.Community_ID AS STRING) AS community_id,
  CAST(m.Given_or_Recorded_Date AS DATE) AS report_date,
  HOUR(CAST(m.Given_or_Recorded_Time AS TIMESTAMP)) AS hour_of_day,
  m.Res_Number
FROM `med_delivery` m
WHERE LOWER(COALESCE(m.Given, 'false')) = 'true'
  AND m.Given_or_Recorded_Date IS NOT NULL
  AND m.Given_or_Recorded_Time IS NOT NULL"""


SQL_21_MED_AGG = """SELECT
  community_id,
  report_date,
  hour_of_day,
  COUNT(*) AS med_passes,
  COUNT(DISTINCT Res_Number) AS med_residents,
  2.0 * COUNT(*) AS med_minutes_est
FROM `med_base`
GROUP BY 1,2,3"""


SQL_30_REVENUE = """SELECT
  CAST(t.Community_ID AS STRING) AS community_id,
  CAST(DATE_TRUNC('MONTH', t.Activity_Date_Begin) AS DATE) AS month_start,
  SUM(t.Amount) AS svc_revenue_month
FROM `trans` t
INNER JOIN `trans_types` tt ON tt.Code = t.Trans_Type
WHERE UPPER(COALESCE(t.Posted, 'false')) = 'TRUE'
  AND UPPER(COALESCE(tt.Payment, 'false')) <> 'TRUE'
  AND UPPER(COALESCE(tt.Prospect_Transaction, 'false')) <> 'TRUE'
  AND UPPER(COALESCE(tt.Rent, 'false')) <> 'TRUE'
  AND (LOWER(tt.Description) LIKE '%service%'
       OR LOWER(tt.Description) LIKE '%care%'
       OR LOWER(tt.Description) LIKE '%med%')
  AND t.Activity_Date_Begin IS NOT NULL
  AND CAST(t.Community_ID AS STRING) NOT IN ('112','113','114','116','2002')
GROUP BY 1,2"""


SQL_31_CENSUS = """SELECT
  CAST(Community_ID AS STRING) AS community_id,
  community_name,
  CAST(report_date AS DATE) AS report_date,
  SUM(COALESCE(is_occupied, 0)) AS census_occupied,
  SUM(COALESCE(capacity_qty, 0)) AS capacity
FROM `occupancy`
WHERE report_date IS NOT NULL
GROUP BY 1,2,3"""


SQL_32_COMM = """SELECT
  CAST(Community_ID AS STRING) AS community_id,
  Community AS community_name
FROM `communities`"""


SQL_40_SCHED_PLAN = """SELECT
  CAST(Community_ID AS STRING) AS community_id,
  SUM(COALESCE(Average_Daily_Minutes, 0)) / 60.0 AS sched_hours_per_day,
  SUM(COALESCE(Hours_Service_Per_Month, 0)) AS sched_hours_per_month,
  COUNT(*) AS plan_line_count,
  COUNT(DISTINCT Res_Number) AS residents_on_plan
FROM `svc_plan`
WHERE (End_Date IS NULL OR End_Date >= CURRENT_DATE)
  AND (Effective_Date IS NULL OR Effective_Date <= CURRENT_DATE)
  AND LOWER(COALESCE(On_Hold, 'false')) = 'false'
GROUP BY 1"""


# --- New tiles for the all-staff pivot -----------------------------------

# All staff (no RA filter) — carries department/title/employee_name
SQL_04_STAFF_PUNCHES = """SELECT
  `Associate ID` AS associate_id,
  CAST(`Timecard Date` AS DATE) AS timecard_date,
  CAST(`Time In` AS TIMESTAMP) AS time_in,
  CAST(`Time Out` AS TIMESTAMP) AS time_out,
  COALESCE(`Department Simplified`, 'Unknown') AS department_simplified,
  COALESCE(`Job Title Description`, 'Unknown') AS job_title_description,
  MAX(`Payroll Name`) AS employee_name,
  MAX(`Community Name`) AS community_name,
  SUM(COALESCE(CAST(`Hours` AS DOUBLE), 0)) AS total_hours,
  SUM(COALESCE(`Final Pay Rate`, 0)) AS total_pay
FROM `punches`
WHERE `Time In` IS NOT NULL
  AND `Time Out` IS NOT NULL
  AND CAST(`Time In` AS TIMESTAMP) < CAST(`Time Out` AS TIMESTAMP)
  AND CAST(`Hours` AS DOUBLE) > 0
GROUP BY 1,2,3,4,5,6"""


# Same overlap math as ra_hourly, carrying the new dims
SQL_05_STAFF_HOURLY = """WITH hours_dim AS (
  SELECT 0 AS h UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
  UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9
  UNION ALL SELECT 10 UNION ALL SELECT 11 UNION ALL SELECT 12 UNION ALL SELECT 13 UNION ALL SELECT 14
  UNION ALL SELECT 15 UNION ALL SELECT 16 UNION ALL SELECT 17 UNION ALL SELECT 18 UNION ALL SELECT 19
  UNION ALL SELECT 20 UNION ALL SELECT 21 UNION ALL SELECT 22 UNION ALL SELECT 23
),
legs AS (
  SELECT
    p.associate_id,
    p.department_simplified,
    p.job_title_description,
    p.employee_name,
    p.community_name,
    CAST(p.time_in AS DATE) AS bucket_date,
    p.time_in AS leg_start,
    CASE WHEN CAST(p.time_out AS DATE) = CAST(p.time_in AS DATE)
         THEN p.time_out
         ELSE CAST(DATE_ADD(CAST(p.time_in AS DATE), 1) AS TIMESTAMP)
    END AS leg_end,
    p.total_hours, p.total_pay,
    (UNIX_TIMESTAMP(p.time_out) - UNIX_TIMESTAMP(p.time_in)) / 60.0 AS punch_minutes
  FROM `staff_punches` p
  UNION ALL
  SELECT
    p.associate_id,
    p.department_simplified,
    p.job_title_description,
    p.employee_name,
    p.community_name,
    CAST(p.time_out AS DATE) AS bucket_date,
    CAST(DATE_ADD(CAST(p.time_in AS DATE), 1) AS TIMESTAMP) AS leg_start,
    p.time_out AS leg_end,
    p.total_hours, p.total_pay,
    (UNIX_TIMESTAMP(p.time_out) - UNIX_TIMESTAMP(p.time_in)) / 60.0 AS punch_minutes
  FROM `staff_punches` p
  WHERE CAST(p.time_out AS DATE) > CAST(p.time_in AS DATE)
)
SELECT
  associate_id,
  department_simplified,
  job_title_description,
  employee_name,
  community_name,
  bucket_date,
  h.h AS hour_of_day,
  GREATEST(0,
    (UNIX_TIMESTAMP(LEAST(leg_end,
       CAST(CONCAT(CAST(bucket_date AS STRING), ' ', LPAD(CAST(h.h + 1 AS STRING), 2, '0'), ':00:00') AS TIMESTAMP)))
   - UNIX_TIMESTAMP(GREATEST(leg_start,
       CAST(CONCAT(CAST(bucket_date AS STRING), ' ', LPAD(CAST(h.h     AS STRING), 2, '0'), ':00:00') AS TIMESTAMP))))
  ) / 60.0 AS overlap_minutes,
  total_pay,
  punch_minutes
FROM legs
CROSS JOIN hours_dim h
WHERE (UNIX_TIMESTAMP(LEAST(leg_end,
         CAST(CONCAT(CAST(bucket_date AS STRING), ' ', LPAD(CAST(h.h + 1 AS STRING), 2, '0'), ':00:00') AS TIMESTAMP)))
     - UNIX_TIMESTAMP(GREATEST(leg_start,
         CAST(CONCAT(CAST(bucket_date AS STRING), ' ', LPAD(CAST(h.h     AS STRING), 2, '0'), ':00:00') AS TIMESTAMP)))) > 0"""


# Per-associate aggregate joined to the crosswalk
SQL_06_STAFF_DETAIL = """SELECT
  h.community_name,
  h.bucket_date AS report_date,
  h.hour_of_day,
  h.department_simplified,
  h.job_title_description,
  h.associate_id,
  MAX(h.employee_name) AS employee_name,
  COALESCE(MAX(c.unified_employee_id), CONCAT('ADP:', h.associate_id)) AS unified_employee_id,
  COALESCE(MAX(c.em_employee_id), '') AS em_employee_id,
  SUM(h.overlap_minutes) / 60.0 AS staff_hours_worked,
  SUM(h.total_pay * (h.overlap_minutes / NULLIF(h.punch_minutes, 0))) AS staff_labor_cost
FROM `staff_hourly` h
LEFT JOIN `crosswalk` c ON c.adp_associate_id = h.associate_id
GROUP BY 1,2,3,4,5,6"""


# --- New sql-99-final: UNION 'Hour' rows + 'Detail' rows -----------------

SQL_99_FINAL_NEW = """WITH hours_dim AS (
  SELECT 0 AS h UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
  UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9
  UNION ALL SELECT 10 UNION ALL SELECT 11 UNION ALL SELECT 12 UNION ALL SELECT 13 UNION ALL SELECT 14
  UNION ALL SELECT 15 UNION ALL SELECT 16 UNION ALL SELECT 17 UNION ALL SELECT 18 UNION ALL SELECT 19
  UNION ALL SELECT 20 UNION ALL SELECT 21 UNION ALL SELECT 22 UNION ALL SELECT 23
),
spine AS (
  SELECT c.community_id, c.community_name, c.report_date, h.h AS hour_of_day,
         c.census_occupied, c.capacity
  FROM `census_daily` c
  CROSS JOIN hours_dim h
)
SELECT
  'Hour' AS Row_Type,
  sp.community_id,
  sp.community_name,
  sp.report_date,
  sp.hour_of_day,
  CASE
    WHEN sp.hour_of_day BETWEEN 6 AND 13 THEN 'AM'
    WHEN sp.hour_of_day BETWEEN 14 AND 21 THEN 'PM'
    ELSE 'NOC'
  END AS shift_label,
  CAST(DATE_TRUNC('MONTH', sp.report_date) AS DATE) AS month_start,
  YEAR(sp.report_date) AS year_num,
  MONTH(sp.report_date) AS month_num,
  DAYOFWEEK(sp.report_date) AS day_of_week,
  CAST(NULL AS STRING) AS department_simplified,
  CAST(NULL AS STRING) AS job_title_description,
  CAST(NULL AS STRING) AS associate_id,
  CAST(NULL AS STRING) AS employee_name,
  CAST(NULL AS STRING) AS unified_employee_id,
  CAST(NULL AS STRING) AS em_employee_id,
  CAST(NULL AS DOUBLE) AS staff_hours_worked,
  CAST(NULL AS DOUBLE) AS staff_labor_cost,
  CAST(sp.census_occupied AS DOUBLE) AS census_occupied,
  CAST(sp.capacity AS DOUBLE) AS capacity,
  COALESCE(ra.ra_hours_worked, 0) AS ra_hours_worked,
  COALESCE(ra.ra_labor_cost, 0) AS ra_labor_cost,
  CAST(COALESCE(ra.ra_associate_count, 0) AS DOUBLE) AS ra_associate_count,
  COALESCE(sv.service_minutes, 0) AS service_minutes,
  COALESCE(sv.covered_service_minutes, 0) AS covered_service_minutes,
  COALESCE(sv.uncovered_service_minutes, 0) AS uncovered_service_minutes,
  CAST(COALESCE(sv.service_count, 0) AS DOUBLE) AS service_count,
  CAST(COALESCE(sv.covered_service_count, 0) AS DOUBLE) AS covered_service_count,
  CAST(COALESCE(sv.uncovered_service_count, 0) AS DOUBLE) AS uncovered_service_count,
  COALESCE(sv.service_billed_hour, 0) AS service_billed_hour,
  CAST(COALESCE(sv.service_residents, 0) AS DOUBLE) AS service_residents,
  CAST(COALESCE(md.med_passes, 0) AS DOUBLE) AS med_passes,
  CAST(COALESCE(md.med_residents, 0) AS DOUBLE) AS med_residents,
  COALESCE(md.med_minutes_est, 0) AS med_minutes_est,
  COALESCE(rv.svc_revenue_month, 0) AS svc_revenue_month,
  COALESCE(rv.svc_revenue_month, 0) / (DAY(LAST_DAY(sp.report_date)) * 24.0) AS svc_revenue_hour_alloc,
  COALESCE(sv.service_minutes, 0) + COALESCE(md.med_minutes_est, 0) AS total_service_minutes,
  COALESCE(pl.sched_hours_per_day, 0) AS sched_hours_per_day,
  CAST(COALESCE(pl.residents_on_plan, 0) AS DOUBLE) AS residents_on_plan,
  CASE WHEN COALESCE(ra.ra_hours_worked, 0) > 0
       THEN (COALESCE(sv.service_minutes, 0) + COALESCE(md.med_minutes_est, 0)) / 60.0
            / ra.ra_hours_worked
       ELSE NULL END AS service_hours_per_ra_hour,
  CASE WHEN sp.census_occupied > 0
       THEN COALESCE(ra.ra_hours_worked, 0) / sp.census_occupied
       ELSE NULL END AS ra_hours_per_census
FROM spine sp
LEFT JOIN `ra_agg` ra
  ON ra.community_name = sp.community_name
  AND ra.report_date = sp.report_date
  AND ra.hour_of_day = sp.hour_of_day
LEFT JOIN `svc_agg` sv
  ON sv.community_id = sp.community_id
  AND sv.report_date = sp.report_date
  AND sv.hour_of_day = sp.hour_of_day
LEFT JOIN `med_agg` md
  ON md.community_id = sp.community_id
  AND md.report_date = sp.report_date
  AND md.hour_of_day = sp.hour_of_day
LEFT JOIN `revenue_monthly` rv
  ON rv.community_id = sp.community_id
  AND rv.month_start = CAST(DATE_TRUNC('MONTH', sp.report_date) AS DATE)
LEFT JOIN `sched_plan` pl
  ON pl.community_id = sp.community_id

UNION ALL

SELECT
  'Detail' AS Row_Type,
  cd.community_id,
  sd.community_name,
  sd.report_date,
  sd.hour_of_day,
  CASE
    WHEN sd.hour_of_day BETWEEN 6 AND 13 THEN 'AM'
    WHEN sd.hour_of_day BETWEEN 14 AND 21 THEN 'PM'
    ELSE 'NOC'
  END AS shift_label,
  CAST(DATE_TRUNC('MONTH', sd.report_date) AS DATE) AS month_start,
  YEAR(sd.report_date) AS year_num,
  MONTH(sd.report_date) AS month_num,
  DAYOFWEEK(sd.report_date) AS day_of_week,
  sd.department_simplified,
  sd.job_title_description,
  sd.associate_id,
  sd.employee_name,
  sd.unified_employee_id,
  sd.em_employee_id,
  sd.staff_hours_worked,
  sd.staff_labor_cost,
  CAST(NULL AS DOUBLE) AS census_occupied,
  CAST(NULL AS DOUBLE) AS capacity,
  CAST(NULL AS DOUBLE) AS ra_hours_worked,
  CAST(NULL AS DOUBLE) AS ra_labor_cost,
  CAST(NULL AS DOUBLE) AS ra_associate_count,
  CAST(NULL AS DOUBLE) AS service_minutes,
  CAST(NULL AS DOUBLE) AS covered_service_minutes,
  CAST(NULL AS DOUBLE) AS uncovered_service_minutes,
  CAST(NULL AS DOUBLE) AS service_count,
  CAST(NULL AS DOUBLE) AS covered_service_count,
  CAST(NULL AS DOUBLE) AS uncovered_service_count,
  CAST(NULL AS DOUBLE) AS service_billed_hour,
  CAST(NULL AS DOUBLE) AS service_residents,
  CAST(NULL AS DOUBLE) AS med_passes,
  CAST(NULL AS DOUBLE) AS med_residents,
  CAST(NULL AS DOUBLE) AS med_minutes_est,
  CAST(NULL AS DOUBLE) AS svc_revenue_month,
  CAST(NULL AS DOUBLE) AS svc_revenue_hour_alloc,
  CAST(NULL AS DOUBLE) AS total_service_minutes,
  CAST(NULL AS DOUBLE) AS sched_hours_per_day,
  CAST(NULL AS DOUBLE) AS residents_on_plan,
  CAST(NULL AS DOUBLE) AS service_hours_per_ra_hour,
  CAST(NULL AS DOUBLE) AS ra_hours_per_census
FROM `staff_detail` sd
LEFT JOIN `comm_dim` cd ON cd.community_name = sd.community_name"""


def build_actions() -> list[dict]:
    return [
        # 9 LoadFromVault tiles (8 existing + new crosswalk)
        *EXISTING_LOADS,
        {
            "type": "LoadFromVault",
            "id": "load-crosswalk",
            "name": "crosswalk",
            "settings": {"preferredDatabaseEntityType": "TEMP_VIEW"},
            "gui": {"x": 816, "y": 200, "color": None, "colorSource": None, "sampleJson": None},
            "previewRowLimit": 10000,
            "propagateAi": False,
            "filterPolicy": "LEGACY",
            "dataSourceId": "42e99d29-460c-478e-ac8c-9898795f1ef3",
            "sourceType": "AUTO",
            "executeFlowWhenUpdated": False,
            "pseudoDataSource": False,
            "truncateTextColumns": False,
            "truncateRows": False,
            "onlyLoadNewVersions": False,
            "recentVersionCutoffMs": 0,
            "tables": [{}],
        },
        # Existing RA-only chain
        _sql_tile("sql-01-ra-punches", "ra_punches", ["load-punches"], 816, 48, SQL_01_RA_PUNCHES),
        _sql_tile("sql-02-ra-hourly", "ra_hourly", ["sql-01-ra-punches"], 912, 48, SQL_02_RA_HOURLY),
        _sql_tile("sql-03-ra-agg", "ra_agg", ["sql-02-ra-hourly"], 1008, 48, SQL_03_RA_AGG),
        # New all-staff chain
        _sql_tile("sql-04-staff-punches", "staff_punches", ["load-punches"], 816, 200, SQL_04_STAFF_PUNCHES),
        _sql_tile("sql-05-staff-hourly", "staff_hourly", ["sql-04-staff-punches"], 912, 200, SQL_05_STAFF_HOURLY),
        _sql_tile("sql-06-staff-detail", "staff_detail", ["sql-05-staff-hourly", "load-crosswalk"], 1008, 200, SQL_06_STAFF_DETAIL),
        # Existing service / med / revenue / census / community / sched-plan tiles
        _sql_tile("sql-10-svc-base", "svc_base", ["load-svc-recv"], 1104, 48, SQL_10_SVC_BASE),
        _sql_tile("sql-11-svc-agg", "svc_agg", ["sql-10-svc-base"], 1200, 48, SQL_11_SVC_AGG),
        _sql_tile("sql-20-med-base", "med_base", ["load-med-del"], 1296, 48, SQL_20_MED_BASE),
        _sql_tile("sql-21-med-agg", "med_agg", ["sql-20-med-base"], 1392, 48, SQL_21_MED_AGG),
        _sql_tile("sql-30-revenue", "revenue_monthly", ["load-trans", "load-tt"], 1488, 48, SQL_30_REVENUE),
        _sql_tile("sql-31-census", "census_daily", ["load-occ"], 1584, 48, SQL_31_CENSUS),
        _sql_tile("sql-32-comm", "comm_dim", ["load-comm"], 1680, 48, SQL_32_COMM),
        _sql_tile("sql-40-sched-plan", "sched_plan", ["load-sched-plan"], 1776, 48, SQL_40_SCHED_PLAN),
        # Final UNION
        _sql_tile(
            "sql-99-final",
            "final_output",
            [
                "sql-03-ra-agg",
                "sql-06-staff-detail",
                "sql-11-svc-agg",
                "sql-21-med-agg",
                "sql-30-revenue",
                "sql-31-census",
                "sql-32-comm",
                "sql-40-sched-plan",
            ],
            1872, 48,
            SQL_99_FINAL_NEW,
        ),
        # Publish
        {
            "type": "PublishToVault",
            "id": "publish-out",
            "name": "DEV | RA Staffing Efficiency",
            "dependsOn": ["sql-99-final"],
            "settings": {"preferredDatabaseEntityType": "TEMP_VIEW"},
            "gui": {"x": 1968, "y": 48, "color": None, "colorSource": None, "sampleJson": None},
            "inputs": ["sql-99-final"],
            "dataSource": {
                "guid": "995db646-e97e-41e8-b8fd-44f517904859",
                "type": "DataFlow",
                "name": "DEV | RA Staffing Efficiency",
                "cloudId": "domo",
            },
            "versionChainType": "REPLACE",
            "schemaSource": "DATAFLOW",
            "partitioned": False,
            "tables": [{}],
        },
    ]


if __name__ == "__main__":
    json.dump(build_actions(), sys.stdout)
