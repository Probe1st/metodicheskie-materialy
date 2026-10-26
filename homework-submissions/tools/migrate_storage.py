#!/usr/bin/env python3
"""
Storage layout migration tool for homework submissions.

Migrates storage from legacy flat structure:
    storage/{clean_group}-{clean_subject}-{clean_work}/files...
to hierarchical structure:
    storage/{clean_group}/{clean_subject}/{clean_work}/files...
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

INVALID_CHARS_REGEX = re.compile(r'[*?"<>|:]')


def clean(value: str) -> str:
    """
    Clean path components using the same logic as storage.py:
    Replace / and \\ with fullwidth slash ／, remove invalid characters *?"<>|:,
    strip control characters and normalize whitespace.
    """
    value = unicodedata.normalize("NFC", value).replace("/", "／").replace("\\", "／")
    value = INVALID_CHARS_REGEX.sub("", value)
    value = "".join("" if unicodedata.category(char).startswith("C") else char for char in value)
    value = " ".join(value.split())
    if not value or value in {".", ".."}:
        raise ValueError(f"invalid path component: {value!r}")
    return value


def get_default_catalog_path() -> Path:
    candidates = [
        Path("homework-submissions/catalog.json"),
        Path("catalog.json"),
        Path(__file__).resolve().parent.parent / "catalog.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    for candidate in candidates:
        example = candidate.with_name("catalog.example.json")
        if example.is_file():
            return example
    return candidates[0]


def get_default_storage_dir() -> Path:
    candidates = [
        Path("homework-submissions/storage"),
        Path("storage"),
        Path(__file__).resolve().parent.parent / "storage",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def resolve_manifest_path(storage_dir: Path, manifest_arg: Path | None) -> Path:
    if manifest_arg is not None:
        return manifest_arg
    # Check default locations: storage-dir or tools dir
    tools_dir = Path(__file__).resolve().parent
    in_storage = storage_dir / "migration-storage-manifest.json"
    in_tools = tools_dir / "migration-storage-manifest.json"
    if in_storage.is_file():
        return in_storage
    if in_tools.is_file():
        return in_tools
    return in_storage


def load_catalog_entities(catalog_path: Path) -> tuple[list[str], list[str]]:
    """
    Load groups and subjects from catalog.json.
    Clean names and sort descending by length to ensure greedy prefix matching.
    """
    if not catalog_path.is_file():
        raise FileNotFoundError(f"Catalog file not found: {catalog_path}")
    with open(catalog_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_groups = [g["name"] for g in data.get("groups", []) if isinstance(g, dict) and "name" in g]
    raw_subjects = [s["name"] for s in data.get("subjects", []) if isinstance(s, dict) and "name" in s]

    cleaned_groups = [clean(g) for g in raw_groups]
    cleaned_subjects = [clean(s) for s in raw_subjects]

    # Deduplicate while preserving unique entries, then sort descending by length
    sorted_groups = sorted(dict.fromkeys(cleaned_groups), key=len, reverse=True)
    sorted_subjects = sorted(dict.fromkeys(cleaned_subjects), key=len, reverse=True)
    return sorted_groups, sorted_subjects


@dataclass(frozen=True)
class DirectoryMigration:
    source_dir: Path
    target_dir: Path
    group: str
    subject: str
    work: str


@dataclass(frozen=True)
class FileMove:
    source: Path
    target: Path


def parse_legacy_directory(
    dir_name: str,
    storage_dir: Path,
    cleaned_groups: list[str],
    cleaned_subjects: list[str],
) -> DirectoryMigration | None:
    """
    Check if dir_name matches {clean_group}-{clean_subject}-{clean_work}.
    Iterate cleaned groups: if dir_name starts with f"{group}-":
      Iterate cleaned subjects: if remainder starts with f"{subject}-":
        work_name = remainder[len(subject) + 1:]
        target = storage_dir / group / subject / work_name
    """
    for group in cleaned_groups:
        prefix = f"{group}-"
        if not dir_name.startswith(prefix):
            continue
        remainder = dir_name[len(prefix):]
        for subject in cleaned_subjects:
            subj_prefix = f"{subject}-"
            if not remainder.startswith(subj_prefix):
                continue
            work_name = remainder[len(subj_prefix):]
            if not work_name.strip():
                continue
            target = storage_dir / group / subject / work_name
            return DirectoryMigration(
                source_dir=storage_dir / dir_name,
                target_dir=target,
                group=group,
                subject=subject,
                work=work_name,
            )
    return None


def plan_migrations(
    storage_dir: Path,
    cleaned_groups: list[str],
    cleaned_subjects: list[str],
) -> tuple[list[DirectoryMigration], list[FileMove], list[str]]:
    """
    Scan storage_dir for legacy directories, calculate target paths, and check for conflicts.
    """
    migrations: list[DirectoryMigration] = []
    file_moves: list[FileMove] = []
    conflicts: list[str] = []

    if not storage_dir.exists() or not storage_dir.is_dir():
        return migrations, file_moves, [f"Storage directory does not exist or is not a directory: {storage_dir}"]

    entries = sorted(storage_dir.iterdir(), key=lambda p: p.name)
    for entry in entries:
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        migration = parse_legacy_directory(entry.name, storage_dir, cleaned_groups, cleaned_subjects)
        if migration is None:
            continue
        migrations.append(migration)

    # Check for directory collisions
    target_to_source: dict[Path, Path] = {}
    for m in migrations:
        if m.target_dir in target_to_source:
            conflicts.append(
                f"Directory collision: {m.source_dir.name} and {target_to_source[m.target_dir].name} "
                f"both map to {m.target_dir}"
            )
        target_to_source[m.target_dir] = m.source_dir

    # Check for file collisions
    target_files: dict[Path, Path] = {}
    for m in migrations:
        for root, _, files in os.walk(m.source_dir):
            root_path = Path(root)
            for fname in sorted(files):
                src_file = root_path / fname
                rel_path = src_file.relative_to(m.source_dir)
                tgt_file = m.target_dir / rel_path
                file_moves.append(FileMove(source=src_file, target=tgt_file))

                if tgt_file in target_files:
                    conflicts.append(
                        f"File target collision: {tgt_file} is targeted by both "
                        f"{target_files[tgt_file]} and {src_file}"
                    )
                elif tgt_file.exists():
                    conflicts.append(f"Target file already exists: {tgt_file} (source {src_file})")
                target_files[tgt_file] = src_file

    return migrations, file_moves, conflicts


def find_report_files(storage_dir: Path, custom_reports_dir: Path | None = None) -> list[Path]:
    """
    Find report-*.md files around storage directory and project root.
    """
    search_dirs: list[Path] = []
    if custom_reports_dir is not None:
        search_dirs.append(custom_reports_dir)
    search_dirs.extend([
        storage_dir.parent,
        storage_dir,
        Path("."),
        Path("homework-submissions"),
        Path(__file__).resolve().parent.parent,
    ])
    seen: set[Path] = set()
    reports: list[Path] = []
    for d in search_dirs:
        if not d.exists() or not d.is_dir():
            continue
        try:
            for p in sorted(d.glob("report-*.md")):
                resolved = p.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    reports.append(p)
        except OSError:
            pass
    return reports


def update_reports(
    report_paths: list[Path],
    directories: Sequence[dict],
    reverse: bool = False,
    dry_run: bool = False,
) -> list[dict]:
    """
    Update flat paths to hierarchical paths in report-*.md files.
    If reverse=True, restores hierarchical paths back to flat paths.
    """
    mappings: list[tuple[str, str]] = []
    for d in directories:
        legacy = d["legacy_name"]
        hierarchical = d["hierarchical_rel"]
        if reverse:
            mappings.append((hierarchical, legacy))
        else:
            mappings.append((legacy, hierarchical))

    # Sort mappings descending by search pattern length to prevent substring corruption
    mappings.sort(key=lambda m: len(m[0]), reverse=True)

    results: list[dict] = []
    for report_path in report_paths:
        if not report_path.is_file():
            continue
        try:
            content = report_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        count = 0
        for pattern, replacement in mappings:
            if pattern in content:
                count += content.count(pattern)
                content = content.replace(pattern, replacement)

        if count > 0:
            if not dry_run:
                report_path.write_text(content, encoding="utf-8")
            results.append({"path": str(report_path.resolve()), "replacements": count})

    return results


def cleanup_empty_dir_hierarchy(dir_path: Path, stop_dir: Path) -> None:
    """
    Remove dir_path and its parent directories if they are empty, up to stop_dir.
    """
    if not dir_path.exists():
        return
    for root, dirs, _ in os.walk(dir_path, topdown=False):
        for d in dirs:
            p = Path(root) / d
            try:
                p.rmdir()
            except OSError:
                pass
    try:
        dir_path.rmdir()
    except OSError:
        pass

    parent = dir_path.parent
    while parent.exists() and parent != stop_dir:
        try:
            if not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
            else:
                break
        except (OSError, ValueError):
            break


def execute_migration(
    storage_dir: Path,
    catalog_path: Path,
    manifest_path: Path,
    update_reports_flag: bool = False,
    custom_reports_dir: Path | None = None,
) -> dict:
    """
    Execute migration: move files, remove empty legacy directories, save manifest, update reports.
    """
    cleaned_groups, cleaned_subjects = load_catalog_entities(catalog_path)
    migrations, file_moves, conflicts = plan_migrations(storage_dir, cleaned_groups, cleaned_subjects)

    if conflicts:
        raise RuntimeError("Cannot execute migration due to conflicts:\n" + "\n".join(conflicts))

    moved_files: list[dict[str, str]] = []
    for fm in file_moves:
        fm.target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(fm.source), str(fm.target))
        moved_files.append({"source": str(fm.source.resolve()), "target": str(fm.target.resolve())})

    # Remove empty legacy directories
    for m in migrations:
        cleanup_empty_dir_hierarchy(m.source_dir, stop_dir=storage_dir)

    dir_records = [
        {
            "source": str(m.source_dir.resolve()),
            "target": str(m.target_dir.resolve()),
            "group": m.group,
            "subject": m.subject,
            "work": m.work,
            "legacy_name": m.source_dir.name,
            "hierarchical_rel": f"{m.group}/{m.subject}/{m.work}",
        }
        for m in migrations
    ]

    report_records: list[dict] = []
    if update_reports_flag:
        reports = find_report_files(storage_dir, custom_reports_dir)
        report_records = update_reports(reports, dir_records, reverse=False, dry_run=False)

    manifest_data = {
        "version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "storage_dir": str(storage_dir.resolve()),
        "catalog": str(catalog_path.resolve()),
        "directories": dir_records,
        "files": moved_files,
        "updated_reports": report_records,
    }

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, ensure_ascii=False, indent=2)

    return manifest_data


def rollback_migration(
    manifest_path: Path,
    storage_dir: Path | None = None,
    custom_reports_dir: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Rollback migration using manifest JSON. Restores files and removes empty hierarchical directories.
    """
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Manifest file not found: {manifest_path}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)

    if storage_dir is None:
        storage_dir = Path(manifest_data.get("storage_dir", "."))

    files = manifest_data.get("files", [])
    directories = manifest_data.get("directories", [])
    updated_reports_info = manifest_data.get("updated_reports", [])

    if dry_run:
        return {
            "mode": "dry-run",
            "files_to_restore": len(files),
            "directories_to_clean": len(directories),
            "reports_to_revert": len(updated_reports_info),
        }

    # Move files back in reverse order
    for item in reversed(files):
        source = Path(item["source"])
        target = Path(item["target"])
        if target.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(source))

    # Clean up created directories if empty
    for d in directories:
        target_dir = Path(d["target"])
        cleanup_empty_dir_hierarchy(target_dir, stop_dir=storage_dir)

    # Revert reports if any were updated
    reports_to_revert: list[Path] = []
    if updated_reports_info:
        for r in updated_reports_info:
            p = Path(r["path"])
            if p.is_file():
                reports_to_revert.append(p)
    else:
        reports_to_revert = find_report_files(storage_dir, custom_reports_dir)

    update_reports(reports_to_revert, directories, reverse=True, dry_run=False)

    # Remove manifest after successful rollback
    try:
        manifest_path.unlink()
    except OSError:
        pass

    return {
        "status": "rolled_back",
        "restored_files": len(files),
        "cleaned_directories": len(directories),
    }


def dry_run_migration(
    storage_dir: Path,
    catalog_path: Path,
    manifest_path: Path | None = None,
    update_reports_flag: bool = False,
    custom_reports_dir: Path | None = None,
) -> dict:
    """
    Simulate migration, verify no conflicts, and print planned operations.
    """
    cleaned_groups, cleaned_subjects = load_catalog_entities(catalog_path)
    migrations, file_moves, conflicts = plan_migrations(storage_dir, cleaned_groups, cleaned_subjects)

    report_plans: list[dict] = []
    if update_reports_flag:
        reports = find_report_files(storage_dir, custom_reports_dir)
        dir_records = [
            {
                "legacy_name": m.source_dir.name,
                "hierarchical_rel": f"{m.group}/{m.subject}/{m.work}",
            }
            for m in migrations
        ]
        report_plans = update_reports(reports, dir_records, reverse=False, dry_run=True)

    return {
        "mode": "dry-run",
        "directories_count": len(migrations),
        "files_count": len(file_moves),
        "conflicts": conflicts,
        "migrations": [
            {
                "from": str(m.source_dir.name),
                "to": f"{m.group}/{m.subject}/{m.work}",
            }
            for m in migrations
        ],
        "file_moves": [
            {
                "from": str(fm.source),
                "to": str(fm.target),
            }
            for fm in file_moves
        ],
        "report_updates": report_plans,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate homework submissions storage layout from flat to hierarchical."
    )
    parser.add_argument(
        "--storage-dir",
        type=Path,
        default=None,
        help="Path to storage directory (default: homework-submissions/storage)",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=None,
        help="Path to catalog.json (default: homework-submissions/catalog.json)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Path to manifest JSON (default: migration-storage-manifest.json in storage-dir or tools)",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=None,
        help="Custom directory to look for report-*.md files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Simulate migration without modifying files",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Actually perform the directory and file moves",
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        default=False,
        help="Restore files and directories using manifest",
    )
    parser.add_argument(
        "--update-reports",
        action="store_true",
        default=False,
        help="Update markdown paths in report-*.md files",
    )

    args = parser.parse_args(argv)

    storage_dir = (args.storage_dir or get_default_storage_dir()).resolve()
    catalog_path = (args.catalog or get_default_catalog_path()).resolve()
    manifest_path = resolve_manifest_path(storage_dir, args.manifest).resolve()

    # Determine mode
    if args.rollback:
        mode = "rollback"
    elif args.execute:
        mode = "execute"
    else:
        mode = "dry-run"

    print("=== Storage Migration Tool ===")
    print(f"Mode: {mode}")
    print(f"Storage dir: {storage_dir}")
    print(f"Catalog:     {catalog_path}")
    print(f"Manifest:    {manifest_path}")

    if mode == "dry-run":
        result = dry_run_migration(
            storage_dir=storage_dir,
            catalog_path=catalog_path,
            manifest_path=manifest_path,
            update_reports_flag=args.update_reports,
            custom_reports_dir=args.reports_dir,
        )
        print("\n[DRY RUN SUMMARY]")
        print(f"Directories to migrate: {result['directories_count']}")
        print(f"Files to move:          {result['files_count']}")
        if result["report_updates"]:
            print(f"Reports to update:      {len(result['report_updates'])}")
            for r in result["report_updates"]:
                print(f"  - {r['path']}: {r['replacements']} replacements planned")

        if result["conflicts"]:
            print(f"\n[ERROR] Conflicts detected ({len(result['conflicts'])}):")
            for c in result["conflicts"]:
                print(f"  ! {c}")
            return 1

        print("\nPlanned directory migrations:")
        for m in result["migrations"]:
            print(f"  {m['from']} -> {m['to']}")
        print("\nNo conflicts found. Dry run completed successfully.")
        return 0

    elif mode == "execute":
        print("\nExecuting storage migration...")
        try:
            result = execute_migration(
                storage_dir=storage_dir,
                catalog_path=catalog_path,
                manifest_path=manifest_path,
                update_reports_flag=args.update_reports,
                custom_reports_dir=args.reports_dir,
            )
            print(f"Successfully migrated {len(result['directories'])} directories and {len(result['files'])} files.")
            if result.get("updated_reports"):
                print(f"Updated {len(result['updated_reports'])} report files:")
                for r in result["updated_reports"]:
                    print(f"  - {r['path']} ({r['replacements']} replacements)")
            print(f"Manifest written to: {manifest_path}")
            return 0
        except (OSError, RuntimeError, ValueError) as e:
            print(f"\n[ERROR] Execution failed: {e}", file=sys.stderr)
            return 1

    elif mode == "rollback":
        print("\nRolling back storage migration...")
        try:
            result = rollback_migration(
                manifest_path=manifest_path,
                storage_dir=storage_dir,
                custom_reports_dir=args.reports_dir,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                print(f"[DRY RUN] Would restore {result['files_to_restore']} files and clean directories.")
            else:
                print(f"Successfully rolled back: {result['restored_files']} files restored.")
            return 0
        except (OSError, RuntimeError, ValueError) as e:
            print(f"\n[ERROR] Rollback failed: {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
