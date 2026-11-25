"""Comprehensive test suite for migrate_v2 CLI, backup-first invariants, and restore."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.database import connect, validate
from tools.legacy_catalog import parse_legacy_catalog
from tools.migrate_v2 import (
    apply_migration,
    parse_submission_filename,
    resolve_file_location,
)


def _create_legacy_db(db_path: Path, wal_mode: bool = False) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        if wal_mode:
            conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute(
            """
            CREATE TABLE group_access (
                group_name TEXT PRIMARY KEY,
                password_hash TEXT,
                is_open INTEGER NOT NULL DEFAULT 0,
                closes_at_utc TEXT,
                version INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE class_assignments (
                id TEXT PRIMARY KEY,
                group_name TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                lesson_date TEXT NOT NULL,
                pair_number INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO group_access (group_name, password_hash, is_open, closes_at_utc, version)
            VALUES ('ИС 25/9-1П', 'hash123', 1, NULL, 1);
            """
        )


def _create_v3_catalog(catalog_path: Path) -> tuple[str, str, str, str]:
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    g_id = str(uuid4())
    s_id = str(uuid4())
    sys_id = str(uuid4())
    t_id = str(uuid4())

    data = {
        "version": 3,
        "groups": [{"id": g_id, "name": "ИС 25/9-1П"}],
        "subjects": [
            {"id": s_id, "name": "Архитектура аппаратных средств"},
            {"id": sys_id, "name": "Без предмета"},
        ],
        "topics": [{"id": t_id, "name": "Виды памяти", "subject_id": s_id}],
        "unassigned_subject_id": sys_id,
    }
    catalog_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return g_id, s_id, sys_id, t_id


def test_init_subcommand(tmp_path: Path) -> None:
    target_db = tmp_path / "new_app.db"
    cmd = [sys.executable, "-m", "tools.migrate_v2", "init", "--target", str(target_db)]
    res = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[1]), capture_output=True, text=True, check=False)
    assert res.returncode == 0
    assert target_db.is_file()
    validate(target_db)

    # Repeat init fails because target exists
    res_repeat = subprocess.run(
        cmd, cwd=str(Path(__file__).resolve().parents[1]), capture_output=True, text=True, check=False
    )
    assert res_repeat.returncode == 1


def test_backup_first_invariant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_db = tmp_path / "legacy.db"
    _create_legacy_db(legacy_db)
    catalog = tmp_path / "catalog.json"
    _create_v3_catalog(catalog)
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "file.txt").write_text("content", encoding="utf-8")
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"version": 1}), encoding="utf-8")

    backup_root = tmp_path / "backups"
    target = tmp_path / "target.db"

    # Inject failure during backup (e.g. shutil.copytree fails)
    def broken_copytree(*_args, **_kwargs):
        raise OSError("Simulated disk error during backup")

    monkeypatch.setattr("shutil.copytree", broken_copytree)

    with pytest.raises(OSError, match="Simulated disk error"):
        apply_migration(
            legacy_db_path=legacy_db,
            catalog_path=catalog,
            storage_path=storage,
            target_path=target,
            backup_root=backup_root,
            mapping_path=mapping,
            offline=True,
        )

    # Invariant: target must NOT exist, source files must be untouched
    assert not target.exists()
    assert legacy_db.is_file()
    assert (storage / "file.txt").read_text(encoding="utf-8") == "content"


def test_wal_copy(tmp_path: Path) -> None:
    legacy_db = tmp_path / "legacy.db"
    _create_legacy_db(legacy_db, wal_mode=True)
    catalog = tmp_path / "catalog.json"
    g_id, s_id, sys_id, t_id = _create_v3_catalog(catalog)

    # Insert committed rows that stay in WAL (no checkpoint)
    wal_assignment_id = str(uuid4())
    with sqlite3.connect(legacy_db) as conn:
        conn.execute(
            """
            INSERT INTO class_assignments (id, group_name, subject_id, topic_id, lesson_date, pair_number, created_at, updated_at)
            VALUES (?, 'ИС 25/9-1П', 'sub-1', ?, '2026-11-03', 1, '2026-11-03', '2026-11-03');
            """,
            (wal_assignment_id, t_id),
        )
        conn.commit()

    storage = tmp_path / "storage"
    storage.mkdir()
    backup_root = tmp_path / "backups"
    target = tmp_path / "target.db"

    # Create mapping
    mapping = tmp_path / "mapping.json"
    mapping_data = {
        "version": 1,
        "entities": {
            "groups": [{"id": g_id, "name": "ИС 25/9-1П"}],
            "subjects": [
                {"id": s_id, "name": "Архитектура аппаратных средств", "is_system": False},
                {"id": sys_id, "name": "Без предмета", "is_system": True},
            ],
            "topics": [{"id": t_id, "name": "Виды памяти", "subject_id": s_id}],
        },
        "legacy_groups": {"ИС 25/9-1П": g_id},
        "students": [],
        "files": {},
    }
    mapping.write_text(json.dumps(mapping_data), encoding="utf-8")
    res = apply_migration(
        legacy_db_path=legacy_db,
        catalog_path=catalog,
        storage_path=storage,
        target_path=target,
        backup_root=backup_root,
        mapping_path=mapping,
        offline=True,
    )
    assert res == 0

    # Verify uncheckpointed WAL row exists in target DB
    with connect(target) as conn:
        row = conn.execute("SELECT id, group_id, topic_id FROM class_assignments WHERE id = ?;", (wal_assignment_id,)).fetchone()
        assert row is not None
        assert row["group_id"] == g_id


def test_corrupt_or_incomplete_backup_restore(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup_run"
    backup_dir.mkdir()
    dest = tmp_path / "restored"

    # 1. Missing manifest
    cmd = [
        sys.executable,
        "-m",
        "tools.migrate_v2",
        "restore",
        "--backup",
        str(backup_dir),
        "--destination",
        str(dest),
    ]
    res1 = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[1]), capture_output=True, text=True, check=False)
    assert res1.returncode != 0

    # 2. Incomplete backup manifest
    manifest_p = backup_dir / "manifest.json"
    manifest_p.write_text(json.dumps({"backup_complete": False}), encoding="utf-8")
    res2 = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[1]), capture_output=True, text=True, check=False)
    assert res2.returncode != 0

    # 3. Corrupt file hash
    cat_file = backup_dir / "catalog.json"
    cat_file.write_text("bad data", encoding="utf-8")
    manifest_p.write_text(
        json.dumps({
            "backup_complete": True,
            "hashes": {
                "catalog": "0000000000000000000000000000000000000000000000000000000000000000",
                "files": {},
            },
        }),
        encoding="utf-8",
    )
    res3 = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[1]), capture_output=True, text=True, check=False)
    assert res3.returncode != 0
    assert not dest.exists()


def test_full_migration(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    legacy_db = tmp_path / "legacy.db"
    _create_legacy_db(legacy_db)
    catalog = tmp_path / "catalog.json"
    _g_id, s_id, _, t_id = _create_v3_catalog(catalog)

    # Insert historical assignment
    with sqlite3.connect(legacy_db) as conn:
        conn.execute(
            """
            INSERT INTO class_assignments (id, group_name, subject_id, topic_id, lesson_date, pair_number, created_at, updated_at)
            VALUES ('11111111-2222-3333-4444-555555555555', 'ИС 25/9-1П', ?, ?, '2026-11-03', 1, '2026-11-03', '2026-11-03');
            """,
            (s_id, t_id),
        )
        conn.commit()

    # Prepare storage with files
    storage = tmp_path / "storage"
    work_dir = storage / "ИС 25／9-1П" / "Архитектура аппаратных средств" / "Виды памяти"
    work_dir.mkdir(parents=True)
    file1 = work_dir / "Иванов Иван 1.zip"
    file1.write_bytes(b"content zip 1")
    file2 = work_dir / "+Иванов Иван 2.zip"
    file2.write_bytes(b"content zip 2")
    (storage / ".gitignore").write_text("*.tmp", encoding="utf-8")

    # Step 1: analyze
    mapping_out = tmp_path / "mapping.json"
    cmd_analyze = [
        sys.executable,
        "-m",
        "tools.migrate_v2",
        "analyze",
        "--legacy-db",
        str(legacy_db),
        "--catalog",
        str(catalog),
        "--storage",
        str(storage),
        "--output",
        str(mapping_out),
    ]
    res_an = subprocess.run(cmd_analyze, cwd=str(repo_root), capture_output=True, text=True, check=False)
    assert res_an.returncode == 0
    assert mapping_out.is_file()

    mapping_data = json.loads(mapping_out.read_text(encoding="utf-8"))
    assert len(mapping_data["students"]) == 1
    assert mapping_data["students"][0]["display_name"] == "Иванов Иван"
    assert any("duplicate student names" in issue.lower() for issue in mapping_data.get("issues", []))

    # Operator confirms student_id mapping for multiple submissions
    st_id = mapping_data["students"][0]["id"]
    for finfo in mapping_data["files"].values():
        if not finfo.get("non_submission_reason"):
            finfo["student_id"] = st_id
    mapping_out.write_text(json.dumps(mapping_data, indent=2, ensure_ascii=False), encoding="utf-8")
    # Step 2: apply
    target_db = tmp_path / "target_v2.db"
    backup_root = tmp_path / "backups"
    cmd_apply = [
        sys.executable,
        "-m",
        "tools.migrate_v2",
        "apply",
        "--offline",
        "--legacy-db",
        str(legacy_db),
        "--catalog",
        str(catalog),
        "--storage",
        str(storage),
        "--target",
        str(target_db),
        "--backup-root",
        str(backup_root),
        "--mapping",
        str(mapping_out),
    ]
    res_ap = subprocess.run(cmd_apply, cwd=str(repo_root), capture_output=True, text=True, check=False)
    assert res_ap.returncode == 0
    assert target_db.is_file()

    # Verify target DB content
    with connect(target_db) as conn:
        subs = conn.execute("SELECT * FROM submissions ORDER BY attempt_number;").fetchall()
        assert len(subs) == 2
        # First attempt: pending, reviewed_at NULL
        assert subs[0]["attempt_number"] == 1
        assert subs[0]["review_status"] == "pending"
        assert subs[0]["reviewed_at"] is None
        # Second attempt: '+' was stripped, review_status reviewed, reviewed_at NULL
        assert subs[1]["attempt_number"] == 2
        assert subs[1]["review_status"] == "reviewed"
        assert subs[1]["reviewed_at"] is None

        # Verify submission files
        files = conn.execute("SELECT * FROM submission_files ORDER BY original_filename;").fetchall()
        assert len(files) == 2
        assert files[0]["original_filename"] == "Иванов Иван 1.zip"
        assert files[1]["original_filename"] == "Иванов Иван 2.zip"
        # Stored path preserves literal '+'
        assert "+Иванов Иван 2.zip" in files[1]["stored_path"]

    # Step 3: verify command
    # Find manifest in backup run dir
    run_dirs = list(backup_root.iterdir())
    assert len(run_dirs) == 1
    manifest_p = run_dirs[0] / "manifest.json"

    cmd_verify = [
        sys.executable,
        "-m",
        "tools.migrate_v2",
        "verify",
        "--database",
        str(target_db),
        "--storage",
        str(storage),
        "--manifest",
        str(manifest_p),
    ]
    res_ver = subprocess.run(cmd_verify, cwd=str(repo_root), capture_output=True, text=True, check=False)
    assert res_ver.returncode == 0


def test_repeat_apply_fails_if_target_exists(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    legacy_db = tmp_path / "legacy.db"
    _create_legacy_db(legacy_db)
    catalog = tmp_path / "catalog.json"
    _create_v3_catalog(catalog)
    storage = tmp_path / "storage"
    storage.mkdir()
    target = tmp_path / "app.db"
    target.write_text("existing", encoding="utf-8")
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"version": 1}), encoding="utf-8")
    backup_root = tmp_path / "backups"

    cmd = [
        sys.executable,
        "-m",
        "tools.migrate_v2",
        "apply",
        "--offline",
        "--legacy-db",
        str(legacy_db),
        "--catalog",
        str(catalog),
        "--storage",
        str(storage),
        "--target",
        str(target),
        "--backup-root",
        str(backup_root),
        "--mapping",
        str(mapping),
    ]
    res = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, check=False)
    assert res.returncode == 1


def test_restore_creates_independent_replica(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    legacy_db = tmp_path / "legacy.db"
    _create_legacy_db(legacy_db)
    catalog = tmp_path / "catalog.json"
    g_id, s_id, sys_id, _ = _create_v3_catalog(catalog)
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "test.txt").write_bytes(b"hello world")
    mapping = tmp_path / "mapping.json"
    mapping_data = {
        "version": 1,
        "entities": {
            "groups": [{"id": g_id, "name": "ИС 25/9-1П"}],
            "subjects": [
                {"id": s_id, "name": "Архитектура аппаратных средств", "is_system": False},
                {"id": sys_id, "name": "Без предмета", "is_system": True},
            ],
            "topics": [],
        },
        "legacy_groups": {"ИС 25/9-1П": g_id},
        "students": [],
        "files": {
            "test.txt": {"non_submission_reason": "test text file"}
        },
    }
    mapping.write_text(json.dumps(mapping_data), encoding="utf-8")
    backup_root = tmp_path / "backups"
    target = tmp_path / "target.db"
    # Run apply to produce a clean verified backup
    res = apply_migration(
        legacy_db_path=legacy_db,
        catalog_path=catalog,
        storage_path=storage,
        target_path=target,
        backup_root=backup_root,
        mapping_path=mapping,
        offline=True,
    )
    assert res == 0

    run_dirs = list(backup_root.iterdir())
    assert len(run_dirs) == 1
    backup_run = run_dirs[0]

    # Restore to independent destination
    dest = tmp_path / "restored_replica"
    cmd = [
        sys.executable,
        "-m",
        "tools.migrate_v2",
        "restore",
        "--backup",
        str(backup_run),
        "--destination",
        str(dest),
    ]
    res_rest = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, check=False)
    assert res_rest.returncode == 0
    assert dest.is_dir()
    assert (dest / "manifest.json").is_file()
    assert (dest / "catalog.json").is_file()
    assert (dest / "access.sqlite3").is_file()
    assert (dest / "storage" / "test.txt").read_bytes() == b"hello world"


def test_legacy_catalog_v1_and_v2(tmp_path: Path) -> None:
    # Test v1
    v1_path = tmp_path / "catalog_v1.json"
    v1_data = {
        "groups": ["ИС 25/9-1П", "ИС 25/9-2П"],
        "disciplines": {
            "Архитектура аппаратных средств": ["Виды памяти", "Процессоры"],
            "Основы БД": ["Проектирование"],
        },
    }
    v1_path.write_text(json.dumps(v1_data, ensure_ascii=False), encoding="utf-8")
    cat_v1 = parse_legacy_catalog(v1_path)
    assert cat_v1.version == 1
    assert len(cat_v1.groups) == 2
    assert len(cat_v1.subjects) == 3  # 2 disciplines + fallback "Без предмета"
    assert len(cat_v1.topics) == 3
    assert cat_v1.unassigned_subject_id is not None

    # Test v2
    v2_path = tmp_path / "catalog_v2.json"
    g_id = str(uuid4())
    v2_data = {
        "version": 2,
        "groups": [{"id": g_id, "name": "ИС 25/9-1П"}],
        "subjects": [{"name": "Архитектура"}],
        "topics": [{"name": "Виды памяти"}],
    }
    v2_path.write_text(json.dumps(v2_data, ensure_ascii=False), encoding="utf-8")
    cat_v2 = parse_legacy_catalog(v2_path)
    assert cat_v2.version == 2
    assert len(cat_v2.groups) == 1
    assert str(cat_v2.groups[0].id) == g_id
    assert len(cat_v2.topics) == 1
    assert cat_v2.topics[0].subject_id == cat_v2.unassigned_subject_id


def test_ambiguous_flat_path_layout_requires_mapping(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    catalog_path = tmp_path / "catalog.json"
    g1 = str(uuid4())
    g2 = str(uuid4())
    s1 = str(uuid4())
    s2 = str(uuid4())
    t1 = str(uuid4())
    t2 = str(uuid4())
    t_uniq = str(uuid4())

    catalog_data = {
        "version": 3,
        "groups": [
            {"id": g1, "name": "Группа А"},
            {"id": g2, "name": "Группа А-Б"},
        ],
        "subjects": [
            {"id": s1, "name": "Б-В", "is_system": False},
            {"id": s2, "name": "В", "is_system": False},
        ],
        "topics": [
            {"id": t1, "name": "Тема", "subject_id": s1},
            {"id": t2, "name": "Тема", "subject_id": s2},
            {"id": t_uniq, "name": "УникальнаяТема", "subject_id": s2},
        ],
        "unassigned_subject_id": s1,
    }
    catalog_path.write_text(json.dumps(catalog_data, indent=2, ensure_ascii=False), encoding="utf-8")
    cat = parse_legacy_catalog(catalog_path)

    # Unit check: ambiguous flat path returns (None, None)
    ambig_loc = resolve_file_location("Группа А-Б-В-Тема/Иванов Иван 1.zip", cat)
    assert ambig_loc == (None, None)

    # Unit check: non-ambiguous flat path returns exactly the single match
    uniq_loc = resolve_file_location("Группа А-Б-В-УникальнаяТема/Иванов Иван 1.zip", cat)
    assert uniq_loc == (UUID(g2), UUID(t_uniq))

    # Test analyze and apply behavior
    storage = tmp_path / "storage"
    ambig_dir = storage / "Группа А-Б-В-Тема"
    ambig_dir.mkdir(parents=True)
    file_p = ambig_dir / "Иванов Иван 1.zip"
    file_p.write_bytes(b"content")

    mapping_out = tmp_path / "mapping.json"
    cmd_an = [
        sys.executable,
        "-m",
        "tools.migrate_v2",
        "analyze",
        "--catalog",
        str(catalog_path),
        "--storage",
        str(storage),
        "--output",
        str(mapping_out),
        "--allow-missing-legacy-db",
    ]
    res_an = subprocess.run(cmd_an, cwd=str(repo_root), capture_output=True, text=True, check=False)
    assert res_an.returncode == 0
    mapping_data = json.loads(mapping_out.read_text(encoding="utf-8"))

    rel_key = "Группа А-Б-В-Тема/Иванов Иван 1.zip"
    assert mapping_data["files"][rel_key]["group_id"] is None
    assert mapping_data["files"][rel_key]["topic_id"] is None
    assert any("ambiguous" in iss.lower() or "unresolvable" in iss.lower() for iss in mapping_data["issues"])

    # Attempting to apply without mapping fails
    target_db = tmp_path / "target.db"
    backup_root = tmp_path / "backups"
    cmd_ap = [
        sys.executable,
        "-m",
        "tools.migrate_v2",
        "apply",
        "--offline",
        "--catalog",
        str(catalog_path),
        "--storage",
        str(storage),
        "--target",
        str(target_db),
        "--backup-root",
        str(backup_root),
        "--mapping",
        str(mapping_out),
        "--allow-missing-legacy-db",
    ]
    res_ap = subprocess.run(cmd_ap, cwd=str(repo_root), capture_output=True, text=True, check=False)
    assert res_ap.returncode != 0

    # With explicit mapping override, apply succeeds
    mapping_data["files"][rel_key]["group_id"] = g1
    mapping_data["files"][rel_key]["topic_id"] = t1
    st_id = str(uuid4())
    mapping_data["students"] = [{"id": st_id, "group_id": g1, "display_name": "Иванов Иван"}]
    mapping_data["files"][rel_key]["student_id"] = st_id
    mapping_out.write_text(json.dumps(mapping_data, indent=2, ensure_ascii=False), encoding="utf-8")

    res_ap2 = subprocess.run(cmd_ap, cwd=str(repo_root), capture_output=True, text=True, check=False)
    assert res_ap2.returncode == 0
    assert target_db.is_file()
    validate(target_db)


def test_unparseable_filename_without_attempt_number(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    assert parse_submission_filename("Иванов Иван.zip") == (False, None, None, "Иванов Иван.zip")
    assert parse_submission_filename("Иванов Иван 0.zip") == (False, None, None, "Иванов Иван 0.zip")
    assert parse_submission_filename("+Иванов 1.zip") == (True, "Иванов", 1, "Иванов 1.zip")

    catalog_path = tmp_path / "catalog.json"
    g_id, _, _, _ = _create_v3_catalog(catalog_path)

    storage = tmp_path / "storage"
    work_dir = storage / "ИС 25／9-1П" / "Архитектура аппаратных средств" / "Виды памяти"
    work_dir.mkdir(parents=True)
    unparseable_file = work_dir / "Смирнов Алексей.zip"
    unparseable_file.write_bytes(b"content unparseable")

    mapping_out = tmp_path / "mapping.json"
    cmd_an = [
        sys.executable,
        "-m",
        "tools.migrate_v2",
        "analyze",
        "--catalog",
        str(catalog_path),
        "--storage",
        str(storage),
        "--output",
        str(mapping_out),
        "--allow-missing-legacy-db",
    ]
    res_an = subprocess.run(cmd_an, cwd=str(repo_root), capture_output=True, text=True, check=False)
    assert res_an.returncode == 0

    mapping_data = json.loads(mapping_out.read_text(encoding="utf-8"))
    assert any("unparseable submission filename, requires explicit mapping" in iss.lower() for iss in mapping_data["issues"])

    rel_key = "ИС 25／9-1П/Архитектура аппаратных средств/Виды памяти/Смирнов Алексей.zip"
    assert mapping_data["files"][rel_key]["attempt_number"] is None
    assert mapping_data["files"][rel_key]["student_id"] is None

    # Apply fails without explicit mapping override
    target_db = tmp_path / "target.db"
    backup_root = tmp_path / "backups"
    cmd_ap = [
        sys.executable,
        "-m",
        "tools.migrate_v2",
        "apply",
        "--offline",
        "--catalog",
        str(catalog_path),
        "--storage",
        str(storage),
        "--target",
        str(target_db),
        "--backup-root",
        str(backup_root),
        "--mapping",
        str(mapping_out),
        "--allow-missing-legacy-db",
    ]
    res_ap = subprocess.run(cmd_ap, cwd=str(repo_root), capture_output=True, text=True, check=False)
    assert res_ap.returncode != 0

    # With explicit override of student and attempt_number, apply succeeds
    st_id = str(uuid4())
    mapping_data["students"] = [{"id": st_id, "group_id": g_id, "display_name": "Смирнов Алексей"}]
    mapping_data["files"][rel_key]["student_id"] = st_id
    mapping_data["files"][rel_key]["attempt_number"] = 1
    mapping_out.write_text(json.dumps(mapping_data, indent=2, ensure_ascii=False), encoding="utf-8")

    res_ap2 = subprocess.run(cmd_ap, cwd=str(repo_root), capture_output=True, text=True, check=False)
    assert res_ap2.returncode == 0
    assert target_db.is_file()
    with connect(target_db) as conn:
        row = conn.execute("SELECT attempt_number, student_id FROM submissions").fetchone()
        assert row is not None
        assert row["attempt_number"] == 1
        assert row["student_id"] == st_id


def test_duplicate_student_names_require_operator_confirmation(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    catalog_path = tmp_path / "catalog.json"
    _create_v3_catalog(catalog_path)

    storage = tmp_path / "storage"
    work_dir = storage / "ИС 25／9-1П" / "Архитектура аппаратных средств" / "Виды памяти"
    work_dir.mkdir(parents=True)
    file1 = work_dir / "Петров Петр 1.zip"
    file1.write_bytes(b"content 1")
    file2 = work_dir / "Петров Петр 2.zip"
    file2.write_bytes(b"content 2")

    mapping_out = tmp_path / "mapping.json"
    cmd_an = [
        sys.executable,
        "-m",
        "tools.migrate_v2",
        "analyze",
        "--catalog",
        str(catalog_path),
        "--storage",
        str(storage),
        "--output",
        str(mapping_out),
        "--allow-missing-legacy-db",
    ]
    res_an = subprocess.run(cmd_an, cwd=str(repo_root), capture_output=True, text=True, check=False)
    assert res_an.returncode == 0

    mapping_data = json.loads(mapping_out.read_text(encoding="utf-8"))
    assert any(
        "duplicate student names detected in group" in iss.lower() and "requiring operator confirmation" in iss.lower()
        for iss in mapping_data["issues"]
    )

    rel1 = "ИС 25／9-1П/Архитектура аппаратных средств/Виды памяти/Петров Петр 1.zip"
    rel2 = "ИС 25／9-1П/Архитектура аппаратных средств/Виды памяти/Петров Петр 2.zip"
    assert mapping_data["files"][rel1]["student_id"] is None
    assert mapping_data["files"][rel2]["student_id"] is None

    # Apply fails because student_id is null
    target_db = tmp_path / "target.db"
    backup_root = tmp_path / "backups"
    cmd_ap = [
        sys.executable,
        "-m",
        "tools.migrate_v2",
        "apply",
        "--offline",
        "--catalog",
        str(catalog_path),
        "--storage",
        str(storage),
        "--target",
        str(target_db),
        "--backup-root",
        str(backup_root),
        "--mapping",
        str(mapping_out),
        "--allow-missing-legacy-db",
    ]
    res_ap = subprocess.run(cmd_ap, cwd=str(repo_root), capture_output=True, text=True, check=False)
    assert res_ap.returncode != 0
    assert not target_db.exists()

    # Operator explicitly assigns student_id in mapping
    st_id = mapping_data["students"][0]["id"]
    mapping_data["files"][rel1]["student_id"] = st_id
    mapping_data["files"][rel2]["student_id"] = st_id
    mapping_out.write_text(json.dumps(mapping_data, indent=2, ensure_ascii=False), encoding="utf-8")

    res_ap2 = subprocess.run(cmd_ap, cwd=str(repo_root), capture_output=True, text=True, check=False)
    assert res_ap2.returncode == 0
    assert target_db.is_file()
    with connect(target_db) as conn:
        rows = conn.execute("SELECT attempt_number, student_id FROM submissions ORDER BY attempt_number").fetchall()
        assert len(rows) == 2
        assert rows[0]["attempt_number"] == 1
        assert rows[1]["attempt_number"] == 2
        assert rows[0]["student_id"] == st_id
        assert rows[1]["student_id"] == st_id
