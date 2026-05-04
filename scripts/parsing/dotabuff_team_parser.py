#!/usr/bin/env python3
"""Parse Dotabuff esports teams pages into JSON.

Usage:
    python3 scripts/parsing/dotabuff_team_parser.py --pretty
    python3 scripts/parsing/dotabuff_team_parser.py https://www.dotabuff.com/esports/teams
    python3 scripts/parsing/dotabuff_team_parser.py --html-file teams.html

Headers can be configured through environment variables or a local .env file:
    DOTABUFF_USER_AGENT
    DOTABUFF_ACCEPT
    DOTABUFF_ACCEPT_LANGUAGE
    DOTABUFF_COOKIE
    DOTABUFF_REFERER
    DOTABUFF_HEADERS_JSON
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from bs4 import BeautifulSoup, Tag
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "This parser needs BeautifulSoup. Install it with: python3 -m pip install beautifulsoup4"
    ) from exc

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))
from scripts.parsing.dotabuff_common import (
    absolute_dotabuff_url,
    build_request_headers,
    clean_text,
    dotabuff_http_error_message,
    load_env_file,
    parse_int,
    tag_text,
)


DOTABUFF_TEAMS_URL = "https://www.dotabuff.com/esports/teams"


@dataclass
class DotabuffTeam:
    team_id: int | None
    slug: str | None
    name: str | None
    url: str | None
    image_url: str | None
    last_match: dict[str, str | None] | None
    popularity_rank: int | None
    matches: int | None
    win_rate: float | None
    kda: float | None
    gpm: int | None
    xpm: int | None
    duration_seconds: int | None
    duration: str | None


def parse_float(value: str | None) -> float | None:
    text = clean_text(value)
    if not text or text == "-":
        return None
    text = text.replace(",", "").rstrip("%")
    text = re.sub(r"[^0-9.\-]", "", text)
    if not text:
        return None
    return float(text)


def parse_rank(value: str | None) -> int | None:
    text = clean_text(value)
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def parse_team_path(path: str | None) -> tuple[int | None, str | None]:
    if not path:
        return None, None
    match = re.search(r"/esports/teams/(\d+)(?:-([^/?#]+))?", path)
    if not match:
        return None, None
    return int(match.group(1)), match.group(2)


def parse_last_match(cell: Tag | None) -> dict[str, str | None] | None:
    if not cell:
        return None
    time_tag = cell.select_one("time")
    if not time_tag:
        return None
    return {
        "text": tag_text(time_tag),
        "datetime": time_tag.get("datetime"),
        "title": time_tag.get("title"),
    }


def parse_team_row(row: Tag) -> DotabuffTeam | None:
    cells = row.find_all("td", recursive=False)
    if len(cells) < 9:
        return None

    team_link = cells[1].select_one("a.team-link[href^='/esports/teams/']")
    if not team_link:
        team_link = row.select_one("a.team-link[href^='/esports/teams/']")
    if not team_link:
        return None

    image = row.select_one("img.img-team")
    team_id, slug = parse_team_path(team_link.get("href"))
    duration_value = parse_int(cells[8].get("data-value"))
    name = tag_text(team_link.select_one(".team-text-full, .team-text"))

    return DotabuffTeam(
        team_id=team_id,
        slug=slug,
        name=name or (image.get("alt") if image else None),
        url=absolute_dotabuff_url(team_link.get("href")),
        image_url=absolute_dotabuff_url(image.get("src")) if image else None,
        last_match=parse_last_match(cells[1]),
        popularity_rank=parse_rank(tag_text(cells[2])),
        matches=parse_int(cells[3].get("data-value") or tag_text(cells[3])),
        win_rate=parse_float(cells[4].get("data-value") or tag_text(cells[4])),
        kda=parse_float(cells[5].get("data-value") or tag_text(cells[5])),
        gpm=parse_int(cells[6].get("data-value") or tag_text(cells[6])),
        xpm=parse_int(cells[7].get("data-value") or tag_text(cells[7])),
        duration_seconds=duration_value,
        duration=tag_text(cells[8]).split(" ", 1)[0] or None,
    )


def parse_teams(html: str, source_url: str | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("#teams-all table")
    if not table:
        table = soup.select_one("section header:-soup-contains('Popular Teams') + article table")
    if not table:
        raise ValueError("Could not find the Popular Teams table.")

    teams = []
    for row in table.select("tbody > tr"):
        team = parse_team_row(row)
        if team:
            teams.append(asdict(team))

    return {
        "source_url": source_url or DOTABUFF_TEAMS_URL,
        "teams": teams,
    }


def fetch_teams_html(url: str, timeout: int, insecure: bool = False) -> tuple[str, str]:
    request = Request(url, headers=build_request_headers())
    context = ssl._create_unverified_context() if insecure else None
    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace"), url
    except HTTPError as exc:
        raise SystemExit(dotabuff_http_error_message(exc, url)) from exc
    except URLError as exc:
        raise SystemExit(f"Could not fetch {url}: {exc.reason}") from exc


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse Dotabuff esports teams HTML into JSON.")
    parser.add_argument("url", nargs="?", default=DOTABUFF_TEAMS_URL, help="Dotabuff teams URL.")
    parser.add_argument("--html-file", help="Read already downloaded Dotabuff HTML from a file.")
    parser.add_argument("--env-file", default=".env", help="Load request header variables from this env file.")
    parser.add_argument("--timeout", type=int, default=20, help="Network timeout in seconds.")
    parser.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    load_env_file(args.env_file)

    source_url = args.url
    if args.html_file:
        with open(args.html_file, "r", encoding="utf-8") as file_obj:
            html = file_obj.read()
        source_url = DOTABUFF_TEAMS_URL
    else:
        html, source_url = fetch_teams_html(args.url, args.timeout, args.insecure)

    parsed = parse_teams(html, source_url=source_url)
    json.dump(parsed, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
