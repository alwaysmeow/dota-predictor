#!/usr/bin/env python3
"""Continuously ingest Dotabuff team match pages and match details.

By default this runner loads Postgres and optional Dotabuff headers from .env.

Usage:
    python3 scripts/db/run_dotabuff_ingest.py --init
    python3 scripts/db/run_dotabuff_ingest.py --once --team-limit 10 --match-limit 20
    python3 scripts/db/run_dotabuff_ingest.py --team-id 9247354 --pages-per-team 2
    python3 scripts/db/run_dotabuff_ingest.py --team-id 9247354 --page-offset 5 --pages-per-team 5
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.db.load_dotabuff_matches_from_team_matches import MatchCandidate, parse_and_upsert_match
from scripts.db.load_dotabuff_team_matches import upsert_team_matches
from scripts.db.postgres_common import ensure_schema, load_parser_env, make_target_conninfo, require_psycopg
from scripts.parsing.dotabuff_team_matches_parser import (
    TeamMatchesTableNotFoundError,
    fetch_team_matches_html,
    parse_team_matches,
)


LOGGER = logging.getLogger("dotabuff_ingest")


@dataclass(frozen=True)
class TeamCandidate:
    team_id: int
    slug: str | None
    name: str | None
    last_match_at: Any
    known_matches: int
    last_indexed_at: Any

    @property
    def dotabuff_ref(self) -> str:
        return f"{self.team_id}-{self.slug}" if self.slug else str(self.team_id)

    @property
    def label(self) -> str:
        name = self.name or self.slug or "unknown"
        return f"{name} ({self.team_id})"


def parse_team_ids(values: list[str] | None) -> list[int]:
    if not values:
        return []

    team_ids: list[int] = []
    for value in values:
        for part in value.split(","):
            stripped = part.strip()
            if stripped:
                team_ids.append(int(stripped))
    return team_ids


def fetch_team_candidates(args: argparse.Namespace) -> list[TeamCandidate]:
    psycopg, _, _, _, dict_row = require_psycopg()
    team_ids = parse_team_ids(args.team_id)
    params: dict[str, Any] = {"limit": args.team_limit}
    clauses = []
    if team_ids:
        clauses.append("t.team_id = ANY(%(team_ids)s)")
        params["team_ids"] = team_ids

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit_sql = "" if team_ids and args.team_limit <= 0 else "LIMIT %(limit)s"
    query = f"""
        SELECT
            t.team_id,
            t.slug,
            t.name,
            t.last_match_at,
            count(tm.match_id) AS known_matches,
            max(tm.updated_at) AS last_indexed_at
        FROM dotabuff_teams t
        LEFT JOIN dotabuff_team_matches tm ON tm.team_id = t.team_id
        {where_sql}
        GROUP BY t.team_id, t.slug, t.name, t.last_match_at, t.popularity_rank
        ORDER BY
            max(tm.updated_at) ASC NULLS FIRST,
            t.last_match_at DESC NULLS LAST,
            t.popularity_rank ASC NULLS LAST,
            t.team_id ASC
        {limit_sql}
    """

    with psycopg.connect(make_target_conninfo(database=args.database), row_factory=dict_row) as conn:
        if args.init:
            ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    return [
        TeamCandidate(
            team_id=int(row["team_id"]),
            slug=row["slug"],
            name=row["name"],
            last_match_at=row["last_match_at"],
            known_matches=int(row["known_matches"] or 0),
            last_indexed_at=row["last_indexed_at"],
        )
        for row in rows
    ]


def fetch_missing_match_candidates(args: argparse.Namespace) -> list[MatchCandidate]:
    psycopg, _, _, _, dict_row = require_psycopg()
    params: dict[str, Any] = {"limit": args.match_limit}
    team_ids = parse_team_ids(args.team_id)
    clauses = ["m.match_id IS NULL"]
    if team_ids:
        clauses.append("tm.team_id = ANY(%(team_ids)s)")
        params["team_ids"] = team_ids

    query = f"""
        WITH ranked AS (
            SELECT
                tm.match_id,
                tm.team_id,
                tm.played_at,
                row_number() OVER (
                    PARTITION BY tm.match_id
                    ORDER BY tm.played_at DESC NULLS LAST
                ) AS row_num
            FROM dotabuff_team_matches tm
            LEFT JOIN dotabuff_matches m ON m.match_id = tm.match_id
            WHERE {' AND '.join(clauses)}
        )
        SELECT match_id, team_id, played_at
        FROM ranked
        WHERE row_num = 1
        ORDER BY played_at DESC NULLS LAST, match_id DESC
        LIMIT %(limit)s
    """

    with psycopg.connect(make_target_conninfo(database=args.database), row_factory=dict_row) as conn:
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


def sleep_between_requests(args: argparse.Namespace, reason: str) -> None:
    delay = args.request_sleep + random.uniform(0, args.request_jitter)
    if delay <= 0:
        return
    LOGGER.debug("sleeping %.1fs before %s", delay, reason)
    time.sleep(delay)


def index_team_match_pages(args: argparse.Namespace, teams: list[TeamCandidate]) -> tuple[int, int]:
    indexed_rows = 0
    failed_pages = 0

    for team_index, team in enumerate(teams, start=1):
        LOGGER.info(
            "team %s/%s: indexing %s, known_matches=%s, last_indexed_at=%s",
            team_index,
            len(teams),
            team.label,
            team.known_matches,
            team.last_indexed_at,
        )
        start_page = args.page_offset + 1
        end_page = args.page_offset + args.pages_per_team
        for page in range(start_page, end_page + 1):
            if team_index > 1 or page > start_page:
                sleep_between_requests(args, "next team match page")

            try:
                html, source_url = fetch_team_matches_html(
                    team.dotabuff_ref,
                    timeout=args.timeout,
                    insecure=args.insecure,
                    page=page,
                )
                parsed = parse_team_matches(html, source_url=source_url)
                count = upsert_team_matches(parsed, init_schema=False, database=args.database)
            except TeamMatchesTableNotFoundError as exc:
                LOGGER.info(
                    "team page has no matches table; stopping team pagination team_id=%s page=%s: %s",
                    team.team_id,
                    page,
                    exc,
                )
                break
            except SystemExit as exc:
                failed_pages += 1
                LOGGER.warning("failed team page team_id=%s page=%s: %s", team.team_id, page, exc)
                if not args.keep_going:
                    raise RuntimeError(f"failed team page team_id={team.team_id} page={page}: {exc}") from exc
            except Exception:
                failed_pages += 1
                LOGGER.exception("failed team page team_id=%s page=%s", team.team_id, page)
                if not args.keep_going:
                    raise
            else:
                indexed_rows += count
                pagination = parsed.get("pagination") or {}
                LOGGER.info(
                    "team page ok team_id=%s page=%s rows=%s next=%s",
                    team.team_id,
                    page,
                    count,
                    bool(pagination.get("next_url")),
                )
                if not pagination.get("next_url"):
                    break

    return indexed_rows, failed_pages


def load_match_details(args: argparse.Namespace) -> tuple[int, int]:
    candidates = fetch_missing_match_candidates(args)
    if not candidates:
        LOGGER.info("no missing match details to load")
        return 0, 0

    LOGGER.info("loading %s missing match detail pages", len(candidates))
    match_args = SimpleNamespace(
        timeout=args.timeout,
        insecure=args.insecure,
        database=args.database,
    )

    loaded = 0
    failed = 0
    for index, candidate in enumerate(candidates, start=1):
        sleep_between_requests(args, "next match detail page")

        try:
            players_count = parse_and_upsert_match(candidate.match_id, match_args)
        except SystemExit as exc:
            failed += 1
            LOGGER.warning("failed match %s: %s", candidate.match_id, exc)
            if not args.keep_going:
                raise RuntimeError(f"failed match {candidate.match_id}: {exc}") from exc
        except Exception:
            failed += 1
            LOGGER.exception("failed match %s", candidate.match_id)
            if not args.keep_going:
                raise
        else:
            loaded += 1
            LOGGER.info(
                "match ok %s/%s match_id=%s players=%s",
                index,
                len(candidates),
                candidate.match_id,
                players_count,
            )

    return loaded, failed


def run_cycle(args: argparse.Namespace, cycle: int) -> None:
    LOGGER.info("cycle %s started", cycle)

    teams = fetch_team_candidates(args)
    if not teams:
        LOGGER.warning("no teams found in dotabuff_teams; load teams first with scripts/db/load_dotabuff_teams.py")
        return

    indexed_rows, failed_pages = index_team_match_pages(args, teams)
    loaded_matches, failed_matches = load_match_details(args)
    LOGGER.info(
        "cycle %s finished: team_match_rows=%s failed_team_pages=%s loaded_matches=%s failed_matches=%s",
        cycle,
        indexed_rows,
        failed_pages,
        loaded_matches,
        failed_matches,
    )


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Continuously load Dotabuff team match indexes and match details.")
    parser.add_argument("--database", help="Target database name. Defaults to POSTGRES_DB or DATABASE_URL dbname.")
    parser.add_argument("--env-file", default=".env", help="Load Postgres and Dotabuff settings from this env file.")
    parser.add_argument("--team-id", action="append", help="Only ingest these team ids. Can be repeated or comma-separated.")
    parser.add_argument("--team-limit", type=int, default=25, help="Teams to scan per cycle. Use 0 for all selected --team-id values.")
    parser.add_argument("--pages-per-team", type=positive_int, default=1, help="Team match pages to scan for each team per cycle.")
    parser.add_argument("--page-offset", type=non_negative_int, default=0, help="Skip this many team match pages before scanning. For example, 5 starts from page 6.")
    parser.add_argument("--match-limit", type=positive_int, default=25, help="Missing detailed matches to load per cycle.")
    parser.add_argument("--request-sleep", type=non_negative_float, default=15.0, help="Base seconds between Dotabuff requests.")
    parser.add_argument("--request-jitter", type=non_negative_float, default=5.0, help="Extra random seconds added to request sleeps.")
    parser.add_argument("--cycle-sleep", type=non_negative_float, default=1800.0, help="Seconds between cycles.")
    parser.add_argument("--timeout", type=positive_int, default=20, help="Network timeout in seconds.")
    parser.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification for Dotabuff fetch.")
    parser.add_argument("--init", action="store_true", help="Apply table schema before the first query.")
    parser.add_argument("--keep-going", action="store_true", default=True, help="Continue after individual page failures.")
    parser.add_argument("--fail-fast", dest="keep_going", action="store_false", help="Stop on the first individual page failure.")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"), help="Logging verbosity.")
    return parser


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.team_limit < 0:
        parser.error("--team-limit must be non-negative")

    configure_logging(args.log_level)
    load_parser_env(args.env_file)
    LOGGER.info("starting Dotabuff ingest; loaded environment from %s", args.env_file)
    LOGGER.info(
        "settings: team_limit=%s pages_per_team=%s page_offset=%s match_limit=%s request_sleep=%.1fs jitter=%.1fs cycle_sleep=%.1fs",
        args.team_limit,
        args.pages_per_team,
        args.page_offset,
        args.match_limit,
        args.request_sleep,
        args.request_jitter,
        args.cycle_sleep,
    )

    cycle = 1
    while True:
        run_cycle(args, cycle)
        if args.once:
            return 0

        LOGGER.info("cycle %s sleeping %.1fs", cycle, args.cycle_sleep)
        time.sleep(args.cycle_sleep)
        cycle += 1


if __name__ == "__main__":
    raise SystemExit(main())
