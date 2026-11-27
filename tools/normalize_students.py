"""Offline CLI tool for auditing, normalizing, and deduplicating student records in SQLite v2."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.student_cleanup import analyze_students_db, apply_cleanup_plan
from tools.migrate_v2 import check_running_server, file_sha256


def _backup_database(db_path: Path, backup_dir: Path) -> tuple[Path, Path]:
    """Create a verified SQLite online backup before mutating the production database."""
    run_id = f"students-cleanup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4()}"
    run_dir = backup_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        run_dir.chmod(0o700)
    except OSError:
        pass

    backup_db = run_dir / db_path.name
    src_uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    src_conn = sqlite3.connect(src_uri, uri=True)
    dst_conn = sqlite3.connect(backup_db)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    try:
        backup_db.chmod(0o600)
    except OSError:
        pass

    with sqlite3.connect(backup_db) as check_conn:
        res = check_conn.execute("PRAGMA integrity_check;").fetchall()
        if not res or res[0][0] != "ok":
            raise RuntimeError(f"Integrity check failed on backup SQLite DB: {res}")

    manifest = {
        "format_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_database": str(db_path),
        "backup_database": str(backup_db),
        "sha256": file_sha256(backup_db),
        "size_bytes": backup_db.stat().st_size,
        "backup_complete": True,
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        manifest_path.chmod(0o600)
    except OSError:
        pass
    return run_dir, backup_db


def _print_analysis_summary(plan: dict[str, Any]) -> None:
    s = plan["summary"]
    sys.stderr.write(
        "Student DB cleanup analysis:\n"
        f"  Groups: {s['groups_count']}\n"
        f"  Total students: {s['total_students']}\n"
        f"  Exact duplicate groups (case/patronymic/tails): {s['exact_merge_groups_count']} "
        f"(sources to merge: {s['exact_merge_sources_count']})\n"
        f"  Single-record renames: {s['renames_only_count']}\n"
        f"  Fuzzy duplicate candidates (review): {s['fuzzy_candidate_groups_count']}\n"
        f"  Paired student entries: {s['paired_students_count']}\n"
        f"  Records with format warnings: {s['warnings_count']}\n"
    )


def cmd_analyze(args: argparse.Namespace) -> int:
    db_path = Path(args.database).resolve()
    if not db_path.is_file():
        sys.stderr.write(f"Error: Database not found: {db_path}\n")
        return 1

    plan = analyze_students_db(db_path, group_id=args.group_id)
    _print_analysis_summary(plan)
    output_json = json.dumps(plan, indent=2, ensure_ascii=False)

    if args.output:
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output_json, encoding="utf-8")
        sys.stderr.write(f"Cleanup plan written to {out_path}\n")
    else:
        sys.stdout.write(output_json + "\n")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    db_path = Path(args.database).resolve()
    if not db_path.is_file():
        sys.stderr.write(f"Error: Database not found: {db_path}\n")
        return 1

    if not args.auto and not args.plan:
        sys.stderr.write("Error: Provide either --plan <file> or --auto\n")
        return 1

    storage_path = Path(args.storage).resolve() if args.storage else None

    if args.plan:
        plan_path = Path(args.plan).resolve()
        if not plan_path.is_file():
            sys.stderr.write(f"Error: Plan file not found: {plan_path}\n")
            return 1
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    else:
        plan = analyze_students_db(db_path, group_id=args.group_id)

    if not args.dry_run and not args.allow_online:
        candidates = [Path("."), db_path.parent, db_path.parent.parent]
        if storage_path:
            candidates.append(storage_path.parent)
        check_running_server(candidates)

    backup_run_dir: Path | None = None
    if not args.dry_run:
        backup_root = Path(args.backup_dir).resolve() if args.backup_dir else (db_path.parent / "backups")
        backup_run_dir, _ = _backup_database(db_path, backup_root)

    report = apply_cleanup_plan(
        db_path,
        storage_path,
        plan,
        apply_exact=not args.no_exact,
        apply_renames=not args.no_renames,
        apply_fuzzy=args.apply_fuzzy,
        split_paired=args.split_paired,
        dry_run=args.dry_run,
    )
    if backup_run_dir is not None:
        report["backup_dir"] = str(backup_run_dir)
        (backup_run_dir / "cleanup-report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.normalize_students",
        description="Audit, normalize, and merge duplicate student records safely.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_analyze = subparsers.add_parser("analyze", help="Analyze student records without modifying DB.")
    p_analyze.add_argument("--database", "-d", required=True, type=Path, help="Path to SQLite v2 database")
    p_analyze.add_argument("--group-id", default=None, help="Optional group UUID filter")
    p_analyze.add_argument("--output", "-o", type=Path, default=None, help="Path to save JSON cleanup plan")
    p_analyze.set_defaults(func=cmd_analyze)

    p_apply = subparsers.add_parser("apply", help="Apply normalization and merges with backup-first safety.")
    p_apply.add_argument("--database", "-d", required=True, type=Path, help="Path to SQLite v2 database")
    p_apply.add_argument("--storage", "-s", type=Path, default=None, help="Path to storage root directory")
    p_apply.add_argument("--backup-dir", type=Path, default=None, help="Backup root directory")
    p_apply.add_argument("--plan", "-p", type=Path, default=None, help="Path to edited JSON plan from analyze")
    p_apply.add_argument("--auto", action="store_true", default=False, help="Auto-generate plan for exact merges and renames")
    p_apply.add_argument("--group-id", default=None, help="Optional group UUID filter when using --auto")
    p_apply.add_argument("--split-paired", action="store_true", default=False, help="Split paired records onto individual students")
    p_apply.add_argument("--apply-fuzzy", action="store_true", default=False, help="Also merge detected fuzzy typo groups")
    p_apply.add_argument("--no-exact", action="store_true", default=False, help="Skip exact merge groups")
    p_apply.add_argument("--no-renames", action="store_true", default=False, help="Skip single-record renames")
    p_apply.add_argument("--dry-run", action="store_true", default=False, help="Simulate changes on a temp copy without mutating DB")
    p_apply.add_argument("--allow-online", action="store_true", default=False, help="Allow running even if .gunicorn.pid exists")
    p_apply.set_defaults(func=cmd_apply)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
