#!/usr/bin/env python3
"""Parse Dotabuff public hero match index pages into a small match-id queue.

Usage:
    python3 scripts/parsing/dotabuff_hero_matches_parser.py abaddon --pretty
    python3 scripts/parsing/dotabuff_hero_matches_parser.py https://www.dotabuff.com/matches?hero=abaddon\&lobby_type=ranked_matchmaking
    python3 scripts/parsing/dotabuff_hero_matches_parser.py --html-file hero_matches.html --hero abaddon
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
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
    build_request_headers,
    dotabuff_http_error_message,
    load_env_file,
    tag_text,
)


DOTABUFF_HERO_MATCHES_URL = "https://www.dotabuff.com/matches"
DEFAULT_LOBBY_TYPE = "ranked_matchmaking"


class HeroMatchesTableNotFoundError(ValueError):
    """Raised when a Dotabuff hero matches page has no recent matches table."""


def build_hero_matches_url(hero_or_url: str, lobby_type: str = DEFAULT_LOBBY_TYPE) -> str:
    if hero_or_url.startswith(("http://", "https://")):
        return hero_or_url
    else:
        hero = hero_or_url.strip().strip("/").removeprefix("heroes/")
        query = {"hero": [hero], "lobby_type": [lobby_type]}
        return urlunparse(urlparse(DOTABUFF_HERO_MATCHES_URL)._replace(query=urlencode(query, doseq=True)))


def parse_match_id(path: str | None) -> int | None:
    if not path:
        return None
    match = re.search(r"/matches/(\d+)", path)
    return int(match.group(1)) if match else None


def parse_hero_slug(source_url: str | None, soup: BeautifulSoup) -> str | None:
    selected = soup.select_one("select#hero option[selected]")
    if selected and selected.get("value"):
        return selected.get("value")

    if not source_url:
        return None
    values = parse_qs(urlparse(source_url).query).get("hero")
    return values[0] if values else None


def parse_duration_seconds(value: str | None) -> int | None:
    parts = (value or "").strip().split(":")
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        return int(parts[0]) * 60 + int(parts[1])
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return None


def parse_winner_side(result_link: Tag | None) -> str | None:
    if not result_link:
        return None
    classes = result_link.get("class") or []
    if "color-faction-radiant" in classes:
        return "radiant"
    if "color-faction-dire" in classes:
        return "dire"

    text = tag_text(result_link).lower()
    if text.startswith("radiant"):
        return "radiant"
    if text.startswith("dire"):
        return "dire"
    return None


def parse_match_row(row: Tag) -> dict[str, Any] | None:
    cells = row.find_all("td", recursive=False)
    if len(cells) < 4:
        return None

    match_link = cells[0].select_one("a[href^='/matches/']")
    result_link = cells[2].select_one("a[href^='/matches/']") if len(cells) > 2 else None
    if not match_link:
        return None

    duration = tag_text(cells[3], separator="|").split("|", 1)[0] or None
    return {
        "match_id": parse_match_id(match_link.get("href")),
        "winner_side": parse_winner_side(result_link),
        "duration_seconds": parse_duration_seconds(duration),
        "duration": duration,
    }


def parse_hero_matches(html: str, source_url: str | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("section table")
    if not table:
        raise HeroMatchesTableNotFoundError("Could not find the Recent Matches table.")

    matches = []
    for row in table.select("tbody > tr"):
        parsed = parse_match_row(row)
        if parsed and parsed.get("match_id") is not None:
            matches.append(parsed)

    return {
        "source_url": source_url,
        "hero_slug": parse_hero_slug(source_url, soup),
        "matches": matches,
    }


def fetch_hero_matches_html(
    hero_or_url: str,
    timeout: int,
    insecure: bool = False,
    lobby_type: str = DEFAULT_LOBBY_TYPE,
) -> tuple[str, str]:
    url = build_hero_matches_url(hero_or_url, lobby_type=lobby_type)
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
    parser = argparse.ArgumentParser(description="Parse Dotabuff public hero match ids into JSON.")
    parser.add_argument("hero", nargs="?", default="abaddon", help="Dotabuff hero slug or full hero matches URL.")
    parser.add_argument("--hero", dest="hero_option", help="Same as the positional hero argument.")
    parser.add_argument("--lobby-type", default=DEFAULT_LOBBY_TYPE, help="Dotabuff lobby_type query value.")
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
    hero = args.hero_option or args.hero
    source_url = build_hero_matches_url(hero, lobby_type=args.lobby_type)
    if args.html_file:
        with open(args.html_file, "r", encoding="utf-8") as file_obj:
            html = file_obj.read()
    else:
        html, source_url = fetch_hero_matches_html(
            hero,
            args.timeout,
            args.insecure,
            lobby_type=args.lobby_type,
        )

    parsed = parse_hero_matches(html, source_url=source_url)
    json.dump(parsed, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
