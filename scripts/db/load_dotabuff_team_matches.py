#!/usr/bin/env python3
"""Load parsed Dotabuff team match index pages into Postgres.

Usage:
    python3 scripts/db/load_dotabuff_team_matches.py 9247354-team-falcons
    python3 scripts/db/load_dotabuff_team_matches.py 9247354-team-falcons --page 2
    python3 scripts/db/load_dotabuff_team_matches.py --html-file team_matches.html --team 9247354-team-falcons
    python3 scripts/db/load_dotabuff_team_matches.py --json-file team_matches.json --init
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))
from scripts.db.postgres_common import ensure_schema, load_parser_env, make_target_conninfo, require_psycopg
from scripts.parsing.dotabuff_team_matches_parser import (
    build_team_matches_url,
    fetch_team_matches_html,
    parse_team_matches,
)


UPSERT_TEAM_MATCH_SQL = """
INSERT INTO dotabuff_team_matches (
    team_id,
    match_id,
    source_url,
    page,
    result,
    won,
    played_at,
    played_at_text,
    played_at_title,
    league_id,
    league_slug,
    league_name,
    league_url,
    league_image_url,
    series_id,
    series_url,
    series_region,
    duration_seconds,
    duration,
    heroes,
    opponent_team_id,
    opponent_slug,
    opponent_name,
    opponent_url,
    opponent_image_url,
    raw,
    updated_at
) VALUES (
    %(team_id)s,
    %(match_id)s,
    %(source_url)s,
    %(page)s,
    %(result)s,
    %(won)s,
    %(played_at)s,
    %(played_at_text)s,
    %(played_at_title)s,
    %(league_id)s,
    %(league_slug)s,
    %(league_name)s,
    %(league_url)s,
    %(league_image_url)s,
    %(series_id)s,
    %(series_url)s,
    %(series_region)s,
    %(duration_seconds)s,
    %(duration)s,
    %(heroes)s::jsonb,
    %(opponent_team_id)s,
    %(opponent_slug)s,
    %(opponent_name)s,
    %(opponent_url)s,
    %(opponent_image_url)s,
    %(raw)s::jsonb,
    now()
)
ON CONFLICT (team_id, match_id) DO UPDATE SET
    source_url = EXCLUDED.source_url,
    page = EXCLUDED.page,
    result = EXCLUDED.result,
    won = EXCLUDED.won,
    played_at = EXCLUDED.played_at,
    played_at_text = EXCLUDED.played_at_text,
    played_at_title = EXCLUDED.played_at_title,
    league_id = EXCLUDED.league_id,
    league_slug = EXCLUDED.league_slug,
    league_name = EXCLUDED.league_name,
    league_url = EXCLUDED.league_url,
    league_image_url = EXCLUDED.league_image_url,
    series_id = EXCLUDED.series_id,
    series_url = EXCLUDED.series_url,
    series_region = EXCLUDED.series_region,
    duration_seconds = EXCLUDED.duration_seconds,
    duration = EXCLUDED.duration,
    heroes = EXCLUDED.heroes,
    opponent_team_id = EXCLUDED.opponent_team_id,
    opponent_slug = EXCLUDED.opponent_slug,
    opponent_name = EXCLUDED.opponent_name,
    opponent_url = EXCLUDED.opponent_url,
    opponent_image_url = EXCLUDED.opponent_image_url,
    raw = EXCLUDED.raw,
    updated_at = now()
"""


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_json_file(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file_obj:
        data = json.load(file_obj)
    if not isinstance(data, dict) or not isinstance(data.get("team"), dict) or not isinstance(data.get("matches"), list):
        raise ValueError("JSON file must contain parser output with team metadata and a matches list.")
    return data


def load_parsed_input(args: argparse.Namespace) -> dict[str, Any]:
    if args.json_file:
        return load_json_file(args.json_file)

    team = args.team_option or args.team
    source_url = build_team_matches_url(team, page=args.page)
    if args.html_file:
        with open(args.html_file, "r", encoding="utf-8") as file_obj:
            return parse_team_matches(file_obj.read(), source_url=source_url)

    html, source_url = fetch_team_matches_html(team, args.timeout, args.insecure, page=args.page)
    return parse_team_matches(html, source_url=source_url)


def team_match_to_row(parsed: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    team = parsed.get("team") or {}
    pagination = parsed.get("pagination") or {}
    league = match.get("league") or {}
    series = match.get("series") or {}
    opponent = match.get("opponent") or {}
    played_at = match.get("played_at") or {}
    team_id = team.get("team_id")
    match_id = match.get("match_id")
    if team_id is None:
        raise ValueError(f"Team matches page has no team_id: {team!r}")
    if match_id is None:
        raise ValueError(f"Team match row has no match_id: {match!r}")

    return {
        "team_id": team_id,
        "match_id": match_id,
        "source_url": parsed.get("source_url"),
        "page": pagination.get("current_page"),
        "result": match.get("result"),
        "won": match.get("won"),
        "played_at": parse_datetime(played_at.get("datetime")),
        "played_at_text": played_at.get("text"),
        "played_at_title": played_at.get("title"),
        "league_id": league.get("league_id"),
        "league_slug": league.get("slug"),
        "league_name": league.get("name"),
        "league_url": league.get("url"),
        "league_image_url": league.get("image_url"),
        "series_id": series.get("series_id"),
        "series_url": series.get("url"),
        "series_region": series.get("region"),
        "duration_seconds": match.get("duration_seconds"),
        "duration": match.get("duration"),
        "heroes": json.dumps(match.get("heroes") or [], ensure_ascii=False),
        "opponent_team_id": opponent.get("team_id"),
        "opponent_slug": opponent.get("slug"),
        "opponent_name": opponent.get("name"),
        "opponent_url": opponent.get("url"),
        "opponent_image_url": opponent.get("image_url"),
        "raw": None,
    }


def upsert_team_matches(parsed: dict[str, Any], init_schema: bool = False, database: str | None = None) -> int:
    psycopg, _, _, _, _ = require_psycopg()
    matches = parsed.get("matches") or []

    with psycopg.connect(make_target_conninfo(database=database)) as conn:
        if init_schema:
            ensure_schema(conn)
        with conn.cursor() as cur:
            for match in matches:
                cur.execute(UPSERT_TEAM_MATCH_SQL, team_match_to_row(parsed, match))
        conn.commit()
    return len(matches)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load parsed Dotabuff team matches into Postgres.")
    parser.add_argument(
        "team",
        nargs="?",
        default="9247354-team-falcons",
        help="Dotabuff team id/slug, team matches path, or full team matches URL.",
    )
    parser.add_argument("--team", dest="team_option", help="Same as the positional team argument.")
    parser.add_argument("--page", type=int, help="Page number to fetch.")
    parser.add_argument("--json-file", help="Read parser JSON output from this file.")
    parser.add_argument("--html-file", help="Read already downloaded Dotabuff HTML from a file.")
    parser.add_argument("--env-file", default=".env", help="Load Dotabuff and Postgres settings from this env file.")
    parser.add_argument("--database", help="Target database name. Defaults to POSTGRES_DB or DATABASE_URL dbname.")
    parser.add_argument("--timeout", type=int, default=20, help="Network timeout in seconds.")
    parser.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification for Dotabuff fetch.")
    parser.add_argument("--init", action="store_true", help="Apply table schema before loading.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    load_parser_env(args.env_file)
    parsed = load_parsed_input(args)
    count = upsert_team_matches(parsed, init_schema=args.init, database=args.database)
    team = parsed.get("team") or {}
    print(f"upserted team matches: {count}")
    if team.get("team_id") is not None:
        print(f"team_id: {team.get('team_id')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
