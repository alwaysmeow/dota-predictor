#!/usr/bin/env python3
"""Load parsed Dotabuff matches into Postgres.

Usage:
    python3 scripts/db/load_dotabuff_matches.py 8786827560
    python3 scripts/db/load_dotabuff_matches.py --json-file match.json
    python3 scripts/db/load_dotabuff_matches.py --html-file match.html --match-id 8786827560
    python3 scripts/db/load_dotabuff_matches.py --json-file match.json --init
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))
from scripts.db.postgres_common import ensure_schema, load_parser_env, make_target_conninfo, require_psycopg
from scripts.parsing.dotabuff_match_parser import fetch_match_html, parse_match


UPSERT_MATCH_SQL = """
INSERT INTO dotabuff_matches (
    match_id,
    source_url,
    is_professional_match,
    radiant_team_id,
    radiant_team_name,
    radiant_team_url,
    dire_team_id,
    dire_team_name,
    dire_team_url,
    winner_side,
    winner_team,
    raw,
    updated_at
) VALUES (
    %(match_id)s,
    %(source_url)s,
    %(is_professional_match)s,
    %(radiant_team_id)s,
    %(radiant_team_name)s,
    %(radiant_team_url)s,
    %(dire_team_id)s,
    %(dire_team_name)s,
    %(dire_team_url)s,
    %(winner_side)s,
    %(winner_team)s,
    %(raw)s::jsonb,
    now()
)
ON CONFLICT (match_id) DO UPDATE SET
    source_url = EXCLUDED.source_url,
    is_professional_match = EXCLUDED.is_professional_match,
    radiant_team_id = EXCLUDED.radiant_team_id,
    radiant_team_name = EXCLUDED.radiant_team_name,
    radiant_team_url = EXCLUDED.radiant_team_url,
    dire_team_id = EXCLUDED.dire_team_id,
    dire_team_name = EXCLUDED.dire_team_name,
    dire_team_url = EXCLUDED.dire_team_url,
    winner_side = EXCLUDED.winner_side,
    winner_team = EXCLUDED.winner_team,
    raw = EXCLUDED.raw,
    updated_at = now()
"""

DELETE_PLAYERS_SQL = "DELETE FROM dotabuff_match_players WHERE match_id = %s"

INSERT_PLAYER_SQL = """
INSERT INTO dotabuff_match_players (
    match_id,
    player_slot,
    side,
    player_id,
    player_name,
    player_url,
    hero,
    hero_slug,
    role,
    role_icon,
    lane,
    lane_icon,
    raw
) VALUES (
    %(match_id)s,
    %(player_slot)s,
    %(side)s,
    %(player_id)s,
    %(player_name)s,
    %(player_url)s,
    %(hero)s,
    %(hero_slug)s,
    %(role)s,
    %(role_icon)s,
    %(lane)s,
    %(lane_icon)s,
    %(raw)s::jsonb
)
"""


def parse_team_id(url: str | None) -> int | None:
    if not url:
        return None
    match = re.search(r"/esports/teams/(\d+)", url)
    return int(match.group(1)) if match else None


def require_match_id(parsed: dict[str, Any]) -> int:
    match_id = parsed.get("match_id")
    if match_id is None:
        raise ValueError("Parsed match has no match_id. Pass --match-id when loading from HTML.")
    return int(match_id)


def match_to_row(parsed: dict[str, Any]) -> dict[str, Any]:
    match_id = require_match_id(parsed)
    teams = parsed.get("teams") or {}
    radiant = teams.get("radiant") or {}
    dire = teams.get("dire") or {}
    label = parsed.get("label") or {}
    return {
        "match_id": match_id,
        "source_url": parsed.get("source_url"),
        "is_professional_match": parsed.get("is_professional_match"),
        "radiant_team_id": parse_team_id(radiant.get("url")),
        "radiant_team_name": radiant.get("name"),
        "radiant_team_url": radiant.get("url"),
        "dire_team_id": parse_team_id(dire.get("url")),
        "dire_team_name": dire.get("name"),
        "dire_team_url": dire.get("url"),
        "winner_side": label.get("winner_side"),
        "winner_team": label.get("winner_team"),
        "raw": None,
    }


def player_to_row(match_id: int, player: dict[str, Any], player_slot: int) -> dict[str, Any]:
    return {
        "match_id": match_id,
        "player_slot": player_slot,
        "side": player.get("side"),
        "player_id": player.get("player_id"),
        "player_name": player.get("player_name"),
        "player_url": player.get("player_url"),
        "hero": player.get("hero"),
        "hero_slug": player.get("hero_slug"),
        "role": player.get("role"),
        "role_icon": player.get("role_icon"),
        "lane": player.get("lane"),
        "lane_icon": player.get("lane_icon"),
        "raw": None,
    }


def load_json_file(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file_obj:
        data = json.load(file_obj)
    if not isinstance(data, dict) or "players" not in data:
        raise ValueError("JSON file must contain one parsed match object with a players list.")
    return data


def load_parsed_input(args: argparse.Namespace) -> dict[str, Any]:
    if args.json_file:
        return load_json_file(args.json_file)

    if args.html_file:
        with open(args.html_file, "r", encoding="utf-8") as file_obj:
            return parse_match(file_obj.read(), match_id=args.match_id)

    html, source_url, fetched_match_id = fetch_match_html(args.match, args.timeout, args.insecure)
    return parse_match(html, match_id=args.match_id or fetched_match_id, source_url=source_url)


def upsert_match(parsed: dict[str, Any], init_schema: bool = False, database: str | None = None) -> int:
    psycopg, _, _, _, _ = require_psycopg()
    match_id = require_match_id(parsed)
    players = parsed.get("players") or []

    with psycopg.connect(make_target_conninfo(database=database)) as conn:
        if init_schema:
            ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(UPSERT_MATCH_SQL, match_to_row(parsed))
            cur.execute(DELETE_PLAYERS_SQL, (match_id,))
            for player_slot, player in enumerate(players):
                cur.execute(INSERT_PLAYER_SQL, player_to_row(match_id, player, player_slot))
        conn.commit()
    return len(players)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load parsed Dotabuff match into Postgres.")
    parser.add_argument("match", nargs="?", help="Dotabuff match id or full match URL.")
    parser.add_argument("--json-file", help="Read parser JSON output from this file.")
    parser.add_argument("--html-file", help="Read already downloaded Dotabuff HTML from a file.")
    parser.add_argument("--match-id", type=int, help="Match id to use with --html-file.")
    parser.add_argument("--env-file", default=".env", help="Load Dotabuff and Postgres settings from this env file.")
    parser.add_argument("--database", help="Target database name. Defaults to POSTGRES_DB or DATABASE_URL dbname.")
    parser.add_argument("--timeout", type=int, default=20, help="Network timeout in seconds.")
    parser.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification for Dotabuff fetch.")
    parser.add_argument("--init", action="store_true", help="Apply table schema before loading.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.json_file and not args.html_file and not args.match:
        parser.error("provide a match id/URL, --json-file, or --html-file")

    load_parser_env(args.env_file)
    parsed = load_parsed_input(args)
    count = upsert_match(parsed, init_schema=args.init, database=args.database)
    print(f"upserted match: {require_match_id(parsed)}")
    print(f"inserted match players: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
