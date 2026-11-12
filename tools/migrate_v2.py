"""Offline migration CLI tool for homework-submissions SQLite v2 transition."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import unicodedata
import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.database import (
    DatabaseError,
    create_schema,
    initialize,
    normalize_name_key,
    validate,
)
from tools.legacy_catalog import (
    LegacyCatalogData,
    parse_legacy_catalog,
)


def file_sha256(path: Path) -> str:
    """Compute sha256 checksum of a file."""
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def clean_path_component(val: str) -> str:
    """Normalize and clean a directory component like legacy storage."""
    val = unicodedata.normalize("NFC", val).replace("/", "／").replace("\\", "／")
    val = "".join("" if unicodedata.category(c).startswith("C") else c for c in val)
    return " ".join(val.split())


def parse_submission_filename(filename: str) -> tuple[bool, str | None, int | None, str]:
    """Parse submission filename into (is_reviewed, student_name, attempt_number, stripped_filename).

    Leading '+' marks reviewed status. Parsed from tail: <name> <N><extensions> with N >= 1.
    If the filename does not match this format, returns (is_reviewed, None, None, stripped_name).
    """
    is_reviewed = filename.startswith("+")
    stripped_name = filename[1:] if is_reviewed else filename

    m = re.match(r"^(?P<name>.+)\s+(?P<attempt>\d+)(?P<ext>(?:\.[^\s.]+)+)?$", stripped_name)
    if not m:
        return is_reviewed, None, None, stripped_name

    attempt = int(m.group("attempt"))
    if attempt < 1:
        return is_reviewed, None, None, stripped_name

    name_part = m.group("name").strip()
    if not name_part:
        return is_reviewed, None, None, stripped_name

    return is_reviewed, name_part, attempt, stripped_name

def check_running_server(candidate_dirs: Sequence[Path]) -> None:
    """Check for active .gunicorn.pid file in candidate directories."""
    for d in candidate_dirs:
        pid_file = d / ".gunicorn.pid"
        if pid_file.is_file():
            try:
                pid_text = pid_file.read_text(encoding="utf-8").strip()
                if not pid_text.isdigit():
                    continue
                pid = int(pid_text)
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    continue
                except PermissionError as err:
                    raise RuntimeError(
                        f"Server process is running (PID {pid} from {pid_file}). Stop server before migration."
                    ) from err
                raise RuntimeError(
                    f"Server process is running (PID {pid} from {pid_file}). Stop server before migration."
                )
            except OSError:
                pass

def execute_backup(
    backup_root: Path,
    legacy_db_path: Path | None,
    catalog_path: Path,
    storage_path: Path,
    allow_missing_legacy_db: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """Execute the mandatory backup step before any mutations.

    Creates backup directory, copies SQLite DB, catalog.json, and storage tree.
    Validates all hashes and runs integrity check on backup DB.
    """
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4()}"
    run_dir = backup_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        run_dir.chmod(0o700)
    except OSError:
        pass

    legacy_db_exists = legacy_db_path.is_file() if legacy_db_path else False
    if not legacy_db_exists and not allow_missing_legacy_db:
        raise FileNotFoundError(f"Legacy database not found: {legacy_db_path}")

    # 1. SQLite online backup
    legacy_db_hash = None
    if legacy_db_exists and legacy_db_path:
        backup_db_path = run_dir / "access.sqlite3"
        src_uri = f"file:{legacy_db_path.resolve()}?mode=ro"
        src_conn = sqlite3.connect(src_uri, uri=True)
        dst_conn = sqlite3.connect(backup_db_path)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()

        try:
            backup_db_path.chmod(0o600)
        except OSError:
            pass

        with sqlite3.connect(backup_db_path) as check_conn:
            res = check_conn.execute("PRAGMA integrity_check;").fetchall()
            if not res or res[0][0] != "ok":
                raise RuntimeError(f"Integrity check failed on backup SQLite DB: {res}")
        legacy_db_hash = file_sha256(backup_db_path)

    # 2. Copy catalog.json
    backup_catalog_path = run_dir / "catalog.json"
    shutil.copyfile(catalog_path, backup_catalog_path)
    try:
        backup_catalog_path.chmod(0o600)
    except OSError:
        pass
    catalog_hash = file_sha256(backup_catalog_path)
    if catalog_hash != file_sha256(catalog_path):
        raise RuntimeError("Catalog hash verification failed after copy")

    # 3. Check for symlinks or special files in storage before copying
    for root, dirs, files in os.walk(storage_path, followlinks=False):
        for entry_name in dirs + files:
            p = Path(root) / entry_name
            if p.is_symlink() or not (p.is_file() or p.is_dir()):
                raise ValueError(f"Symlinks or special files not allowed in storage: {p}")

    # 4. Copy storage/ completely
    backup_storage_path = run_dir / "storage"
    shutil.copytree(storage_path, backup_storage_path, symlinks=False)

    # Verify copied storage files
    file_hashes: dict[str, dict[str, Any]] = {}
    for root, _, files in os.walk(storage_path):
        for fname in files:
            src_file = Path(root) / fname
            rel_path = src_file.relative_to(storage_path).as_posix()
            dst_file = backup_storage_path / rel_path
            if not dst_file.is_file():
                raise FileNotFoundError(f"Backup storage file missing: {dst_file}")
            src_size = src_file.stat().st_size
            dst_size = dst_file.stat().st_size
            if src_size != dst_size:
                raise ValueError(f"File size mismatch for {rel_path}: {src_size} vs {dst_size}")
            src_sha = file_sha256(src_file)
            dst_sha = file_sha256(dst_file)
            if src_sha != dst_sha:
                raise ValueError(f"Checksum mismatch for {rel_path}")
            file_hashes[rel_path] = {"size_bytes": src_size, "sha256": src_sha}

    manifest_data = {
        "format_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_paths": {
            "legacy_db": str(legacy_db_path) if legacy_db_exists else None,
            "catalog": str(catalog_path),
            "storage": str(storage_path),
        },
        "legacy_db_missing": not legacy_db_exists,
        "hashes": {
            "catalog": catalog_hash,
            "legacy_db": legacy_db_hash,
            "files": file_hashes,
        },
        "file_counts": {
            "storage_files": len(file_hashes),
        },
        "backup_complete": True,
    }

    temp_manifest = run_dir / f"manifest.json.tmp.{uuid.uuid4()}"
    with temp_manifest.open("w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    try:
        temp_manifest.chmod(0o600)
    except OSError:
        pass
    os.replace(temp_manifest, run_dir / "manifest.json")

    return run_dir, manifest_data


def resolve_file_location(
    rel_path: str,
    catalog: LegacyCatalogData,
) -> tuple[UUID | None, UUID | None]:
    """Resolve storage relative path to (group_id, topic_id)."""
    parts = rel_path.split("/")
    if len(parts) <= 1:
        return None, None

    dir_parts = parts[:-1]

    # 1. Check if UUID-based layout: storage / {group_uuid} / {topic_uuid} / file
    if len(dir_parts) == 2:
        try:
            g_u = UUID(dir_parts[0])
            t_u = UUID(dir_parts[1])
            if any(g.id == g_u for g in catalog.groups) and any(t.id == t_u for t in catalog.topics):
                return g_u, t_u
        except ValueError:
            pass

    # Build clean name maps
    clean_groups = {clean_path_component(g.name): g.id for g in catalog.groups}
    clean_topics_by_name = {clean_path_component(t.name): t.id for t in catalog.topics}

    # 2. Hierarchical: storage / group / subject / topic / file
    if len(dir_parts) >= 3:
        grp_name = clean_path_component(dir_parts[0])
        topic_name = clean_path_component(dir_parts[-1])
        if grp_name in clean_groups and topic_name in clean_topics_by_name:
            return clean_groups[grp_name], clean_topics_by_name[topic_name]

    # 3. Hierarchical: storage / group / topic / file
    if len(dir_parts) == 2:
        grp_name = clean_path_component(dir_parts[0])
        topic_name = clean_path_component(dir_parts[1])
        if grp_name in clean_groups and topic_name in clean_topics_by_name:
            return clean_groups[grp_name], clean_topics_by_name[topic_name]

    # 4. Flat legacy layout: storage / clean_group-clean_subject-clean_work / file
    if len(dir_parts) == 1:
        dname = dir_parts[0]
        matches: list[tuple[UUID, UUID]] = []
        for g in catalog.groups:
            cg = clean_path_component(g.name)
            prefix = f"{cg}-"
            if not dname.startswith(prefix):
                continue
            remainder = dname[len(prefix) :]
            for s in catalog.subjects:
                cs = clean_path_component(s.name)
                sprefix = f"{cs}-"
                if not remainder.startswith(sprefix):
                    continue
                work_name = clean_path_component(remainder[len(sprefix) :])
                for t in catalog.topics:
                    if clean_path_component(t.name) == work_name and (
                        t.subject_id == s.id or t.subject_id == catalog.unassigned_subject_id
                    ):
                        matches.append((g.id, t.id))

        if len(matches) == 1:
            return matches[0]
        return None, None
    return None, None


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.target).resolve()
    if target.exists():
        sys.stderr.write(f"Error: Target database already exists at {target}\n")
        return 1
    initialize(target)
    sys.stdout.write(f"Initialized v2 database at {target}\n")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    storage_path = Path(args.storage).resolve()
    catalog_path = Path(args.catalog).resolve()
    legacy_db_path = Path(args.legacy_db).resolve() if args.legacy_db else None

    if not storage_path.is_dir():
        sys.stderr.write(f"Error: Storage directory not found: {storage_path}\n")
        return 1
    if not catalog_path.is_file():
        sys.stderr.write(f"Error: Catalog file not found: {catalog_path}\n")
        return 1
    if legacy_db_path and not legacy_db_path.is_file() and not args.allow_missing_legacy_db:
        sys.stderr.write(f"Error: Legacy DB not found: {legacy_db_path}\n")
        return 1

    catalog = parse_legacy_catalog(catalog_path)

    # Inspect legacy database for assignments
    assignments_by_gt: dict[tuple[str, str], list[str]] = defaultdict(list)
    if legacy_db_path and legacy_db_path.is_file():
        with sqlite3.connect(legacy_db_path) as conn:
            conn.row_factory = sqlite3.Row
            table_check = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='class_assignments';"
            ).fetchone()
            if table_check:
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(class_assignments);").fetchall()}
                name_to_gid = {normalize_name_key(g.name): str(g.id) for g in catalog.groups}
                for r in conn.execute("SELECT * FROM class_assignments;").fetchall():
                    aid = str(r["id"])
                    tid = str(r["topic_id"])
                    if "group_id" in cols:
                        gid = str(r["group_id"])
                    else:
                        gid = name_to_gid.get(normalize_name_key(r["group_name"]), "")
                    if gid:
                        assignments_by_gt[(gid, tid)].append(aid)

    files_map: dict[str, Any] = {}
    issues: list[str] = []

    # First pass: collect files and identify submissions vs non-submissions
    raw_candidates: list[tuple[str, Path, str]] = []
    for root, _, files in os.walk(storage_path):
        for fname in sorted(files):
            file_p = Path(root) / fname
            rel_path = file_p.relative_to(storage_path).as_posix()

            # Identify non-submissions
            if (
                fname.startswith((".", "report-"))
                or "/.pending/" in f"/{rel_path}/"
                or rel_path.startswith(".pending/")
                or "manifest" in fname
            ):
                files_map[rel_path] = {"non_submission_reason": f"System/non-submission file ({fname})"}
                continue

            raw_candidates.append((rel_path, file_p, fname))

    # Second pass: parse file locations and filenames, count student names per group
    parsed_candidates: list[dict[str, Any]] = []
    group_student_counts: dict[tuple[str, str], int] = Counter()

    for rel_path, file_p, fname in raw_candidates:
        gid, tid = resolve_file_location(rel_path, catalog)
        _, student_name, attempt, _ = parse_submission_filename(fname)

        if gid is None or tid is None:
            issues.append(f"Ambiguous or unresolvable storage path for {rel_path!r}, requires explicit mapping")

        if student_name is None or attempt is None:
            issues.append(f"Unparseable submission filename, requires explicit mapping: {rel_path!r}")

        if gid and student_name:
            group_student_counts[(str(gid), student_name)] += 1

        parsed_candidates.append({
            "rel_path": rel_path,
            "gid": gid,
            "tid": tid,
            "student_name": student_name,
            "attempt": attempt,
        })

    # Detect duplicate student names in each group
    duplicate_names_in_group: set[tuple[str, str]] = set()
    for (gid_str, student_name), count in sorted(group_student_counts.items()):
        if count > 1:
            duplicate_names_in_group.add((gid_str, student_name))
            issues.append(
                f"Duplicate student names detected in group '{gid_str}' for {student_name!r}, "
                "requiring operator confirmation of student_id in mapping"
            )

    # Build students list with proposed IDs
    students_map: dict[tuple[str, str], str] = {}
    students_list: list[dict[str, str]] = []
    for gid_str, student_name in sorted(group_student_counts.keys()):
        st_uuid = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"homework-submissions:v2:student:{gid_str}:{student_name}",
            )
        )
        students_map[(gid_str, student_name)] = st_uuid
        students_list.append({
            "id": st_uuid,
            "group_id": gid_str,
            "display_name": student_name,
        })

    # Build files_map
    for item in parsed_candidates:
        rel_path = item["rel_path"]
        gid = item["gid"]
        tid = item["tid"]
        student_name = item["student_name"]
        attempt = item["attempt"]

        student_id = None
        if gid and student_name:
            key = (str(gid), student_name)
            # If student name is ambiguous or appears multiple times in group, leave student_id null
            if key not in duplicate_names_in_group:
                student_id = students_map.get(key)

        assignment_id = None
        if gid and tid:
            cands = assignments_by_gt.get((str(gid), str(tid)), [])
            if len(cands) == 1:
                assignment_id = cands[0]

        files_map[rel_path] = {
            "student_id": student_id,
            "group_id": str(gid) if gid else None,
            "topic_id": str(tid) if tid else None,
            "attempt_number": attempt,
            "assignment_id": assignment_id,
        }

    mapping_data = {
        "version": 1,
        "entities": {
            "groups": [{"id": str(g.id), "name": g.name} for g in catalog.groups],
            "subjects": [{"id": str(s.id), "name": s.name, "is_system": s.is_system} for s in catalog.subjects],
            "topics": [{"id": str(t.id), "name": t.name, "subject_id": str(t.subject_id)} for t in catalog.topics],
        },
        "catalog_overrides": {},
        "legacy_groups": {g.name: str(g.id) for g in catalog.groups},
        "assignments": {},
        "students": students_list,
        "files": files_map,
        "issues": issues,
    }

    output_json = json.dumps(mapping_data, indent=2, ensure_ascii=False)
    if args.output:
        out_p = Path(args.output).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(output_json, encoding="utf-8")
        sys.stderr.write(f"Draft mapping written to {out_p}\n")

    if issues:
        sys.stderr.write(f"Warning: Analyze found {len(issues)} issue(s) requiring manual review:\n")
        for iss in issues:
            sys.stderr.write(f"  - {iss}\n")

    sys.stdout.write(output_json + "\n")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    legacy_db_path = Path(args.legacy_db).resolve() if args.legacy_db else None
    catalog_path = Path(args.catalog).resolve()
    storage_path = Path(args.storage).resolve()
    target_path = Path(args.target).resolve()
    backup_root = Path(args.backup_root).resolve()
    mapping_path = Path(args.mapping).resolve()

    return apply_migration(
        legacy_db_path=legacy_db_path,
        catalog_path=catalog_path,
        storage_path=storage_path,
        target_path=target_path,
        backup_root=backup_root,
        mapping_path=mapping_path,
        allow_missing_legacy_db=args.allow_missing_legacy_db,
        offline=args.offline,
    )


def apply_migration(
    legacy_db_path: Path | None,
    catalog_path: Path,
    storage_path: Path,
    target_path: Path,
    backup_root: Path,
    mapping_path: Path,
    allow_missing_legacy_db: bool = False,
    offline: bool = False,
) -> int:
    if not offline:
        sys.stderr.write("Error: Migration apply requires --offline flag.\n")
        return 1

    target_path = target_path.resolve()
    if target_path.exists():
        sys.stderr.write(f"Error: Target database already exists at {target_path}\n")
        return 1

    if legacy_db_path and target_path == legacy_db_path.resolve():
        sys.stderr.write("Error: Target database cannot be the same as legacy database\n")
        return 1

    backup_root = backup_root.resolve()
    storage_path = storage_path.resolve()
    if backup_root == storage_path or backup_root.is_relative_to(storage_path):
        sys.stderr.write(f"Error: Backup root {backup_root} cannot be inside storage {storage_path}\n")
        return 1

    # Check for active server process
    check_running_server([Path("."), Path("homework-submissions"), storage_path.parent, target_path.parent])

    # FIRST MUTATING STEP: BACKUP
    run_dir, manifest = execute_backup(
        backup_root=backup_root,
        legacy_db_path=legacy_db_path,
        catalog_path=catalog_path,
        storage_path=storage_path,
        allow_missing_legacy_db=allow_missing_legacy_db,
    )

    # Initialize staging DB in run_dir
    staging_db_path = run_dir / "app_v2.staging.sqlite3"
    staging_conn = sqlite3.connect(staging_db_path)
    staging_conn.row_factory = sqlite3.Row
    staging_conn.execute("PRAGMA foreign_keys = ON;")
    staging_conn.execute("PRAGMA journal_mode = WAL;")

    create_schema(staging_conn)

    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    if mapping.get("version") != 1:
        raise ValueError(f"Unsupported mapping version: {mapping.get('version')}")

    legacy_backup_db = run_dir / "access.sqlite3"
    legacy_has_db = legacy_backup_db.is_file()

    with staging_conn:
        entities = mapping.get("entities", {})
        groups = entities.get("groups", [])
        subjects = entities.get("subjects", [])
        topics = entities.get("topics", [])

        for g in groups:
            gid = str(UUID(str(g["id"])))
            gname = str(g["name"]).strip()
            staging_conn.execute(
                "INSERT INTO groups (id, name, name_key, archived_at) VALUES (?, ?, ?, NULL)",
                (gid, gname, normalize_name_key(gname)),
            )

        for s in subjects:
            sid = str(UUID(str(s["id"])))
            sname = str(s["name"]).strip()
            is_sys = 1 if s.get("is_system") else 0
            staging_conn.execute(
                "INSERT INTO subjects (id, name, name_key, is_system, archived_at) VALUES (?, ?, ?, ?, NULL)",
                (sid, sname, normalize_name_key(sname), is_sys),
            )

        for t in topics:
            tid = str(UUID(str(t["id"])))
            tname = str(t["name"]).strip()
            tsub_id = str(UUID(str(t["subject_id"])))
            staging_conn.execute(
                "INSERT INTO topics (id, subject_id, name, name_key, archived_at) VALUES (?, ?, ?, ?, NULL)",
                (tid, tsub_id, tname, normalize_name_key(tname)),
            )

        imported_groups_access = set()
        name_to_gid = {normalize_name_key(g["name"]): str(UUID(str(g["id"]))) for g in groups}
        legacy_groups_map = {
            normalize_name_key(k): str(UUID(str(v)))
            for k, v in mapping.get("legacy_groups", {}).items()
        }
        name_to_gid.update(legacy_groups_map)

        if legacy_has_db:
            legacy_conn = sqlite3.connect(legacy_backup_db)
            legacy_conn.row_factory = sqlite3.Row

            cursor = legacy_conn.execute("PRAGMA table_info(group_access);")
            cols = {row["name"] for row in cursor.fetchall()}

            if "group_name" in cols:
                for row in legacy_conn.execute("SELECT * FROM group_access;").fetchall():
                    g_key = normalize_name_key(row["group_name"])
                    if g_key in name_to_gid:
                        gid = name_to_gid[g_key]
                        staging_conn.execute(
                            """
                            INSERT INTO group_access (group_id, password_hash, is_open, closes_at_utc, version)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (gid, row["password_hash"], row["is_open"], row["closes_at_utc"], row["version"]),
                        )
                        imported_groups_access.add(gid)
            elif "group_id" in cols:
                for row in legacy_conn.execute("SELECT * FROM group_access;").fetchall():
                    gid = str(UUID(str(row["group_id"])))
                    staging_conn.execute(
                        """
                        INSERT INTO group_access (group_id, password_hash, is_open, closes_at_utc, version)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (gid, row["password_hash"], row["is_open"], row["closes_at_utc"], row["version"]),
                    )
                    imported_groups_access.add(gid)

            # Check class_assignments table
            ca_check = legacy_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='class_assignments';"
            ).fetchone()
            if ca_check:
                ca_cols = {row["name"] for row in legacy_conn.execute("PRAGMA table_info(class_assignments);").fetchall()}
                for row in legacy_conn.execute("SELECT * FROM class_assignments;").fetchall():
                    try:
                        aid = str(UUID(str(row["id"])))
                    except ValueError:
                        aid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"homework-submissions:v2:assignment:{row['id']}"))
                    if "group_id" in ca_cols:
                        gid = str(UUID(str(row["group_id"])))
                    else:
                        gid = name_to_gid.get(normalize_name_key(row["group_name"]))
                        if not gid:
                            continue
                    tid = str(UUID(str(row["topic_id"])))
                    created_at = row["created_at"]
                    updated_at = row["updated_at"]
                    staging_conn.execute(
                        """
                        INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at, archived_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                        """,
                        (aid, gid, tid, row["lesson_date"], row["pair_number"], created_at, updated_at),
                    )
            legacy_conn.close()

        # Insert default group_access for groups without access record
        for g in groups:
            gid = str(UUID(str(g["id"])))
            if gid not in imported_groups_access:
                staging_conn.execute(
                    "INSERT INTO group_access (group_id, password_hash, is_open, closes_at_utc, version) VALUES (?, NULL, 0, NULL, 1)",
                    (gid,),
                )

        # Import students
        known_student_ids: set[str] = set()
        for st in mapping.get("students", []):
            if not st.get("id"):
                raise ValueError("Student entry missing 'id'")
            stid = str(UUID(str(st["id"])))
            if not st.get("group_id"):
                raise ValueError(f"Student {stid} missing 'group_id'")
            sgid = str(UUID(str(st["group_id"])))
            sdisp = str(st.get("display_name", "")).strip()
            if not sdisp:
                raise ValueError(f"Student {stid} missing 'display_name'")
            known_student_ids.add(stid)
            staging_conn.execute(
                "INSERT INTO students (id, group_id, display_name, archived_at) VALUES (?, ?, ?, NULL)",
                (stid, sgid, sdisp),
            )

        # Import submissions & submission_files
        migration_ts = datetime.now(timezone.utc).isoformat()
        mapping_files = mapping.get("files", {})
        storage_backup_dir = run_dir / "storage"

        for root, _, files in os.walk(storage_backup_dir):
            for fname in sorted(files):
                file_p = Path(root) / fname
                rel_path = file_p.relative_to(storage_backup_dir).as_posix()
                finfo = mapping_files.get(rel_path)
                if not finfo:
                    raise ValueError(f"Storage file {rel_path!r} not found in mapping.json")
                if finfo.get("non_submission_reason"):
                    continue
                if not finfo.get("student_id"):
                    raise ValueError(f"Mapping for {rel_path!r} is missing 'student_id'")

                student_id = str(UUID(str(finfo["student_id"])))
                if student_id not in known_student_ids:
                    raise ValueError(f"Student ID {student_id!r} for {rel_path!r} does not exist in mapping['students']")

                if not finfo.get("group_id"):
                    raise ValueError(f"Mapping for {rel_path!r} is missing 'group_id'")
                group_id = str(UUID(str(finfo["group_id"])))

                if not finfo.get("topic_id"):
                    raise ValueError(f"Mapping for {rel_path!r} is missing 'topic_id'")
                topic_id = str(UUID(str(finfo["topic_id"])))

                if finfo.get("attempt_number") is None:
                    raise ValueError(f"Mapping for {rel_path!r} is missing 'attempt_number'")
                try:
                    attempt_number = int(finfo["attempt_number"])
                    if attempt_number < 1:
                        raise ValueError(f"Invalid attempt_number {attempt_number} for {rel_path!r}, must be >= 1")
                except (TypeError, ValueError) as err:
                    raise ValueError(f"Invalid attempt_number for {rel_path!r}: {err}") from err

                assignment_id = str(UUID(str(finfo["assignment_id"]))) if finfo.get("assignment_id") else None

                is_reviewed = fname.startswith("+")
                review_status = "reviewed" if is_reviewed else "pending"
                reviewed_at = None
                original_filename = fname[1:] if is_reviewed else fname

                sub_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"homework-submissions:v2:submission:{rel_path}")
                file_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"homework-submissions:v2:file:{rel_path}")

                size_bytes = file_p.stat().st_size
                sha256_hex = file_sha256(file_p)

                staging_conn.execute(
                    """
                    INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, review_status, reviewed_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(sub_uuid),
                        student_id,
                        group_id,
                        topic_id,
                        assignment_id,
                        attempt_number,
                        review_status,
                        reviewed_at,
                        migration_ts,
                    ),
                )
                staging_conn.execute(
                    """
                    INSERT INTO submission_files (id, submission_id, original_filename, stored_path, size_bytes, sha256)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(file_uuid),
                        str(sub_uuid),
                        original_filename,
                        rel_path,
                        size_bytes,
                        sha256_hex,
                    ),
                )

    # Validate staging database integrity and constraints
    res = staging_conn.execute("PRAGMA integrity_check;").fetchall()
    if not res or res[0][0] != "ok":
        raise RuntimeError(f"Integrity check failed on staging DB: {res}")
    fk_res = staging_conn.execute("PRAGMA foreign_key_check;").fetchall()
    if fk_res:
        raise RuntimeError(f"Foreign key violations in staging DB: {fk_res}")

    report = {
        "run_id": manifest["run_id"],
        "status": "success",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "groups_count": len(groups),
        "subjects_count": len(subjects),
        "topics_count": len(topics),
        "students_count": len(mapping.get("students", [])),
    }
    (run_dir / "migration-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Publish target DB
    staging_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    staging_conn.close()

    if target_path.exists():
        raise FileExistsError(f"Target database already exists at {target_path}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(staging_db_path, target_path)
    except OSError as err:
        if err.errno == 18:
            temp_target = target_path.with_name(f"{target_path.name}.tmp.{uuid.uuid4()}")
            shutil.copyfile(staging_db_path, temp_target)
            temp_target.chmod(0o600)
            os.replace(temp_target, target_path)
            staging_db_path.unlink(missing_ok=True)
        else:
            raise

    try:
        target_path.chmod(0o600)
    except OSError:
        pass

    validate(target_path)
    sys.stdout.write(f"Migration completed successfully. Target published at {target_path}\n")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    database_path = Path(args.database).resolve()
    storage_path = Path(args.storage).resolve()
    manifest_path = Path(args.manifest).resolve()

    if not database_path.is_file():
        sys.stderr.write(f"Error: Database not found: {database_path}\n")
        return 1
    if not storage_path.is_dir():
        sys.stderr.write(f"Error: Storage not found: {storage_path}\n")
        return 1
    if not manifest_path.is_file():
        sys.stderr.write(f"Error: Manifest not found: {manifest_path}\n")
        return 1

    # 1. Validate database structure
    try:
        validate(database_path)
    except (DatabaseError, sqlite3.Error, OSError) as err:
        sys.stderr.write(f"Error: Database validation failed: {err}\n")
        return 1

    # 2. Validate manifest
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("backup_complete"):
        sys.stderr.write("Error: Manifest indicates incomplete backup\n")
        return 1

    # 3. Check storage files against manifest
    manifest_files = manifest.get("hashes", {}).get("files", {})
    for rel_path, info in manifest_files.items():
        file_p = storage_path / rel_path
        if not file_p.is_file():
            sys.stderr.write(f"Error: File missing from storage: {rel_path}\n")
            return 1
        if file_p.stat().st_size != info["size_bytes"]:
            sys.stderr.write(f"Error: Size mismatch for {rel_path}\n")
            return 1
        if file_sha256(file_p) != info["sha256"]:
            sys.stderr.write(f"Error: Checksum mismatch for {rel_path}\n")
            return 1

    # 4. Check DB submission_files against storage
    with sqlite3.connect(database_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT stored_path, size_bytes, sha256 FROM submission_files;").fetchall()
        for r in rows:
            sp = r["stored_path"]
            file_p = storage_path / sp
            if not file_p.is_file():
                sys.stderr.write(f"Error: DB submission file missing on disk: {sp}\n")
                return 1
            if file_p.stat().st_size != r["size_bytes"]:
                sys.stderr.write(f"Error: DB size mismatch for {sp}\n")
                return 1
            if r["sha256"] and file_sha256(file_p) != r["sha256"]:
                sys.stderr.write(f"Error: DB checksum mismatch for {sp}\n")
                return 1

    sys.stdout.write("Verification successful. Database and storage match manifest.\n")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    backup_dir = Path(args.backup).resolve()
    dest_dir = Path(args.destination).resolve()

    if not backup_dir.is_dir():
        sys.stderr.write(f"Error: Backup directory not found: {backup_dir}\n")
        return 1

    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.is_file():
        sys.stderr.write(f"Error: Manifest not found in backup: {manifest_path}\n")
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("backup_complete"):
        sys.stderr.write("Error: Backup manifest indicates incomplete backup.\n")
        return 1

    # Verify backup hashes before restoring
    catalog_backup = backup_dir / "catalog.json"
    if not catalog_backup.is_file() or file_sha256(catalog_backup) != manifest["hashes"]["catalog"]:
        sys.stderr.write("Error: Corrupted catalog.json in backup.\n")
        return 1

    if manifest.get("hashes", {}).get("legacy_db"):
        db_backup = backup_dir / "access.sqlite3"
        if not db_backup.is_file() or file_sha256(db_backup) != manifest["hashes"]["legacy_db"]:
            sys.stderr.write("Error: Corrupted access.sqlite3 in backup.\n")
            return 1

    for rel_path, info in manifest.get("hashes", {}).get("files", {}).items():
        fp = backup_dir / "storage" / rel_path
        if not fp.is_file() or fp.stat().st_size != info["size_bytes"] or file_sha256(fp) != info["sha256"]:
            sys.stderr.write(f"Error: Corrupted storage file in backup: {rel_path}\n")
            return 1

    if dest_dir.exists():
        sys.stderr.write(f"Error: Destination already exists: {dest_dir}\n")
        return 1

    dest_dir.mkdir(parents=True, exist_ok=False)
    try:
        dest_dir.chmod(0o700)
    except OSError:
        pass

    # Copy files
    shutil.copyfile(manifest_path, dest_dir / "manifest.json")
    (dest_dir / "manifest.json").chmod(0o600)

    shutil.copyfile(catalog_backup, dest_dir / "catalog.json")
    (dest_dir / "catalog.json").chmod(0o600)

    if (backup_dir / "access.sqlite3").is_file():
        shutil.copyfile(backup_dir / "access.sqlite3", dest_dir / "access.sqlite3")
        (dest_dir / "access.sqlite3").chmod(0o600)

    shutil.copytree(backup_dir / "storage", dest_dir / "storage", symlinks=False)
    for root, dirs, files in os.walk(dest_dir / "storage"):
        for d in dirs:
            try:
                (Path(root) / d).chmod(0o700)
            except OSError:
                pass
        for f in files:
            try:
                (Path(root) / f).chmod(0o600)
            except OSError:
                pass

    # Verify destination hashes
    for rel_path, info in manifest.get("hashes", {}).get("files", {}).items():
        fp = dest_dir / "storage" / rel_path
        if not fp.is_file() or file_sha256(fp) != info["sha256"]:
            sys.stderr.write(f"Error: Post-restore verification failed for {rel_path}\n")
            return 1

    sys.stdout.write(f"Restore completed successfully to {dest_dir}\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.migrate_v2",
        description="Offline migration tool for homework-submissions v2.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = subparsers.add_parser("init", help="Initialize a fresh v2 SQLite database.")
    p_init.add_argument("--target", required=True, type=Path, help="Path to create target database")
    p_init.set_defaults(func=cmd_init)

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="Analyze legacy files and produce draft mapping.")
    p_analyze.add_argument("--legacy-db", type=Path, default=None, help="Path to legacy access.sqlite3")
    p_analyze.add_argument("--catalog", required=True, type=Path, help="Path to catalog.json")
    p_analyze.add_argument("--storage", required=True, type=Path, help="Path to storage directory")
    p_analyze.add_argument("--output", type=Path, default=None, help="Output path for mapping.json")
    p_analyze.add_argument(
        "--allow-missing-legacy-db",
        action="store_true",
        default=False,
        help="Allow proceeding if legacy DB is missing",
    )
    p_analyze.set_defaults(func=cmd_analyze)

    # apply
    p_apply = subparsers.add_parser("apply", help="Execute offline migration.")
    p_apply.add_argument("--offline", action="store_true", required=True, help="Must be passed explicitly")
    p_apply.add_argument("--legacy-db", type=Path, default=None, help="Path to legacy access.sqlite3")
    p_apply.add_argument("--catalog", required=True, type=Path, help="Path to catalog.json")
    p_apply.add_argument("--storage", required=True, type=Path, help="Path to storage directory")
    p_apply.add_argument("--target", required=True, type=Path, help="Path to target v2 database")
    p_apply.add_argument("--backup-root", required=True, type=Path, help="Path to backup storage root")
    p_apply.add_argument("--mapping", required=True, type=Path, help="Path to mapping.json")
    p_apply.add_argument(
        "--allow-missing-legacy-db",
        action="store_true",
        default=False,
        help="Allow proceeding if legacy DB is missing",
    )
    p_apply.set_defaults(func=cmd_apply)

    # verify
    p_verify = subparsers.add_parser("verify", help="Verify database and storage against manifest.")
    p_verify.add_argument("--database", required=True, type=Path, help="Path to v2 database")
    p_verify.add_argument("--storage", required=True, type=Path, help="Path to storage directory")
    p_verify.add_argument("--manifest", required=True, type=Path, help="Path to manifest.json")
    p_verify.set_defaults(func=cmd_verify)

    # restore
    p_restore = subparsers.add_parser("restore", help="Restore backup to destination.")
    p_restore.add_argument("--backup", required=True, type=Path, help="Path to backup directory with manifest.json")
    p_restore.add_argument("--destination", required=True, type=Path, help="Path to destination directory")
    p_restore.set_defaults(func=cmd_restore)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
