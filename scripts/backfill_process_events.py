"""Backfill complaint-process events for cases that were built before the
`case_process_event` table existed.

The extraction itself is the deterministic rule set already used during case
building (`lucera.process.extract_process_events`); this script only replays it
over stored episodes.  It is resumable: pairs that already have rows are skipped
unless `--rebuild` is given, so it can be run inside a short shell timeout.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lucera.db import LuceraDB  # noqa: E402
from lucera.paths import DATABASE_PATH  # noqa: E402
from lucera.process import rebuild_case_process_events  # noqa: E402


def backfill(db: LuceraDB, *, time_budget: float, rebuild: bool) -> dict[str, int]:
    pairs = db.conn.execute(
        """SELECT DISTINCT ce.case_id, ce.episode_id
             FROM case_evidence ce
            WHERE ce.episode_id IS NOT NULL
            ORDER BY ce.case_id, ce.episode_id"""
    ).fetchall()
    done = set()
    if not rebuild:
        done = {
            (row[0], row[1])
            for row in db.conn.execute(
                "SELECT DISTINCT case_id, episode_id FROM case_process_event"
            )
        }
    started = time.monotonic()
    processed = events = skipped = 0
    for row in pairs:
        key = (row["case_id"], row["episode_id"])
        if key in done:
            skipped += 1
            continue
        if time.monotonic() - started > time_budget:
            break
        events += rebuild_case_process_events(db, row["case_id"], row["episode_id"])
        processed += 1
        if processed % 50 == 0:
            db.commit()
    db.commit()
    return {
        "pairs_total": len(pairs),
        "pairs_processed": processed,
        "pairs_skipped": skipped,
        "pairs_remaining": len(pairs) - skipped - processed,
        "events_inserted": events,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--time-budget-seconds", type=float, default=35.0)
    parser.add_argument("--rebuild", action="store_true", help="기존 이벤트를 지우고 다시 추출")
    args = parser.parse_args()
    db = LuceraDB(args.db)
    try:
        for key, value in backfill(db, time_budget=args.time_budget_seconds, rebuild=args.rebuild).items():
            print(f"{key}: {value}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
