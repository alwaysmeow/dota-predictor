"""Shared dataclasses for synthetic draft generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Draft:
    hero_slugs: list[str]
    kind: str
    properties: dict[str, Any]


@dataclass(frozen=True)
class DraftPair:
    scenario: str
    good_draft: Draft
    bad_draft: Draft


@dataclass(frozen=True)
class SyntheticMatch:
    fingerprint: str
    generator_version: str
    seed: int
    scenario: str
    radiant_hero_slugs: list[str]
    dire_hero_slugs: list[str]
    winner_side: str
    radiant_score: float
    dire_score: float
    outdraft_gap: float
    confidence: float
    raw: dict[str, Any]
