#!/usr/bin/env python3
"""Parse Dotabuff match overview pages into pre-match features plus result label.

Usage:
    python3 scripts/parsing/dotabuff_match_parser.py 8786827560 --pretty
    python3 scripts/parsing/dotabuff_match_parser.py https://www.dotabuff.com/matches/8786827560
    python3 scripts/parsing/dotabuff_match_parser.py --html-file match.html --match-id 8786827560

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
    attr_contains,
    build_request_headers,
    clean_text,
    dotabuff_http_error_message,
    first_class_match,
    load_env_file,
    parse_int,
    read_text_response,
    tag_text,
)


DOTABUFF_MATCH_URL = "https://www.dotabuff.com/matches/{match_id}"


@dataclass
class MatchPlayer:
    side: str
    player_id: int | None
    player_name: str | None
    player_url: str | None
    hero: str | None
    hero_slug: str | None
    level: int | None
    role: str | None
    lane: str | None
    lane_text: str | None
    lane_outcome: str | None
    kills: int | None
    deaths: int | None
    assists: int | None
    net_worth: int | None
    last_hits: int | None
    denies: int | None
    gpm: int | None
    xpm: int | None
    hero_damage: int | None
    hero_healing: int | None
    tower_damage: int | None
    observer_wards: int | None
    sentry_wards: int | None
    items: list[dict[str, Any]] = field(default_factory=list)
    neutral_item: dict[str, Any] | None = None
    backpack: list[dict[str, Any]] = field(default_factory=list)
    buffs: list[dict[str, Any]] = field(default_factory=list)


def fetch_match_html(match_id_or_url: str, timeout: int, insecure: bool = False) -> tuple[str, str, int | None]:
    if re.fullmatch(r"\d+", match_id_or_url):
        match_id = int(match_id_or_url)
        url = DOTABUFF_MATCH_URL.format(match_id=match_id)
    else:
        url = match_id_or_url
        match = re.search(r"/matches/(\d+)", url)
        match_id = int(match.group(1)) if match else None

    request = Request(url, headers=build_request_headers(url))
    context = ssl._create_unverified_context() if insecure else None
    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            return read_text_response(response), url, match_id
    except HTTPError as exc:
        raise SystemExit(dotabuff_http_error_message(exc, url)) from exc
    except URLError as exc:
        raise SystemExit(f"Could not fetch {url}: {exc.reason}") from exc


def parse_header_metadata(soup: BeautifulSoup) -> dict[str, Any]:
    result: dict[str, Any] = {}
    header = soup.select_one(".header-content")
    if not header:
        return result

    title = header.select_one(".header-content-title h1")
    if title:
        small = title.select_one("small")
        if small:
            small.extract()
        result["title"] = tag_text(title)

    for dl in header.select(".header-content-secondary dl"):
        key = tag_text(dl.select_one("dt")).lower().replace(" ", "_")
        dd = dl.select_one("dd")
        if not key or dd is None:
            continue
        value: Any = tag_text(dd)
        link = dd.select_one("a[href]")
        time_tag = dd.select_one("time")
        if time_tag and time_tag.get("datetime"):
            value = {
                "text": tag_text(time_tag),
                "datetime": time_tag.get("datetime"),
                "title": time_tag.get("title"),
            }
        elif link:
            value = {
                "name": tag_text(link),
                "url": absolute_dotabuff_url(link.get("href")),
            }
        result[key] = value

    return result


def parse_result(soup: BeautifulSoup) -> dict[str, Any]:
    result: dict[str, Any] = {}

    victory = soup.select_one(".match-show .match-result")
    if victory:
        classes = victory.get("class") or []
        winner_side = "radiant" if "radiant" in classes else "dire" if "dire" in classes else None
        team = victory.select_one(".team-text-full, .team-text")
        result["winner_side"] = winner_side
        result["winner_team"] = tag_text(team) or tag_text(victory).replace(" Victory!", "")

    subtitle = soup.select_one(".match-victory-subtitle")
    if subtitle:
        radiant = subtitle.select_one(".the-radiant.score")
        dire = subtitle.select_one(".the-dire.score")
        duration = subtitle.select_one(".duration")
        result["radiant_score"] = parse_int(tag_text(radiant))
        result["dire_score"] = parse_int(tag_text(dire))
        result["duration"] = tag_text(duration)

    return result


def parse_label(soup: BeautifulSoup) -> dict[str, Any]:
    victory = soup.select_one(".match-show .match-result")
    if not victory:
        return {"winner_side": None, "winner_team": None}

    classes = victory.get("class") or []
    winner_side = "radiant" if "radiant" in classes else "dire" if "dire" in classes else None
    team = victory.select_one(".team-text-full, .team-text")
    return {
        "winner_side": winner_side,
        "winner_team": tag_text(team) or tag_text(victory).replace(" Victory!", ""),
    }


def parse_teams(soup: BeautifulSoup) -> dict[str, dict[str, Any]]:
    teams: dict[str, dict[str, Any]] = {}
    for side in ("radiant", "dire"):
        section = soup.select_one(f".team-results > section.{side}")
        if not section:
            continue
        link = section.select_one("header a.team-link[href]")
        teams[side] = {
            "name": tag_text(link.select_one(".team-text-full, .team-text")) if link else None,
            "url": absolute_dotabuff_url(link.get("href")) if link else None,
            "victory": bool(section.select_one(".victory-icon")),
        }
    return teams


def parse_prematch_teams(soup: BeautifulSoup) -> dict[str, dict[str, Any]]:
    teams: dict[str, dict[str, Any]] = {}
    for side in ("radiant", "dire"):
        section = soup.select_one(f".team-results > section.{side}")
        if not section:
            continue
        link = section.select_one("header a.team-link[href]")
        teams[side] = {
            "name": tag_text(link.select_one(".team-text-full, .team-text")) if link else None,
            "url": absolute_dotabuff_url(link.get("href")) if link else None,
        }
    return teams


def parse_played_at(soup: BeautifulSoup) -> dict[str, str | None] | None:
    metadata = parse_header_metadata(soup)
    match_ended = metadata.get("match_ended")
    if not isinstance(match_ended, dict):
        return None
    return {
        "text": match_ended.get("text"),
        "datetime": match_ended.get("datetime"),
        "title": match_ended.get("title"),
    }


def parse_is_professional_match(soup: BeautifulSoup) -> bool:
    selectors = (
        ".header-content-secondary a[href^='/esports/']",
        ".match-series-header",
        ".team-results a[href^='/esports/teams/']",
        ".team-results a[href^='/esports/players/']",
    )
    return any(soup.select_one(selector) is not None for selector in selectors)


def role_or_lane_from_icon(cell: Tag, prefix: str) -> str | None:
    icon = cell.select_one(f"i[class*='fa-{prefix}-']")
    if not icon:
        return None
    raw = first_class_match(icon, rf"fa-{prefix}-([a-z0-9-]+)")
    if not raw:
        return None
    return raw.replace("-", "_")


def icon_class_from_cell(cell: Tag, prefix: str) -> str | None:
    icon = cell.select_one(f"i[class*='fa-{prefix}-']")
    if not icon:
        return None
    for class_name in icon.get("class") or []:
        if class_name.startswith(f"fa-{prefix}-"):
            return class_name
    return None


def parse_item_container(container: Tag | None) -> list[dict[str, Any]]:
    if not container:
        return []
    parsed: list[dict[str, Any]] = []
    for item in container.select(".match-item-with-time"):
        link = item.select_one("a[href]")
        image = item.select_one("img[alt]")
        if not link and not image:
            continue
        time_nodes = item.select("div")
        time_text = None
        for node in time_nodes:
            text = tag_text(node)
            if re.fullmatch(r"\d+m", text):
                time_text = text
                break
        parsed.append(
            {
                "name": image.get("alt") if image else tag_text(link),
                "slug": link.get("href", "").split("/")[-1] if link else None,
                "url": absolute_dotabuff_url(link.get("href")) if link else None,
                "time": time_text,
            }
        )
    return parsed


def parse_player_row(row: Tag, side: str) -> MatchPlayer:
    cells = row.find_all("td", recursive=False)
    player_id = parse_int(first_class_match(row, r"player-(\d+)"))

    hero_link = row.select_one("td.cell-fill-image a[href^='/heroes/']")
    hero_img = hero_link.select_one("img[alt]") if hero_link else None
    level = parse_int(tag_text(hero_link.select_one("div")) if hero_link else None)

    player_link = row.select_one("a.player-link[href*='/players/'], a.player-link[href*='/esports/players/']")
    player_name = tag_text(player_link.select_one(".player-text-full, .player-text")) if player_link else None

    lane_text = tag_text(row.select_one(".player-lane-text > acronym"))
    lane_outcome = tag_text(row.select_one(".lane-outcome")).lower() or None

    stat_cells = cells[5:19] if len(cells) >= 19 else []
    wards_cell = cells[18] if len(cells) > 18 else None
    item_cell = cells[19] if len(cells) > 19 else None

    ward_values: list[int | None] = []
    if wards_cell:
        ward_values = [parse_int(tag_text(span)) for span in wards_cell.select("span.color-item-observer-ward, span.color-item-sentry-ward")]

    return MatchPlayer(
        side=side,
        player_id=player_id,
        player_name=player_name,
        player_url=absolute_dotabuff_url(player_link.get("href")) if player_link else None,
        hero=hero_img.get("alt") if hero_img else None,
        hero_slug=hero_link.get("href", "").split("/")[-1] if hero_link else None,
        level=level,
        role=role_or_lane_from_icon(cells[1], "role") if len(cells) > 1 else None,
        lane=role_or_lane_from_icon(cells[2], "lane") if len(cells) > 2 else None,
        lane_text=lane_text or None,
        lane_outcome=lane_outcome,
        kills=parse_int(tag_text(stat_cells[0])) if len(stat_cells) > 0 else None,
        deaths=parse_int(tag_text(stat_cells[1])) if len(stat_cells) > 1 else None,
        assists=parse_int(tag_text(stat_cells[2])) if len(stat_cells) > 2 else None,
        net_worth=parse_int(tag_text(stat_cells[3])) if len(stat_cells) > 3 else None,
        last_hits=parse_int(tag_text(stat_cells[4])) if len(stat_cells) > 4 else None,
        denies=parse_int(tag_text(stat_cells[6])) if len(stat_cells) > 6 else None,
        gpm=parse_int(tag_text(stat_cells[7])) if len(stat_cells) > 7 else None,
        xpm=parse_int(tag_text(stat_cells[9])) if len(stat_cells) > 9 else None,
        hero_damage=parse_int(tag_text(stat_cells[10])) if len(stat_cells) > 10 else None,
        hero_healing=parse_int(tag_text(stat_cells[11])) if len(stat_cells) > 11 else None,
        tower_damage=parse_int(tag_text(stat_cells[12])) if len(stat_cells) > 12 else None,
        observer_wards=ward_values[0] if len(ward_values) > 0 else None,
        sentry_wards=ward_values[1] if len(ward_values) > 1 else None,
        items=parse_item_container(item_cell.select_one(".player-inventory-items") if item_cell else None),
        neutral_item=(parse_item_container(item_cell.select_one(".player-neutral-item") if item_cell else None) or [None])[0],
        backpack=parse_item_container(item_cell.select_one(".subtext") if item_cell else None),
        buffs=parse_item_container(item_cell.select_one(".buff-icons") if item_cell else None),
    )


def parse_players(soup: BeautifulSoup) -> list[dict[str, Any]]:
    players: list[dict[str, Any]] = []
    for side in ("radiant", "dire"):
        table = soup.select_one(f".team-results > section.{side} table.match-team-table")
        if not table:
            continue
        for row in table.select("tbody > tr"):
            players.append(asdict(parse_player_row(row, side)))
    return players


def parse_prematch_player_row(row: Tag, side: str) -> dict[str, Any]:
    cells = row.find_all("td", recursive=False)
    player_id = parse_int(first_class_match(row, r"player-(\d+)"))

    hero_link = row.select_one("td.cell-fill-image a[href^='/heroes/']")
    hero_img = hero_link.select_one("img[alt]") if hero_link else None
    player_link = row.select_one("a.player-link[href*='/players/'], a.player-link[href*='/esports/players/']")
    player_name = tag_text(player_link.select_one(".player-text-full, .player-text")) if player_link else None

    role_cell = cells[1] if len(cells) > 1 else None
    lane_cell = cells[2] if len(cells) > 2 else None

    return {
        "side": side,
        "player_id": player_id,
        "player_name": player_name,
        "player_url": absolute_dotabuff_url(player_link.get("href")) if player_link else None,
        "hero": hero_img.get("alt") if hero_img else None,
        "hero_slug": hero_link.get("href", "").split("/")[-1] if hero_link else None,
        "role": role_or_lane_from_icon(role_cell, "role") if role_cell else None,
        "role_icon": icon_class_from_cell(role_cell, "role") if role_cell else None,
        "lane": role_or_lane_from_icon(lane_cell, "lane") if lane_cell else None,
        "lane_icon": icon_class_from_cell(lane_cell, "lane") if lane_cell else None,
    }


def parse_prematch_players(soup: BeautifulSoup) -> list[dict[str, Any]]:
    players: list[dict[str, Any]] = []
    for side in ("radiant", "dire"):
        table = soup.select_one(f".team-results > section.{side} table.match-team-table")
        if not table:
            continue
        for row in table.select("tbody > tr"):
            players.append(parse_prematch_player_row(row, side))
    return players


def parse_team_totals(soup: BeautifulSoup) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    keys = [
        "kills",
        "deaths",
        "assists",
        "net_worth",
        "last_hits",
        None,
        "denies",
        "gpm",
        None,
        "xpm",
        "hero_damage",
        "hero_healing",
        "tower_damage",
        "wards",
    ]
    for side in ("radiant", "dire"):
        row = soup.select_one(f".team-results > section.{side} table.match-team-table tfoot tr")
        if not row:
            continue
        cells = row.find_all("td", recursive=False)[5:19]
        side_totals: dict[str, Any] = {}
        for key, cell in zip(keys, cells):
            if not key:
                continue
            if key == "wards":
                side_totals["observer_wards"] = parse_int(tag_text(cell.select_one(".color-item-observer-ward")))
                side_totals["sentry_wards"] = parse_int(tag_text(cell.select_one(".color-item-sentry-ward")))
            else:
                side_totals[key] = parse_int(tag_text(cell))
        totals[side] = side_totals
    return totals


def parse_picks_bans(soup: BeautifulSoup) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for side in ("radiant", "dire"):
        footer = soup.select_one(f".team-results > section.{side} > footer .picks-inline")
        if not footer:
            continue
        for node in footer.find_all("div", recursive=False):
            classes = node.get("class") or []
            action = "pick" if "pick" in classes else "ban" if "ban" in classes else None
            if not action:
                continue
            link = node.select_one("a[href^='/heroes/']")
            img = node.select_one("img[alt]")
            entries.append(
                {
                    "side": side,
                    "action": action,
                    "sequence": parse_int(tag_text(node.select_one(".seq"))),
                    "hero": img.get("alt") if img else None,
                    "hero_slug": link.get("href", "").split("/")[-1] if link else None,
                }
            )
    return sorted(entries, key=lambda item: item["sequence"] or 0)


def parse_builds(soup: BeautifulSoup) -> dict[str, list[dict[str, Any]]]:
    builds: dict[str, list[dict[str, Any]]] = {"radiant": [], "dire": []}
    for side in ("radiant", "dire"):
        table = soup.select_one(f".match-ability-builds section.{side} table")
        if not table:
            continue
        for row in table.select("tbody > tr"):
            hero_img = row.select_one("th.cell-icon img[alt]")
            hero_link = row.select_one("th.cell-icon a[href^='/heroes/']")
            skills: list[dict[str, Any]] = []
            for level, cell in enumerate(row.find_all("td", recursive=False)[1:], start=1):
                if attr_contains(cell, "class", "empty"):
                    continue
                image = cell.select_one("img[alt]")
                link = cell.select_one("a[href]")
                if not image:
                    continue
                hotkey = None
                for div in cell.find_all("div"):
                    if "tw-text-[10px]" in (div.get("class") or []):
                        hotkey = tag_text(div)
                        break
                skills.append(
                    {
                        "level": level,
                        "name": image.get("alt") or "Talent",
                        "slug": link.get("href", "").split("/")[-1] if link else None,
                        "hotkey": hotkey,
                        "is_talent": "talent" in (image.get("src") or ""),
                    }
                )
            builds[side].append(
                {
                    "player_id": parse_int(first_class_match(row, r"player-(\d+)")),
                    "hero": hero_img.get("alt") if hero_img else None,
                    "hero_slug": hero_link.get("href", "").split("/")[-1] if hero_link else None,
                    "skills": skills,
                }
            )
    return builds


def parse_kill_matrix(soup: BeautifulSoup) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {"radiant": [], "dire": []}
    for side in ("radiant", "dire"):
        section = soup.select_one(f".match-victim-kills section.{side}")
        if not section:
            continue
        for row in section.select("tbody > tr"):
            killer_img = row.select_one("th img[oldtitle], th img[alt]")
            cells = row.find_all("td", recursive=False)
            victims = []
            for cell in cells:
                victim_img = cell.select_one(".victim img[oldtitle], .victim img[alt]")
                kills_text = tag_text(cell.select_one(".kills"))
                kills_match = re.search(r"x(\d+)", kills_text)
                gold_match = re.search(r"([\d,]+)g", kills_text)
                victims.append(
                    {
                        "hero": victim_img.get("oldtitle") or victim_img.get("alt") if victim_img else None,
                        "kills": int(kills_match.group(1)) if kills_match else 0,
                        "gold": parse_int(gold_match.group(1)) if gold_match else None,
                    }
                )
            result[side].append(
                {
                    "player_id": parse_int(first_class_match(row, r"player-(\d+)")),
                    "hero": killer_img.get("oldtitle") or killer_img.get("alt") if killer_img else None,
                    "victims": victims,
                }
            )
    return result


def parse_minimap_objectives(soup: BeautifulSoup) -> dict[str, Any]:
    minimap = soup.select_one(".match-minimap")
    if not minimap:
        return {}
    return {
        "winner": minimap.get("data-winner"),
        "radiant_towers": parse_int(minimap.get("data-towers-radiant")),
        "dire_towers": parse_int(minimap.get("data-towers-dire")),
        "radiant_barracks": parse_int(minimap.get("data-barracks-radiant")),
        "dire_barracks": parse_int(minimap.get("data-barracks-dire")),
    }


def parse_match(html: str, match_id: int | None = None, source_url: str | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")

    if match_id is None:
        title = soup.select_one("title")
        match = re.search(r"Match\s+(\d+)", tag_text(title))
        if match:
            match_id = int(match.group(1))

    data = {
        "match_id": match_id,
        "source_url": source_url or (DOTABUFF_MATCH_URL.format(match_id=match_id) if match_id else None),
        "is_professional_match": parse_is_professional_match(soup),
        "played_at": parse_played_at(soup),
        "teams": parse_prematch_teams(soup),
        "players": parse_prematch_players(soup),
        "label": parse_label(soup),
    }
    return data


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse Dotabuff match overview HTML into JSON.")
    parser.add_argument("match", nargs="?", help="Dotabuff match id or full match URL.")
    parser.add_argument("--match-id", type=int, help="Match id to use with --html-file.")
    parser.add_argument("--html-file", help="Read already downloaded Dotabuff HTML from a file.")
    parser.add_argument("--env-file", default=".env", help="Load request header variables from this env file.")
    parser.add_argument("--timeout", type=int, default=20, help="Network timeout in seconds.")
    parser.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.html_file and not args.match:
        parser.error("provide a match id/URL or --html-file")

    load_env_file(args.env_file)

    source_url = None
    match_id = args.match_id
    if args.html_file:
        with open(args.html_file, "r", encoding="utf-8") as file_obj:
            html = file_obj.read()
    else:
        html, source_url, fetched_match_id = fetch_match_html(args.match, args.timeout, args.insecure)
        match_id = match_id or fetched_match_id

    parsed = parse_match(html, match_id=match_id, source_url=source_url)
    json.dump(parsed, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
