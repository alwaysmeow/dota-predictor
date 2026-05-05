#!/usr/bin/env python3
"""Parse Dotabuff esports team matches pages into JSON.

Usage:
    python3 scripts/parsing/dotabuff_team_matches_parser.py 9247354-team-falcons --pretty
    python3 scripts/parsing/dotabuff_team_matches_parser.py https://www.dotabuff.com/esports/teams/9247354-team-falcons/matches?page=2
    python3 scripts/parsing/dotabuff_team_matches_parser.py --html-file team_matches.html --team 9247354-team-falcons

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
from dataclasses import asdict, dataclass, field
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
    absolute_dotabuff_url,
    build_request_headers,
    dotabuff_http_error_message,
    load_env_file,
    parse_int,
    tag_text,
)


DOTABUFF_TEAM_MATCHES_URL = "https://www.dotabuff.com/esports/teams/{team}/matches"


class TeamMatchesTableNotFoundError(ValueError):
    """Raised when a Dotabuff team matches page has no matches table."""


@dataclass
class DotabuffTeamRef:
    team_id: int | None
    slug: str | None
    name: str | None
    url: str | None
    image_url: str | None = None


@dataclass
class DotabuffLeagueRef:
    league_id: int | None
    slug: str | None
    name: str | None
    url: str | None
    image_url: str | None = None


@dataclass
class DotabuffSeriesRef:
    series_id: int | None
    url: str | None
    region: str | None


@dataclass
class DotabuffHeroRef:
    slug: str | None
    name: str | None
    url: str | None


@dataclass
class DotabuffTeamMatch:
    match_id: int | None
    url: str | None
    result: str | None
    won: bool | None
    played_at: dict[str, str | None] | None
    league: DotabuffLeagueRef | None
    series: DotabuffSeriesRef | None
    duration_seconds: int | None
    duration: str | None
    heroes: list[DotabuffHeroRef] = field(default_factory=list)
    opponent: DotabuffTeamRef | None = None


def build_team_matches_url(team_or_url: str, page: int | None = None) -> str:
    if team_or_url.startswith("http://") or team_or_url.startswith("https://"):
        url = team_or_url
    else:
        team = team_or_url.strip("/")
        if team.startswith("esports/teams/"):
            team = team.removeprefix("esports/teams/")
        team = team.removesuffix("/matches").strip("/")
        url = DOTABUFF_TEAM_MATCHES_URL.format(team=team)

    if page is None:
        return url

    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["page"] = [str(page)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def parse_team_path(path: str | None) -> tuple[int | None, str | None]:
    if not path:
        return None, None
    match = re.search(r"/esports/teams/(\d+)(?:-([^/?#]+))?", path)
    if not match:
        return None, None
    return int(match.group(1)), match.group(2)


def parse_league_path(path: str | None) -> tuple[int | None, str | None]:
    if not path:
        return None, None
    match = re.search(r"/esports/leagues/(\d+)(?:-([^/?#]+))?", path)
    if not match:
        return None, None
    return int(match.group(1)), match.group(2)


def parse_series_path(path: str | None) -> int | None:
    if not path:
        return None
    match = re.search(r"/esports/series/(\d+)", path)
    return int(match.group(1)) if match else None


def parse_match_path(path: str | None) -> int | None:
    if not path:
        return None
    match = re.search(r"/matches/(\d+)", path)
    return int(match.group(1)) if match else None


def parse_duration_seconds(value: str | None) -> int | None:
    text = tag_text(value) if isinstance(value, Tag) else (value or "").strip()
    match = re.fullmatch(r"(\d+):(\d{2})", text)
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def parse_header_team(soup: BeautifulSoup, source_url: str | None = None) -> DotabuffTeamRef | None:
    link = soup.select_one(".header-content-avatar a.team-link[href^='/esports/teams/']")
    if not link:
        link = soup.select_one(".header-content a.team-link[href^='/esports/teams/']")
    if not link and source_url:
        team_id, slug = parse_team_path(urlparse(source_url).path)
        if team_id is not None:
            return DotabuffTeamRef(team_id=team_id, slug=slug, name=None, url=absolute_dotabuff_url(urlparse(source_url).path))
        return None

    image = link.select_one("img.img-team") if link else None
    team_id, slug = parse_team_path(link.get("href") if link else None)
    title = soup.select_one(".header-content-title h1")
    small = title.select_one("small") if title else None
    if small:
        small.extract()
    name = tag_text(title) or (image.get("alt") if image else None)
    return DotabuffTeamRef(
        team_id=team_id,
        slug=slug,
        name=name,
        url=absolute_dotabuff_url(link.get("href")) if link else None,
        image_url=absolute_dotabuff_url(image.get("src")) if image else None,
    )


def parse_league(cell: Tag | None) -> DotabuffLeagueRef | None:
    link = cell.select_one("a.league-link[href^='/esports/leagues/']") if cell else None
    if not link:
        return None
    image = link.select_one("img.img-league")
    league_id, slug = parse_league_path(link.get("href"))
    return DotabuffLeagueRef(
        league_id=league_id,
        slug=slug,
        name=image.get("alt") if image else tag_text(link) or None,
        url=absolute_dotabuff_url(link.get("href")),
        image_url=absolute_dotabuff_url(image.get("src")) if image else None,
    )


def parse_series(cell: Tag | None) -> DotabuffSeriesRef | None:
    link = cell.select_one("a[href^='/esports/series/']") if cell else None
    if not link:
        return None
    return DotabuffSeriesRef(
        series_id=parse_series_path(link.get("href")),
        url=absolute_dotabuff_url(link.get("href")),
        region=tag_text(cell.select_one("small")) or None,
    )


def parse_heroes(cell: Tag | None) -> list[DotabuffHeroRef]:
    heroes: list[DotabuffHeroRef] = []
    if not cell:
        return heroes
    for link in cell.select("a[href^='/heroes/']"):
        slug = link.get("href", "").removeprefix("/heroes/") or None
        image = link.select_one("img.image-hero")
        name = None
        if image:
            name = image.get("oldtitle") or image.get("title") or image.get("alt")
        heroes.append(DotabuffHeroRef(slug=slug, name=name, url=absolute_dotabuff_url(link.get("href"))))
    return heroes


def parse_opponent(cell: Tag | None) -> DotabuffTeamRef | None:
    link = cell.select_one("a.team-link[href^='/esports/teams/']") if cell else None
    if not link:
        return None
    image = link.select_one("img.img-team")
    text_link = cell.select_one("a.team-link .team-text-full, a.team-link .team-text") if cell else None
    team_id, slug = parse_team_path(link.get("href"))
    return DotabuffTeamRef(
        team_id=team_id,
        slug=slug,
        name=tag_text(text_link) or (image.get("alt") if image else None),
        url=absolute_dotabuff_url(link.get("href")),
        image_url=absolute_dotabuff_url(image.get("src")) if image else None,
    )


def parse_played_at(cell: Tag | None) -> dict[str, str | None] | None:
    time_tag = cell.select_one("time") if cell else None
    if not time_tag:
        return None
    return {
        "text": tag_text(time_tag),
        "datetime": time_tag.get("datetime"),
        "title": time_tag.get("title"),
    }


def parse_match_row(row: Tag) -> DotabuffTeamMatch | None:
    cells = row.find_all("td", recursive=False)
    if len(cells) < 6:
        return None

    result_link = cells[1].select_one("a[href^='/matches/']")
    if not result_link:
        return None

    result_classes = result_link.get("class") or []
    result_text = tag_text(result_link)
    duration = tag_text(cells[3]).split(" ", 1)[0] or None
    return DotabuffTeamMatch(
        match_id=parse_match_path(result_link.get("href")),
        url=absolute_dotabuff_url(result_link.get("href")),
        result=result_text,
        won=True if "won" in result_classes else False if "lost" in result_classes else None,
        played_at=parse_played_at(cells[1]),
        league=parse_league(cells[0]),
        series=parse_series(cells[2]),
        duration_seconds=parse_duration_seconds(duration),
        duration=duration,
        heroes=parse_heroes(cells[4]),
        opponent=parse_opponent(cells[5]),
    )


def parse_pagination(soup: BeautifulSoup) -> dict[str, Any]:
    viewport = tag_text(soup.select_one(".viewport")) or None
    current_text = tag_text(soup.select_one("nav.pagination .page.current"))
    current_page = parse_int(current_text)
    last_link = soup.select_one("nav.pagination .last a[href]")
    next_link = soup.select_one("nav.pagination .next a[href]")
    last_page = None
    if last_link:
        query = parse_qs(urlparse(last_link.get("href", "")).query)
        last_values = query.get("page")
        last_page = parse_int(last_values[0]) if last_values else None

    return {
        "viewport": viewport,
        "current_page": current_page,
        "last_page": last_page,
        "next_url": absolute_dotabuff_url(next_link.get("href")) if next_link else None,
        "last_url": absolute_dotabuff_url(last_link.get("href")) if last_link else None,
    }


def parse_team_matches(html: str, source_url: str | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.recent-esports-matches")
    if not table:
        raise TeamMatchesTableNotFoundError("Could not find the Recent Esports Matches table.")

    team = parse_header_team(soup, source_url=source_url)
    matches = []
    for row in table.select("tbody > tr"):
        parsed = parse_match_row(row)
        if parsed:
            matches.append(asdict(parsed))

    return {
        "source_url": source_url,
        "team": asdict(team) if team else None,
        "pagination": parse_pagination(soup),
        "matches": matches,
    }


def fetch_team_matches_html(team_or_url: str, timeout: int, insecure: bool = False, page: int | None = None) -> tuple[str, str]:
    url = build_team_matches_url(team_or_url, page=page)
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
    parser = argparse.ArgumentParser(description="Parse Dotabuff esports team matches HTML into JSON.")
    parser.add_argument(
        "team",
        nargs="?",
        default="9247354-team-falcons",
        help="Dotabuff team id/slug, team matches path, or full team matches URL.",
    )
    parser.add_argument("--team", dest="team_option", help="Same as the positional team argument.")
    parser.add_argument("--page", type=int, help="Page number to fetch.")
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

    team = args.team_option or args.team
    source_url = build_team_matches_url(team, page=args.page)
    if args.html_file:
        with open(args.html_file, "r", encoding="utf-8") as file_obj:
            html = file_obj.read()
    else:
        html, source_url = fetch_team_matches_html(team, args.timeout, args.insecure, page=args.page)

    parsed = parse_team_matches(html, source_url=source_url)
    json.dump(parsed, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
