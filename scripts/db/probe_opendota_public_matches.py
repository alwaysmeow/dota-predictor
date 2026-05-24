#!/usr/bin/env python3
"""Probe or ingest recent Ancient+ ranked public matches from OpenDota.

This script fetches /api/publicMatches from OpenDota, filters the response to
ranked matchmaking lobby type and avg_rank_tier >= 60, then prints rows shaped
like opendota_matches inserts. By default it does not write anything to
Postgres; pass --insert to add new rows into opendota_matches.

Usage:
    python3 scripts/db/probe_opendota_public_matches.py
    python3 scripts/db/probe_opendota_public_matches.py --limit 20 --pretty
    python3 scripts/db/probe_opendota_public_matches.py --pages 3 --sleep 1
    python3 scripts/db/probe_opendota_public_matches.py --insert --init --limit 50
    python3 scripts/db/probe_opendota_public_matches.py --insert --continuous --pages 100
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import socket
import ssl
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


OPENDOTA_PUBLIC_MATCHES_URL = "https://api.opendota.com/api/publicMatches"
OPENDOTA_MATCH_URL = "https://api.opendota.com/api/matches/{match_id}"
REPO_ROOT = Path(__file__).resolve().parents[2]
HEROES_PATH = REPO_ROOT / "dotaconstants" / "build" / "heroes.json"
RANKED_LOBBY_TYPE = 7
MIN_ANCIENT_RANK_TIER = 60
LOGGER = logging.getLogger("opendota_public_matches_probe")


INSERT_OPENDOTA_MATCH_SQL = """
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
    now()
)
ON CONFLICT (match_id) DO NOTHING
"""


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
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


def should_retry_http_status(code: int) -> bool:
    return code in {408, 429, 500, 502, 503, 504}


def build_public_matches_url(less_than_match_id: int | None = None) -> str:
    if less_than_match_id is None:
        return OPENDOTA_PUBLIC_MATCHES_URL
    return f"{OPENDOTA_PUBLIC_MATCHES_URL}?{urlencode({'less_than_match_id': less_than_match_id})}"


def fetch_public_matches_once(
    *,
    timeout: int,
    insecure: bool = False,
    less_than_match_id: int | None = None,
) -> list[dict[str, Any]]:
    url = build_public_matches_url(less_than_match_id)
    request = Request(
        url,
        headers={
            "User-Agent": "dota-predictor-opendota-public-matches-probe/1.0",
            "Accept": "application/json",
            "Connection": "close",
        },
    )

    context = ssl._create_unverified_context() if insecure else None
    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            payload = json.loads(response.read().decode(charset, errors="replace"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenDota returned non-JSON response from /publicMatches: {exc}") from exc

    if not isinstance(payload, list):
        raise RuntimeError(f"OpenDota returned unexpected /publicMatches payload: {type(payload).__name__}")
    return payload


def fetch_public_matches(
    *,
    timeout: int,
    insecure: bool = False,
    less_than_match_id: int | None = None,
    retries: int = 3,
    retry_sleep: float = 5.0,
    retry_backoff: float = 2.0,
) -> list[dict[str, Any]]:
    attempts = retries + 1
    delay = retry_sleep
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            return fetch_public_matches_once(
                timeout=timeout,
                insecure=insecure,
                less_than_match_id=less_than_match_id,
            )
        except HTTPError as exc:
            last_error = exc
            if not should_retry_http_status(exc.code) or attempt >= attempts:
                body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"OpenDota returned HTTP {exc.code}: {body[:500]}") from exc
        except (TimeoutError, socket.timeout, URLError) as exc:
            last_error = exc
            if attempt >= attempts:
                reason = exc.reason if isinstance(exc, URLError) else exc
                raise RuntimeError(f"Could not fetch OpenDota /publicMatches: {reason}") from exc

        LOGGER.warning(
            "OpenDota /publicMatches fetch failed attempt=%s/%s: %s; sleeping %.1fs before retry",
            attempt,
            attempts,
            last_error,
            delay,
        )
        time.sleep(delay)
        delay *= retry_backoff

    raise RuntimeError(f"OpenDota /publicMatches fetch failed: {last_error}")


def parsed_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_start_time(start_time: Any) -> str | None:
    parsed = parsed_int(start_time)
    if parsed is None:
        return None
    return datetime.fromtimestamp(parsed, tz=timezone.utc).isoformat()


def parse_start_time(start_time: Any) -> datetime | None:
    parsed = parsed_int(start_time)
    if parsed is None:
        return None
    return datetime.fromtimestamp(parsed, tz=timezone.utc)


def load_hero_names(path: Path = HEROES_PATH) -> dict[int, str]:
    heroes = json.loads(path.read_text(encoding="utf-8"))
    return {
        int(hero["id"]): str(hero.get("localized_name") or hero.get("name") or hero["id"])
        for hero in heroes.values()
    }


def is_ranked_ancient_plus(match: dict[str, Any], *, min_rank_tier: int) -> bool:
    lobby_type = parsed_int(match.get("lobby_type"))
    avg_rank_tier = parsed_int(match.get("avg_rank_tier"))
    return lobby_type == RANKED_LOBBY_TYPE and avg_rank_tier is not None and avg_rank_tier >= min_rank_tier


def compact_match(match: dict[str, Any]) -> dict[str, Any]:
    return {
        "match_id": parsed_int(match.get("match_id")),
        "match_seq_num": parsed_int(match.get("match_seq_num")),
        "start_time": format_start_time(match.get("start_time")),
        "duration": parsed_int(match.get("duration")),
        "lobby_type": parsed_int(match.get("lobby_type")),
        "game_mode": parsed_int(match.get("game_mode")),
        "avg_rank_tier": parsed_int(match.get("avg_rank_tier")),
        "radiant_win": match.get("radiant_win"),
    }


def winner_side(match: dict[str, Any]) -> str | None:
    radiant_win = match.get("radiant_win")
    if radiant_win is None:
        return None
    return "radiant" if radiant_win else "dire"


def hero_payload(hero_id: Any, hero_names: dict[int, str]) -> dict[str, Any]:
    parsed_hero_id = parsed_int(hero_id)
    if parsed_hero_id is None:
        return {"hero_id": None, "hero": None}
    return {
        "hero_id": parsed_hero_id,
        "hero": hero_names.get(parsed_hero_id),
    }


def public_team_players(hero_ids: Any, hero_names: dict[int, str]) -> list[dict[str, Any]]:
    if not isinstance(hero_ids, list):
        return []
    return [
        {
            "id": None,
            "name": None,
            **hero_payload(hero_id, hero_names),
        }
        for hero_id in hero_ids
    ]


def public_match_players(match: dict[str, Any], hero_names: dict[int, str]) -> dict[str, list[dict[str, Any]]]:
    return {
        "radiant": public_team_players(match.get("radiant_team"), hero_names),
        "dire": public_team_players(match.get("dire_team"), hero_names),
    }


def public_match_to_opendota_row(match: dict[str, Any], hero_names: dict[int, str]) -> dict[str, Any]:
    match_id = parsed_int(match.get("match_id"))
    return {
        "match_id": match_id,
        "source_url": OPENDOTA_MATCH_URL.format(match_id=match_id) if match_id is not None else None,
        "played_at": parse_start_time(match.get("start_time")),
        "radiant_team_id": None,
        "radiant_team_name": None,
        "dire_team_id": None,
        "dire_team_name": None,
        "winner_side": winner_side(match),
        "winner_team_id": None,
        "winner_team_name": None,
        "players": public_match_players(match, hero_names),
    }


def opendota_row_to_sql_params(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "players": json.dumps(row["players"], ensure_ascii=False),
    }


def require_db_helpers():
    if __package__ in {None, ""}:
        sys.path.append(str(REPO_ROOT))

    from scripts.db.postgres_common import ensure_schema, load_parser_env, make_target_conninfo, require_psycopg

    return ensure_schema, load_parser_env, make_target_conninfo, require_psycopg


def fetch_existing_match_ids(cur, match_ids: list[int]) -> set[int]:
    if not match_ids:
        return set()
    cur.execute(
        """
        SELECT match_id
        FROM opendota_matches
        WHERE match_id = ANY(%s)
        """,
        (match_ids,),
    )
    return {int(row[0]) for row in cur.fetchall()}


def insert_new_opendota_rows(rows: list[dict[str, Any]], args: argparse.Namespace) -> tuple[list[dict[str, Any]], int]:
    ensure_schema, load_parser_env, make_target_conninfo, require_psycopg = require_db_helpers()
    load_parser_env(args.env_file)
    psycopg, _, _, _, _ = require_psycopg()

    with psycopg.connect(make_target_conninfo(database=args.database)) as conn:
        if args.init:
            ensure_schema(conn)
        with conn.cursor() as cur:
            rows_with_match_id = [row for row in rows if row.get("match_id") is not None]
            match_ids = [row["match_id"] for row in rows_with_match_id]
            existing_match_ids = fetch_existing_match_ids(cur, match_ids)
            new_rows = [row for row in rows_with_match_id if row["match_id"] not in existing_match_ids]
            for row in new_rows:
                cur.execute(INSERT_OPENDOTA_MATCH_SQL, opendota_row_to_sql_params(row))
        conn.commit()

    return new_rows, len(rows) - len(new_rows)


def page_cursor(matches: list[dict[str, Any]]) -> int | None:
    match_ids = [parsed for match in matches if (parsed := parsed_int(match.get("match_id"))) is not None]
    if not match_ids:
        return None
    return min(match_ids)


def print_matches(
    matches: list[dict[str, Any]],
    *,
    output_format: str,
    pretty: bool,
    hero_names: dict[int, str],
) -> None:
    if output_format == "raw":
        payload = matches
    elif output_format == "compact":
        payload = [compact_match(match) for match in matches]
    else:
        payload = [public_match_to_opendota_row(match, hero_names) for match in matches]
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, default=str))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe Ancient+ ranked matches from OpenDota /publicMatches.")
    parser.add_argument("--limit", type=positive_int, default=10, help="Maximum number of filtered matches to print.")
    parser.add_argument("--pages", type=positive_int, default=1, help="Maximum number of /publicMatches pages to fetch.")
    parser.add_argument("--min-rank-tier", type=positive_int, default=MIN_ANCIENT_RANK_TIER, help="Minimum avg_rank_tier to keep.")
    parser.add_argument("--less-than-match-id", type=positive_int, help="Start pagination below this match id.")
    parser.add_argument("--sleep", type=non_negative_float, default=1.0, help="Seconds to sleep between OpenDota requests.")
    parser.add_argument("--continuous", action="store_true", help="Keep fetching older pages with less_than_match_id until stopped.")
    parser.add_argument("--cycle-sleep", type=non_negative_float, default=30.0, help="Seconds to sleep between continuous cycles.")
    parser.add_argument("--timeout", type=positive_int, default=30, help="OpenDota request timeout in seconds.")
    parser.add_argument("--retries", type=non_negative_int, default=3, help="Retry attempts after the first OpenDota request.")
    parser.add_argument("--retry-sleep", type=non_negative_float, default=5.0, help="Initial seconds to sleep before retrying OpenDota.")
    parser.add_argument("--retry-backoff", type=non_negative_float, default=2.0, help="Multiplier for retry sleep after each failed attempt.")
    parser.add_argument("--output-format", choices=("db-row", "compact", "raw"), default="db-row", help="Console output shape.")
    parser.add_argument("--raw", action="store_true", help="Alias for --output-format raw.")
    parser.add_argument("--insert", action="store_true", help="Insert filtered matches that are not already in opendota_matches.")
    parser.add_argument("--print-inserted", action="store_true", help="With --insert, also print rows prepared for writing.")
    parser.add_argument("--env-file", default=".env", help="Load Postgres settings from this env file when using --insert.")
    parser.add_argument("--database", help="Target database name. Defaults to POSTGRES_DB or DATABASE_URL dbname.")
    parser.add_argument("--init", action="store_true", help="Apply table schema before inserting rows.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification for OpenDota fetch.")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"), help="Logging verbosity.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.raw:
        args.output_format = "raw"
    configure_logging(args.log_level)
    hero_names = load_hero_names()

    cursor = args.less_than_match_id
    cycle = 1

    while True:
        LOGGER.info(
            "probing OpenDota /publicMatches; cycle=%s ranked_lobby_type=%s min_rank_tier=%s pages=%s limit=%s cursor=%s",
            cycle,
            RANKED_LOBBY_TYPE,
            args.min_rank_tier,
            args.pages,
            args.limit,
            cursor,
        )

        scanned = 0
        selected: list[dict[str, Any]] = []

        for page in range(1, args.pages + 1):
            LOGGER.info("fetching /publicMatches page %s cursor=%s", page, cursor)
            matches = fetch_public_matches(
                timeout=args.timeout,
                insecure=args.insecure,
                less_than_match_id=cursor,
                retries=args.retries,
                retry_sleep=args.retry_sleep,
                retry_backoff=args.retry_backoff,
            )
            scanned += len(matches)
            filtered = [match for match in matches if is_ranked_ancient_plus(match, min_rank_tier=args.min_rank_tier)]
            selected.extend(filtered)

            next_cursor = page_cursor(matches)
            LOGGER.info(
                "page %s: fetched=%s matched=%s total_matched=%s next_cursor=%s",
                page,
                len(matches),
                len(filtered),
                len(selected),
                next_cursor,
            )

            if next_cursor is None or next_cursor == cursor:
                LOGGER.info("pagination stopped: no next cursor")
                break
            cursor = next_cursor

            if len(selected) >= args.limit:
                break

            if args.sleep > 0 and page < args.pages:
                LOGGER.debug("sleeping %.1fs before next OpenDota request", args.sleep)
                time.sleep(args.sleep)

        output = selected[: args.limit]
        rows = [public_match_to_opendota_row(match, hero_names) for match in output]
        inserted = 0
        skipped_existing = 0

        if args.insert:
            inserted_rows, skipped_existing = insert_new_opendota_rows(rows, args)
            inserted = len(inserted_rows)
            LOGGER.info("inserted %s new opendota_matches rows, skipped_existing=%s", inserted, skipped_existing)
            if args.print_inserted:
                print(json.dumps(inserted_rows, ensure_ascii=False, indent=2 if args.pretty else None, default=str))
        else:
            print_matches(output, output_format=args.output_format, pretty=args.pretty, hero_names=hero_names)

        LOGGER.info(
            "cycle %s finished: scanned=%s matched=%s prepared=%s inserted=%s skipped_existing=%s cursor=%s",
            cycle,
            scanned,
            len(selected),
            len(rows),
            inserted,
            skipped_existing,
            cursor,
        )

        if not args.continuous:
            return 0

        cycle += 1
        if args.cycle_sleep > 0:
            LOGGER.info("sleeping %.1fs before next continuous cycle", args.cycle_sleep)
            time.sleep(args.cycle_sleep)


if __name__ == "__main__":
    raise SystemExit(main())

"""
python3 scripts/db/probe_opendota_public_matches.py \
  --insert \
  --init \
  --continuous \
  --pages 100 \
  --limit 100000 \
  --sleep 1 \
  --cycle-sleep 30 \
  --log-level INFO --insecure
"""