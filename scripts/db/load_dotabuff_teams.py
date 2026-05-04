#!/usr/bin/env python3
"""Load parsed Dotabuff teams into Postgres.

Usage:
    python3 scripts/db/load_dotabuff_teams.py
    python3 scripts/db/load_dotabuff_teams.py --html-file teams.html
    python3 scripts/db/load_dotabuff_teams.py --json-file teams.json
    python3 scripts/db/load_dotabuff_teams.py --init
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
from scripts.parsing.dotabuff_team_parser import DOTABUFF_TEAMS_URL, fetch_teams_html, parse_teams


UPSERT_TEAM_SQL = """
INSERT INTO dotabuff_teams (
    team_id,
    slug,
    name,
    url,
    image_url,
    last_match_text,
    last_match_at,
    last_match_title,
    popularity_rank,
    matches,
    win_rate,
    kda,
    gpm,
    xpm,
    duration_seconds,
    duration,
    source_url,
    raw,
    updated_at
) VALUES (
    %(team_id)s,
    %(slug)s,
    %(name)s,
    %(url)s,
    %(image_url)s,
    %(last_match_text)s,
    %(last_match_at)s,
    %(last_match_title)s,
    %(popularity_rank)s,
    %(matches)s,
    %(win_rate)s,
    %(kda)s,
    %(gpm)s,
    %(xpm)s,
    %(duration_seconds)s,
    %(duration)s,
    %(source_url)s,
    %(raw)s::jsonb,
    now()
)
ON CONFLICT (team_id) DO UPDATE SET
    slug = EXCLUDED.slug,
    name = EXCLUDED.name,
    url = EXCLUDED.url,
    image_url = EXCLUDED.image_url,
    last_match_text = EXCLUDED.last_match_text,
    last_match_at = EXCLUDED.last_match_at,
    last_match_title = EXCLUDED.last_match_title,
    popularity_rank = EXCLUDED.popularity_rank,
    matches = EXCLUDED.matches,
    win_rate = EXCLUDED.win_rate,
    kda = EXCLUDED.kda,
    gpm = EXCLUDED.gpm,
    xpm = EXCLUDED.xpm,
    duration_seconds = EXCLUDED.duration_seconds,
    duration = EXCLUDED.duration,
    source_url = EXCLUDED.source_url,
    raw = EXCLUDED.raw,
    updated_at = now()
"""


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def team_to_row(team: dict[str, Any], source_url: str | None) -> dict[str, Any]:
    if team.get("team_id") is None:
        raise ValueError(f"Team has no team_id: {team!r}")

    last_match = team.get("last_match") or {}
    return {
        "team_id": team.get("team_id"),
        "slug": team.get("slug"),
        "name": team.get("name"),
        "url": team.get("url"),
        "image_url": team.get("image_url"),
        "last_match_text": last_match.get("text"),
        "last_match_at": parse_datetime(last_match.get("datetime")),
        "last_match_title": last_match.get("title"),
        "popularity_rank": team.get("popularity_rank"),
        "matches": team.get("matches"),
        "win_rate": team.get("win_rate"),
        "kda": team.get("kda"),
        "gpm": team.get("gpm"),
        "xpm": team.get("xpm"),
        "duration_seconds": team.get("duration_seconds"),
        "duration": team.get("duration"),
        "source_url": source_url,
        "raw": None,
    }


def load_json_file(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file_obj:
        data = json.load(file_obj)
    if isinstance(data, list):
        return {"source_url": None, "teams": data}
    if not isinstance(data, dict) or not isinstance(data.get("teams"), list):
        raise ValueError("JSON file must contain either a team list or an object with a teams list.")
    return data


def load_parsed_input(args: argparse.Namespace) -> dict[str, Any]:
    if args.json_file:
        return load_json_file(args.json_file)

    if args.html_file:
        with open(args.html_file, "r", encoding="utf-8") as file_obj:
            return parse_teams(file_obj.read(), source_url=args.url)

    html, source_url = fetch_teams_html(args.url, args.timeout, args.insecure)
    return parse_teams(html, source_url=source_url)


def upsert_teams(parsed: dict[str, Any], init_schema: bool = False, database: str | None = None) -> int:
    psycopg, _, _, _, _ = require_psycopg()
    teams = parsed.get("teams") or []
    source_url = parsed.get("source_url")

    with psycopg.connect(make_target_conninfo(database=database)) as conn:
        if init_schema:
            ensure_schema(conn)
        with conn.cursor() as cur:
            for team in teams:
                cur.execute(UPSERT_TEAM_SQL, team_to_row(team, source_url))
        conn.commit()
    return len(teams)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load parsed Dotabuff teams into Postgres.")
    parser.add_argument("url", nargs="?", default=DOTABUFF_TEAMS_URL, help="Dotabuff teams URL.")
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
    count = upsert_teams(parsed, init_schema=args.init, database=args.database)
    print(f"upserted teams: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
