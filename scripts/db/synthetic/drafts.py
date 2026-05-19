"""Build standalone good and bad synthetic drafts."""

from __future__ import annotations

import random

from scripts.db.synthetic.heroes import BAD_DRAFT_HERO_POOLS, GOOD_DRAFT_COUNTER_POOLS, TEAM_SIZE
from scripts.db.synthetic.models import Draft, DraftPair


BadDraftKind = str

BAD_DRAFT_KINDS: tuple[BadDraftKind, ...] = tuple(BAD_DRAFT_HERO_POOLS)


def sample_unique(rng: random.Random, heroes: tuple[str, ...], count: int, used: set[str] | None = None) -> list[str]:
    if used is None:
        used = set()
    candidates = [hero for hero in heroes if hero not in used]
    if len(candidates) < count:
        raise ValueError(f"Need {count} unique heroes, got {len(candidates)}")
    selected = rng.sample(candidates, count)
    used.update(selected)
    return selected


def make_bad_draft(rng: random.Random, used: set[str] | None = None, kind: BadDraftKind | None = None) -> Draft:
    kind = kind or rng.choice(BAD_DRAFT_KINDS)
    try:
        hero_pool = BAD_DRAFT_HERO_POOLS[kind]
    except KeyError as exc:
        raise ValueError(f"Unknown bad draft kind: {kind!r}") from exc

    heroes = sample_unique(rng, hero_pool, TEAM_SIZE, used)
    return Draft(
        hero_slugs=heroes,
        kind=f"five_{kind}",
        properties={
            "bad_property": kind,
            kind: heroes,
        },
    )


def make_good_draft(rng: random.Random, bad_draft: Draft, used: set[str] | None = None) -> Draft:
    bad_property = bad_draft.properties["bad_property"]
    try:
        position_pools = GOOD_DRAFT_COUNTER_POOLS[bad_property]
    except KeyError as exc:
        raise ValueError(f"No good-draft counter pools for bad property: {bad_property!r}") from exc

    selected_by_position = {
        position: sample_unique(rng, position_pools[position], 1, used)[0]
        for position in range(1, TEAM_SIZE + 1)
    }
    return Draft(
        hero_slugs=[selected_by_position[position] for position in range(1, TEAM_SIZE + 1)],
        kind=f"counter_{bad_property}",
        properties={
            "counters_bad_property": bad_property,
            "selected_by_position": selected_by_position,
            "candidate_pools_by_position": position_pools,
        },
    )


def make_draft_pair(rng: random.Random) -> DraftPair:
    used: set[str] = set()
    bad_draft = make_bad_draft(rng, used)
    good_draft = make_good_draft(rng, bad_draft, used)
    return DraftPair(
        scenario=f"{good_draft.kind}_vs_{bad_draft.kind}",
        good_draft=good_draft,
        bad_draft=bad_draft,
    )
