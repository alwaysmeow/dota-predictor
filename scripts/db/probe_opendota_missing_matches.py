#!/usr/bin/env python3
"""Load missing Dotabuff match ids from OpenDota into Postgres.

This selects match ids present in dotabuff_team_matches or dotabuff_hero_matches
but absent from dotabuff_matches, then fetches /api/matches/{match_id} from
OpenDota and upserts a compact representation into opendota_matches.

Usage:
    python3 scripts/db/probe_opendota_missing_matches.py --init --limit 50
    python3 scripts/db/probe_opendota_missing_matches.py --source hero --limit 50 --cycle-sleep 300
    python3 scripts/db/probe_opendota_missing_matches.py --match-id 8786827560 --once
    python3 scripts/db/probe_opendota_missing_matches.py --limit 1 --dry-run --pretty --once
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import ssl
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.db.postgres_common import ensure_schema, load_parser_env, make_target_conninfo, require_psycopg


OPENDOTA_MATCH_URL = "https://api.opendota.com/api/matches/{match_id}"
REPO_ROOT = Path(__file__).resolve().parents[2]
HEROES_PATH = REPO_ROOT / "dotaconstants" / "build" / "heroes.json"
LOGGER = logging.getLogger("opendota_ingest")


UPSERT_OPENDOTA_MATCH_SQL = """
INSERT INTO opendota_matches (
    match_id,
    source_url,
    played_at,
    radiant_team_id,
    radiant_team_name,
    dire_team_id,
    dire_team_name,
    winner_side,
    winner_team_id,
    winner_team_name,
    players,
    raw,
    updated_at
) VALUES (
    %(match_id)s,
    %(source_url)s,
    %(played_at)s,
    %(radiant_team_id)s,
    %(radiant_team_name)s,
    %(dire_team_id)s,
    %(dire_team_name)s,
    %(winner_side)s,
    %(winner_team_id)s,
    %(winner_team_name)s,
    %(players)s::jsonb,
    %(raw)s::jsonb,
    now()
)
ON CONFLICT (match_id) DO UPDATE SET
    source_url = EXCLUDED.source_url,
    played_at = EXCLUDED.played_at,
    radiant_team_id = EXCLUDED.radiant_team_id,
    radiant_team_name = EXCLUDED.radiant_team_name,
    dire_team_id = EXCLUDED.dire_team_id,
    dire_team_name = EXCLUDED.dire_team_name,
    winner_side = EXCLUDED.winner_side,
    winner_team_id = EXCLUDED.winner_team_id,
    winner_team_name = EXCLUDED.winner_team_name,
    players = EXCLUDED.players,
    raw = EXCLUDED.raw,
    updated_at = now()
"""


@dataclass(frozen=True)
class MatchCandidate:
    match_id: int
    sources: list[str]
    last_seen_at: Any


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


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def build_candidate_query(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {"limit": args.limit}
    source_selects = []

    if args.source in {"all", "team"}:
        source_selects.append(
            """
            SELECT
                tm.match_id,
                'team' AS source,
                tm.updated_at AS last_seen_at
            FROM dotabuff_team_matches tm
            """
        )

    if args.source in {"all", "hero"}:
        source_selects.append(
            """
            SELECT
                hm.match_id,
                'hero' AS source,
                hm.updated_at AS last_seen_at
            FROM dotabuff_hero_matches hm
            """
        )

    source_sql = "\nUNION ALL\n".join(source_selects)
    direction = "ASC" if args.oldest_first else "DESC"

    return (
        f"""
        WITH source_matches AS (
            {source_sql}
        ),
        missing_dotabuff_matches AS (
            SELECT
                sm.match_id,
                array_agg(DISTINCT sm.source ORDER BY sm.source) AS sources,
                max(sm.last_seen_at) AS last_seen_at
            FROM source_matches sm
            LEFT JOIN dotabuff_matches m ON m.match_id = sm.match_id
            WHERE m.match_id IS NULL
            GROUP BY sm.match_id
        )
        SELECT mdm.match_id, mdm.sources, mdm.last_seen_at
        FROM missing_dotabuff_matches mdm
        {"LEFT JOIN opendota_matches om ON om.match_id = mdm.match_id" if not args.reload else ""}
        {"WHERE om.match_id IS NULL" if not args.reload else ""}
        ORDER BY last_seen_at {direction} NULLS LAST, match_id {direction}
        LIMIT %(limit)s
        """,
        params,
    )


def fetch_candidates(args: argparse.Namespace) -> list[MatchCandidate]:
    if args.match_id is not None:
        return [MatchCandidate(match_id=args.match_id, sources=["manual"], last_seen_at=None)]

    psycopg, _, _, _, dict_row = require_psycopg()
    query, params = build_candidate_query(args)

    with psycopg.connect(make_target_conninfo(database=args.database), row_factory=dict_row) as conn:
        if args.init:
            ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    return [
        MatchCandidate(
            match_id=int(row["match_id"]),
            sources=list(row["sources"] or []),
            last_seen_at=row["last_seen_at"],
        )
        for row in rows
    ]


def fetch_opendota_match(match_id: int, timeout: int, insecure: bool = False) -> dict[str, Any]:
    url = OPENDOTA_MATCH_URL.format(match_id=match_id)
    request = Request(
        url,
        headers={
            "User-Agent": "dota-predictor-opendota-probe/1.0",
            "Accept": "application/json",
        },
    )

    context = ssl._create_unverified_context() if insecure else None
    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(charset, errors="replace"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenDota returned HTTP {exc.code}: {body[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not fetch OpenDota match {match_id}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenDota returned non-JSON response for match {match_id}: {exc}") from exc


def load_hero_names(path: Path = HEROES_PATH) -> dict[int, str]:
    heroes = json.loads(path.read_text(encoding="utf-8"))
    return {
        int(hero["id"]): str(hero.get("localized_name") or hero.get("name") or hero["id"])
        for hero in heroes.values()
    }


def player_side(player_slot: Any) -> str | None:
    if player_slot is None:
        return None
    slot = int(player_slot)
    return "radiant" if slot < 128 else "dire"


def format_start_time(start_time: Any) -> str | None:
    if start_time is None:
        return None
    return datetime.fromtimestamp(int(start_time), tz=timezone.utc).isoformat()


def parse_start_time(start_time: Any) -> datetime | None:
    if start_time is None:
        return None
    return datetime.fromtimestamp(int(start_time), tz=timezone.utc)


def winner_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    radiant_win = payload.get("radiant_win")
    if radiant_win is None:
        return None

    side = "radiant" if radiant_win else "dire"
    return {
        "side": side,
        "team_id": payload.get(f"{side}_team_id"),
        "team_name": payload.get(f"{side}_name"),
    }


def player_name(player: dict[str, Any]) -> str | None:
    return player.get("personaname") or player.get("name")


def hero_payload(hero_id: Any, hero_names: dict[int, str]) -> dict[str, Any]:
    if hero_id is None:
        return {"hero_id": None, "hero": None}
    parsed_hero_id = int(hero_id)
    return {
        "hero_id": parsed_hero_id,
        "hero": hero_names.get(parsed_hero_id),
    }


def compact_match_payload(payload: dict[str, Any], hero_names: dict[int, str]) -> dict[str, Any]:
    players = payload.get("players")
    compact_players: dict[str, list[dict[str, Any]]] = {"radiant": [], "dire": []}

    if isinstance(players, list):
        for player in players:
            side = player_side(player.get("player_slot"))
            hero = hero_payload(player.get("hero_id"), hero_names)
            if side in compact_players:
                compact_players[side].append(
                    {
                        "id": player.get("account_id"),
                        "name": player_name(player),
                        **hero,
                    }
                )

    return {
        "played_at": format_start_time(payload.get("start_time")),
        "winner": winner_payload(payload),
        "players": compact_players if isinstance(players, list) else None,
    }


def opendota_match_to_row(payload: dict[str, Any], hero_names: dict[int, str]) -> dict[str, Any]:
    match_id = payload.get("match_id")
    if match_id is None:
        raise ValueError("OpenDota response has no match_id.")

    winner = winner_payload(payload) or {}
    compact = compact_match_payload(payload, hero_names)
    return {
        "match_id": int(match_id),
        "source_url": OPENDOTA_MATCH_URL.format(match_id=match_id),
        "played_at": parse_start_time(payload.get("start_time")),
        "radiant_team_id": payload.get("radiant_team_id"),
        "radiant_team_name": payload.get("radiant_name"),
        "dire_team_id": payload.get("dire_team_id"),
        "dire_team_name": payload.get("dire_name"),
        "winner_side": winner.get("side"),
        "winner_team_id": winner.get("team_id"),
        "winner_team_name": winner.get("team_name"),
        "players": json.dumps(compact.get("players") or {}, ensure_ascii=False),
        "raw": None,
    }


def upsert_opendota_match(payload: dict[str, Any], hero_names: dict[int, str], args: argparse.Namespace) -> int:
    psycopg, _, _, _, _ = require_psycopg()
    players = payload.get("players")
    players_count = len(players) if isinstance(players, list) else 0

    with psycopg.connect(make_target_conninfo(database=args.database)) as conn:
        if args.init:
            ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(UPSERT_OPENDOTA_MATCH_SQL, opendota_match_to_row(payload, hero_names))
        conn.commit()

    return players_count


def print_payload(
    candidate: MatchCandidate,
    payload: dict[str, Any],
    args: argparse.Namespace,
    hero_names: dict[int, str],
) -> None:
    if args.raw:
        output = {
            "candidate": {
                "match_id": candidate.match_id,
                "sources": candidate.sources,
                "last_seen_at": candidate.last_seen_at.isoformat() if candidate.last_seen_at else None,
            },
            "opendota": payload,
        }
    else:
        output = compact_match_payload(payload, hero_names)
    print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None, default=str))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load OpenDota API matches missing from dotabuff_matches.")
    parser.add_argument("--match-id", type=int, help="Fetch this match id directly instead of reading DB candidates.")
    parser.add_argument("--source", choices=("all", "team", "hero"), default="all", help="Candidate source table.")
    parser.add_argument("--limit", type=positive_int, default=1, help="Maximum number of matches to fetch.")
    parser.add_argument("--oldest-first", action="store_true", help="Fetch older indexed candidates first.")
    parser.add_argument("--reload", action="store_true", help="Reload matches even if opendota_matches already has them.")
    parser.add_argument("--dry-run", action="store_true", help="Print fetched data instead of writing to Postgres.")
    parser.add_argument("--raw", action="store_true", help="With --dry-run, print the full OpenDota response.")
    parser.add_argument("--pretty", action="store_true", help="With --dry-run, pretty-print JSON output.")
    parser.add_argument("--sleep", type=non_negative_float, default=1.0, help="Seconds to sleep between OpenDota requests.")
    parser.add_argument("--cycle-sleep", type=non_negative_float, default=300.0, help="Seconds between ingest cycles.")
    parser.add_argument("--timeout", type=positive_int, default=30, help="OpenDota request timeout in seconds.")
    parser.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification for OpenDota fetch.")
    parser.add_argument("--env-file", default=".env", help="Load Postgres settings from this env file.")
    parser.add_argument("--database", help="Target database name. Defaults to POSTGRES_DB or DATABASE_URL dbname.")
    parser.add_argument("--init", action="store_true", help="Apply table schema before selecting candidates.")
    parser.add_argument("--keep-going", action="store_true", default=True, help="Continue after individual OpenDota failures.")
    parser.add_argument("--fail-fast", dest="keep_going", action="store_false", help="Stop on the first individual OpenDota failure.")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"), help="Logging verbosity.")
    return parser


def run_cycle(args: argparse.Namespace, cycle: int, hero_names: dict[int, str]) -> tuple[int, int]:
    LOGGER.info("cycle %s started", cycle)
    candidates = fetch_candidates(args)
    if not candidates:
        LOGGER.info("cycle %s: no missing matches found", cycle)
        return 0, 0

    loaded = 0
    failed = 0
    for index, candidate in enumerate(candidates, start=1):
        LOGGER.info(
            "fetching OpenDota match %s/%s match_id=%s sources=%s",
            index,
            len(candidates),
            candidate.match_id,
            ",".join(candidate.sources),
        )
        try:
            payload = fetch_opendota_match(candidate.match_id, timeout=args.timeout, insecure=args.insecure)
            if not args.dry_run:
                players_count = upsert_opendota_match(payload, hero_names, args)
        except Exception as exc:
            failed += 1
            LOGGER.warning("failed match %s: %s", candidate.match_id, exc)
            if not args.keep_going:
                raise RuntimeError(f"failed match {candidate.match_id}: {exc}") from exc
        else:
            loaded += 1
            if args.dry_run:
                print_payload(candidate, payload, args, hero_names)
            else:
                LOGGER.info("upserted opendota match %s, players=%s", candidate.match_id, players_count)

        if args.sleep > 0 and index < len(candidates):
            LOGGER.debug("sleeping %.1fs before next OpenDota request", args.sleep)
            time.sleep(args.sleep)

    LOGGER.info("cycle %s finished: loaded=%s failed=%s", cycle, loaded, failed)
    return loaded, failed


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.match_id is not None and args.limit != 1:
        parser.error("--match-id can only be used with --limit 1")
    if args.match_id is not None and not args.once:
        parser.error("--match-id requires --once")
    if args.dry_run and not args.once:
        parser.error("--dry-run requires --once")

    configure_logging(args.log_level)
    load_parser_env(args.env_file)
    hero_names = load_hero_names()
    LOGGER.info(
        "starting OpenDota ingest; source=%s limit=%s sleep=%.1fs cycle_sleep=%.1fs",
        args.source,
        args.limit,
        args.sleep,
        args.cycle_sleep,
    )

    cycle = 1
    while True:
        try:
            _, failed = run_cycle(args, cycle, hero_names)
        except Exception as exc:
            LOGGER.error("cycle %s failed: %s", cycle, exc)
            return 1

        if args.once:
            return 1 if failed else 0

        LOGGER.info("cycle %s sleeping %.1fs", cycle, args.cycle_sleep)
        time.sleep(args.cycle_sleep)
        cycle += 1


if __name__ == "__main__":
    raise SystemExit(main())
