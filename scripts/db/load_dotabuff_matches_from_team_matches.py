#!/usr/bin/env python3
"""Load detailed Dotabuff matches from ids stored in dotabuff_team_matches.

Usage:
    python3 scripts/db/load_dotabuff_matches_from_team_matches.py
    python3 scripts/db/load_dotabuff_matches_from_team_matches.py --limit 20
    python3 scripts/db/load_dotabuff_matches_from_team_matches.py --team-id 9247354 --missing-only
    python3 scripts/db/load_dotabuff_matches_from_team_matches.py --match-id 8776543835
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))
from scripts.db.load_dotabuff_matches import require_match_id, upsert_match
from scripts.db.postgres_common import ensure_schema, load_parser_env, make_target_conninfo, require_psycopg
from scripts.parsing.dotabuff_match_parser import fetch_match_html, parse_match


@dataclass
class MatchCandidate:
    match_id: int
    team_id: int | None
    played_at: Any


def build_candidate_query(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    clauses = []
    params: dict[str, Any] = {
        "limit": args.limit,
    }

    if args.match_id:
        clauses.append("tm.match_id = %(match_id)s")
        params["match_id"] = args.match_id
    if args.team_id:
        clauses.append("tm.team_id = %(team_id)s")
        params["team_id"] = args.team_id
    if args.missing_only:
        clauses.append("m.match_id IS NULL")

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    direction = "ASC" if args.oldest_first else "DESC"
    return (
        f"""
        WITH ranked AS (
            SELECT
                tm.match_id,
                tm.team_id,
                tm.played_at,
                row_number() OVER (
                    PARTITION BY tm.match_id
                    ORDER BY tm.played_at {direction} NULLS LAST
                ) AS row_num
            FROM dotabuff_team_matches tm
            LEFT JOIN dotabuff_matches m ON m.match_id = tm.match_id
            {where_sql}
        )
        SELECT
            match_id,
            team_id,
            played_at
        FROM ranked
        WHERE row_num = 1
        ORDER BY played_at {direction} NULLS LAST, match_id {direction}
        LIMIT %(limit)s
        """,
        params,
    )


def fetch_candidates(args: argparse.Namespace, database: str | None = None) -> list[MatchCandidate]:
    psycopg, _, _, _, dict_row = require_psycopg()
    query, params = build_candidate_query(args)

    with psycopg.connect(make_target_conninfo(database=database), row_factory=dict_row) as conn:
        if args.init:
            ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    return [
        MatchCandidate(
            match_id=int(row["match_id"]),
            team_id=int(row["team_id"]) if row["team_id"] is not None else None,
            played_at=row["played_at"],
        )
        for row in rows
    ]


def parse_and_upsert_match(match_id: int, args: argparse.Namespace) -> int:
    html, source_url, fetched_match_id = fetch_match_html(str(match_id), args.timeout, args.insecure)
    parsed = parse_match(html, match_id=fetched_match_id or match_id, source_url=source_url)
    parsed_match_id = require_match_id(parsed)
    if parsed_match_id != match_id:
        raise ValueError(f"Expected match_id {match_id}, parsed {parsed_match_id}")
    return upsert_match(parsed, init_schema=False, database=args.database)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch detailed Dotabuff matches from dotabuff_team_matches ids.")
    parser.add_argument("--match-id", type=int, help="Load one specific match id from dotabuff_team_matches.")
    parser.add_argument("--team-id", type=int, help="Only load matches listed for this Dotabuff team id.")
    parser.add_argument("--limit", type=int, default=1, help="Maximum number of matches to load.")
    parser.add_argument("--missing-only", action="store_true", help="Only load matches absent from dotabuff_matches.")
    parser.add_argument("--oldest-first", action="store_true", help="Process older team matches before newer ones.")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between Dotabuff requests.")
    parser.add_argument("--env-file", default=".env", help="Load Dotabuff and Postgres settings from this env file.")
    parser.add_argument("--database", help="Target database name. Defaults to POSTGRES_DB or DATABASE_URL dbname.")
    parser.add_argument("--timeout", type=int, default=20, help="Network timeout in seconds.")
    parser.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification for Dotabuff fetch.")
    parser.add_argument("--init", action="store_true", help="Apply table schema before selecting candidates.")
    parser.add_argument("--keep-going", action="store_true", help="Continue after individual match failures.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.match_id and args.limit != 1:
        parser.error("--match-id can only be used with --limit 1")

    load_parser_env(args.env_file)
    candidates = fetch_candidates(args, database=args.database)
    if not candidates:
        print("no matches to load")
        return 0

    loaded = 0
    failed = 0
    for index, candidate in enumerate(candidates, start=1):
        try:
            players_count = parse_and_upsert_match(candidate.match_id, args)
        except Exception as exc:
            failed += 1
            print(f"[{index}/{len(candidates)}] failed match {candidate.match_id}: {exc}", file=sys.stderr)
            if not args.keep_going:
                return 1
        else:
            loaded += 1
            print(f"[{index}/{len(candidates)}] upserted match {candidate.match_id}, players: {players_count}")

        if args.sleep > 0 and index < len(candidates):
            time.sleep(args.sleep)

    print(f"loaded: {loaded}")
    print(f"failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
