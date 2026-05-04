#!/usr/bin/env python3
"""Create the Postgres database and Dotabuff parser tables.

Usage:
    python3 scripts/db/init_postgres.py
    python3 scripts/db/init_postgres.py --database dota_predictor
    python3 scripts/db/init_postgres.py --skip-create-db

Connection settings are read from .env and environment variables:
    DATABASE_URL
    POSTGRES_HOST
    POSTGRES_PORT
    POSTGRES_USER
    POSTGRES_PASSWORD
    POSTGRES_DB
    POSTGRES_MAINTENANCE_DB
    POSTGRES_SSLMODE
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))
from scripts.db.postgres_common import (
    ensure_schema,
    load_parser_env,
    make_maintenance_conninfo,
    make_target_conninfo,
    require_psycopg,
    target_database_name,
)


def create_database_if_missing(database: str) -> bool:
    psycopg, sql, _, _, _ = require_psycopg()
    conn = psycopg.connect(make_maintenance_conninfo(), autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
            if cur.fetchone():
                return False
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            return True
    finally:
        conn.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create Postgres DB and parser tables.")
    parser.add_argument("--database", help="Target database name. Defaults to POSTGRES_DB or DATABASE_URL dbname.")
    parser.add_argument("--env-file", default=".env", help="Load Postgres settings from this env file.")
    parser.add_argument("--skip-create-db", action="store_true", help="Only apply schema in the target database.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    load_parser_env(args.env_file)
    database = args.database or target_database_name()

    if not args.skip_create_db:
        created = create_database_if_missing(database)
        print(f"database {database}: {'created' if created else 'already exists'}")

    psycopg, _, _, _, _ = require_psycopg()
    with psycopg.connect(make_target_conninfo(database=database)) as conn:
        ensure_schema(conn)
    print("schema applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
