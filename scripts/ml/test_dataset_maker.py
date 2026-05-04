#!/usr/bin/env python3
"""Smoke test for DatasetMaker.

Usage:
    python3 scripts/ml/test_dataset_maker.py 8786827560
    python3 scripts/ml/test_dataset_maker.py 8786827560 --database dota_predictor
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.ml.dataset import DatasetMaker


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run DatasetMaker methods against one match.")
    parser.add_argument("match_id", type=int, help="Dotabuff match id stored in Postgres.")
    parser.add_argument("--database", help="Target database name. Defaults to POSTGRES_DB or DATABASE_URL dbname.")
    parser.add_argument("--env-file", default=".env", help="Load Postgres settings from this env file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    with DatasetMaker(database=args.database, env_file=args.env_file) as dataset:
        draft = dataset.fetch_match_draft(args.match_id)
        normalized = dataset.normalize_match_draft(draft)

    print(f"match_id: {draft.match_id}")
    print(f"winner_side: {draft.winner_side}")
    print(f"radiant_heroes: {', '.join(draft.radiant_heroes)}")
    print(f"dire_heroes: {', '.join(draft.dire_heroes)}")
    print(f"normalized_winner_side: {normalized.winner_side}")
    print(f"radiant_hero_ids: {normalized.radiant_hero_ids}")
    print(f"dire_hero_ids: {normalized.dire_hero_ids}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
