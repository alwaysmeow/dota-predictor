"""Dataset helpers for ML experiments."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.db.postgres_common import load_parser_env, make_target_conninfo, require_psycopg

REPO_ROOT = Path(__file__).resolve().parents[2]
HEROES_PATH = REPO_ROOT / "dotaconstants" / "build" / "heroes.json"
HERO_ALIASES = {
    "outworlddestroyer": "outworlddevourer",
    "beastmode": "beastmaster",
    "ferocity": "primalbeast",
}

MATCH_DRAFT_QUERY = """
    SELECT
        m.match_id,
        m.winner_side,
        p.side,
        p.hero_slug AS hero,
        p.player_slot
    FROM dotabuff_matches m
    LEFT JOIN dotabuff_match_players p ON p.match_id = m.match_id
    WHERE m.match_id = %(match_id)s
    ORDER BY p.player_slot ASC
"""

MATCH_DRAFTS_QUERY = """
    WITH selected_matches AS (
        SELECT
            m.match_id
        FROM dotabuff_matches m
        JOIN dotabuff_match_players p ON p.match_id = m.match_id
        WHERE
            m.winner_side IN ('radiant', 'dire')
            AND p.side IN ('radiant', 'dire')
            AND p.hero_slug IS NOT NULL
        GROUP BY m.match_id
        HAVING
            COUNT(*) = 10
            AND COUNT(*) FILTER (WHERE p.side = 'radiant') = 5
            AND COUNT(*) FILTER (WHERE p.side = 'dire') = 5
        ORDER BY m.match_id DESC
        LIMIT %(limit)s
        OFFSET %(offset)s
    )
    SELECT
        m.match_id,
        m.winner_side,
        p.side,
        p.hero_slug AS hero,
        p.player_slot
    FROM selected_matches sm
    JOIN dotabuff_matches m ON m.match_id = sm.match_id
    JOIN dotabuff_match_players p ON p.match_id = m.match_id
    ORDER BY m.match_id DESC, p.player_slot ASC
"""


@dataclass(frozen=True)
class MatchDraft:
    match_id: int
    radiant_heroes: list[str]
    dire_heroes: list[str]
    winner_side: str | None


@dataclass(frozen=True)
class NormalizedMatchDraft:
    match_id: int
    radiant_hero_ids: list[int]
    dire_hero_ids: list[int]
    winner_side: int | None


@dataclass(frozen=True)
class HeroMappings:
    exact: dict[str, int]
    localized_prefixes: tuple[tuple[str, int], ...]


def normalize_hero_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def load_hero_mappings(path: Path = HEROES_PATH) -> HeroMappings:
    heroes = json.loads(path.read_text(encoding="utf-8"))
    exact: dict[str, int] = {}
    localized_prefixes: list[tuple[str, int]] = []

    for hero in heroes.values():
        hero_id = int(hero["id"])
        localized_name = hero.get("localized_name")
        internal_name = hero.get("name")

        if localized_name:
            normalized_localized_name = normalize_hero_name(localized_name)
            exact[normalized_localized_name] = hero_id
            localized_prefixes.append((normalized_localized_name, hero_id))
        if internal_name:
            short_name = internal_name.removeprefix("npc_dota_hero_")
            exact[normalize_hero_name(short_name)] = hero_id

    localized_prefixes.sort(key=lambda item: len(item[0]), reverse=True)
    return HeroMappings(exact=exact, localized_prefixes=tuple(localized_prefixes))


def load_hero_id_map(path: Path = HEROES_PATH) -> dict[str, int]:
    return load_hero_mappings(path).exact


def winner_side_to_label(winner_side: str | None) -> int | None:
    if winner_side is None:
        return None
    if winner_side == "radiant":
        return 0
    if winner_side == "dire":
        return 1
    raise ValueError(f"Unexpected winner_side: {winner_side!r}")


def rows_to_match_draft(match_id: int, rows: list[dict]) -> MatchDraft:
    if not rows:
        raise LookupError(f"Match {match_id} was not found in dotabuff_matches.")

    radiant_heroes: list[str] = []
    dire_heroes: list[str] = []
    for row in rows:
        hero = row["hero"]
        if not hero:
            continue

        if row["side"] == "radiant":
            radiant_heroes.append(hero)
        elif row["side"] == "dire":
            dire_heroes.append(hero)

    return MatchDraft(
        match_id=int(rows[0]["match_id"]),
        radiant_heroes=radiant_heroes,
        dire_heroes=dire_heroes,
        winner_side=rows[0]["winner_side"],
    )


def rows_to_match_drafts(rows: list[dict]) -> list[MatchDraft]:
    drafts: list[MatchDraft] = []
    current_match_id: int | None = None
    current_rows: list[dict] = []

    for row in rows:
        row_match_id = int(row["match_id"])
        if current_match_id is None:
            current_match_id = row_match_id
        elif row_match_id != current_match_id:
            drafts.append(rows_to_match_draft(current_match_id, current_rows))
            current_match_id = row_match_id
            current_rows = []

        current_rows.append(row)

    if current_match_id is not None:
        drafts.append(rows_to_match_draft(current_match_id, current_rows))

    return drafts


class DatasetMaker:
    """Reusable Postgres connection for ML dataset queries."""

    def __init__(
        self,
        *,
        database: str | None = None,
        env_file: str | None = ".env",
        heroes_path: Path = HEROES_PATH,
    ) -> None:
        if env_file:
            load_parser_env(env_file)

        psycopg, _, _, _, dict_row = require_psycopg()
        self._conn = psycopg.connect(
            make_target_conninfo(database=database),
            row_factory=dict_row,
            autocommit=True,
        )
        self._hero_mappings = load_hero_mappings(heroes_path)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "DatasetMaker":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def fetch_match_draft(self, match_id: int) -> MatchDraft:
        """Fetch hero drafts and winner side for one match."""
        with self._conn.cursor() as cur:
            cur.execute(MATCH_DRAFT_QUERY, {"match_id": match_id})
            rows = cur.fetchall()

        return rows_to_match_draft(match_id, rows)

    def fetch_match_drafts(self, limit: int, *, offset: int = 0) -> list[MatchDraft]:
        """Fetch complete 5v5 match drafts with known winner side.

        Matches are returned in descending ``match_id`` order. ``offset`` can be
        used for simple pagination.
        """
        if limit < 1:
            raise ValueError(f"limit must be positive, got {limit}")
        if offset < 0:
            raise ValueError(f"offset must be non-negative, got {offset}")

        with self._conn.cursor() as cur:
            cur.execute(MATCH_DRAFTS_QUERY, {"limit": limit, "offset": offset})
            rows = cur.fetchall()

        return rows_to_match_drafts(rows)

    def normalize_match_draft(self, draft: MatchDraft) -> NormalizedMatchDraft:
        """Convert hero names and winner side into numeric model inputs."""
        return NormalizedMatchDraft(
            match_id=draft.match_id,
            radiant_hero_ids=[self.hero_name_to_id(hero) for hero in draft.radiant_heroes],
            dire_hero_ids=[self.hero_name_to_id(hero) for hero in draft.dire_heroes],
            winner_side=winner_side_to_label(draft.winner_side),
        )

    def fetch_normalized_match_draft(self, match_id: int) -> NormalizedMatchDraft:
        """Fetch one match draft and normalize heroes and winner side."""
        return self.normalize_match_draft(self.fetch_match_draft(match_id))

    def fetch_normalized_match_drafts(self, limit: int, *, offset: int = 0) -> list[NormalizedMatchDraft]:
        """Fetch several complete match drafts and normalize them for models."""
        return [self.normalize_match_draft(draft) for draft in self.fetch_match_drafts(limit, offset=offset)]

    def hero_name_to_id(self, hero_name: str) -> int:
        key = normalize_hero_name(hero_name)
        key = HERO_ALIASES.get(key, key)
        try:
            return self._hero_mappings.exact[key]
        except KeyError:
            pass

        for hero_prefix, hero_id in self._hero_mappings.localized_prefixes:
            if key.startswith(hero_prefix):
                return hero_id

        raise KeyError(f"Unknown hero name: {hero_name!r}")
