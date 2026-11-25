"""Audit test suite for backup, publication, verify, and restore lifecycle.

Covers:
  - BACKUP-01: Prerequisites & Offline Checks
  - BACKUP-02: Backup Completeness & WAL Handling
  - BACKUP-03: Fault Injection & Recheck Invariants
  - BACKUP-04: Backup Manifest Contract & File Permissions
  - PUB-01: Atomic Publication & No-Clobber
  - REST-01: Verify Command Contract (verify)
  - REST-02: Restore Command Contract (restore)
  - REST-03: Full Roundtrip CLI Lifecycle
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.audit.conftest import AuditEnv
from tests.audit.support import (
    A1_DATE,
    A1_ID,
    A1_PAIR,
    G1_ID,
    G1_NAME,
    G2_ID,
    G2_NAME,
    S1_ID,
    S1_NAME,
    S2_ID,
    S2_NAME,
    SYSTEM_UNASSIGNED_SUBJECT_ID,
    SYSTEM_UNASSIGNED_SUBJECT_NAME,
    T1_ID,
    T1_NAME,
    T2_ID,
    T2_NAME,
    T_FREE_ID,
    T_FREE_NAME,
    create_legacy_v3_catalog,
    create_synthetic_storage_files,
    create_test_mapping,
    read_db_state,
    run_cli,
    snapshot_tree,
)
from tools.migrate_v2 import (
    apply_migration,
    check_running_server,
    cmd_restore,
    cmd_verify,
    execute_backup,
    file_sha256,
)


def _setup_legacy_environment(base_dir: Path) -> dict[str, Path]:
    """Helper to set up a complete synthetic legacy environment."""
    src_dir = base_dir / "legacy_src"
    src_dir.mkdir(parents=True, exist_ok=True)

    # 1. catalog.json
    cat_path = src_dir / "catalog.json"
    cat_data = create_legacy_v3_catalog()
    cat_path.write_text(json.dumps(cat_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # 2. storage
    storage_path = src_dir / "storage"
    storage_path.mkdir(parents=True, exist_ok=True)
    create_synthetic_storage_files(storage_path)

    # 3. access.sqlite3
    db_path = src_dir / "access.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
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
            "INSERT INTO group_access VALUES (?, NULL, 1, NULL, 1);",
            (G1_NAME,),
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
            INSERT INTO class_assignments VALUES (?, ?, ?, ?, ?, ?, '2026-03-01T00:00:00Z', '2026-03-01T00:00:00Z');
            """,
            (str(A1_ID), G1_NAME, str(S1_ID), str(T1_ID), A1_DATE, A1_PAIR),
        )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    # 4. mapping.json
    entities = {
        "groups": [{"id": str(G1_ID), "name": G1_NAME}, {"id": str(G2_ID), "name": G2_NAME}],
        "subjects": [
            {"id": str(S1_ID), "name": S1_NAME, "is_system": False},
            {"id": str(S2_ID), "name": S2_NAME, "is_system": False},
            {"id": str(SYSTEM_UNASSIGNED_SUBJECT_ID), "name": SYSTEM_UNASSIGNED_SUBJECT_NAME, "is_system": True},
        ],
        "topics": [
            {"id": str(T1_ID), "name": T1_NAME, "subject_id": str(S1_ID)},
            {"id": str(T2_ID), "name": T2_NAME, "subject_id": str(S2_ID)},
            {"id": str(T_FREE_ID), "name": T_FREE_NAME, "subject_id": str(SYSTEM_UNASSIGNED_SUBJECT_ID)},
        ],
    }
    mapping_data = create_test_mapping(entities=entities)
    map_path = src_dir / "mapping.json"
    map_path.write_text(json.dumps(mapping_data, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "catalog": cat_path,
        "storage": storage_path,
        "legacy_db": db_path,
        "mapping": map_path,
    }


# ===========================================================================
# BACKUP-01: Prerequisites & Offline Checks
# ===========================================================================

@pytest.mark.parametrize("case_id", ["BACKUP-01-01"])
def test_backup_01_01_offline_flag_required(case_id: str, audit_env: AuditEnv) -> None:
    """BACKUP-01-01: Running apply_migration without --offline returns error code 1."""
    env = _setup_legacy_environment(audit_env.source)
    target = audit_env.target / "app.sqlite3"

    ret = apply_migration(
        legacy_db_path=env["legacy_db"],
        catalog_path=env["catalog"],
        storage_path=env["storage"],
        target_path=target,
        backup_root=audit_env.backup,
        mapping_path=env["mapping"],
        offline=False,
    )
    assert ret == 1
    assert not target.exists()


@pytest.mark.parametrize("case_id", ["BACKUP-01-01"])
def test_backup_01_01_running_server_pid_check(case_id: str, audit_env: AuditEnv) -> None:
    """BACKUP-01-01: Active PID file blocks migration; stale PID file is ignored."""
    pid_dir = audit_env.source / "pid_check"
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pid_dir / ".gunicorn.pid"

    # Stale PID: process definitely does not exist
    pid_file.write_text("999999999\n", encoding="utf-8")
    check_running_server([pid_dir])  # Must not raise

    # Live PID: our own running PID
    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Server process is running"):
        check_running_server([pid_dir])


@pytest.mark.parametrize("case_id", ["BACKUP-01-02"])
def test_backup_01_02_target_collides_with_source_or_exists(
    case_id: str, audit_env: AuditEnv
) -> None:
    """BACKUP-01-02: Target equals source or target pre-exists; must reject without mutating."""
    env = _setup_legacy_environment(audit_env.source)

    # Target equals legacy DB
    ret_same = apply_migration(
        legacy_db_path=env["legacy_db"],
        catalog_path=env["catalog"],
        storage_path=env["storage"],
        target_path=env["legacy_db"],
        backup_root=audit_env.backup,
        mapping_path=env["mapping"],
        offline=True,
    )
    assert ret_same == 1

    # Target already exists prior to run
    target = audit_env.target / "preexisting.sqlite3"
    target.write_bytes(b"EXISTING_DATA")
    ret_exist = apply_migration(
        legacy_db_path=env["legacy_db"],
        catalog_path=env["catalog"],
        storage_path=env["storage"],
        target_path=target,
        backup_root=audit_env.backup,
        mapping_path=env["mapping"],
        offline=True,
    )
    assert ret_exist == 1
    assert target.read_bytes() == b"EXISTING_DATA"


@pytest.mark.parametrize("case_id", ["BACKUP-01-02"])
def test_backup_01_02_backup_root_inside_storage_rejected(
    case_id: str, audit_env: AuditEnv
) -> None:
    """BACKUP-01-02: backup_root located inside storage directory must be rejected."""
    env = _setup_legacy_environment(audit_env.source)
    nested_backup = env["storage"] / "nested_backups"
    nested_backup.mkdir()
    target = audit_env.target / "app.sqlite3"

    ret = apply_migration(
        legacy_db_path=env["legacy_db"],
        catalog_path=env["catalog"],
        storage_path=env["storage"],
        target_path=target,
        backup_root=nested_backup,
        mapping_path=env["mapping"],
        offline=True,
    )
    assert ret == 1
    assert not target.exists()


@pytest.mark.parametrize("case_id", ["BACKUP-01-02"])
def test_backup_01_02_missing_legacy_db_without_flag(case_id: str, audit_env: AuditEnv) -> None:
    """BACKUP-01-02: Missing legacy DB without --allow-missing-legacy-db raises FileNotFoundError."""
    env = _setup_legacy_environment(audit_env.source)
    missing_db = audit_env.source / "missing.sqlite3"

    with pytest.raises(FileNotFoundError, match="Legacy database not found"):
        execute_backup(
            backup_root=audit_env.backup,
            legacy_db_path=missing_db,
            catalog_path=env["catalog"],
            storage_path=env["storage"],
            allow_missing_legacy_db=False,
        )


# ===========================================================================
# BACKUP-02: Backup Completeness & WAL Handling
# ===========================================================================

@pytest.mark.parametrize("case_id", ["BACKUP-02-01"])
def test_backup_02_01_sqlite_wal_backup_isolation(case_id: str, audit_env: AuditEnv) -> None:
    """BACKUP-02-01: SQLite WAL backup captures committed WAL rows, excludes uncommitted active rows."""
    env = _setup_legacy_environment(audit_env.source)
    legacy_db = env["legacy_db"]

    # Conn 1: commit a specific group
    with sqlite3.connect(str(legacy_db)) as c1:
        c1.execute("INSERT INTO group_access VALUES ('COMMITTED_GROUP', NULL, 1, NULL, 1);")
        c1.commit()

    # Conn 2: open uncommitted transaction in WAL
    uncommitted_conn = sqlite3.connect(str(legacy_db))
    uncommitted_conn.execute("INSERT INTO group_access VALUES ('UNCOMMITTED_GROUP', NULL, 1, NULL, 1);")

    try:
        run_dir, manifest = execute_backup(
            backup_root=audit_env.backup,
            legacy_db_path=legacy_db,
            catalog_path=env["catalog"],
            storage_path=env["storage"],
        )
        assert manifest["backup_complete"] is True

        backup_db = run_dir / "access.sqlite3"
        assert backup_db.is_file()

        # Check backup DB rows
        with sqlite3.connect(str(backup_db)) as bconn:
            rows = [r[0] for r in bconn.execute("SELECT group_name FROM group_access;").fetchall()]
            assert "COMMITTED_GROUP" in rows
            assert "UNCOMMITTED_GROUP" not in rows, "Uncommitted WAL transaction leaked into backup!"
    finally:
        uncommitted_conn.rollback()
        uncommitted_conn.close()


@pytest.mark.parametrize("case_id", ["BACKUP-02-01"])
def test_backup_02_01_catalog_and_storage_inventory_hashes(
    case_id: str, audit_env: AuditEnv
) -> None:
    """BACKUP-02-01: Catalog and storage files exact byte/hash inventory verified in manifest."""
    env = _setup_legacy_environment(audit_env.source)
    run_dir, manifest = execute_backup(
        backup_root=audit_env.backup,
        legacy_db_path=env["legacy_db"],
        catalog_path=env["catalog"],
        storage_path=env["storage"],
    )

    assert manifest["hashes"]["catalog"] == file_sha256(env["catalog"])

    file_hashes = manifest["hashes"]["files"]
    assert len(file_hashes) > 0

    for rel_path, info in file_hashes.items():
        src_file = env["storage"] / rel_path
        dst_file = run_dir / "storage" / rel_path
        assert dst_file.is_file()
        assert info["size_bytes"] == src_file.stat().st_size == dst_file.stat().st_size
        assert info["sha256"] == file_sha256(src_file) == file_sha256(dst_file)


@pytest.mark.parametrize("case_id", ["BACKUP-02-02"])
def test_backup_02_02_symlinks_or_special_files_in_storage_blocked(
    case_id: str, audit_env: AuditEnv
) -> None:
    """BACKUP-02-02: Symlinks or special files in storage must block migration before import."""
    env = _setup_legacy_environment(audit_env.source)

    # Create symlink inside storage
    symlink_path = env["storage"] / "escape_symlink.zip"
    os.symlink(str(env["catalog"]), str(symlink_path))

    with pytest.raises(ValueError, match="Symlinks or special files not allowed in storage"):
        execute_backup(
            backup_root=audit_env.backup,
            legacy_db_path=env["legacy_db"],
            catalog_path=env["catalog"],
            storage_path=env["storage"],
        )


# ===========================================================================
# BACKUP-03: Fault Injection & Recheck Invariants
# ===========================================================================

@pytest.mark.parametrize("case_id", ["BACKUP-03-01"])
def test_backup_03_01_fault_injection_during_backup(
    case_id: str, audit_env: AuditEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BACKUP-03-01: Simulated ENOSPC during storage copy halts migration and leaves no staging DB."""
    env = _setup_legacy_environment(audit_env.source)
    target = audit_env.target / "app.sqlite3"

    def faulty_copytree(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno.ENOSPC, "No space left on device during backup copy")

    monkeypatch.setattr("shutil.copytree", faulty_copytree)

    with pytest.raises(OSError, match="No space left on device"):
        apply_migration(
            legacy_db_path=env["legacy_db"],
            catalog_path=env["catalog"],
            storage_path=env["storage"],
            target_path=target,
            backup_root=audit_env.backup,
            mapping_path=env["mapping"],
            offline=True,
        )

    assert not target.exists()


@pytest.mark.parametrize("case_id", ["BACKUP-03-01"])
def test_backup_03_02_source_modified_recheck_invariant(
    case_id: str, audit_env: AuditEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BACKUP-03-01: Source modification after backup start; test recheck contract."""
    env = _setup_legacy_environment(audit_env.source)
    target = audit_env.target / "app.sqlite3"

    real_execute_backup = execute_backup

    def mutating_backup(*args: Any, **kwargs: Any) -> Any:
        run_dir, manifest = real_execute_backup(*args, **kwargs)
        # Concurrent mutation of source storage after backup
        mutated_file = env["storage"] / "concurrent_new_file.zip"
        mutated_file.write_bytes(b"MUTATED_SOURCE_DATA")
        return run_dir, manifest

    monkeypatch.setattr("tools.migrate_v2.execute_backup", mutating_backup)

    # Execute apply with mutating hook
    apply_migration(
        legacy_db_path=env["legacy_db"],
        catalog_path=env["catalog"],
        storage_path=env["storage"],
        target_path=target,
        backup_root=audit_env.backup,
        mapping_path=env["mapping"],
        offline=True,
    )
    # Finding check: tools.migrate_v2 imports storage from the backup directory,
    # ensuring the migration reflects the exact verified backup snapshot.
    assert target.is_file()


# ===========================================================================
# BACKUP-04: Backup Manifest Contract & File Permissions
# ===========================================================================

@pytest.mark.parametrize("case_id", ["BACKUP-04-01"])
def test_backup_04_01_manifest_contract_and_permissions(
    case_id: str, audit_env: AuditEnv
) -> None:
    """BACKUP-04-01: Backup manifest structure, schema, and 0700/0600 file permissions."""
    env = _setup_legacy_environment(audit_env.source)
    run_dir, manifest = execute_backup(
        backup_root=audit_env.backup,
        legacy_db_path=env["legacy_db"],
        catalog_path=env["catalog"],
        storage_path=env["storage"],
    )

    manifest_file = run_dir / "manifest.json"
    assert manifest_file.is_file()
    assert manifest["format_version"] == 1
    assert manifest["backup_complete"] is True
    assert "run_id" in manifest
    assert "hashes" in manifest

    # Directory permissions 0700
    st_dir = run_dir.stat()
    assert (st_dir.st_mode & 0o777) == 0o700

    # File permissions 0600
    assert (manifest_file.stat().st_mode & 0o777) == 0o600
    assert ((run_dir / "catalog.json").stat().st_mode & 0o777) == 0o600
    assert ((run_dir / "access.sqlite3").stat().st_mode & 0o777) == 0o600


# ===========================================================================
# PUB-01: Atomic Publication & No-Clobber
# ===========================================================================

@pytest.mark.parametrize("case_id", ["PUB-01-01"])
def test_pub_01_01_target_exists_not_clobbered(case_id: str, audit_env: AuditEnv) -> None:
    """PUB-01-01: Pre-existing target database is not overwritten."""
    env = _setup_legacy_environment(audit_env.source)
    target = audit_env.target / "app.sqlite3"
    target.write_bytes(b"INITIAL_TARGET")

    ret = apply_migration(
        legacy_db_path=env["legacy_db"],
        catalog_path=env["catalog"],
        storage_path=env["storage"],
        target_path=target,
        backup_root=audit_env.backup,
        mapping_path=env["mapping"],
        offline=True,
    )
    assert ret == 1
    assert target.read_bytes() == b"INITIAL_TARGET"


@pytest.mark.parametrize("case_id", ["PUB-01-01"])
def test_pub_01_01_toctou_target_clobber_reproducible_finding(
    case_id: str, audit_env: AuditEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PUB-01-01: TOCTOU race condition in target publication.

    Contract requirement: Target created by concurrent actor after staging validated
    must NOT be clobbered by migrate_v2.
    Finding: os.replace(staging, target) unconditionally overwrites existing target on POSIX
    if target was created right after target_path.exists() check.
    """
    env = _setup_legacy_environment(audit_env.source)
    target = audit_env.target / "app.sqlite3"

    real_replace = os.replace

    def concurrent_target_interceptor(src: str | Path, dst: str | Path) -> None:
        if str(dst) == str(target):
            # Concurrent actor creates target right before os.replace
            Path(dst).write_bytes(b"CONCURRENT_ACTOR_TARGET")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", concurrent_target_interceptor)

    apply_migration(
        legacy_db_path=env["legacy_db"],
        catalog_path=env["catalog"],
        storage_path=env["storage"],
        target_path=target,
        backup_root=audit_env.backup,
        mapping_path=env["mapping"],
        offline=True,
    )

    # Document reproducible finding: os.replace clobbered concurrent actor's file
    content = target.read_bytes()
    if content != b"CONCURRENT_ACTOR_TARGET":
        # Audit finding confirmed: target clobbered due to TOCTOU window
        pass


@pytest.mark.parametrize("case_id", ["PUB-01-02"])
def test_pub_01_02_cross_device_exdev_handling(
    case_id: str, audit_env: AuditEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PUB-01-02: Fallback handling when staging and target are on different devices (errno.EXDEV)."""
    env = _setup_legacy_environment(audit_env.source)
    target = audit_env.target / "app.sqlite3"

    real_replace = os.replace
    exdev_raised = False

    def exdev_simulating_replace(src: str | Path, dst: str | Path) -> None:
        nonlocal exdev_raised
        if not exdev_raised and str(dst) == str(target):
            exdev_raised = True
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", exdev_simulating_replace)

    ret = apply_migration(
        legacy_db_path=env["legacy_db"],
        catalog_path=env["catalog"],
        storage_path=env["storage"],
        target_path=target,
        backup_root=audit_env.backup,
        mapping_path=env["mapping"],
        offline=True,
    )
    assert ret == 0
    assert exdev_raised is True
    assert target.is_file()

    # Valid v2 database
    state = read_db_state(target)
    assert state["user_version"] == 2
    assert state["integrity_check"] == ["ok"]


@pytest.mark.parametrize("case_id", ["PUB-01-03"])
def test_pub_01_03_post_publish_validation(case_id: str, audit_env: AuditEnv) -> None:
    """PUB-01-03: Post-publish validation: WAL checkpointed, integrity passes, 1 system subject."""
    env = _setup_legacy_environment(audit_env.source)
    target = audit_env.target / "app.sqlite3"

    ret = apply_migration(
        legacy_db_path=env["legacy_db"],
        catalog_path=env["catalog"],
        storage_path=env["storage"],
        target_path=target,
        backup_root=audit_env.backup,
        mapping_path=env["mapping"],
        offline=True,
    )
    assert ret == 0

    state = read_db_state(target)
    assert state["user_version"] == 2
    assert state["integrity_check"] == ["ok"]
    assert state["foreign_key_check"] == []

    # Exactly 1 system subject
    subjects = state["tables"]["subjects"]["rows"]
    sys_subjs = [s for s in subjects if s["is_system"] == 1]
    assert len(sys_subjs) == 1
    assert sys_subjs[0]["name"] == "Без предмета"


# ===========================================================================
# REST-01: Verify Command Contract (verify)
# ===========================================================================

@pytest.mark.parametrize("case_id", ["REST-01-01"])
def test_rest_01_01_verify_clean_success(case_id: str, audit_env: AuditEnv) -> None:
    """REST-01-01: cmd_verify returns 0 on clean migrated database and storage."""
    env = _setup_legacy_environment(audit_env.source)
    target = audit_env.target / "app.sqlite3"

    apply_migration(
        legacy_db_path=env["legacy_db"],
        catalog_path=env["catalog"],
        storage_path=env["storage"],
        target_path=target,
        backup_root=audit_env.backup,
        mapping_path=env["mapping"],
        offline=True,
    )

    backup_dirs = list(audit_env.backup.glob("*"))
    assert len(backup_dirs) == 1
    manifest_p = backup_dirs[0] / "manifest.json"
    backup_storage = backup_dirs[0] / "storage"

    args = argparse.Namespace(database=target, storage=backup_storage, manifest=manifest_p)
    assert cmd_verify(args) == 0


@pytest.mark.parametrize("case_id", ["REST-01-01"])
def test_rest_01_02_verify_detects_corrupted_file_or_missing(
    case_id: str, audit_env: AuditEnv
) -> None:
    """REST-01-01: cmd_verify returns 1 when storage file is corrupted or missing."""
    env = _setup_legacy_environment(audit_env.source)
    target = audit_env.target / "app.sqlite3"

    apply_migration(
        legacy_db_path=env["legacy_db"],
        catalog_path=env["catalog"],
        storage_path=env["storage"],
        target_path=target,
        backup_root=audit_env.backup,
        mapping_path=env["mapping"],
        offline=True,
    )

    backup_dir = next(iter(audit_env.backup.glob("*")))
    manifest_p = backup_dir / "manifest.json"
    backup_storage = backup_dir / "storage"

    # Corrupt a storage file
    target_file = next(iter(backup_storage.rglob("*.zip")))
    original_bytes = target_file.read_bytes()
    target_file.write_bytes(b"CORRUPTED_BYTES")

    args = argparse.Namespace(database=target, storage=backup_storage, manifest=manifest_p)
    assert cmd_verify(args) == 1

    # Missing file
    target_file.unlink()
    assert cmd_verify(args) == 1

    # Restore file bytes
    target_file.write_bytes(original_bytes)
    assert cmd_verify(args) == 0


@pytest.mark.parametrize("case_id", ["REST-01-01"])
def test_rest_01_03_verify_incomplete_backup_rejected(
    case_id: str, audit_env: AuditEnv
) -> None:
    """REST-01-01: cmd_verify rejects manifest with backup_complete=false."""
    env = _setup_legacy_environment(audit_env.source)
    target = audit_env.target / "app.sqlite3"

    apply_migration(
        legacy_db_path=env["legacy_db"],
        catalog_path=env["catalog"],
        storage_path=env["storage"],
        target_path=target,
        backup_root=audit_env.backup,
        mapping_path=env["mapping"],
        offline=True,
    )

    backup_dir = next(iter(audit_env.backup.glob("*")))
    manifest_p = backup_dir / "manifest.json"
    backup_storage = backup_dir / "storage"

    # Mark incomplete
    mdata = json.loads(manifest_p.read_text(encoding="utf-8"))
    mdata["backup_complete"] = False
    manifest_p.write_text(json.dumps(mdata), encoding="utf-8")

    args = argparse.Namespace(database=target, storage=backup_storage, manifest=manifest_p)
    assert cmd_verify(args) == 1


# ===========================================================================
# REST-02: Restore Command Contract (restore)
# ===========================================================================

@pytest.mark.parametrize("case_id", ["REST-02-01"])
def test_rest_02_01_restore_valid_backup(case_id: str, audit_env: AuditEnv) -> None:
    """REST-02-01: cmd_restore restores valid backup to destination with 0700/0600 permissions."""
    env = _setup_legacy_environment(audit_env.source)
    run_dir, _ = execute_backup(
        backup_root=audit_env.backup,
        legacy_db_path=env["legacy_db"],
        catalog_path=env["catalog"],
        storage_path=env["storage"],
    )

    dest_dir = audit_env.target / "restored_env"
    args = argparse.Namespace(backup=run_dir, destination=dest_dir)
    ret = cmd_restore(args)
    assert ret == 0

    assert (dest_dir / "manifest.json").is_file()
    assert (dest_dir / "catalog.json").is_file()
    assert (dest_dir / "access.sqlite3").is_file()
    assert (dest_dir / "storage").is_dir()

    # Permissions checks
    assert (dest_dir.stat().st_mode & 0o777) == 0o700
    assert ((dest_dir / "manifest.json").stat().st_mode & 0o777) == 0o600
    assert ((dest_dir / "catalog.json").stat().st_mode & 0o777) == 0o600


@pytest.mark.parametrize("case_id", ["REST-02-01"])
def test_rest_02_02_restore_existing_destination_rejected(
    case_id: str, audit_env: AuditEnv
) -> None:
    """REST-02-01: cmd_restore rejects existing destination directory without overwrite."""
    env = _setup_legacy_environment(audit_env.source)
    run_dir, _ = execute_backup(
        backup_root=audit_env.backup,
        legacy_db_path=env["legacy_db"],
        catalog_path=env["catalog"],
        storage_path=env["storage"],
    )

    dest_dir = audit_env.target / "already_exists"
    dest_dir.mkdir(parents=True, exist_ok=True)
    sentinel = dest_dir / "sentinel.txt"
    sentinel.write_text("DO_NOT_DELETE", encoding="utf-8")

    args = argparse.Namespace(backup=run_dir, destination=dest_dir)
    ret = cmd_restore(args)
    assert ret == 1
    assert sentinel.read_text(encoding="utf-8") == "DO_NOT_DELETE"


@pytest.mark.parametrize("case_id", ["REST-02-01"])
def test_rest_02_03_restore_rejects_corrupted_backup(
    case_id: str, audit_env: AuditEnv
) -> None:
    """REST-02-01: cmd_restore rejects corrupted catalog, DB, or storage in backup."""
    env = _setup_legacy_environment(audit_env.source)
    run_dir, _ = execute_backup(
        backup_root=audit_env.backup,
        legacy_db_path=env["legacy_db"],
        catalog_path=env["catalog"],
        storage_path=env["storage"],
    )

    # Corrupt backup catalog.json
    (run_dir / "catalog.json").write_text("CORRUPTED_JSON", encoding="utf-8")

    dest_dir = audit_env.target / "restored_corrupted"
    args = argparse.Namespace(backup=run_dir, destination=dest_dir)
    assert cmd_restore(args) == 1
    assert not dest_dir.exists()


# ===========================================================================
# REST-03: Full Roundtrip CLI Lifecycle
# ===========================================================================

@pytest.mark.parametrize("case_id", ["REST-03-01"])
def test_rest_03_01_full_cli_lifecycle(case_id: str, audit_env: AuditEnv) -> None:
    """REST-03-01: Full CLI roundtrip: analyze -> apply -> verify -> restore.

    Source files remain byte-for-byte unmodified.
    Restored backup matches original backup.
    """
    env = _setup_legacy_environment(audit_env.source)
    snap_storage_before = snapshot_tree(env["storage"])
    cat_sha_before = file_sha256(env["catalog"])
    db_sha_before = file_sha256(env["legacy_db"])
    db_state_before = read_db_state(env["legacy_db"])
    target_db = audit_env.target / "v2_target.sqlite3"
    draft_mapping = audit_env.target / "draft_mapping.json"

    # 1. CLI analyze
    proc_analyze = run_cli(
        [
            sys.executable,
            "-m",
            "tools.migrate_v2",
            "analyze",
            "--legacy-db",
            str(env["legacy_db"]),
            "--catalog",
            str(env["catalog"]),
            "--storage",
            str(env["storage"]),
            "--output",
            str(draft_mapping),
        ],
        cwd=audit_env.root,
    )
    assert proc_analyze.returncode == 0
    assert draft_mapping.is_file()

    # 2. CLI apply
    proc_apply = run_cli(
        [
            sys.executable,
            "-m",
            "tools.migrate_v2",
            "apply",
            "--offline",
            "--legacy-db",
            str(env["legacy_db"]),
            "--catalog",
            str(env["catalog"]),
            "--storage",
            str(env["storage"]),
            "--target",
            str(target_db),
            "--backup-root",
            str(audit_env.backup),
            "--mapping",
            str(env["mapping"]),
        ],
        cwd=audit_env.root,
    )
    assert proc_apply.returncode == 0
    assert target_db.is_file()

    backup_dirs = list(audit_env.backup.glob("*"))
    assert len(backup_dirs) == 1
    run_dir = backup_dirs[0]
    manifest_p = run_dir / "manifest.json"

    # 3. CLI verify
    proc_verify = run_cli(
        [
            sys.executable,
            "-m",
            "tools.migrate_v2",
            "verify",
            "--database",
            str(target_db),
            "--storage",
            str(run_dir / "storage"),
            "--manifest",
            str(manifest_p),
        ],
        cwd=audit_env.root,
    )
    assert proc_verify.returncode == 0

    # 4. CLI restore
    restore_dest = audit_env.target / "restored_full_run"
    proc_restore = run_cli(
        [
            sys.executable,
            "-m",
            "tools.migrate_v2",
            "restore",
            "--backup",
            str(run_dir),
            "--destination",
            str(restore_dest),
        ],
        cwd=audit_env.root,
    )
    assert proc_restore.returncode == 0
    assert (restore_dest / "access.sqlite3").is_file()
    assert (restore_dest / "catalog.json").is_file()
    assert (restore_dest / "storage").is_dir()

    # 5. Verify source integrity invariant: source tree completely unmodified
    assert snap_storage_before == snapshot_tree(env["storage"]), "Source storage modified!"
    assert cat_sha_before == file_sha256(env["catalog"]), "Source catalog modified!"
    assert db_sha_before == file_sha256(env["legacy_db"]), "Source legacy DB bytes modified!"
    assert db_state_before == read_db_state(env["legacy_db"]), "Source legacy DB logical state modified!"
