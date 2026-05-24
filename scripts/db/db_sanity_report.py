"""Check match-id sanity across Dotabuff and OpenDota tables.

Examples:
    python3 scripts/db/check_match_database_sanity.py
    python3 scripts/db/check_match_database_sanity.py --json
    python3 scripts/db/check_match_database_sanity.py --fail-on-warnings
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.db.postgres_common import load_parser_env, make_target_conninfo, require_psycopg


PRIMARY_MATCH_TABLES = ("dotabuff_matches", "opendota_matches")
DOTABUFF_INDEX_TABLES = ("dotabuff_team_matches", "dotabuff_hero_matches")
VALID_WINNER_SIDES = ("radiant", "dire")
VALID_PLAYER_SIDES = ("radiant", "dire")


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    message: str
    details: dict[str, Any] | None = None


def fetch_one(cur, query: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    cur.execute(query, params or {})
    row = cur.fetchone()
    if row is None:
        return {}
    return dict(row)


def fetch_all(cur, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cur.execute(query, params or {})
    return [dict(row) for row in cur.fetchall()]


def status_for_count(count: int, *, warn_only: bool = False) -> str:
    if count == 0:
        return "ok"
    return "warning" if warn_only else "fail"


def add_count_check(
    checks: list[Check],
    *,
    name: str,
    count: int,
    ok_message: str,
    bad_message: str,
    details: dict[str, Any] | None = None,
    warn_only: bool = False,
) -> None:
    checks.append(
        Check(
            name=name,
            status=status_for_count(count, warn_only=warn_only),
            message=ok_message if count == 0 else bad_message,
            details=details,
        )
    )


def primary_key_duplicate_checks(cur, sample_limit: int) -> list[Check]:
    checks: list[Check] = []
    for table in PRIMARY_MATCH_TABLES:
        rows = fetch_all(
            cur,
            f"""
            SELECT match_id, COUNT(*) AS rows
            FROM {table}
            GROUP BY match_id
            HAVING COUNT(*) > 1
            ORDER BY rows DESC, match_id DESC
            LIMIT %(sample_limit)s
            """,
            {"sample_limit": sample_limit},
        )
        add_count_check(
            checks,
            name=f"{table}.duplicate_match_id",
            count=len(rows),
            ok_message=f"{table}: no duplicate match_id rows.",
            bad_message=f"{table}: duplicate match_id rows found.",
            details={"sample": rows},
        )
    return checks


def dotabuff_index_duplicate_checks(cur, sample_limit: int) -> list[Check]:
    checks: list[Check] = []

    team_rows = fetch_all(
        cur,
        """
        SELECT team_id, match_id, COUNT(*) AS rows
        FROM dotabuff_team_matches
        GROUP BY team_id, match_id
        HAVING COUNT(*) > 1
        ORDER BY rows DESC, match_id DESC
        LIMIT %(sample_limit)s
        """,
        {"sample_limit": sample_limit},
    )
    add_count_check(
        checks,
        name="dotabuff_team_matches.duplicate_team_match_key",
        count=len(team_rows),
        ok_message="dotabuff_team_matches: no duplicate (team_id, match_id) rows.",
        bad_message="dotabuff_team_matches: duplicate (team_id, match_id) rows found.",
        details={"sample": team_rows},
    )

    hero_rows = fetch_all(
        cur,
        """
        SELECT hero_slug, match_id, COUNT(*) AS rows
        FROM dotabuff_hero_matches
        GROUP BY hero_slug, match_id
        HAVING COUNT(*) > 1
        ORDER BY rows DESC, match_id DESC
        LIMIT %(sample_limit)s
        """,
        {"sample_limit": sample_limit},
    )
    add_count_check(
        checks,
        name="dotabuff_hero_matches.duplicate_hero_match_key",
        count=len(hero_rows),
        ok_message="dotabuff_hero_matches: no duplicate (hero_slug, match_id) rows.",
        bad_message="dotabuff_hero_matches: duplicate (hero_slug, match_id) rows found.",
        details={"sample": hero_rows},
    )

    high_team_hits = fetch_all(
        cur,
        """
        SELECT match_id, COUNT(*) AS rows, COUNT(DISTINCT team_id) AS teams
        FROM dotabuff_team_matches
        GROUP BY match_id
        HAVING COUNT(*) > 2
        ORDER BY rows DESC, match_id DESC
        LIMIT %(sample_limit)s
        """,
        {"sample_limit": sample_limit},
    )
    add_count_check(
        checks,
        name="dotabuff_team_matches.match_id_seen_by_more_than_two_teams",
        count=len(high_team_hits),
        ok_message="dotabuff_team_matches: no match_id is attached to more than two teams.",
        bad_message="dotabuff_team_matches: some match_id values are attached to more than two teams.",
        details={"sample": high_team_hits},
        warn_only=True,
    )

    high_hero_hits = fetch_all(
        cur,
        """
        SELECT match_id, COUNT(*) AS rows, COUNT(DISTINCT hero_slug) AS heroes
        FROM dotabuff_hero_matches
        GROUP BY match_id
        HAVING COUNT(*) > 10
        ORDER BY rows DESC, match_id DESC
        LIMIT %(sample_limit)s
        """,
        {"sample_limit": sample_limit},
    )
    add_count_check(
        checks,
        name="dotabuff_hero_matches.match_id_seen_by_more_than_ten_heroes",
        count=len(high_hero_hits),
        ok_message="dotabuff_hero_matches: no match_id is attached to more than ten heroes.",
        bad_message="dotabuff_hero_matches: some match_id values are attached to more than ten heroes.",
        details={"sample": high_hero_hits},
        warn_only=True,
    )

    return checks


def count_summary(cur) -> dict[str, Any]:
    return {
        "dotabuff_matches": fetch_one(cur, "SELECT COUNT(*) AS rows, COUNT(DISTINCT match_id) AS distinct_match_ids FROM dotabuff_matches"),
        "opendota_matches": fetch_one(cur, "SELECT COUNT(*) AS rows, COUNT(DISTINCT match_id) AS distinct_match_ids FROM opendota_matches"),
        "dotabuff_team_matches": fetch_one(
            cur,
            """
            SELECT
                COUNT(*) AS rows,
                COUNT(DISTINCT match_id) AS distinct_match_ids,
                COUNT(DISTINCT team_id) AS distinct_team_ids
            FROM dotabuff_team_matches
            """,
        ),
        "dotabuff_hero_matches": fetch_one(
            cur,
            """
            SELECT
                COUNT(*) AS rows,
                COUNT(DISTINCT match_id) AS distinct_match_ids,
                COUNT(DISTINCT hero_slug) AS distinct_hero_slugs
            FROM dotabuff_hero_matches
            """,
        ),
    }


def overlap_summary(cur, sample_limit: int) -> dict[str, Any]:
    direct_overlap = fetch_one(
        cur,
        """
        SELECT COUNT(*) AS matches
        FROM dotabuff_matches dm
        JOIN opendota_matches om ON om.match_id = dm.match_id
        """,
    )
    direct_sample = fetch_all(
        cur,
        """
        SELECT dm.match_id, dm.played_at AS dotabuff_played_at, om.played_at AS opendota_played_at
        FROM dotabuff_matches dm
        JOIN opendota_matches om ON om.match_id = dm.match_id
        ORDER BY dm.match_id DESC
        LIMIT %(sample_limit)s
        """,
        {"sample_limit": sample_limit},
    )

    indexed_dotabuff_overlap = fetch_one(
        cur,
        """
        WITH indexed_dotabuff_ids AS (
            SELECT match_id FROM dotabuff_team_matches
            UNION
            SELECT match_id FROM dotabuff_hero_matches
        )
        SELECT COUNT(*) AS matches
        FROM indexed_dotabuff_ids ids
        JOIN opendota_matches om ON om.match_id = ids.match_id
        """,
    )

    return {
        "dotabuff_matches_x_opendota_matches": direct_overlap["matches"],
        "dotabuff_index_ids_x_opendota_matches": indexed_dotabuff_overlap["matches"],
        "sample": direct_sample,
    }


def unparsed_dotabuff_summary(cur, sample_limit: int) -> dict[str, Any]:
    totals = fetch_one(
        cur,
        """
        WITH indexed_dotabuff_ids AS (
            SELECT match_id, 'team' AS source FROM dotabuff_team_matches
            UNION
            SELECT match_id, 'hero' AS source FROM dotabuff_hero_matches
        ),
        missing_dotabuff_ids AS (
            SELECT ids.match_id
            FROM indexed_dotabuff_ids ids
            LEFT JOIN dotabuff_matches dm ON dm.match_id = ids.match_id
            WHERE dm.match_id IS NULL
            GROUP BY ids.match_id
        )
        SELECT
            COUNT(*) AS missing_dotabuff_ids,
            COUNT(*) FILTER (WHERE om.match_id IS NOT NULL) AS already_in_opendota,
            COUNT(*) FILTER (WHERE om.match_id IS NULL) AS missing_from_both_match_tables
        FROM missing_dotabuff_ids ids
        LEFT JOIN opendota_matches om ON om.match_id = ids.match_id
        """,
    )

    by_source = fetch_all(
        cur,
        """
        WITH indexed_dotabuff_ids AS (
            SELECT match_id, 'team' AS source FROM dotabuff_team_matches
            UNION
            SELECT match_id, 'hero' AS source FROM dotabuff_hero_matches
        ),
        missing_dotabuff_ids AS (
            SELECT ids.match_id, array_agg(DISTINCT ids.source ORDER BY ids.source) AS sources
            FROM indexed_dotabuff_ids ids
            LEFT JOIN dotabuff_matches dm ON dm.match_id = ids.match_id
            WHERE dm.match_id IS NULL
            GROUP BY ids.match_id
        )
        SELECT
            sources,
            COUNT(*) AS missing_dotabuff_ids,
            COUNT(*) FILTER (WHERE om.match_id IS NOT NULL) AS already_in_opendota,
            COUNT(*) FILTER (WHERE om.match_id IS NULL) AS missing_from_both_match_tables
        FROM missing_dotabuff_ids ids
        LEFT JOIN opendota_matches om ON om.match_id = ids.match_id
        GROUP BY sources
        ORDER BY missing_dotabuff_ids DESC
        """,
    )

    sample = fetch_all(
        cur,
        """
        WITH indexed_dotabuff_ids AS (
            SELECT match_id, 'team' AS source FROM dotabuff_team_matches
            UNION
            SELECT match_id, 'hero' AS source FROM dotabuff_hero_matches
        ),
        missing_dotabuff_ids AS (
            SELECT ids.match_id, array_agg(DISTINCT ids.source ORDER BY ids.source) AS sources
            FROM indexed_dotabuff_ids ids
            LEFT JOIN dotabuff_matches dm ON dm.match_id = ids.match_id
            WHERE dm.match_id IS NULL
            GROUP BY ids.match_id
        )
        SELECT ids.match_id, ids.sources, om.match_id IS NOT NULL AS already_in_opendota
        FROM missing_dotabuff_ids ids
        LEFT JOIN opendota_matches om ON om.match_id = ids.match_id
        ORDER BY ids.match_id DESC
        LIMIT %(sample_limit)s
        """,
        {"sample_limit": sample_limit},
    )

    return {"totals": totals, "by_source": by_source, "sample": sample}


def draft_sanity_checks(cur, sample_limit: int) -> list[Check]:
    checks: list[Check] = []

    invalid_dotabuff_winners = fetch_all(
        cur,
        """
        SELECT match_id, winner_side
        FROM dotabuff_matches
        WHERE winner_side IS NOT NULL AND winner_side <> ALL(%(valid_winner_sides)s)
        ORDER BY match_id DESC
        LIMIT %(sample_limit)s
        """,
        {"valid_winner_sides": list(VALID_WINNER_SIDES), "sample_limit": sample_limit},
    )
    add_count_check(
        checks,
        name="dotabuff_matches.invalid_winner_side",
        count=len(invalid_dotabuff_winners),
        ok_message="dotabuff_matches: winner_side values are valid.",
        bad_message="dotabuff_matches: invalid winner_side values found.",
        details={"sample": invalid_dotabuff_winners},
    )

    invalid_opendota_winners = fetch_all(
        cur,
        """
        SELECT match_id, winner_side
        FROM opendota_matches
        WHERE winner_side IS NOT NULL AND winner_side <> ALL(%(valid_winner_sides)s)
        ORDER BY match_id DESC
        LIMIT %(sample_limit)s
        """,
        {"valid_winner_sides": list(VALID_WINNER_SIDES), "sample_limit": sample_limit},
    )
    add_count_check(
        checks,
        name="opendota_matches.invalid_winner_side",
        count=len(invalid_opendota_winners),
        ok_message="opendota_matches: winner_side values are valid.",
        bad_message="opendota_matches: invalid winner_side values found.",
        details={"sample": invalid_opendota_winners},
    )

    invalid_player_sides = fetch_all(
        cur,
        """
        SELECT match_id, player_slot, side
        FROM dotabuff_match_players
        WHERE side <> ALL(%(valid_player_sides)s)
        ORDER BY match_id DESC, player_slot ASC
        LIMIT %(sample_limit)s
        """,
        {"valid_player_sides": list(VALID_PLAYER_SIDES), "sample_limit": sample_limit},
    )
    add_count_check(
        checks,
        name="dotabuff_match_players.invalid_side",
        count=len(invalid_player_sides),
        ok_message="dotabuff_match_players: side values are valid.",
        bad_message="dotabuff_match_players: invalid side values found.",
        details={"sample": invalid_player_sides},
    )

    duplicate_player_heroes = fetch_all(
        cur,
        """
        SELECT match_id, hero_slug, COUNT(*) AS rows
        FROM dotabuff_match_players
        WHERE hero_slug IS NOT NULL
        GROUP BY match_id, hero_slug
        HAVING COUNT(*) > 1
        ORDER BY rows DESC, match_id DESC
        LIMIT %(sample_limit)s
        """,
        {"sample_limit": sample_limit},
    )
    add_count_check(
        checks,
        name="dotabuff_match_players.duplicate_hero_in_match",
        count=len(duplicate_player_heroes),
        ok_message="dotabuff_match_players: no duplicate hero_slug inside parsed matches.",
        bad_message="dotabuff_match_players: duplicate hero_slug values inside parsed matches.",
        details={"sample": duplicate_player_heroes},
        warn_only=True,
    )

    incomplete_dotabuff_drafts = fetch_all(
        cur,
        """
        SELECT
            dm.match_id,
            COUNT(p.*) AS player_rows,
            COUNT(*) FILTER (WHERE p.side = 'radiant') AS radiant_rows,
            COUNT(*) FILTER (WHERE p.side = 'dire') AS dire_rows,
            COUNT(*) FILTER (WHERE p.hero_slug IS NULL) AS missing_hero_rows
        FROM dotabuff_matches dm
        LEFT JOIN dotabuff_match_players p ON p.match_id = dm.match_id
        GROUP BY dm.match_id
        HAVING
            COUNT(p.*) <> 10
            OR COUNT(*) FILTER (WHERE p.side = 'radiant') <> 5
            OR COUNT(*) FILTER (WHERE p.side = 'dire') <> 5
            OR COUNT(*) FILTER (WHERE p.hero_slug IS NULL) > 0
        ORDER BY dm.match_id DESC
        LIMIT %(sample_limit)s
        """,
        {"sample_limit": sample_limit},
    )
    add_count_check(
        checks,
        name="dotabuff_match_players.incomplete_parsed_draft",
        count=len(incomplete_dotabuff_drafts),
        ok_message="dotabuff parsed matches: all sampled checks have complete 5v5 hero rows.",
        bad_message="dotabuff parsed matches: incomplete 5v5 hero rows found.",
        details={"sample": incomplete_dotabuff_drafts},
        warn_only=True,
    )

    incomplete_opendota_drafts = fetch_all(
        cur,
        """
        SELECT
            match_id,
            jsonb_array_length(coalesce(players->'radiant', '[]'::jsonb)) AS radiant_rows,
            jsonb_array_length(coalesce(players->'dire', '[]'::jsonb)) AS dire_rows
        FROM opendota_matches
        WHERE
            jsonb_typeof(players) <> 'object'
            OR jsonb_array_length(coalesce(players->'radiant', '[]'::jsonb)) <> 5
            OR jsonb_array_length(coalesce(players->'dire', '[]'::jsonb)) <> 5
        ORDER BY match_id DESC
        LIMIT %(sample_limit)s
        """,
        {"sample_limit": sample_limit},
    )
    add_count_check(
        checks,
        name="opendota_matches.incomplete_draft",
        count=len(incomplete_opendota_drafts),
        ok_message="opendota_matches: all sampled checks have complete 5v5 player JSON.",
        bad_message="opendota_matches: incomplete 5v5 player JSON found.",
        details={"sample": incomplete_opendota_drafts},
        warn_only=True,
    )

    return checks


def run_checks(args: argparse.Namespace) -> dict[str, Any]:
    psycopg, _, _, _, dict_row = require_psycopg()
    conninfo = make_target_conninfo(database=args.database)
    with psycopg.connect(conninfo, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            checks = []
            checks.extend(primary_key_duplicate_checks(cur, args.sample_limit))
            checks.extend(dotabuff_index_duplicate_checks(cur, args.sample_limit))
            checks.extend(draft_sanity_checks(cur, args.sample_limit))

            return {
                "counts": count_summary(cur),
                "overlap": overlap_summary(cur, args.sample_limit),
                "unparsed_dotabuff": unparsed_dotabuff_summary(cur, args.sample_limit),
                "checks": [check.__dict__ for check in checks],
            }


def print_table(title: str, rows: list[dict[str, Any]]) -> None:
    print(title)
    if not rows:
        print("  none")
        return
    for row in rows:
        print("  " + ", ".join(f"{key}={value}" for key, value in row.items()))


def print_human_report(report: dict[str, Any]) -> None:
    print("Database sanity report")
    print("======================")

    print("\nCounts")
    for table, values in report["counts"].items():
        print("  " + table + ": " + ", ".join(f"{key}={value}" for key, value in values.items()))

    overlap = report["overlap"]
    print("\nDotabuff/OpenDota overlap")
    print(f"  dotabuff_matches x opendota_matches: {overlap['dotabuff_matches_x_opendota_matches']}")
    print(f"  dotabuff index ids x opendota_matches: {overlap['dotabuff_index_ids_x_opendota_matches']}")
    print_table("  sample direct overlaps", overlap["sample"])

    unparsed = report["unparsed_dotabuff"]
    print("\nUnparsed Dotabuff ids")
    print("  " + ", ".join(f"{key}={value}" for key, value in unparsed["totals"].items()))
    print_table("  by source", unparsed["by_source"])
    print_table("  sample", unparsed["sample"])

    print("\nChecks")
    for check in report["checks"]:
        print(f"  [{check['status'].upper()}] {check['name']}: {check['message']}")
        sample = (check.get("details") or {}).get("sample")
        if sample:
            print_table("    sample", sample)


def has_failures(report: dict[str, Any], *, fail_on_warnings: bool) -> bool:
    bad_statuses = {"fail", "warning"} if fail_on_warnings else {"fail"}
    return any(check["status"] in bad_statuses for check in report["checks"])


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check Dotabuff/OpenDota match-id sanity in Postgres.")
    parser.add_argument("--env-file", default=".env", help="Load Postgres settings from this env file.")
    parser.add_argument("--database", help="Target database name. Defaults to POSTGRES_DB or DATABASE_URL dbname.")
    parser.add_argument("--sample-limit", type=int, default=10, help="Rows to show for each sample section.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of a text report.")
    parser.add_argument("--fail-on-warnings", action="store_true", help="Exit non-zero for warnings as well as failures.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.sample_limit < 0:
        raise SystemExit("--sample-limit must be non-negative")

    load_parser_env(args.env_file)
    report = run_checks(args)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print_human_report(report)

    return 1 if has_failures(report, fail_on_warnings=args.fail_on_warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
