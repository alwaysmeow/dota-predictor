"""Generate synthetic obvious-outdraft matches."""

from __future__ import annotations

import hashlib
import json
import random

from scripts.db.synthetic.models import SyntheticMatch
from scripts.db.synthetic.drafts import make_draft_pair


GENERATOR_VERSION = "synthetic-outdraft-v2"
OUTDRAFT_SCORE = 100.0
OUTDRAFTED_SCORE = 0.0
OUTDRAFT_GAP = OUTDRAFT_SCORE - OUTDRAFTED_SCORE
OUTDRAFT_CONFIDENCE = 0.99


def make_fingerprint(radiant: list[str], dire: list[str], winner_side: str, scenario: str) -> str:
    payload = {
        "generator_version": GENERATOR_VERSION,
        "scenario": scenario,
        "radiant": radiant,
        "dire": dire,
        "winner_side": winner_side,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def make_match(rng: random.Random, seed: int) -> SyntheticMatch:
    draft_pair = make_draft_pair(rng)
    winner_side = rng.choice(("radiant", "dire"))
    if winner_side == "radiant":
        radiant = draft_pair.good_draft.hero_slugs
        dire = draft_pair.bad_draft.hero_slugs
        radiant_score = OUTDRAFT_SCORE
        dire_score = OUTDRAFTED_SCORE
    else:
        radiant = draft_pair.bad_draft.hero_slugs
        dire = draft_pair.good_draft.hero_slugs
        radiant_score = OUTDRAFTED_SCORE
        dire_score = OUTDRAFT_SCORE

    raw = {
        "good_draft": {
            "kind": draft_pair.good_draft.kind,
            "hero_slugs": draft_pair.good_draft.hero_slugs,
            "properties": draft_pair.good_draft.properties,
        },
        "bad_draft": {
            "kind": draft_pair.bad_draft.kind,
            "hero_slugs": draft_pair.bad_draft.hero_slugs,
            "properties": draft_pair.bad_draft.properties,
        },
        "generator": {
            "version": GENERATOR_VERSION,
            "source": "hand_curated_hero_lists",
        },
    }
    return SyntheticMatch(
        fingerprint=make_fingerprint(radiant, dire, winner_side, draft_pair.scenario),
        generator_version=GENERATOR_VERSION,
        seed=seed,
        scenario=draft_pair.scenario,
        radiant_hero_slugs=radiant,
        dire_hero_slugs=dire,
        winner_side=winner_side,
        radiant_score=radiant_score,
        dire_score=dire_score,
        outdraft_gap=OUTDRAFT_GAP,
        confidence=OUTDRAFT_CONFIDENCE,
        raw=raw,
    )


def generate_matches(count: int, seed: int) -> list[SyntheticMatch]:
    rng = random.Random(seed)
    matches = []
    seen: set[str] = set()
    while len(matches) < count:
        match = make_match(rng, seed)
        if match.fingerprint in seen:
            continue
        seen.add(match.fingerprint)
        matches.append(match)
    return matches
