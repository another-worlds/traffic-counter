"""Flush all application data for a clean cutover to the per-hour model.

Removes every project/video/segment/line row and all stored artifacts
(parquets, frames, trajectories, exports, tus temp files), then re-runs the
schema init so the system restarts empty on the new segmented data model.
The schema and the `_migrations` ledger are preserved.

Runs inside the api container (it has both the DB and the /data volume):

    docker compose exec api python -m app.scripts.flush_data            # dry-run
    docker compose exec api python -m app.scripts.flush_data --yes      # execute
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from app.db import engine, init_db
from app.services.storage import get_storage

# Deleted in FK-dependency order; CASCADE on projects covers the rest, but we
# name them so the dry-run can report per-table counts.
TABLES = ["video_segments", "counting_lines", "tus_uploads", "videos", "projects"]

# Storage prefixes (under LOCAL_STORAGE_ROOT / the bucket) holding artifacts.
STORAGE_PREFIXES = ["projects", "exports", "tmp"]


def _counts() -> dict[str, int]:
    out: dict[str, int] = {}
    with engine.connect() as conn:
        for tbl in TABLES:
            out[tbl] = conn.execute(text(f"SELECT count(*) FROM {tbl}")).scalar() or 0
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Flush all application data (destructive).")
    ap.add_argument("--yes", action="store_true",
                    help="actually delete. Without this the script only reports.")
    args = ap.parse_args(argv)

    before = _counts()
    print("Current row counts:")
    for tbl, n in before.items():
        print(f"  {tbl:<16} {n}")
    print(f"Storage prefixes to clear: {STORAGE_PREFIXES}")

    if not args.yes:
        print("\nDRY RUN — nothing deleted. Re-run with --yes to flush.")
        return 0

    storage = get_storage()
    for prefix in STORAGE_PREFIXES:
        storage.delete_prefix(prefix)
        print(f"cleared storage prefix: {prefix}")

    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} CASCADE"))
    print("truncated tables: " + ", ".join(TABLES))

    # Ensure the schema is present/current on the new model.
    init_db()
    print("schema re-initialised (init_db)")

    after = _counts()
    print("\nRow counts after flush:")
    for tbl, n in after.items():
        print(f"  {tbl:<16} {n}")
    print("\nFlush complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
