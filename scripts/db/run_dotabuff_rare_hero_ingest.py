#!/usr/bin/env python3
"""Ingest public ranked Dotabuff matches for the least represented heroes.

The script picks rare heroes from dotabuff_match_players, stores a small
dotabuff_hero_matches index for each hero, then loads detailed match pages into
dotabuff_matches and dotabuff_match_players using the existing match parser.

Usage:
    python3 scripts/db/run_dotabuff_rare_hero_ingest.py --init
    python3 scripts/db/run_dotabuff_rare_hero_ingest.py -n 3
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

from scripts.db.load_dotabuff_matches_from_team_matches import parse_and_upsert_match
from scripts.db.postgres_common import ensure_schema, load_parser_env, make_target_conninfo, require_psycopg
from scripts.parsing.dotabuff_hero_matches_parser import (
    DEFAULT_LOBBY_TYPE,
    HeroMatchesTableNotFoundError,
    fetch_hero_matches_html,
    parse_hero_matches,
)


LOGGER = logging.getLogger("dotabuff_rare_hero_ingest")


UPSERT_HERO_MATCH_SQL = """
INSERT INTO dotabuff_hero_matches (
    hero_slug,
    match_id,
    source_url,
    winner_side,
    duration_seconds,
    duration,
    raw,
    updated_at
) VALUES (
    %(hero_slug)s,
    %(match_id)s,
    %(source_url)s,
    %(winner_side)s,
    %(duration_seconds)s,
    %(duration)s,
    %(raw)s::jsonb,
    now()
)
ON CONFLICT (hero_slug, match_id) DO UPDATE SET
    source_url = EXCLUDED.source_url,
    winner_side = EXCLUDED.winner_side,
    duration_seconds = EXCLUDED.duration_seconds,
    duration = EXCLUDED.duration,
    raw = EXCLUDED.raw,
    updated_at = now()
"""


@dataclass(frozen=True)
class HeroCandidate:
    hero_slug: str
    known_players: int


@dataclass(frozen=True)
class IndexedMatch:
    hero_slug: str
    match_id: int
    updated_at: Any = None


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


def fetch_rare_heroes(args: argparse.Namespace) -> list[HeroCandidate]:
    psycopg, _, _, _, dict_row = require_psycopg()
    query = """
        SELECT
            hero_slug,
            count(*) AS known_players
        FROM dotabuff_match_players
        WHERE hero_slug IS NOT NULL AND hero_slug <> ''
        GROUP BY hero_slug
        ORDER BY count(*) ASC, hero_slug ASC
        LIMIT %(limit)s
    """

    with psycopg.connect(make_target_conninfo(database=args.database), row_factory=dict_row) as conn:
        if args.init:
            ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(query, {"limit": args.hero_limit})
            rows = cur.fetchall()

    return [
        HeroCandidate(hero_slug=row["hero_slug"], known_players=int(row["known_players"] or 0))
        for row in rows
    ]


def hero_match_to_row(parsed: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    hero_slug = parsed.get("hero_slug")
    match_id = match.get("match_id")
    if not hero_slug:
        raise ValueError(f"Hero match page has no hero_slug: {parsed!r}")
    if match_id is None:
        raise ValueError(f"Hero match row has no match_id: {match!r}")

    return {
        "hero_slug": hero_slug,
        "match_id": match_id,
        "source_url": parsed.get("source_url"),
        "winner_side": match.get("winner_side"),
        "duration_seconds": match.get("duration_seconds"),
        "duration": match.get("duration"),
        "raw": None,
    }


def upsert_hero_matches(parsed: dict[str, Any], database: str | None) -> list[IndexedMatch]:
    psycopg, _, _, _, _ = require_psycopg()
    rows = [hero_match_to_row(parsed, match) for match in parsed.get("matches") or []]

    with psycopg.connect(make_target_conninfo(database=database)) as conn:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(UPSERT_HERO_MATCH_SQL, row)
        conn.commit()

    return [
        IndexedMatch(
            hero_slug=row["hero_slug"],
            match_id=int(row["match_id"]),
        )
        for row in rows
    ]


def sleep_between_requests(args: argparse.Namespace, reason: str) -> None:
    delay = args.request_sleep + random.uniform(0, args.request_jitter)
    if delay <= 0:
        return
    LOGGER.debug("sleeping %.1fs before %s", delay, reason)
    time.sleep(delay)


def index_hero_pages(args: argparse.Namespace, heroes: list[HeroCandidate]) -> tuple[list[IndexedMatch], int]:
    indexed_matches: list[IndexedMatch] = []
    failed_pages = 0

    for hero_index, hero in enumerate(heroes, start=1):
        LOGGER.info(
            "hero %s/%s: indexing %s, known_players=%s",
            hero_index,
            len(heroes),
            hero.hero_slug,
            hero.known_players,
        )
        if hero_index > 1:
            sleep_between_requests(args, "next hero match page")

        try:
            html, source_url = fetch_hero_matches_html(
                hero.hero_slug,
                timeout=args.timeout,
                insecure=args.insecure,
                lobby_type=args.lobby_type,
            )
            parsed = parse_hero_matches(html, source_url=source_url)
            rows = upsert_hero_matches(parsed, database=args.database)
        except HeroMatchesTableNotFoundError as exc:
            LOGGER.info("hero page has no matches table; hero=%s: %s", hero.hero_slug, exc)
        except SystemExit as exc:
            failed_pages += 1
            LOGGER.warning("failed hero page hero=%s: %s", hero.hero_slug, exc)
            if not args.keep_going:
                raise RuntimeError(f"failed hero page hero={hero.hero_slug}: {exc}") from exc
        except Exception:
            failed_pages += 1
            LOGGER.exception("failed hero page hero=%s", hero.hero_slug)
            if not args.keep_going:
                raise
        else:
            indexed_matches.extend(rows)
            LOGGER.info("hero page ok hero=%s rows=%s", hero.hero_slug, len(rows))

    return indexed_matches, failed_pages


def fetch_queued_match_candidates(args: argparse.Namespace) -> tuple[list[int], int]:
    psycopg, _, _, _, dict_row = require_psycopg()
    params: dict[str, Any] = {}
    join_sql = "" if args.reload_matches else "LEFT JOIN dotabuff_matches m ON m.match_id = hm.match_id"
    where_sql = "" if args.reload_matches else "WHERE m.match_id IS NULL"
    limit_sql = ""
    if args.match_limit is not None:
        params["limit"] = args.match_limit
        limit_sql = "LIMIT %(limit)s"

    query = f"""
        WITH ranked AS (
            SELECT
                hm.match_id,
                max(hm.updated_at) AS last_indexed_at
            FROM dotabuff_hero_matches hm
            {join_sql}
            {where_sql}
            GROUP BY hm.match_id
        )
        SELECT match_id
        FROM ranked
        ORDER BY last_indexed_at DESC NULLS LAST, match_id DESC
        {limit_sql}
    """

    with psycopg.connect(make_target_conninfo(database=args.database), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    candidates = [int(row["match_id"]) for row in rows]
    skipped = 0
    if not args.reload_matches:
        count_query = """
            SELECT count(DISTINCT hm.match_id) AS skipped
            FROM dotabuff_hero_matches hm
            JOIN dotabuff_matches m ON m.match_id = hm.match_id
        """
        with psycopg.connect(make_target_conninfo(database=args.database), row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(count_query)
                row = cur.fetchone()
                skipped = int(row["skipped"] or 0)
    return candidates, skipped


def load_match_details(args: argparse.Namespace) -> tuple[int, int, int]:
    candidates, skipped = fetch_queued_match_candidates(args)
    if not candidates:
        LOGGER.info("no new match details to load; skipped_existing=%s", skipped)
        return 0, 0, skipped

    match_args = SimpleNamespace(timeout=args.timeout, insecure=args.insecure, database=args.database)
    loaded = 0
    failed = 0
    for index, match_id in enumerate(candidates, start=1):
        sleep_between_requests(args, "next match detail page")
        try:
            players_count = parse_and_upsert_match(match_id, match_args)
        except SystemExit as exc:
            failed += 1
            LOGGER.warning("failed match %s: %s", match_id, exc)
            if not args.keep_going:
                raise RuntimeError(f"failed match {match_id}: {exc}") from exc
        except Exception:
            failed += 1
            LOGGER.exception("failed match %s", match_id)
            if not args.keep_going:
                raise
        else:
            loaded += 1
            LOGGER.info("match ok %s/%s match_id=%s players=%s", index, len(candidates), match_id, players_count)

    return loaded, failed, skipped


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest public Dotabuff matches for least represented heroes.")
    parser.add_argument("-n", "--hero-limit", type=positive_int, default=1, help="Number of rare heroes to ingest.")
    parser.add_argument("--match-limit", type=positive_int, help="Maximum detailed match pages to load after indexing.")
    parser.add_argument("--lobby-type", default=DEFAULT_LOBBY_TYPE, help="Dotabuff lobby_type query value.")
    parser.add_argument("--request-sleep", type=non_negative_float, default=15.0, help="Base seconds between Dotabuff requests.")
    parser.add_argument("--request-jitter", type=non_negative_float, default=5.0, help="Extra random seconds added to sleeps.")
    parser.add_argument("--timeout", type=positive_int, default=20, help="Network timeout in seconds.")
    parser.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification for Dotabuff fetch.")
    parser.add_argument("--reload-matches", action="store_true", help="Reload match details even if dotabuff_matches has them.")
    parser.add_argument("--init", action="store_true", help="Apply table schema before querying.")
    parser.add_argument("--keep-going", action="store_true", default=True, help="Continue after individual page failures.")
    parser.add_argument("--fail-fast", dest="keep_going", action="store_false", help="Stop on the first page failure.")
    parser.add_argument("--env-file", default=".env", help="Load Dotabuff and Postgres settings from this env file.")
    parser.add_argument("--database", help="Target database name. Defaults to POSTGRES_DB or DATABASE_URL dbname.")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"), help="Logging verbosity.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    configure_logging(args.log_level)
    load_parser_env(args.env_file)
    LOGGER.info(
        "starting rare hero ingest: hero_limit=%s match_limit=%s request_sleep=%.1fs jitter=%.1fs",
        args.hero_limit,
        args.match_limit,
        args.request_sleep,
        args.request_jitter,
    )

    heroes = fetch_rare_heroes(args)
    if not heroes:
        LOGGER.warning("no heroes found in dotabuff_match_players")
        return 0

    indexed_matches, failed_pages = index_hero_pages(args, heroes)
    loaded_matches, failed_matches, skipped_matches = load_match_details(args)
    LOGGER.info(
        "finished: heroes=%s indexed_match_rows=%s failed_hero_pages=%s loaded_matches=%s failed_matches=%s skipped_existing=%s",
        len(heroes),
        len(indexed_matches),
        failed_pages,
        loaded_matches,
        failed_matches,
        skipped_matches,
    )
    return 1 if failed_pages or failed_matches else 0


if __name__ == "__main__":
    raise SystemExit(main())
