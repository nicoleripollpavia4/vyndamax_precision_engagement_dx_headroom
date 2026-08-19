"""
create_undiagnosed_output_table.py
----------------------------------
Create a Snowflake output table using the headroom query provided by the user.

Usage:
    python create_undiagnosed_output_table.py

Credential resolution follows the same pattern as connect_snowpark.py:
1) constants below
2) environment variables (recommended)
"""

import os
import re
import sys
from snowflake.snowpark import Session

# -- Credentials ---------------------------------------------------------------
ACCOUNT = os.getenv("SNOWFLAKE_ACCOUNT")  # env: SNOWFLAKE_ACCOUNT
USER = os.getenv("SNOWFLAKE_USER")  # env: SNOWFLAKE_USER
AUTHENTICATOR = os.getenv("SNOWFLAKE_AUTHENTICATOR", "externalbrowser")  # env: SNOWFLAKE_AUTHENTICATOR
PASSWORD = None  # env: SNOWFLAKE_PASSWORD
ROLE = os.getenv("SNOWFLAKE_ROLE")  # env: SNOWFLAKE_ROLE
WAREHOUSE = os.getenv("SNOWFLAKE_WAREHOUSE")  # env: SNOWFLAKE_WAREHOUSE
DATABASE = os.getenv("SNOWFLAKE_DATABASE")  # env: SNOWFLAKE_DATABASE
SCHEMA = os.getenv("SNOWFLAKE_SCHEMA")  # env: SNOWFLAKE_SCHEMA

# -- Output table --------------------------------------------------------------
# Fully qualified name recommended: DB.SCHEMA.TABLE
OUTPUT_TABLE = "VAW_AMER_DESIGN.US_VYNDA_DT_2.US_VYNDA_DT_2_DX_MODEL_HEADROOM_OUTPUT_INF"

# -- Query ---------------------------------------------------------------------
RESULT_QUERY = """
with hfpef as (
    select b.*, 'HFPEF ADDITIONAL' as SOURCE
    from VAW_AMER_DESIGN.US_VYNDA_DT_2.US_VYNDA_DT_2_PATIENT_HCP_MAPPING a
    full outer join VAW_AMER_DESIGN.US_VYNDA_DT_2.US_VYNDA_DT_2_DX_MODEL_SIM_SCORE_INF b
        on a.patient_id = b.patient_id
    where a.pfz_cust_id is null
      and b.hfpef_bin = 1
),
model_1 as (
    select b.*, 'MODEL' as SOURCE
    from VAW_AMER_DESIGN.US_VYNDA_DT_2.US_VYNDA_DT_2_PATIENT_HCP_MAPPING a
    full outer join VAW_AMER_DESIGN.US_VYNDA_DT_2.US_VYNDA_DT_2_DX_MODEL_SIM_SCORE_INF b
        on a.patient_id = b.patient_id
    where a.pfz_cust_id is not null
)
select * from hfpef
union
select * from model_1
"""


# -- Internal helpers ----------------------------------------------------------

def _resolve(local_val, env_key, required=True):
    value = local_val or os.getenv(env_key, "").strip() or None
    if required and not value:
        raise ValueError(
            f"Missing required value. Set '{env_key}' or assign the matching constant in this script."
        )
    return value


def _validate_table_identifier(identifier):
    # Allow only letters, numbers, underscore, dot, and optional double quotes.
    # This keeps CREATE TABLE target predictable and avoids accidental SQL injection.
    pattern = r'^[A-Za-z0-9_\.\"]+$'
    if not re.fullmatch(pattern, identifier):
        raise ValueError(
            "Invalid OUTPUT_TABLE format. Use a simple identifier like DB.SCHEMA.TABLE "
            "with letters/numbers/underscore/dot."
        )


def build_session():
    account = _resolve(ACCOUNT, "SNOWFLAKE_ACCOUNT")
    user = _resolve(USER, "SNOWFLAKE_USER")
    authenticator = _resolve(AUTHENTICATOR, "SNOWFLAKE_AUTHENTICATOR", required=False) or "snowflake"
    password = _resolve(PASSWORD, "SNOWFLAKE_PASSWORD", required=False)
    role = _resolve(ROLE, "SNOWFLAKE_ROLE", required=False)
    warehouse = _resolve(WAREHOUSE, "SNOWFLAKE_WAREHOUSE")
    database = _resolve(DATABASE, "SNOWFLAKE_DATABASE")
    schema = _resolve(SCHEMA, "SNOWFLAKE_SCHEMA")

    if authenticator == "snowflake" and not password:
        raise ValueError(
            "SNOWFLAKE_PASSWORD is required when SNOWFLAKE_AUTHENTICATOR is 'snowflake'. "
            "Use 'externalbrowser' for SSO."
        )

    config = {
        "account": account,
        "user": user,
        "authenticator": authenticator,
        "warehouse": warehouse,
        "database": database,
        "schema": schema,
    }
    if password:
        config["password"] = password
    if role:
        config["role"] = role

    proxy_host = os.getenv("SNOWFLAKE_PROXY_HOST")
    proxy_port = os.getenv("SNOWFLAKE_PROXY_PORT")
    if proxy_host:
        config["proxy_host"] = proxy_host
    if proxy_port:
        config["proxy_port"] = int(proxy_port)

    return Session.builder.configs(config).create()


def execute_create_table(session, output_table):
    create_sql = f"create or replace table {output_table} as\n{RESULT_QUERY}"
    session.sql(create_sql).collect()


def count_rows(session, table_name):
    return session.table(table_name).count()


def main():
    output_table = os.getenv("SNOWFLAKE_OUTPUT_TABLE", OUTPUT_TABLE).strip()
    _validate_table_identifier(output_table)

    if _resolve(AUTHENTICATOR, "SNOWFLAKE_AUTHENTICATOR", required=False) == "externalbrowser":
        print("Starting SSO login in browser...")

    try:
        session = build_session()
    except Exception as exc:
        print(f"Session creation failed: {exc}")
        return 1

    print(f"Connected warehouse: {session.get_current_warehouse()}")
    print(f"Connected database : {session.get_current_database()}")
    print(f"Connected schema   : {session.get_current_schema()}")
    print(f"Connected role     : {session.get_current_role()}")

    try:
        src_a = "VAW_AMER_DESIGN.US_VYNDA_DT_2.US_VYNDA_DT_2_PATIENT_HCP_MAPPING"
        src_b = "VAW_AMER_DESIGN.US_VYNDA_DT_2.US_VYNDA_DT_2_DX_MODEL_SIM_SCORE_INF"

        print("Reading source table row counts...")
        print(f"{src_a}: {count_rows(session, src_a):,}")
        print(f"{src_b}: {count_rows(session, src_b):,}")

        print(f"Creating output table: {output_table}")
        execute_create_table(session, output_table)

        output_count = count_rows(session, output_table)
        print(f"Output row count: {output_count:,}")
        print("Done.")
    except Exception as exc:
        print(f"Execution failed: {exc}")
        session.close()
        return 1

    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

