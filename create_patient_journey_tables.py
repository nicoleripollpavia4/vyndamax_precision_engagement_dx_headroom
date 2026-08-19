"""
create_patient_journey_tables.py
--------------------------------
Build a patient journey event table and a patient milestone table from
COMM_US_PUB_PROD_DB.BI_US_RNA.LAAD_FCT_MX.

The event table normalizes key diagnosis milestones into one row per
patient-event-date. The milestone table rolls those events back up to one row
per patient with first dates and lead-time fields that can feed downstream
journey analytics and modeling.

Usage:
    python create_patient_journey_tables.py

Optional environment variables:
    SNOWFLAKE_SOURCE_TABLE
    SNOWFLAKE_EVENT_TABLE
    SNOWFLAKE_MILESTONE_TABLE
    SNOWFLAKE_PATIENT_IDS            comma-separated list for targeted runs
    SNOWFLAKE_PRINT_SQL_ONLY         true/false
"""

import os
import re
import sys

from create_undiagnosed_output_table import build_session


SOURCE_TABLE = "COMM_US_PUB_PROD_DB.BI_US_RNA.LAAD_FCT_MX"
PROCEDURE_SOURCE_TABLE = "COMM_US_PUB_PROD_DB.BI_US_RNA.LAAD_FCT_PX"
EVENT_TABLE = "VAW_AMER_DESIGN.US_VYNDA_DT_2.US_VYNDA_DT_2_PATIENT_JOURNEY_EVENTS_INF"
MILESTONE_TABLE = "VAW_AMER_DESIGN.US_VYNDA_DT_2.US_VYNDA_DT_2_PATIENT_JOURNEY_MILESTONES_INF"

DIAGNOSIS_COLUMNS = [f"DIAGNOSIS_CODE_{idx}" for idx in range(1, 13)]

EVENT_DEFINITIONS = [
    {
        "event_name": "AFIB",
        "event_type": "RED_FLAG",
        "patterns": [("LIKE", "I48%")],
    },
    {
        "event_name": "CARPAL_TUNNEL_SYNDROME",
        "event_type": "RED_FLAG",
        "patterns": [("LIKE", "G56.0%")],
    },
    {
        "event_name": "AORTIC_STENOSIS",
        "event_type": "RED_FLAG",
        "patterns": [("IN", ("I35.0", "I35.2", "I06.0", "I06.2"))],
    },
    {
        "event_name": "BICEP_TENDON_RUPTURE",
        "event_type": "RED_FLAG",
        "patterns": [("LIKE", "S46.1%"), ("LIKE", "M66.82%")],
    },
    {
        "event_name": "SPINAL_STENOSIS",
        "event_type": "RED_FLAG",
        "patterns": [("LIKE", "M48.0%")],
    },
    {
        "event_name": "AV_BLOCK",
        "event_type": "RED_FLAG",
        "patterns": [("IN", ("I44.0", "I44.1", "I44.2", "I44.3"))],
    },
    {
        "event_name": "HEART_FAILURE",
        "event_type": "HEART_FAILURE",
        "patterns": [("LIKE", "I42%"), ("LIKE", "I50%")],
    },
    {
        "event_name": "HFPEF",
        "event_type": "HFPEF",
        "patterns": [("IN", ("I50.30", "I50.31", "I50.32", "I50.33"))],
    },
    {
        "event_name": "ATTR_CM_DIAGNOSIS",
        "event_type": "ATTR_CM_DIAGNOSIS",
        "patterns": [("LIKE", "E85.4%"), ("EQ", "E85.82")],
    },
]

PROCEDURE_EVENT_DEFINITIONS = [
    {"event_name": "PX_PYP", "event_type": "PROCEDURE", "pattern": "%PYP%"},
    {"event_name": "PX_IHC", "event_type": "PROCEDURE", "pattern": "%IHC%"},
    {"event_name": "PX_TAVR", "event_type": "PROCEDURE", "pattern": "%TAVR%"},
    {"event_name": "PX_SERUM_IMMUNOFIXATION", "event_type": "PROCEDURE", "pattern": "%IMMUNOFIX%SERUM%"},
    {"event_name": "PX_URINE_IMMU", "event_type": "PROCEDURE", "pattern": "%IMMUNOFIX%URINE%"},
    {"event_name": "PX_MS", "event_type": "PROCEDURE", "pattern": "%MS%"},
    {"event_name": "PX_MYOCARDIAL", "event_type": "PROCEDURE", "pattern": "%MYOCARDIAL%"},
    {"event_name": "PX_RECTAL_BIOPSY", "event_type": "PROCEDURE", "pattern": "%RECTAL%BIOPSY%"},
    {"event_name": "PX_FAT_PAD_BIOPSY", "event_type": "PROCEDURE", "pattern": "%FAT%PAD%BIOPSY%"},
    {"event_name": "PX_ECHO_STRAIN", "event_type": "PROCEDURE", "pattern": "%ECHO%STRAIN%"},
    {"event_name": "PX_CARDIAC_MRI", "event_type": "PROCEDURE", "pattern": "%CARDIAC%MRI%"},
    {"event_name": "PX_ECG", "event_type": "PROCEDURE", "pattern": "%ECG%"},
    {"event_name": "PX_FLC", "event_type": "PROCEDURE", "pattern": "%FREE LIGHT%"},
    {"event_name": "PX_CARDIAC_BIOPSY", "event_type": "PROCEDURE", "pattern": "%CARDIAC%BIOPSY%"},
    {"event_name": "PX_ECHO_CARDIOGRAPHY", "event_type": "PROCEDURE", "pattern": "%ECHO%CARDIO%"},
    {"event_name": "PX_GENETIC_TESTING", "event_type": "PROCEDURE", "pattern": "%GENETIC%TESTING%"},
    {"event_name": "PX_PROTEIN_ELECTROPHORESIS", "event_type": "PROCEDURE", "pattern": "%PROTEIN%ELECTROPHOR%"},
    {"event_name": "PX_KAPPA_LAMBDA", "event_type": "PROCEDURE", "pattern": "%KAPPA%"},
]


def _validate_table_identifier(identifier):
    pattern = r'^[A-Za-z0-9_\.\"]+$'
    if not re.fullmatch(pattern, identifier):
        raise ValueError(
            "Invalid table identifier. Use a simple identifier like DB.SCHEMA.TABLE "
            "with letters, numbers, underscore, dot, and optional double quotes."
        )


def _parse_bool(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _parse_patient_ids(value):
    if not value or not str(value).strip():
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _quoted_csv(values):
    return ", ".join("'" + value.replace("'", "''") + "'" for value in values)


def _patient_filter_sql(patient_ids):
    if not patient_ids:
        return ""
    return f"WHERE PATIENT_ID IN ({_quoted_csv(patient_ids)})"


def _pattern_sql(column_name, pattern):
    operator, value = pattern
    qualified = f"claim.{column_name}"
    if operator == "LIKE":
        return f"{qualified} LIKE '{value}'"
    if operator == "EQ":
        return f"{qualified} = '{value}'"
    if operator == "IN":
        value_sql = ", ".join(f"'{item}'" for item in value)
        return f"{qualified} IN ({value_sql})"
    raise ValueError(f"Unsupported pattern operator: {operator}")


def _column_match_sql(column_name, patterns):
    return "(" + " OR ".join(_pattern_sql(column_name, pattern) for pattern in patterns) + ")"


def _event_match_sql(patterns):
    return " OR ".join(_column_match_sql(column_name, patterns) for column_name in DIAGNOSIS_COLUMNS)


def _matched_code_sql(patterns):
    parts = [
        f"CASE WHEN {_column_match_sql(column_name, patterns)} THEN claim.{column_name} END"
        for column_name in DIAGNOSIS_COLUMNS
    ]
    return "COALESCE(\n                " + ",\n                ".join(parts) + "\n            )"


def _matched_position_sql(patterns):
    lines = ["CASE"]
    for idx, column_name in enumerate(DIAGNOSIS_COLUMNS, start=1):
        lines.append(f"                WHEN {_column_match_sql(column_name, patterns)} THEN {idx}")
    lines.append("                ELSE NULL")
    lines.append("            END")
    return "\n".join(lines)


def build_events_query(source_table, procedure_source_table, patient_ids):
    filter_sql = _patient_filter_sql(patient_ids)
    diagnosis_event_selects = []

    for definition in EVENT_DEFINITIONS:
        match_sql = _event_match_sql(definition["patterns"])
        matched_code_sql = _matched_code_sql(definition["patterns"])
        matched_position_sql = _matched_position_sql(definition["patterns"])

        diagnosis_event_selects.append(
            f"""
    SELECT
        claim.PATIENT_ID,
        claim.SERVICE_DATE AS EVENT_DATE,
        '{definition["event_type"]}' AS EVENT_TYPE,
        '{definition["event_name"]}' AS EVENT_NAME,
        '{source_table}' AS SOURCE_TABLE,
        claim.MX_CLAIM_ID,
        claim.MX_SERVICE_NUMBER,
        {matched_code_sql} AS MATCHED_DIAGNOSIS_CODE,
        {matched_position_sql} AS MATCHED_DIAGNOSIS_POSITION,
        claim.PROCEDURE_CODE,
        claim.PLACE_OF_SERVICE_CODE,
        claim.FACILITY_TYPE_CODE,
        claim.DATA_SOURCE,
        claim.RENDERING_PROVIDER_ID,
        claim.REFERRING_PROVIDER_ID,
        claim.PROVIDER_BILLING_ID,
        claim.PROVIDER_FACILITY_ID,
        CAST(NULL AS VARCHAR) AS MATCHED_PROCEDURE_DESCRIPTION
    FROM filtered_claims claim
    WHERE {match_sql}
            """.strip()
        )

    procedure_event_selects = []
    for definition in PROCEDURE_EVENT_DEFINITIONS:
        procedure_event_selects.append(
            f"""
    SELECT
        proc.PATIENT_ID,
        proc.SERVICE_DATE AS EVENT_DATE,
        '{definition["event_type"]}' AS EVENT_TYPE,
        '{definition["event_name"]}' AS EVENT_NAME,
        '{procedure_source_table}' AS SOURCE_TABLE,
        proc.CLAIM_ID AS MX_CLAIM_ID,
        proc.CLAIM_SERVICE_NUMBER AS MX_SERVICE_NUMBER,
        CAST(NULL AS VARCHAR) AS MATCHED_DIAGNOSIS_CODE,
        CAST(NULL AS NUMBER) AS MATCHED_DIAGNOSIS_POSITION,
        proc.PROCEDURE_CODE,
        CAST(NULL AS VARCHAR) AS PLACE_OF_SERVICE_CODE,
        CAST(NULL AS VARCHAR) AS FACILITY_TYPE_CODE,
        proc.DATA_SOURCE,
        proc.RENDERING_PRESCRIBER_ID AS RENDERING_PROVIDER_ID,
        proc.REFERRING_PRESCRIBER_ID AS REFERRING_PROVIDER_ID,
        proc.REND_PFZ_CUST_ID AS PROVIDER_BILLING_ID,
        proc.REF_PFZ_CUST_ID AS PROVIDER_FACILITY_ID,
        proc.PROCEDURE_DESCRIPTION AS MATCHED_PROCEDURE_DESCRIPTION
    FROM filtered_procedures proc
    WHERE UPPER(proc.PROCEDURE_DESCRIPTION) LIKE '{definition["pattern"]}'
            """.strip()
        )

    diagnosis_sql = "\n    UNION ALL\n".join(diagnosis_event_selects)
    procedure_sql = "\n    UNION ALL\n".join(procedure_event_selects)

    return f"""
WITH filtered_claims AS (
    SELECT
        MX_CLAIM_ID,
        MX_SERVICE_NUMBER,
        PATIENT_ID,
        SERVICE_DATE,
        PROCEDURE_CODE,
        PLACE_OF_SERVICE_CODE,
        FACILITY_TYPE_CODE,
        DATA_SOURCE,
        RENDERING_PROVIDER_ID,
        REFERRING_PROVIDER_ID,
        PROVIDER_BILLING_ID,
        PROVIDER_FACILITY_ID,
        {", ".join(DIAGNOSIS_COLUMNS)}
    FROM {source_table}
    {filter_sql}
),
filtered_procedures AS (
    SELECT
        CLAIM_ID,
        CLAIM_SERVICE_NUMBER,
        PATIENT_ID,
        SERVICE_DATE,
        PROCEDURE_CODE,
        PROCEDURE_DESCRIPTION,
        DATA_SOURCE,
        RENDERING_PRESCRIBER_ID,
        REFERRING_PRESCRIBER_ID,
        REND_PFZ_CUST_ID,
        REF_PFZ_CUST_ID
    FROM {procedure_source_table}
    {filter_sql}
)
SELECT *
FROM (
    {diagnosis_sql}
    UNION ALL
    {procedure_sql}
)
""".strip()


def build_milestones_query(source_table, procedure_source_table, event_table, patient_ids):
    filter_sql = _patient_filter_sql(patient_ids)

    return f"""
WITH filtered_claims AS (
    SELECT
        PATIENT_ID,
        SERVICE_DATE
    FROM {source_table}
    {filter_sql}
),
filtered_procedures AS (
    SELECT
        PATIENT_ID,
        SERVICE_DATE
    FROM {procedure_source_table}
    {filter_sql}
),
all_claim_dates AS (
    SELECT PATIENT_ID, SERVICE_DATE FROM filtered_claims
    UNION ALL
    SELECT PATIENT_ID, SERVICE_DATE FROM filtered_procedures
),
claim_span AS (
    SELECT
        PATIENT_ID,
        COUNT(*) AS CLAIM_ROW_COUNT,
        MIN(SERVICE_DATE) AS FIRST_CLAIM_DATE,
        MAX(SERVICE_DATE) AS LAST_CLAIM_DATE
    FROM all_claim_dates
    GROUP BY PATIENT_ID
),
event_first_dates AS (
    SELECT
        PATIENT_ID,
        EVENT_TYPE,
        EVENT_NAME,
        MIN(EVENT_DATE) AS FIRST_EVENT_DATE
    FROM {event_table}
    GROUP BY PATIENT_ID, EVENT_TYPE, EVENT_NAME
),
red_flag_ranked AS (
    SELECT
        PATIENT_ID,
        EVENT_NAME,
        FIRST_EVENT_DATE,
        ROW_NUMBER() OVER (
            PARTITION BY PATIENT_ID
            ORDER BY FIRST_EVENT_DATE, EVENT_NAME
        ) AS RED_FLAG_RANK
    FROM event_first_dates
    WHERE EVENT_TYPE = 'RED_FLAG'
),
red_flag_counts AS (
    SELECT
        PATIENT_ID,
        COUNT(*) AS TOTAL_CONDITIONS,
        MIN(FIRST_EVENT_DATE) AS FIRST_RED_FLAG_DATE
    FROM event_first_dates
    WHERE EVENT_TYPE = 'RED_FLAG'
    GROUP BY PATIENT_ID
),
attr_dx AS (
    SELECT
        PATIENT_ID,
        FIRST_EVENT_DATE AS FIRST_ATTR_CM_DX_DATE
    FROM event_first_dates
    WHERE EVENT_NAME = 'ATTR_CM_DIAGNOSIS'
),
red_flags_before_attr_dx AS (
    SELECT
        e.PATIENT_ID,
        COUNT(*) AS RED_FLAGS_BEFORE_ATTR_DX
    FROM event_first_dates e
    INNER JOIN attr_dx d
        ON d.PATIENT_ID = e.PATIENT_ID
    WHERE e.EVENT_TYPE = 'RED_FLAG'
      AND e.FIRST_EVENT_DATE <= d.FIRST_ATTR_CM_DX_DATE
    GROUP BY e.PATIENT_ID
),
pivoted_events AS (
    SELECT
        PATIENT_ID,
        MAX(CASE WHEN EVENT_NAME = 'AFIB' THEN FIRST_EVENT_DATE END) AS FIRST_AFIB_DATE,
        MAX(CASE WHEN EVENT_NAME = 'CARPAL_TUNNEL_SYNDROME' THEN FIRST_EVENT_DATE END) AS FIRST_CARPAL_TUNNEL_DATE,
        MAX(CASE WHEN EVENT_NAME = 'AORTIC_STENOSIS' THEN FIRST_EVENT_DATE END) AS FIRST_AORTIC_STENOSIS_DATE,
        MAX(CASE WHEN EVENT_NAME = 'BICEP_TENDON_RUPTURE' THEN FIRST_EVENT_DATE END) AS FIRST_BICEP_RUPTURE_DATE,
        MAX(CASE WHEN EVENT_NAME = 'SPINAL_STENOSIS' THEN FIRST_EVENT_DATE END) AS FIRST_SPINAL_STENOSIS_DATE,
        MAX(CASE WHEN EVENT_NAME = 'AV_BLOCK' THEN FIRST_EVENT_DATE END) AS FIRST_AV_BLOCK_DATE,
        MAX(CASE WHEN EVENT_NAME = 'HEART_FAILURE' THEN FIRST_EVENT_DATE END) AS FIRST_HF_DATE,
        MAX(CASE WHEN EVENT_NAME = 'HFPEF' THEN FIRST_EVENT_DATE END) AS FIRST_HFPEF_DATE,
        MAX(CASE WHEN EVENT_NAME = 'ATTR_CM_DIAGNOSIS' THEN FIRST_EVENT_DATE END) AS FIRST_ATTR_CM_DX_DATE,
        MAX(CASE WHEN EVENT_NAME = 'PX_PYP' THEN FIRST_EVENT_DATE END) AS FIRST_PYP_DATE,
        MAX(CASE WHEN EVENT_NAME = 'PX_IHC' THEN FIRST_EVENT_DATE END) AS FIRST_IHC_DATE,
        MAX(CASE WHEN EVENT_NAME = 'PX_TAVR' THEN FIRST_EVENT_DATE END) AS FIRST_TAVR_DATE,
        MAX(CASE WHEN EVENT_NAME = 'PX_SERUM_IMMUNOFIXATION' THEN FIRST_EVENT_DATE END) AS FIRST_SERUM_IMMUNOFIXATION_DATE,
        MAX(CASE WHEN EVENT_NAME = 'PX_URINE_IMMU' THEN FIRST_EVENT_DATE END) AS FIRST_URINE_IMMUNOFIXATION_DATE,
        MAX(CASE WHEN EVENT_NAME = 'PX_MS' THEN FIRST_EVENT_DATE END) AS FIRST_MS_DATE,
        MAX(CASE WHEN EVENT_NAME = 'PX_MYOCARDIAL' THEN FIRST_EVENT_DATE END) AS FIRST_MYOCARDIAL_DATE,
        MAX(CASE WHEN EVENT_NAME = 'PX_RECTAL_BIOPSY' THEN FIRST_EVENT_DATE END) AS FIRST_RECTAL_BIOPSY_DATE,
        MAX(CASE WHEN EVENT_NAME = 'PX_FAT_PAD_BIOPSY' THEN FIRST_EVENT_DATE END) AS FIRST_FAT_PAD_BIOPSY_DATE,
        MAX(CASE WHEN EVENT_NAME = 'PX_ECHO_STRAIN' THEN FIRST_EVENT_DATE END) AS FIRST_ECHO_STRAIN_DATE,
        MAX(CASE WHEN EVENT_NAME = 'PX_CARDIAC_MRI' THEN FIRST_EVENT_DATE END) AS FIRST_CARDIAC_MRI_DATE,
        MAX(CASE WHEN EVENT_NAME = 'PX_ECG' THEN FIRST_EVENT_DATE END) AS FIRST_ECG_DATE,
        MAX(CASE WHEN EVENT_NAME = 'PX_FLC' THEN FIRST_EVENT_DATE END) AS FIRST_FREE_LIGHT_CHAIN_DATE,
        MAX(CASE WHEN EVENT_NAME = 'PX_CARDIAC_BIOPSY' THEN FIRST_EVENT_DATE END) AS FIRST_CARDIAC_BIOPSY_DATE,
        MAX(CASE WHEN EVENT_NAME = 'PX_ECHO_CARDIOGRAPHY' THEN FIRST_EVENT_DATE END) AS FIRST_ECHO_CARDIOGRAPHY_DATE,
        MAX(CASE WHEN EVENT_NAME = 'PX_GENETIC_TESTING' THEN FIRST_EVENT_DATE END) AS FIRST_GENETIC_TESTING_DATE,
        MAX(CASE WHEN EVENT_NAME = 'PX_PROTEIN_ELECTROPHORESIS' THEN FIRST_EVENT_DATE END) AS FIRST_PROTEIN_ELECTROPHORESIS_DATE,
        MAX(CASE WHEN EVENT_NAME = 'PX_KAPPA_LAMBDA' THEN FIRST_EVENT_DATE END) AS FIRST_KAPPA_LAMBDA_DATE
    FROM event_first_dates
    GROUP BY PATIENT_ID
)
SELECT
    c.PATIENT_ID,
    c.CLAIM_ROW_COUNT,
    c.FIRST_CLAIM_DATE,
    c.LAST_CLAIM_DATE,
    p.FIRST_AFIB_DATE,
    p.FIRST_CARPAL_TUNNEL_DATE,
    p.FIRST_AORTIC_STENOSIS_DATE,
    p.FIRST_BICEP_RUPTURE_DATE,
    p.FIRST_SPINAL_STENOSIS_DATE,
    p.FIRST_AV_BLOCK_DATE,
    p.FIRST_HF_DATE,
    p.FIRST_HFPEF_DATE,
    p.FIRST_PYP_DATE,
    p.FIRST_IHC_DATE,
    p.FIRST_TAVR_DATE,
    p.FIRST_SERUM_IMMUNOFIXATION_DATE,
    p.FIRST_URINE_IMMUNOFIXATION_DATE,
    p.FIRST_MS_DATE,
    p.FIRST_MYOCARDIAL_DATE,
    p.FIRST_RECTAL_BIOPSY_DATE,
    p.FIRST_FAT_PAD_BIOPSY_DATE,
    p.FIRST_ECHO_STRAIN_DATE,
    p.FIRST_CARDIAC_MRI_DATE,
    p.FIRST_ECG_DATE,
    p.FIRST_FREE_LIGHT_CHAIN_DATE,
    p.FIRST_CARDIAC_BIOPSY_DATE,
    p.FIRST_ECHO_CARDIOGRAPHY_DATE,
    p.FIRST_GENETIC_TESTING_DATE,
    p.FIRST_PROTEIN_ELECTROPHORESIS_DATE,
    p.FIRST_KAPPA_LAMBDA_DATE,
    COALESCE(r.TOTAL_CONDITIONS, 0) AS TOTAL_CONDITIONS,
    r.FIRST_RED_FLAG_DATE,
    d2.FIRST_EVENT_DATE AS DATE_2PLUS_QUALIFIED,
    CASE WHEN COALESCE(r.TOTAL_CONDITIONS, 0) >= 2 THEN 1 ELSE 0 END AS FLAG_2PLUS_CONDITIONS,
    CASE WHEN COALESCE(r.TOTAL_CONDITIONS, 0) >= 2 AND p.FIRST_HF_DATE IS NOT NULL THEN 1 ELSE 0 END AS FLAG_2PLUS_AND_HF,
    CASE WHEN COALESCE(r.TOTAL_CONDITIONS, 0) >= 2 AND p.FIRST_HFPEF_DATE IS NOT NULL THEN 1 ELSE 0 END AS FLAG_2PLUS_AND_HFPEF,
    p.FIRST_ATTR_CM_DX_DATE,
    CASE WHEN p.FIRST_ATTR_CM_DX_DATE IS NOT NULL THEN 1 ELSE 0 END AS ATTR_CM_DIAGNOSED_FLAG,
    COALESCE(rb.RED_FLAGS_BEFORE_ATTR_DX, 0) AS RED_FLAGS_BEFORE_ATTR_DX,
    CASE
        WHEN p.FIRST_HF_DATE IS NOT NULL AND p.FIRST_ATTR_CM_DX_DATE IS NOT NULL
        THEN DATEDIFF('day', p.FIRST_HF_DATE, p.FIRST_ATTR_CM_DX_DATE)
        ELSE NULL
    END AS DAYS_FROM_FIRST_HF_TO_ATTR_DX,
    CASE
        WHEN r.FIRST_RED_FLAG_DATE IS NOT NULL AND p.FIRST_ATTR_CM_DX_DATE IS NOT NULL
        THEN DATEDIFF('day', r.FIRST_RED_FLAG_DATE, p.FIRST_ATTR_CM_DX_DATE)
        ELSE NULL
    END AS DAYS_FROM_FIRST_RED_FLAG_TO_ATTR_DX,
    CASE
        WHEN d2.FIRST_EVENT_DATE IS NOT NULL AND p.FIRST_ATTR_CM_DX_DATE IS NOT NULL
        THEN DATEDIFF('day', d2.FIRST_EVENT_DATE, p.FIRST_ATTR_CM_DX_DATE)
        ELSE NULL
    END AS DAYS_FROM_2PLUS_TO_ATTR_DX
FROM claim_span c
LEFT JOIN pivoted_events p
    ON c.PATIENT_ID = p.PATIENT_ID
LEFT JOIN red_flag_counts r
    ON c.PATIENT_ID = r.PATIENT_ID
LEFT JOIN red_flags_before_attr_dx rb
    ON c.PATIENT_ID = rb.PATIENT_ID
LEFT JOIN red_flag_ranked d2
    ON c.PATIENT_ID = d2.PATIENT_ID
   AND d2.RED_FLAG_RANK = 2
""".strip()


def execute_create_table(session, table_name, select_sql):
    session.sql(f"CREATE OR REPLACE TABLE {table_name} AS\n{select_sql}").collect()


def count_rows(session, table_name):
    return session.table(table_name).count()


def preview_table(session, table_name, limit_rows=10, patient_ids=None):
    if patient_ids:
        patient_filter = _quoted_csv(patient_ids)
        sql = (
            f"SELECT * FROM {table_name} "
            f"WHERE PATIENT_ID IN ({patient_filter}) "
            f"ORDER BY 1, 2 "
            f"LIMIT {limit_rows}"
        )
    else:
        sql = f"SELECT * FROM {table_name} LIMIT {limit_rows}"

    return session.sql(sql).to_pandas()


def main():
    source_table = os.getenv("SNOWFLAKE_SOURCE_TABLE", SOURCE_TABLE).strip()
    procedure_source_table = os.getenv("SNOWFLAKE_PROCEDURE_SOURCE_TABLE", PROCEDURE_SOURCE_TABLE).strip()
    event_table = os.getenv("SNOWFLAKE_EVENT_TABLE", EVENT_TABLE).strip()
    milestone_table = os.getenv("SNOWFLAKE_MILESTONE_TABLE", MILESTONE_TABLE).strip()
    patient_ids = _parse_patient_ids(os.getenv("SNOWFLAKE_PATIENT_IDS", ""))
    print_sql_only = _parse_bool(os.getenv("SNOWFLAKE_PRINT_SQL_ONLY", ""))

    _validate_table_identifier(source_table)
    _validate_table_identifier(procedure_source_table)
    _validate_table_identifier(event_table)
    _validate_table_identifier(milestone_table)

    events_sql = build_events_query(source_table, procedure_source_table, patient_ids)
    milestones_sql = build_milestones_query(source_table, procedure_source_table, event_table, patient_ids)

    if print_sql_only:
        print("EVENT TABLE SQL")
        print("=" * 80)
        print(events_sql)
        print()
        print("MILESTONE TABLE SQL")
        print("=" * 80)
        print(milestones_sql)
        return 0

    try:
        session = build_session()
    except Exception as exc:
        print(f"Session creation failed: {exc}")
        return 1

    print(f"Connected warehouse: {session.get_current_warehouse()}")
    print(f"Connected database : {session.get_current_database()}")
    print(f"Connected schema   : {session.get_current_schema()}")
    print(f"Connected role     : {session.get_current_role()}")

    if patient_ids:
        print(f"Running targeted build for patient IDs: {', '.join(patient_ids)}")
    else:
        print("Running full-cohort build. This may take several minutes.")

    try:
        print(f"Creating event table: {event_table}")
        execute_create_table(session, event_table, events_sql)
        print(f"Event rows: {count_rows(session, event_table):,}")

        print(f"Creating milestone table: {milestone_table}")
        execute_create_table(session, milestone_table, milestones_sql)
        print(f"Milestone rows: {count_rows(session, milestone_table):,}")

        print("Event table preview:")
        print(preview_table(session, event_table, limit_rows=10, patient_ids=patient_ids).to_string(index=False))
        print()
        print("Milestone table preview:")
        print(preview_table(session, milestone_table, limit_rows=10, patient_ids=patient_ids).to_string(index=False))
        print("Done.")
    except Exception as exc:
        print(f"Execution failed: {exc}")
        session.close()
        return 1

    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

