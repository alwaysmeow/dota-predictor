#!/usr/bin/env python3
"""Generate synthetic 5v5 Dota draft rows with an obvious outdraft label.

Usage:
    python3 scripts/db/synthetic/generate_synthetic_outdraft_matches.py --count 1000 --init
    python3 scripts/db/synthetic/generate_synthetic_outdraft_matches.py --count 10 --seed 42 --dry-run --pretty
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import asdict
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[3]))

from scripts.db.postgres_common import load_parser_env
from scripts.db.synthetic.generator import generate_matches
from scripts.db.synthetic.repository import insert_matches


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate synthetic obvious-outdraft match drafts into Postgres.")
    parser.add_argument("--count", type=positive_int, default=100, help="Number of synthetic matches to generate.")
    parser.add_argument("--seed", type=int, help="Random seed. Defaults to a generated 32-bit seed.")
    parser.add_argument("--env-file", default=".env", help="Load Postgres settings from this env file.")
    parser.add_argument("--database", help="Target database name. Defaults to POSTGRES_DB or DATABASE_URL dbname.")
    parser.add_argument("--init", action="store_true", help="Apply table schema before inserting.")
    parser.add_argument("--dry-run", action="store_true", help="Print generated rows instead of inserting them.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print dry-run JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(2**31)
    matches = generate_matches(args.count, seed)

    if args.dry_run:
        payload = [asdict(match) for match in matches]
        indent = 2 if args.pretty else None
        print(json.dumps(payload, ensure_ascii=False, indent=indent))
        return 0

    load_parser_env(args.env_file)
    inserted = insert_matches(matches, init_schema=args.init, database=args.database)
    print(f"generated synthetic matches: {len(matches)}")
    print(f"inserted synthetic matches: {inserted}")
    print(f"seed: {seed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
