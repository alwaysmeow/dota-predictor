"""Shared Postgres helpers for parser import scripts."""

from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))
from scripts.parsing.dotabuff_common import load_env_file


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "db" / "schema.sql"


def require_psycopg():
    try:
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - environment guard
        raise SystemExit(
            "Postgres scripts need psycopg. Install dependencies with: python3 -m pip install -r requirements.txt"
        ) from exc
    return psycopg, sql, conninfo_to_dict, make_conninfo, dict_row


def load_parser_env(path: str) -> None:
    load_env_file(path)


def target_database_name(default: str = "dota_predictor") -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        _, _, conninfo_to_dict, _, _ = require_psycopg()
        parsed = conninfo_to_dict(database_url)
        return parsed.get("dbname") or os.environ.get("POSTGRES_DB", default)
    return os.environ.get("POSTGRES_DB", default)


def make_target_conninfo(database: str | None = None) -> str:
    _, _, _, make_conninfo, _ = require_psycopg()
    database_url = os.environ.get("DATABASE_URL")
    dbname = database or target_database_name()
    if database_url:
        return make_conninfo(database_url, dbname=dbname)

    values = {
        "host": os.environ.get("POSTGRES_HOST", "localhost"),
        "port": os.environ.get("POSTGRES_PORT", "5432"),
        "user": os.environ.get("POSTGRES_USER", "postgres"),
        "password": os.environ.get("POSTGRES_PASSWORD"),
        "dbname": dbname,
        "sslmode": os.environ.get("POSTGRES_SSLMODE"),
    }
    return make_conninfo("", **{key: value for key, value in values.items() if value})


def make_maintenance_conninfo() -> str:
    maintenance_db = os.environ.get("POSTGRES_MAINTENANCE_DB", "postgres")
    return make_target_conninfo(database=maintenance_db)


def read_schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(read_schema_sql())
    conn.commit()
