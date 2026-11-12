"""Storage reconciliation and audit tool for homework-submissions v2."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.database import connect
from app.storage import resolve_safe_path


def reconcile_storage(db_path: Path | str, storage_root: Path | str) -> dict[str, Any]:
    """Audit the SQLite database against the file storage directory.

    Returns a dict with status and lists of missing, mismatched, orphaned,
    and pending files.
    """
    db_path = Path(db_path).resolve()
    storage_root = Path(storage_root).resolve()

    missing_files: list[dict[str, Any]] = []
    checksum_mismatches: list[dict[str, Any]] = []
    size_mismatches: list[dict[str, Any]] = []
    orphaned_files: list[str] = []
    pending_files: list[str] = []

    if not db_path.is_file():
        return {
            "status": "discrepancies_found",
            "error": f"Database file not found: {db_path}",
            "missing_files": [],
            "checksum_mismatches": [],
            "size_mismatches": [],
            "orphaned_files": [],
            "pending_files": [],
        }

    known_stored_paths: set[str] = set()

    try:
        with connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, submission_id, original_filename, stored_path, size_bytes, sha256
                FROM submission_files
                """
            ).fetchall()
    except (sqlite3.Error, OSError) as err:
        return {
            "status": "discrepancies_found",
            "error": f"Failed to query database: {err}",
            "missing_files": [],
            "checksum_mismatches": [],
            "size_mismatches": [],
            "orphaned_files": [],
            "pending_files": [],
        }

    for row in rows:
        stored_path = row["stored_path"]
        known_stored_paths.add(stored_path)

        try:
            full_path = resolve_safe_path(storage_root, stored_path)
            if not full_path.is_file():
                missing_files.append({
                    "stored_path": stored_path,
                    "submission_id": str(row["submission_id"]),
                })
                continue
        except (ValueError, PermissionError):
            missing_files.append({
                "stored_path": stored_path,
                "submission_id": str(row["submission_id"]),
                "error": "path_traversal",
            })
            continue

        try:
            actual_size = full_path.stat().st_size
        except OSError:
            missing_files.append({
                "stored_path": stored_path,
                "submission_id": str(row["submission_id"]),
            })
            continue

        if actual_size != row["size_bytes"]:
            size_mismatches.append({
                "stored_path": stored_path,
                "submission_id": str(row["submission_id"]),
                "expected_size_bytes": row["size_bytes"],
                "actual_size_bytes": actual_size,
            })

        if row["sha256"]:
            hasher = hashlib.sha256()
            try:
                with full_path.open("rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        hasher.update(chunk)
                actual_sha = hasher.hexdigest()
                if actual_sha != row["sha256"]:
                    checksum_mismatches.append({
                        "stored_path": stored_path,
                        "submission_id": str(row["submission_id"]),
                        "expected_sha256": row["sha256"],
                        "actual_sha256": actual_sha,
                    })
            except OSError:
                missing_files.append({
                    "stored_path": stored_path,
                    "submission_id": str(row["submission_id"]),
                })

    # Scan filesystem for orphaned files
    if storage_root.is_dir():
        for root, dirs, files in os.walk(storage_root):
            # Prune hidden directories and .pending from recursion
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in sorted(files):
                if fname.startswith("."):
                    continue
                file_p = Path(root) / fname
                rel = file_p.relative_to(storage_root).as_posix()
                if rel not in known_stored_paths:
                    orphaned_files.append(rel)

        # Check .pending directory for leftover temporary files
        pending_dir = storage_root / ".pending"
        if pending_dir.is_dir():
            for p in sorted(pending_dir.iterdir()):
                if p.is_file():
                    pending_files.append(p.relative_to(storage_root).as_posix())

    has_discrepancies = bool(
        missing_files
        or checksum_mismatches
        or size_mismatches
        or orphaned_files
        or pending_files
    )

    return {
        "status": "discrepancies_found" if has_discrepancies else "ok",
        "missing_files": missing_files,
        "checksum_mismatches": checksum_mismatches,
        "size_mismatches": size_mismatches,
        "orphaned_files": orphaned_files,
        "pending_files": pending_files,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit SQLite submissions database against filesystem storage."
    )
    parser.add_argument(
        "--database",
        "-d",
        type=Path,
        required=True,
        help="Path to SQLite database",
    )
    parser.add_argument(
        "--storage",
        "-s",
        type=Path,
        required=True,
        help="Path to storage root directory",
    )
    args = parser.parse_args(argv)

    report = reconcile_storage(args.database, args.storage)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
