"""Audit test suite for homework-submissions SQLite v2 CLI roundtrip.

Covers real subprocess execution of tools.migrate_v2 commands:
  - Test 1: Standard mapping with empty entities: {} + explicit students/files (fails as expected with exit code 1).
  - Test 2: Full CLI roundtrip (analyze -> apply -> verify -> restore) with snapshot and state validation.
  - Test 3: Safety and re-run guarantees (target collision, destination collision, source immutability).
"""

from __future__ import annotations

import json
import sys

from app.database import connect
from tests.audit.conftest import AuditEnv
from tests.audit.support import (
    create_legacy_v3_catalog,
    create_synthetic_storage_files,
    create_test_mapping,
    read_db_state,
    run_cli,
    snapshot_tree,
)


def test_cli_standard_mapping_empty_entities_fails(audit_env: AuditEnv) -> None:
    """MIG-03-01: Running CLI apply with standard mapping (empty entities: {}) fails with exit code 1.

    Audit finding: In the standard contract, mapping.entities is empty by default, and apply must import
    groups/subjects/topics from catalog snapshot. In current tools.migrate_v2, apply only reads
    mapping['entities'], so empty entities results in sqlite3.IntegrityError: FOREIGN KEY constraint failed
    when inserting students.
    """
    cat_file = audit_env.source / "catalog.json"
    cat_file.write_text(json.dumps(create_legacy_v3_catalog()), encoding="utf-8")

    # Create dummy storage file with non_submission_reason
    (audit_env.storage / "note.txt").write_text("Dummy file", encoding="utf-8")

    # Standard mapping with empty entities: {}
    mapping_file = audit_env.source / "mapping.json"
    m = create_test_mapping(
        entities={"groups": [], "subjects": [], "topics": []},
        files={"note.txt": {"non_submission_reason": "Not a homework submission"}},
    )
    mapping_file.write_text(json.dumps(m, indent=2), encoding="utf-8")

    target_db = audit_env.target / "app_v2.sqlite3"

    proc = run_cli(
        [
            sys.executable,
            "-m",
            "tools.migrate_v2",
            "apply",
            "--offline",
            "--catalog",
            str(cat_file),
            "--storage",
            str(audit_env.storage),
            "--target",
            str(target_db),
            "--backup-root",
            str(audit_env.backup),
            "--mapping",
            str(mapping_file),
            "--allow-missing-legacy-db",
        ],
        cwd=audit_env.root,
    )

    # Invariant: CLI apply fails with non-zero exit code due to FK failure
    assert proc.returncode != 0
    assert not target_db.exists(), "Target database must not be published on migration failure"
    assert "IntegrityError" in proc.stderr or "FOREIGN KEY constraint failed" in proc.stderr


def test_cli_full_roundtrip_with_populated_entities(audit_env: AuditEnv) -> None:
    """REST-03-01: Full CLI roundtrip (analyze -> apply -> verify -> restore) with populated entities succeeds and verifies parity."""
    # 1. Setup source catalog, legacy DB, and storage files
    cat_file = audit_env.source / "catalog.json"
    cat_data = create_legacy_v3_catalog()
    cat_file.write_text(json.dumps(cat_data), encoding="utf-8")

    legacy_db = audit_env.source / "access.sqlite3"
    with connect(legacy_db) as conn:
        conn.execute(
            """
            CREATE TABLE group_access (
                group_id TEXT PRIMARY KEY,
                password_hash TEXT,
                is_open INTEGER,
                closes_at_utc TEXT,
                version INTEGER
            );
            """
        )
        conn.execute(
            """
            INSERT INTO group_access VALUES
            ('018d0000-0000-7000-8000-000000000001', 'hash_g1', 1, '2026-12-31T00:00:00Z', 1),
            ('018d0000-0000-7000-8000-000000000002', 'hash_g2', 1, '2026-12-31T00:00:00Z', 1);
            """
        )

    create_synthetic_storage_files(audit_env.storage)

    # Record snapshot of source storage and catalog bytes before any mutation
    source_storage_snap_before = snapshot_tree(audit_env.storage)
    cat_bytes_before = cat_file.read_bytes()
    legacy_db_state_before = read_db_state(legacy_db)

    # 2. CLI analyze
    mapping_file = audit_env.source / "mapping.json"
    proc_analyze = run_cli(
        [
            sys.executable,
            "-m",
            "tools.migrate_v2",
            "analyze",
            "--catalog",
            str(cat_file),
            "--storage",
            str(audit_env.storage),
            "--legacy-db",
            str(legacy_db),
            "--output",
            str(mapping_file),
        ],
        cwd=audit_env.root,
    )
    assert proc_analyze.returncode == 0
    assert mapping_file.is_file()

    # Populate non_submission_reason for extra non-homework files detected by analyze
    mapping_content = json.loads(mapping_file.read_text(encoding="utf-8"))
    for finfo in mapping_content.get("files", {}).values():
        if not finfo.get("student_id") and not finfo.get("non_submission_reason"):
            finfo["non_submission_reason"] = "Audit non-submission test artifact"
    mapping_file.write_text(json.dumps(mapping_content, indent=2), encoding="utf-8")

    # 3. CLI apply
    target_db = audit_env.target / "app_v2.sqlite3"
    proc_apply = run_cli(
        [
            sys.executable,
            "-m",
            "tools.migrate_v2",
            "apply",
            "--offline",
            "--catalog",
            str(cat_file),
            "--storage",
            str(audit_env.storage),
            "--legacy-db",
            str(legacy_db),
            "--target",
            str(target_db),
            "--backup-root",
            str(audit_env.backup),
            "--mapping",
            str(mapping_file),
        ],
        cwd=audit_env.root,
    )
    assert proc_apply.returncode == 0, f"apply failed: {proc_apply.stderr}"
    assert target_db.is_file()

    # Find created backup manifest
    manifest_candidates = list(audit_env.backup.glob("**/manifest.json"))
    assert len(manifest_candidates) == 1, "Expected exactly one backup manifest"
    manifest_path = manifest_candidates[0]

    # 4. CLI verify
    proc_verify = run_cli(
        [
            sys.executable,
            "-m",
            "tools.migrate_v2",
            "verify",
            "--database",
            str(target_db),
            "--storage",
            str(audit_env.storage),
            "--manifest",
            str(manifest_path),
        ],
        cwd=audit_env.root,
    )
    assert proc_verify.returncode == 0, f"verify failed: {proc_verify.stderr}"

    # 5. CLI restore to fresh destination
    dest_dir = audit_env.root / "restored_destination"
    proc_restore = run_cli(
        [
            sys.executable,
            "-m",
            "tools.migrate_v2",
            "restore",
            "--backup",
            str(manifest_path.parent),
            "--destination",
            str(dest_dir),
        ],
        cwd=audit_env.root,
    )
    assert proc_restore.returncode == 0, f"restore failed: {proc_restore.stderr}"

    # 6. Verify restored database vs source legacy database
    restored_legacy_db = dest_dir / "access.sqlite3"
    assert restored_legacy_db.is_file()
    restored_db_state = read_db_state(restored_legacy_db)
    assert restored_db_state["tables"] == legacy_db_state_before["tables"]
    assert restored_db_state["integrity_check"] == ["ok"]
    target_db_state = read_db_state(target_db)
    assert target_db_state["integrity_check"] == ["ok"]
    assert target_db_state["foreign_key_check"] == []
    # 7. Verify restored storage vs source storage (files, sizes, SHA-256)
    restored_storage_snap = snapshot_tree(dest_dir / "storage")
    source_files = {k: v["sha256"] for k, v in source_storage_snap_before.items() if v["type"] == "file"}
    restored_files = {k: v["sha256"] for k, v in restored_storage_snap.items() if v["type"] == "file"}
    assert source_files == restored_files

    # 8. Invariant: Source catalog and source storage are 100% byte-for-byte unmodified
    source_storage_snap_after = snapshot_tree(audit_env.storage)
    assert source_storage_snap_before == source_storage_snap_after
    assert cat_file.read_bytes() == cat_bytes_before


def test_cli_safety_and_rerun_guarantees(audit_env: AuditEnv) -> None:
    """PUB-01-01 & REST-02-01: Repeated apply on existing target fails without clobber; repeated restore on existing dest fails."""
    cat_file = audit_env.source / "catalog.json"
    cat_file.write_text(json.dumps(create_legacy_v3_catalog()), encoding="utf-8")
    create_synthetic_storage_files(audit_env.storage)

    # Produce mapping
    mapping_file = audit_env.source / "mapping.json"
    run_cli(
        [
            sys.executable,
            "-m",
            "tools.migrate_v2",
            "analyze",
            "--catalog",
            str(cat_file),
            "--storage",
            str(audit_env.storage),
            "--output",
            str(mapping_file),
            "--allow-missing-legacy-db",
        ],
        cwd=audit_env.root,
    )
    mapping_content = json.loads(mapping_file.read_text(encoding="utf-8"))
    for finfo in mapping_content.get("files", {}).values():
        if not finfo.get("student_id") and not finfo.get("non_submission_reason"):
            finfo["non_submission_reason"] = "Audit non-submission test artifact"
    mapping_file.write_text(json.dumps(mapping_content, indent=2), encoding="utf-8")

    # Apply migration
    target_db = audit_env.target / "app_v2.sqlite3"
    run_cli(
        [
            sys.executable,
            "-m",
            "tools.migrate_v2",
            "apply",
            "--offline",
            "--catalog",
            str(cat_file),
            "--storage",
            str(audit_env.storage),
            "--target",
            str(target_db),
            "--backup-root",
            str(audit_env.backup),
            "--mapping",
            str(mapping_file),
            "--allow-missing-legacy-db",
        ],
        cwd=audit_env.root,
    )
    assert target_db.is_file()
    target_stat_before = target_db.stat()

    # 1. Repeated apply: target_db already exists -> must fail and NOT overwrite target
    proc_repeat_apply = run_cli(
        [
            sys.executable,
            "-m",
            "tools.migrate_v2",
            "apply",
            "--offline",
            "--catalog",
            str(cat_file),
            "--storage",
            str(audit_env.storage),
            "--target",
            str(target_db),
            "--backup-root",
            str(audit_env.backup),
            "--mapping",
            str(mapping_file),
            "--allow-missing-legacy-db",
        ],
        cwd=audit_env.root,
    )
    assert proc_repeat_apply.returncode != 0
    assert "already exists" in proc_repeat_apply.stderr
    assert target_db.stat().st_mtime_ns == target_stat_before.st_mtime_ns

    # 2. Repeated restore: destination already exists -> must fail
    manifest_path = list(audit_env.backup.glob("**/manifest.json"))[0]
    dest_dir = audit_env.root / "destination"
    run_cli(
        [
            sys.executable,
            "-m",
            "tools.migrate_v2",
            "restore",
            "--backup",
            str(manifest_path.parent),
            "--destination",
            str(dest_dir),
        ],
        cwd=audit_env.root,
    )
    assert dest_dir.is_dir()

    # Second restore to same destination
    proc_repeat_restore = run_cli(
        [
            sys.executable,
            "-m",
            "tools.migrate_v2",
            "restore",
            "--backup",
            str(manifest_path.parent),
            "--destination",
            str(dest_dir),
        ],
        cwd=audit_env.root,
    )
    assert proc_repeat_restore.returncode != 0
    assert "already exists" in proc_repeat_restore.stderr
