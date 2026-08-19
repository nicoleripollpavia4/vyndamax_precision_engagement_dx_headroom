"""
connect_snowpark.py
--------------------
Standalone script to verify a Snowpark Session and preview one or more tables.
Fill in your credentials directly below, or set the corresponding
environment variables (recommended).

Usage:
    python connect_snowpark.py
"""

import os
import sys
from snowflake.snowpark import Session

# â”€â”€ Credentials â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Option A: set values here directly (for quick testing only).
# Option B: leave as None and set the matching environment variable instead.

ACCOUNT = os.getenv("SNOWFLAKE_ACCOUNT")   # e.g. "myorg-myaccount"  | env: SNOWFLAKE_ACCOUNT
USER = os.getenv("SNOWFLAKE_USER")   # e.g. "jane.doe@pfizer.com" | env: SNOWFLAKE_USER
AUTHENTICATOR = os.getenv("SNOWFLAKE_AUTHENTICATOR", "externalbrowser")   # "externalbrowser" (SSO) or "snowflake" (password)
                       # env: SNOWFLAKE_AUTHENTICATOR  (default: snowflake)
PASSWORD = os.getenv("SNOWFLAKE_PASSWORD")   # leave None when using SSO  | env: SNOWFLAKE_PASSWORD
ROLE = os.getenv("SNOWFLAKE_ROLE")   # e.g. "COMM_NAME_ROLE"        | env: SNOWFLAKE_ROLE
WAREHOUSE = os.getenv("SNOWFLAKE_WAREHOUSE")   # e.g. "COMPUTE_WH"          | env: SNOWFLAKE_WAREHOUSE

# â”€â”€ Target location â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
DATABASE = os.getenv("SNOWFLAKE_DATABASE")   # e.g. "MY_DATABASE"         | env: SNOWFLAKE_DATABASE
SCHEMA = os.getenv("SNOWFLAKE_SCHEMA")   # e.g. "MY_SCHEMA"           | env: SNOWFLAKE_SCHEMA
TABLE         = "VYNDA_ATTITUDINAL_SEGMENTATION.VYNDA_ATTITUDINAL_SEGMENTATION_SPP_DATA_INF_SEG_VYNDA_JOINED"   # e.g. "MY_TABLE"            | env: SNOWFLAKE_TABLE

# Optional: preview multiple tables by providing a list here or setting
# SNOWFLAKE_TABLES="DB.SCHEMA.TABLE_A,DB.SCHEMA.TABLE_B".
TABLES        = [
    "US_VYNDA_DT_2.VYNDA_ATTITUDINAL_SEGMENTATION_PATIENT_RX",
    "VYNDA_ATTITUDINAL_SEGMENTATION.VYNDA_ATTITUDINAL_SEGMENTATION_SPP_DATA_INF_SEG_VYNDA_JOINED",
]


# â”€â”€ Internal helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _resolve(local_val, env_key, required=True):
    value = local_val or os.getenv(env_key, "").strip() or None
    if required and not value:
        raise ValueError(
            f"Missing required value. Set the '{env_key}' environment variable "
            f"or assign the '{env_key.replace('SNOWFLAKE_', '').title()}' constant above."
        )
    return value


def resolve_tables() -> list[str]:
    """Resolve target tables from constants/env with safe fallbacks."""
    env_tables = os.getenv("SNOWFLAKE_TABLES", "")
    if env_tables.strip():
        return [t.strip() for t in env_tables.split(",") if t.strip()]

    if TABLES:
        return [t.strip() for t in TABLES if str(t).strip()]

    single_table = _resolve(TABLE, "SNOWFLAKE_TABLE", required=False)
    return [single_table] if single_table else []

def build_session() -> Session:
    account       = _resolve(ACCOUNT,       "SNOWFLAKE_ACCOUNT")
    user          = _resolve(USER,          "SNOWFLAKE_USER")
    authenticator = _resolve(AUTHENTICATOR, "SNOWFLAKE_AUTHENTICATOR", required=False) or "snowflake"
    password      = _resolve(PASSWORD,      "SNOWFLAKE_PASSWORD",      required=False)
    role          = _resolve(ROLE,          "SNOWFLAKE_ROLE",          required=False)
    warehouse     = _resolve(WAREHOUSE,     "SNOWFLAKE_WAREHOUSE")
    database      = _resolve(DATABASE,      "SNOWFLAKE_DATABASE")
    schema        = _resolve(SCHEMA,        "SNOWFLAKE_SCHEMA")

    if authenticator == "snowflake" and not password:
        raise ValueError(
            "SNOWFLAKE_PASSWORD is required when SNOWFLAKE_AUTHENTICATOR is 'snowflake'. "
            "Use 'externalbrowser' for SSO."
        )

    config = {
        "account":       account,
        "user":          user,
        "authenticator": authenticator,
        "warehouse":     warehouse,
        "database":      database,
        "schema":        schema,
    }
    if password:
        config["password"] = password
    if role:
        config["role"] = role

    # Optional proxy (picked up from env only)
    proxy_host = os.getenv("SNOWFLAKE_PROXY_HOST")
    proxy_port = os.getenv("SNOWFLAKE_PROXY_PORT")
    if proxy_host:
        config["proxy_host"] = proxy_host
    if proxy_port:
        config["proxy_port"] = int(proxy_port)

    return Session.builder.configs(config).create()


def preview_tables(session: Session, tables: list[str], limit: int = 10) -> int:
    """Preview each table and return non-zero on the first failure."""
    for table in tables:
        try:
            df = session.table(table)
            print(f"\nTable '{table}': {df.count():,} rows  x  {len(df.columns)} cols")
            print(f"First {limit} rows:")
            df.show(limit)
        except Exception as exc:
            print(f"Table preview failed for '{table}': {exc}")
            return 1

    return 0


def main() -> int:
    if _resolve(AUTHENTICATOR, "SNOWFLAKE_AUTHENTICATOR", required=False) == "externalbrowser":
        print("Starting SSO login in browser...")

    try:
        session = build_session()
    except Exception as exc:
        print(f"Session creation failed: {exc}")
        return 1

    print(f"Connected  â€”  warehouse : {session.get_current_warehouse()}")
    print(f"             database  : {session.get_current_database()}")
    print(f"             schema    : {session.get_current_schema()}")
    print(f"             role      : {session.get_current_role()}")

    tables = resolve_tables()
    if tables:
        preview_result = preview_tables(session, tables, limit=10)
        if preview_result != 0:
            return preview_result
    else:
        print("No table configured. Set TABLE, TABLES, SNOWFLAKE_TABLE, or SNOWFLAKE_TABLES.")

    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

