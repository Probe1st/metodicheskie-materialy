"""Audit support library for homework-submissions SQLite v2 transition audit.

Provides independent inspection utilities, snapshotting, CLI runner with scrubbed
environment, consistency assertions, and synthetic test data factories.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import uuid
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from werkzeug.security import generate_password_hash

# ---------------------------------------------------------------------------
# Canonical UUIDs and Fixed Constants
# ---------------------------------------------------------------------------

def canonical_uuid(seed: str) -> UUID:
    """Generate a deterministic UUID from a seed string using UUIDv5."""
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"homework-submissions.audit.{seed}")


def canonical_uuid7(index: int) -> UUID:
    """Generate a deterministic UUID matching the UUIDv7 layout (version 7, RFC 4122 variant)."""
    # 48 bits timestamp/prefix, 4 bits ver=7, 12 bits sequence, 2 bits var=10, 62 bits suffix
    prefix = 0x018D_0000_0000
    val = (prefix << 80) | (0x7 << 76) | ((index & 0xFFF) << 64) | (0x2 << 62) | (index & 0x3FFF_FFFF_FFFF_FFFF)
    return UUID(int=val)


# System Unassigned Subject (fixed canonical UUID)
SYSTEM_UNASSIGNED_SUBJECT_ID: UUID = UUID("018d0000-0000-7000-8000-000000000000")
SYSTEM_UNASSIGNED_SUBJECT_NAME: str = "Без предмета"

# Groups (with passwords and access version = 7)
G1_ID: UUID = UUID("018d0000-0000-7000-8000-000000000001")
G1_NAME: str = "ПИ-201"
G1_PASSWORD: str = "Password-G1-Secret"
G1_PASSWORD_HASH: str = generate_password_hash(G1_PASSWORD)
G1_ACCESS_VERSION: int = 7

G2_ID: UUID = UUID("018d0000-0000-7000-8000-000000000002")
G2_NAME: str = "ИС-202"
G2_PASSWORD: str = "Password-G2-Secret"
G2_PASSWORD_HASH: str = generate_password_hash(G2_PASSWORD)
G2_ACCESS_VERSION: int = 7

# Subjects
S1_ID: UUID = UUID("018d0000-0000-7000-8000-000000000011")
S1_NAME: str = "Информатика"

S2_ID: UUID = UUID("018d0000-0000-7000-8000-000000000012")
S2_NAME: str = "Математика"

# Topics: T1 (in S1) and T2 (in S2) with IDENTICAL name to test cross-subject collisions
COLLISION_TOPIC_NAME: str = "Лабораторная работа 1"
T1_ID: UUID = UUID("018d0000-0000-7000-8000-000000000021")
T1_NAME: str = COLLISION_TOPIC_NAME

T2_ID: UUID = UUID("018d0000-0000-7000-8000-000000000022")
T2_NAME: str = COLLISION_TOPIC_NAME

# Free topic attached to system unassigned subject
T_FREE_ID: UUID = UUID("018d0000-0000-7000-8000-000000000020")
T_FREE_NAME: str = "Свободная тема"

# Assignments: two assignments for the same topic on different dates and pairs
A1_ID: UUID = UUID("018d0000-0000-7000-8000-000000000031")
A1_DATE: str = "2026-03-01"
A1_PAIR: int = 1

A2_ID: UUID = UUID("018d0000-0000-7000-8000-000000000032")
A2_DATE: str = "2026-03-02"
A2_PAIR: int = 2

# Students: two students with IDENTICAL name "Иванов Иван" but DIFFERENT canonical UUIDs in G1
STUDENT_SAME_NAME: str = "Иванов Иван"
STUDENT1_ID: UUID = UUID("018d0000-0000-7000-8000-000000000041")
STUDENT1_NAME: str = STUDENT_SAME_NAME

STUDENT2_ID: UUID = UUID("018d0000-0000-7000-8000-000000000042")
STUDENT2_NAME: str = STUDENT_SAME_NAME

STUDENT_OTHER_ID: UUID = UUID("018d0000-0000-7000-8000-000000000043")
STUDENT_OTHER_NAME: str = "Петров Петр"

# Deadlines
FUTURE_DEADLINE: str = "2099-01-01T00:00:00Z"
PAST_DEADLINE: str = "2020-01-01T00:00:00Z"

# Environment variables that must be scrubbed during isolated CLI execution
SCRUBBED_ENV_VARS: frozenset[str] = frozenset({
    "DATABASE_PATH",
    "STORAGE_ROOT",
    "PRIVATE_DATA_DIR",
    "ADMIN_PASSWORD",
    "SECRET_KEY",
    "PORT",
    "HOST",
    "FLASK_APP",
    "FLASK_ENV",
    "FLASK_DEBUG",
    "SUBMISSIONS_DATABASE",
    "SUBMISSIONS_STORAGE",
    "SUBMISSIONS_PRIVATE",
    "CATALOG_PATH",
    "ACCESS_DATABASE",
    "ASSIGNMENTS_DATABASE",
})


# ---------------------------------------------------------------------------
# Checksum Helper
# ---------------------------------------------------------------------------

def compute_sha256(path: Path | str) -> str:
    """Compute sha256 checksum hex digest of a file."""
    p = Path(path)
    hasher = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# 1. snapshot_tree
# ---------------------------------------------------------------------------

def snapshot_tree(root: Path | str) -> dict[str, dict[str, Any]]:
    """Recursively snapshots the directory tree.

    For each entry:
      - relative path string
      - entry type ('file', 'dir', 'symlink', 'special')
      - permissions (octal mode string and int)
      - size
      - sha256 hex digest for regular files
      - mtime_ns
    Does NOT follow symlinks (records symlink target if symlink).
    """
    root_path = Path(root).resolve()
    if not root_path.exists():
        return {}

    entries: dict[str, dict[str, Any]] = {}

    def _walk(current: Path) -> None:
        try:
            with os.scandir(current) as it:
                dir_entries = sorted(it, key=lambda e: e.name)
        except (OSError, PermissionError):
            return

        for entry in dir_entries:
            entry_path = Path(entry.path)
            rel_path = entry_path.relative_to(root_path).as_posix()
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue

            target: str | None = None
            sha256_hex: str | None = None

            if entry.is_symlink():
                entry_type = "symlink"
                try:
                    target = os.readlink(entry_path)
                except OSError:
                    target = None
                size = st.st_size
            elif entry.is_file(follow_symlinks=False):
                entry_type = "file"
                size = st.st_size
                sha256_hex = compute_sha256(entry_path)
            elif entry.is_dir(follow_symlinks=False):
                entry_type = "dir"
                size = st.st_size
            else:
                entry_type = "special"
                size = st.st_size

            mode_oct = oct(st.st_mode & 0o777)
            mode_int = st.st_mode & 0o777

            entries[rel_path] = {
                "rel_path": rel_path,
                "type": entry_type,
                "mode": mode_oct,
                "mode_int": mode_int,
                "size": size,
                "sha256": sha256_hex,
                "mtime_ns": st.st_mtime_ns,
                "target": target,
            }

            if entry_type == "dir":
                _walk(entry_path)

    _walk(root_path)
    return entries


# ---------------------------------------------------------------------------
# 2. read_db_state
# ---------------------------------------------------------------------------

def read_db_state(path: Path | str, timeout: float = 5.0) -> dict[str, Any]:
    """Independent inspection using bare sqlite3.connect with mode=ro.

    Reads list of tables, column definitions (PRAGMA table_info), foreign keys,
    indexes, and sorted rows. Runs PRAGMA foreign_key_check, PRAGMA integrity_check,
    and PRAGMA quick_check.
    """
    db_path = Path(path).resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")

    # Connect with read-only URI mode if supported, fallback to regular connect
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=timeout)
    except sqlite3.Error:
        conn = sqlite3.connect(str(db_path), timeout=timeout)

    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()

        # User version
        cur.execute("PRAGMA user_version;")
        row_uv = cur.fetchone()
        user_version = row_uv[0] if row_uv else 0

        # Query all non-internal tables and views
        cur.execute(
            """
            SELECT name, type, sql
            FROM sqlite_master
            WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'
            ORDER BY name;
            """
        )
        table_records = cur.fetchall()
        tables: dict[str, dict[str, Any]] = {}

        for tbl in table_records:
            tbl_name = tbl["name"]
            tbl_type = tbl["type"]
            tbl_sql = tbl["sql"]

            # Columns
            cur.execute(f'PRAGMA table_info("{tbl_name}");')
            cols = [dict(c) for c in cur.fetchall()]

            # Foreign keys
            cur.execute(f'PRAGMA foreign_key_list("{tbl_name}");')
            fks = [dict(fk) for fk in cur.fetchall()]

            # Indexes
            cur.execute(f'PRAGMA index_list("{tbl_name}");')
            indexes = [dict(idx) for idx in cur.fetchall()]

            # Rows: sort by rowid if possible; otherwise order by all columns
            col_names = [c["name"] for c in cols]
            try:
                cur.execute(f'SELECT * FROM "{tbl_name}" ORDER BY rowid;')
            except sqlite3.OperationalError:
                if col_names:
                    order_by = ", ".join(f'"{c}"' for c in col_names)
                    cur.execute(f'SELECT * FROM "{tbl_name}" ORDER BY {order_by};')
                else:
                    cur.execute(f'SELECT * FROM "{tbl_name}";')

            rows = [dict(r) for r in cur.fetchall()]

            tables[tbl_name] = {
                "name": tbl_name,
                "type": tbl_type,
                "sql": tbl_sql,
                "columns": cols,
                "foreign_keys": fks,
                "indexes": indexes,
                "rows": rows,
                "row_count": len(rows),
            }

        cur.execute("PRAGMA foreign_key_check;")
        fk_check = [dict(r) if hasattr(r, "keys") else list(r) for r in cur.fetchall()]

        cur.execute("PRAGMA integrity_check;")
        integrity_check = [r[0] for r in cur.fetchall()]

        cur.execute("PRAGMA quick_check;")
        quick_check = [r[0] for r in cur.fetchall()]

        return {
            "path": str(db_path),
            "user_version": user_version,
            "tables": tables,
            "foreign_key_check": fk_check,
            "integrity_check": integrity_check,
            "quick_check": quick_check,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. run_cli
# ---------------------------------------------------------------------------

def run_cli(
    args: Sequence[str | Path],
    cwd: Path | str,
    env: dict[str, str] | None = None,
    timeout: float | None = 60.0,
) -> subprocess.CompletedProcess[str]:
    """Runs a CLI command via subprocess with scrubbed environment.

    Removes DATABASE_PATH, STORAGE_ROOT, PRIVATE_DATA_DIR, ADMIN_PASSWORD, etc.
    Sets PYTHONPATH to include homework-submissions root.
    Captures stdout, stderr, text=True, check=False.
    """
    cwd_path = Path(cwd).resolve()
    clean_env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_ENV_VARS}

    # Ensure PYTHONPATH includes homework-submissions root
    # support.py is in homework-submissions/tests/audit/support.py -> parents[2] is homework-submissions
    hw_root = Path(__file__).resolve().parents[2]
    existing_pp = clean_env.get("PYTHONPATH", "")
    new_pp = f"{hw_root}:{existing_pp}" if existing_pp else str(hw_root)
    clean_env["PYTHONPATH"] = new_pp

    if env:
        clean_env.update(env)

    cmd = [str(a) for a in args]
    return subprocess.run(
        cmd,
        cwd=cwd_path,
        env=clean_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# 4. assert_consistent
# ---------------------------------------------------------------------------

def assert_consistent(
    database: Path | str,
    storage: Path | str,
    ignore_hidden: bool = True,
) -> None:
    """Verifies consistency between database records and physical files in storage.

    - Checks SQLite integrity check and foreign key check.
    - Checks that all submission files recorded in DB exist in storage with matching size and sha256.
    - Checks for orphan files in storage.
    - Raises AssertionError with detailed diff if inconsistent.
    """
    db_path = Path(database).resolve()
    storage_root = Path(storage).resolve()

    errors: list[str] = []

    if not db_path.is_file():
        raise AssertionError(f"Database file not found: {db_path}")

    if not storage_root.is_dir():
        raise AssertionError(f"Storage directory not found: {storage_root}")

    # 1. Database-level checks
    with sqlite3.connect(str(db_path), timeout=5.0) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # PRAGMA integrity_check
        cur.execute("PRAGMA integrity_check;")
        int_rows = [r[0] for r in cur.fetchall()]
        if int_rows != ["ok"]:
            errors.append(f"PRAGMA integrity_check failed: {int_rows}")

        # PRAGMA foreign_key_check
        cur.execute("PRAGMA foreign_key_check;")
        fk_rows = cur.fetchall()
        if fk_rows:
            formatted_fks = [dict(r) for r in fk_rows]
            errors.append(f"PRAGMA foreign_key_check failed ({len(fk_rows)} violations): {formatted_fks}")

        # Query submission_files
        try:
            cur.execute(
                """
                SELECT id, submission_id, original_filename, stored_path, size_bytes, sha256
                FROM submission_files
                ORDER BY stored_path;
                """
            )
            files_in_db = cur.fetchall()
        except sqlite3.OperationalError as e:
            errors.append(f"Failed to query submission_files: {e}")
            files_in_db = []

    # 2. Check each DB record against storage
    known_paths: set[str] = set()
    for row in files_in_db:
        stored_path = row["stored_path"]
        known_paths.add(stored_path)
        file_p = storage_root / stored_path

        if not file_p.is_file():
            errors.append(
                f"Missing file in storage: {stored_path} (submission_id={row['submission_id']})"
            )
            continue

        try:
            actual_size = file_p.stat().st_size
            if actual_size != row["size_bytes"]:
                errors.append(
                    f"Size mismatch for {stored_path}: expected {row['size_bytes']}, got {actual_size}"
                )
        except OSError as e:
            errors.append(f"Could not stat file {stored_path}: {e}")
            continue

        if row["sha256"]:
            actual_sha = compute_sha256(file_p)
            if actual_sha != row["sha256"]:
                errors.append(
                    f"SHA256 mismatch for {stored_path}: expected {row['sha256']}, got {actual_sha}"
                )

    # 3. Check for orphan files in storage
    orphan_files: list[str] = []
    for root, dirs, files in os.walk(storage_root):
        if ignore_hidden:
            # Exclude hidden directories and .pending directory
            dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in sorted(files):
            if ignore_hidden and fname.startswith("."):
                continue
            file_p = Path(root) / fname
            rel = file_p.relative_to(storage_root).as_posix()
            if rel not in known_paths:
                orphan_files.append(rel)

    if orphan_files:
        errors.append(f"Orphaned files found in storage ({len(orphan_files)}): {orphan_files}")

    if errors:
        diff_msg = "\n  - " + "\n  - ".join(errors)
        raise AssertionError(f"Consistency check failed between DB and storage:{diff_msg}")


# ---------------------------------------------------------------------------
# 5. Synthetic Test Data Factories and Fixtures
# ---------------------------------------------------------------------------

def create_legacy_v1_catalog(
    groups: list[str | dict[str, Any]] | None = None,
    disciplines: dict[str, list[str | dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Create a synthetic legacy catalog dictionary in v1 format."""
    return {
        "groups": groups if groups is not None else [G1_NAME, G2_NAME],
        "disciplines": disciplines
        if disciplines is not None
        else {
            S1_NAME: [T1_NAME],
            S2_NAME: [T2_NAME],
        },
    }


def create_legacy_v2_catalog(
    groups: list[str | dict[str, Any]] | None = None,
    subjects: list[str | dict[str, Any]] | None = None,
    topics: list[str | dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a synthetic legacy catalog dictionary in v2 format (without UUIDs or with old structure)."""
    return {
        "version": 2,
        "groups": groups if groups is not None else [G1_NAME, G2_NAME],
        "subjects": subjects
        if subjects is not None
        else [S1_NAME, S2_NAME, SYSTEM_UNASSIGNED_SUBJECT_NAME],
        "topics": topics
        if topics is not None
        else [
            {"name": T1_NAME},
            {"name": T_FREE_NAME},
        ],
    }


def create_legacy_v3_catalog(
    groups: list[dict[str, Any]] | None = None,
    subjects: list[dict[str, Any]] | None = None,
    topics: list[dict[str, Any]] | None = None,
    unassigned_subject_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Create a synthetic legacy catalog dictionary in v3 format (with unassigned_subject_id)."""
    unassigned_id_str = str(unassigned_subject_id or SYSTEM_UNASSIGNED_SUBJECT_ID)
    return {
        "version": 3,
        "unassigned_subject_id": unassigned_id_str,
        "groups": groups
        if groups is not None
        else [
            {"id": str(G1_ID), "name": G1_NAME},
            {"id": str(G2_ID), "name": G2_NAME},
        ],
        "subjects": subjects
        if subjects is not None
        else [
            {"id": str(S1_ID), "name": S1_NAME},
            {"id": str(S2_ID), "name": S2_NAME},
            {"id": unassigned_id_str, "name": SYSTEM_UNASSIGNED_SUBJECT_NAME},
        ],
        "topics": topics
        if topics is not None
        else [
            {"id": str(T1_ID), "name": T1_NAME, "subject_id": str(S1_ID)},
            {"id": str(T2_ID), "name": T2_NAME, "subject_id": str(S2_ID)},
            {"id": str(T_FREE_ID), "name": T_FREE_NAME, "subject_id": unassigned_id_str},
        ],
    }


def _create_minimal_zip(path: Path, filename: str = "main.py", content: bytes = b"# solution\n") -> None:
    """Helper to create a small valid zip file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(filename, content)


def create_synthetic_storage_files(
    storage_root: Path | str,
    group_name: str = G1_NAME,
    topic_name: str = T1_NAME,
) -> dict[str, Path]:
    """Populate synthetic files in storage for migration and reconciliation tests.

    Creates:
      - historical zips: 'Иванов Иван 1.zip' and '+Иванов Иван 2.zip'
      - renamed folder: '{group_name}/RenamedFolder/note.txt'
      - empty file: '{group_name}/{topic_name}/empty.txt'
      - service .gitignore: '{group_name}/{topic_name}/.gitignore'
      - unknown .secret.zip: '{group_name}/{topic_name}/.secret.zip'
    """
    root = Path(storage_root).resolve()
    topic_dir = root / group_name / topic_name
    topic_dir.mkdir(parents=True, exist_ok=True)

    created: dict[str, Path] = {}

    # 1. Historical regular zip (attempt 1)
    zip1 = topic_dir / "Иванов Иван 1.zip"
    _create_minimal_zip(zip1, "attempt1.py", b"# attempt 1 code\n")
    created["historical_zip_1"] = zip1

    # 2. Historical reviewed zip (attempt 2, marked with leading '+')
    zip2 = topic_dir / "+Иванов Иван 2.zip"
    _create_minimal_zip(zip2, "attempt2.py", b"# attempt 2 reviewed code\n")
    created["historical_zip_2"] = zip2

    # 3. Renamed folder / arbitrary subdirectory
    renamed_dir = root / group_name / "RenamedFolder"
    renamed_dir.mkdir(parents=True, exist_ok=True)
    renamed_file = renamed_dir / "note.txt"
    renamed_file.write_text("Renamed folder content", encoding="utf-8")
    created["renamed_folder_file"] = renamed_file

    # 4. Empty file
    empty_f = topic_dir / "empty.txt"
    empty_f.write_bytes(b"")
    created["empty_file"] = empty_f

    # 5. Service .gitignore
    git_ignore = topic_dir / ".gitignore"
    git_ignore.write_text("*\n!.gitignore\n", encoding="utf-8")
    created["service_gitignore"] = git_ignore

    # 6. Unknown .secret.zip
    secret_zip = topic_dir / ".secret.zip"
    _create_minimal_zip(secret_zip, "secret.key", b"hidden secret")
    created["secret_zip"] = secret_zip

    return created


# ---------------------------------------------------------------------------
# Mapping Generator
# ---------------------------------------------------------------------------

def create_test_mapping(
    entities: dict[str, Any] | None = None,
    students: list[dict[str, Any]] | None = None,
    files: dict[str, Any] | None = None,
    legacy_groups: dict[str, str] | None = None,
    assignments: dict[str, Any] | None = None,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    """Produce a valid v2 mapping structure.

    Default: empty entities + explicit students and files.
    """
    default_entities = (
        entities
        if entities is not None
        else {
            "groups": [],
            "subjects": [],
            "topics": [],
        }
    )

    default_students = (
        students
        if students is not None
        else [
            {
                "id": str(STUDENT1_ID),
                "group_id": str(G1_ID),
                "display_name": STUDENT1_NAME,
            },
            {
                "id": str(STUDENT2_ID),
                "group_id": str(G1_ID),
                "display_name": STUDENT2_NAME,
            },
        ]
    )

    default_files = (
        files
        if files is not None
        else {
            f"{G1_NAME}/{T1_NAME}/Иванов Иван 1.zip": {
                "group_id": str(G1_ID),
                "topic_id": str(T1_ID),
                "student_id": str(STUDENT1_ID),
                "attempt_number": 1,
                "assignment_id": str(A1_ID),
            },
            f"{G1_NAME}/{T1_NAME}/+Иванов Иван 2.zip": {
                "group_id": str(G1_ID),
                "topic_id": str(T1_ID),
                "student_id": str(STUDENT1_ID),
                "attempt_number": 2,
                "assignment_id": str(A1_ID),
            },
            f"{G1_NAME}/{T1_NAME}/empty.txt": {
                "non_submission_reason": "Empty file",
            },
            f"{G1_NAME}/{T1_NAME}/.gitignore": {
                "non_submission_reason": "System/non-submission file (.gitignore)",
            },
            f"{G1_NAME}/{T1_NAME}/.secret.zip": {
                "non_submission_reason": "System/non-submission file (.secret.zip)",
            },
            f"{G1_NAME}/RenamedFolder/note.txt": {
                "non_submission_reason": "Non-submission folder content",
            },
        }
    )

    return {
        "version": 1,
        "entities": default_entities,
        "catalog_overrides": {},
        "legacy_groups": legacy_groups if legacy_groups is not None else {G1_NAME: str(G1_ID), G2_NAME: str(G2_ID)},
        "assignments": assignments if assignments is not None else {},
        "students": default_students,
        "files": default_files,
        "issues": issues if issues is not None else [],
    }


def create_mapping_with_archived(
    archived_groups: list[dict[str, Any]] | None = None,
    archived_subjects: list[dict[str, Any]] | None = None,
    archived_topics: list[dict[str, Any]] | None = None,
    archived_students: list[dict[str, Any]] | None = None,
    archived_at_ts: str = "2026-01-01T00:00:00Z",
    **kwargs: Any,
) -> dict[str, Any]:
    """Create a mapping structure that includes archived entities for edge-case testing."""
    entities: dict[str, Any] = {
        "groups": [
            {"id": str(G1_ID), "name": G1_NAME, "archived_at": None},
        ],
        "subjects": [
            {"id": str(S1_ID), "name": S1_NAME, "is_system": False, "archived_at": None},
        ],
        "topics": [
            {"id": str(T1_ID), "name": T1_NAME, "subject_id": str(S1_ID), "archived_at": None},
        ],
    }

    if archived_groups:
        for g in archived_groups:
            entities["groups"].append({
                "id": str(g["id"]),
                "name": str(g["name"]),
                "archived_at": g.get("archived_at", archived_at_ts),
            })

    if archived_subjects:
        for s in archived_subjects:
            entities["subjects"].append({
                "id": str(s["id"]),
                "name": str(s["name"]),
                "is_system": s.get("is_system", False),
                "archived_at": s.get("archived_at", archived_at_ts),
            })

    if archived_topics:
        for t in archived_topics:
            entities["topics"].append({
                "id": str(t["id"]),
                "name": str(t["name"]),
                "subject_id": str(t["subject_id"]),
                "archived_at": t.get("archived_at", archived_at_ts),
            })

    students = [
        {"id": str(STUDENT1_ID), "group_id": str(G1_ID), "display_name": STUDENT1_NAME, "archived_at": None},
    ]

    if archived_students:
        for st in archived_students:
            students.append({
                "id": str(st["id"]),
                "group_id": str(st["group_id"]),
                "display_name": str(st["display_name"]),
                "archived_at": st.get("archived_at", archived_at_ts),
            })

    return create_test_mapping(
        entities=entities,
        students=students,
        **kwargs,
    )
