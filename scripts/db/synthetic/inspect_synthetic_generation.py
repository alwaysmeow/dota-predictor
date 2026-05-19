#!/usr/bin/env python3
"""Inspect synthetic draft generation without writing to Postgres.

Usage:
    python3 scripts/db/synthetic/inspect_synthetic_generation.py
    python3 scripts/db/synthetic/inspect_synthetic_generation.py --samples 1000 --seed 42
"""

from __future__ import annotations

import argparse
import itertools
import random
import sys
from dataclasses import asdict
from math import comb
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[3]))

from scripts.db.synthetic.drafts import make_draft_pair
from scripts.db.synthetic.generator import generate_matches
from scripts.db.synthetic.heroes import BAD_DRAFT_HERO_POOLS, GOOD_DRAFT_COUNTER_POOLS, TEAM_SIZE


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def validate_pools() -> list[str]:
    errors = []
    for bad_property, bad_pool in BAD_DRAFT_HERO_POOLS.items():
        if len(set(bad_pool)) != len(bad_pool):
            errors.append(f"{bad_property}: bad hero pool has duplicates")
        if len(bad_pool) < TEAM_SIZE:
            errors.append(f"{bad_property}: bad hero pool has fewer than {TEAM_SIZE} heroes")
        if any("_" in hero for hero in bad_pool):
            errors.append(f"{bad_property}: bad hero pool has underscore slugs")

        position_pools = GOOD_DRAFT_COUNTER_POOLS.get(bad_property)
        if not position_pools:
            errors.append(f"{bad_property}: missing good-draft counter pools")
            continue

        missing_positions = sorted(set(range(1, TEAM_SIZE + 1)) - set(position_pools))
        if missing_positions:
            errors.append(f"{bad_property}: missing position pools {missing_positions}")

        position_seen: dict[str, int] = {}
        for position, pool in position_pools.items():
            if len(set(pool)) != len(pool):
                errors.append(f"{bad_property}: position {position} counter pool has duplicates")
            if not pool:
                errors.append(f"{bad_property}: position {position} counter pool is empty")
            if any("_" in hero for hero in pool):
                errors.append(f"{bad_property}: position {position} counter pool has underscore slugs")
            for hero in pool:
                if hero in position_seen:
                    errors.append(
                        f"{bad_property}: hero {hero!r} is in both position "
                        f"{position_seen[hero]} and position {position} counter pools"
                    )
                position_seen[hero] = position

    extra_counter_keys = set(GOOD_DRAFT_COUNTER_POOLS) - set(BAD_DRAFT_HERO_POOLS)
    for bad_property in sorted(extra_counter_keys):
        errors.append(f"{bad_property}: counter pool has no matching bad hero pool")
    return errors


def count_good_drafts_for_bad_team(bad_property: str, bad_team: tuple[str, ...]) -> int:
    position_pools = GOOD_DRAFT_COUNTER_POOLS[bad_property]
    bad_heroes = set(bad_team)
    count = 1
    for position in range(1, TEAM_SIZE + 1):
        count *= sum(1 for hero in position_pools[position] if hero not in bad_heroes)
    return count


def count_combinations_by_property() -> dict[str, dict[str, int]]:
    counts = {}
    for bad_property, bad_pool in BAD_DRAFT_HERO_POOLS.items():
        bad_drafts = comb(len(bad_pool), TEAM_SIZE)
        draft_pairs = 0
        for bad_team in itertools.combinations(bad_pool, TEAM_SIZE):
            draft_pairs += count_good_drafts_for_bad_team(bad_property, bad_team)
        counts[bad_property] = {
            "bad_drafts": bad_drafts,
            "draft_pairs": draft_pairs,
            "matches_with_side": draft_pairs * 2,
        }
    return counts


def validate_generated_samples(count: int, seed: int) -> list[str]:
    errors = []
    matches = generate_matches(count, seed)
    fingerprints = set()
    for index, match in enumerate(matches, start=1):
        all_heroes = match.radiant_hero_slugs + match.dire_hero_slugs
        if len(match.radiant_hero_slugs) != TEAM_SIZE:
            errors.append(f"sample {index}: radiant does not have {TEAM_SIZE} heroes")
        if len(match.dire_hero_slugs) != TEAM_SIZE:
            errors.append(f"sample {index}: dire does not have {TEAM_SIZE} heroes")
        if len(set(all_heroes)) != TEAM_SIZE * 2:
            errors.append(f"sample {index}: duplicate heroes across draft")
        if any("_" in hero for hero in all_heroes):
            errors.append(f"sample {index}: underscore hero slug")
        if match.fingerprint in fingerprints:
            errors.append(f"sample {index}: duplicate fingerprint {match.fingerprint}")
        fingerprints.add(match.fingerprint)

        raw = match.raw
        good_heroes = raw["good_draft"]["hero_slugs"]
        if match.winner_side == "radiant" and match.radiant_hero_slugs != good_heroes:
            errors.append(f"sample {index}: radiant winner is not good draft")
        if match.winner_side == "dire" and match.dire_hero_slugs != good_heroes:
            errors.append(f"sample {index}: dire winner is not good draft")
    return errors


def print_sample(seed: int) -> None:
    draft_pair = make_draft_pair(random.Random(seed))
    print("Example draft pair:")
    print(f"  scenario: {draft_pair.scenario}")
    print(f"  bad:  {draft_pair.bad_draft.kind} -> {', '.join(draft_pair.bad_draft.hero_slugs)}")
    print(f"  good: {draft_pair.good_draft.kind} -> {', '.join(draft_pair.good_draft.hero_slugs)}")
    print(f"  good positions: {asdict(draft_pair.good_draft)['properties']['selected_by_position']}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect synthetic draft generation without DB writes.")
    parser.add_argument("--samples", type=positive_int, default=200, help="Generated samples to validate.")
    parser.add_argument("--seed", type=int, default=42, help="Seed for sample validation.")
    parser.add_argument("--no-sample", action="store_true", help="Do not print an example draft pair.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    errors = validate_pools()
    errors.extend(validate_generated_samples(args.samples, args.seed))

    counts = count_combinations_by_property()
    total_bad_drafts = sum(item["bad_drafts"] for item in counts.values())
    total_draft_pairs = sum(item["draft_pairs"] for item in counts.values())
    total_matches_with_side = sum(item["matches_with_side"] for item in counts.values())

    print("Combination counts:")
    for bad_property, item in counts.items():
        print(
            f"  {bad_property}: bad_drafts={item['bad_drafts']:,}, "
            f"draft_pairs={item['draft_pairs']:,}, matches_with_side={item['matches_with_side']:,}"
        )
    print(
        f"  total: bad_drafts={total_bad_drafts:,}, "
        f"draft_pairs={total_draft_pairs:,}, matches_with_side={total_matches_with_side:,}"
    )

    print(f"\nValidated generated samples: {args.samples:,}")
    if args.no_sample is False:
        print()
        print_sample(args.seed)

    if errors:
        print("\nErrors:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
