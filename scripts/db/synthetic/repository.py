"""Postgres persistence for synthetic outdraft matches."""

from __future__ import annotations

import json
from dataclasses import asdict

from scripts.db.postgres_common import ensure_schema, make_target_conninfo, require_psycopg
from scripts.db.synthetic.models import SyntheticMatch


INSERT_SYNTHETIC_MATCH_SQL = """
INSERT INTO synthetic_outdraft_matches (
    fingerprint,
    generator_version,
    seed,
    scenario,
    radiant_hero_slugs,
    dire_hero_slugs,
    winner_side,
    radiant_score,
    dire_score,
    outdraft_gap,
    confidence,
    raw
) VALUES (
    %(fingerprint)s,
    %(generator_version)s,
    %(seed)s,
    %(scenario)s,
    %(radiant_hero_slugs)s,
    %(dire_hero_slugs)s,
    %(winner_side)s,
    %(radiant_score)s,
    %(dire_score)s,
    %(outdraft_gap)s,
    %(confidence)s,
    %(raw)s::jsonb
)
ON CONFLICT (fingerprint) DO NOTHING
"""


def row_from_match(match: SyntheticMatch) -> dict:
    row = asdict(match)
    row["raw"] = json.dumps(match.raw, ensure_ascii=False)
    return row


def insert_matches(matches: list[SyntheticMatch], *, init_schema: bool, database: str | None) -> int:
    psycopg, _, _, _, _ = require_psycopg()
    inserted = 0
    with psycopg.connect(make_target_conninfo(database=database)) as conn:
        if init_schema:
            ensure_schema(conn)
        with conn.cursor() as cur:
            for match in matches:
                cur.execute(INSERT_SYNTHETIC_MATCH_SQL, row_from_match(match))
                inserted += cur.rowcount
        conn.commit()
    return inserted
