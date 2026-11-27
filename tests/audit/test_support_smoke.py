"""Smoke tests verifying that tests/audit/support.py and conftest.py work as specified."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from uuid import UUID

import pytest
from werkzeug.security import check_password_hash

from tests.audit.conftest import AuditEnv
from tests.audit.support import (
    A1_DATE,
    A1_ID,
    A1_PAIR,
    A2_DATE,
    A2_ID,
    A2_PAIR,
    COLLISION_TOPIC_NAME,
    G1_ACCESS_VERSION,
    G1_ID,
    G1_NAME,
    G1_PASSWORD,
    G1_PASSWORD_HASH,
    G2_ACCESS_VERSION,
    G2_ID,
    G2_NAME,
    G2_PASSWORD,
    G2_PASSWORD_HASH,
    S1_ID,
    S1_NAME,
    S2_ID,
    STUDENT1_ID,
    STUDENT1_NAME,
    STUDENT2_ID,
    STUDENT2_NAME,
    SYSTEM_UNASSIGNED_SUBJECT_ID,
    T1_ID,
    T1_NAME,
    T2_ID,
    T2_NAME,
    assert_consistent,
    canonical_uuid,
    canonical_uuid7,
    compute_sha256,
    create_legacy_v1_catalog,
    create_legacy_v2_catalog,
    create_legacy_v3_catalog,
    create_mapping_with_archived,
    create_synthetic_storage_files,
    create_test_mapping,
    read_db_state,
    run_cli,
    snapshot_tree,
)


def test_canonical_constants_and_uuids() -> None:
    """Verify canonical UUIDs, fixed constants, version 7 UUIDs, passwords, and name collisions."""
    # UUID generator
    u1 = canonical_uuid("seed1")
    u2 = canonical_uuid("seed1")
    u3 = canonical_uuid("seed2")
    assert isinstance(u1, UUID)
    assert u1 == u2
    assert u1 != u3

    # UUIDv7 generator
    u7 = canonical_uuid7(1)
    assert u7.version == 7
    assert u7.variant == "specified in RFC 4122"

    # Groups & Passwords & Version=7
    assert G1_ID.version == 7
    assert G2_ID.version == 7
    assert G1_ACCESS_VERSION == 7
    assert G2_ACCESS_VERSION == 7
    assert check_password_hash(G1_PASSWORD_HASH, G1_PASSWORD)
    assert check_password_hash(G2_PASSWORD_HASH, G2_PASSWORD)

    # Cross-subject topic name collision
    assert T1_NAME == T2_NAME == COLLISION_TOPIC_NAME
    assert S1_ID != S2_ID
    assert T1_ID != T2_ID

    # Assignments for same topic on different dates and pairs
    assert A1_ID != A2_ID
    assert A1_DATE != A2_DATE
    assert A1_PAIR == 1
    assert A2_PAIR == 2

    # Two students with identical name but different UUIDs
    assert STUDENT1_NAME == STUDENT2_NAME == "Иванов Иван"
    assert STUDENT1_ID != STUDENT2_ID


def test_snapshot_tree(tmp_path: Path) -> None:
    """Verify snapshot_tree collects accurate metadata without following symlinks."""
    d = tmp_path / "tree"
    d.mkdir()
    sub = d / "subdir"
    sub.mkdir()

    f1 = d / "file1.txt"
    f1.write_text("hello world", encoding="utf-8")

    f2 = sub / "file2.bin"
    f2.write_bytes(b"\x00\x01\x02")

    # Create symlink pointing to file1
    link = d / "link_to_f1"
    os.symlink("file1.txt", link)

    snap = snapshot_tree(d)

    assert "file1.txt" in snap
    assert snap["file1.txt"]["type"] == "file"
    assert snap["file1.txt"]["size"] == len("hello world")
    assert snap["file1.txt"]["sha256"] == compute_sha256(f1)
    assert "0" in snap["file1.txt"]["mode"]

    assert "subdir" in snap
    assert snap["subdir"]["type"] == "dir"
    assert snap["subdir"]["sha256"] is None

    assert "subdir/file2.bin" in snap
    assert snap["subdir/file2.bin"]["type"] == "file"
    assert snap["subdir/file2.bin"]["size"] == 3

    assert "link_to_f1" in snap
    assert snap["link_to_f1"]["type"] == "symlink"
    assert snap["link_to_f1"]["target"] == "file1.txt"


def test_read_db_state(audit_env: AuditEnv) -> None:
    """Verify read_db_state captures tables, schema, rows, and integrity checks."""
    db_path = audit_env.populate_canonical()
    state = read_db_state(db_path)

    assert state["user_version"] == 3
    assert state["integrity_check"] == ["ok"]
    assert state["quick_check"] == ["ok"]
    assert state["foreign_key_check"] == []

    tables = state["tables"]
    assert "groups" in tables
    assert "subjects" in tables
    assert "topics" in tables
    assert "class_assignments" in tables
    assert "students" in tables
    assert "submissions" in tables
    assert "submission_files" in tables

    # Check rows in groups
    group_names = {row["name"] for row in tables["groups"]["rows"]}
    assert G1_NAME in group_names
    assert G2_NAME in group_names

    # Check students in G1
    student_rows = tables["students"]["rows"]
    assert len(student_rows) == 2
    assert all(r["display_name"] == "Иванов Иван" for r in student_rows)


def test_run_cli_scrubbed_env(tmp_path: Path) -> None:
    """Verify run_cli scrubs dangerous inherited environment variables."""
    # Set inherited environment variable
    old_val = os.environ.get("DATABASE_PATH")
    try:
        os.environ["DATABASE_PATH"] = "/danger/path.sqlite3"
        proc = run_cli(
            [
                "python",
                "-c",
                "import os, sys; print(os.environ.get('DATABASE_PATH', 'SCRUBBED')); print('PYTHONPATH_SET:' + str(bool(os.environ.get('PYTHONPATH'))))",
            ],
            cwd=tmp_path,
        )
        assert proc.returncode == 0
        assert "SCRUBBED" in proc.stdout
        assert "PYTHONPATH_SET:True" in proc.stdout
    finally:
        if old_val is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_val


def test_assert_consistent(audit_env: AuditEnv) -> None:
    """Verify assert_consistent checks physical storage against DB records and detects anomalies."""
    db_path = audit_env.populate_canonical()
    storage_root = audit_env.storage

    # Create submission and file in DB
    sub_id = "018d0000-0000-7000-8000-000000000051"
    file_id = "018d0000-0000-7000-8000-000000000052"
    rel_path = f"{G1_NAME}/{T1_NAME}/submission.zip"
    full_path = storage_root / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(b"content-of-zip")

    size = len(b"content-of-zip")
    sha = compute_sha256(full_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, review_status, created_at)
            VALUES (?, ?, ?, ?, ?, 1, 'pending', '2026-03-01T12:00:00Z');
            """,
            (sub_id, str(STUDENT1_ID), str(G1_ID), str(T1_ID), str(A1_ID)),
        )
        conn.execute(
            """
            INSERT INTO submission_files (id, submission_id, original_filename, stored_path, size_bytes, sha256)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (file_id, sub_id, "submission.zip", rel_path, size, sha),
        )
        conn.commit()

    # Should pass cleanly
    assert_consistent(db_path, storage_root)

    # 1. Orphan file detection
    orphan = storage_root / "orphan.zip"
    orphan.write_bytes(b"orphan")
    with pytest.raises(AssertionError, match="Orphaned files found in storage"):
        assert_consistent(db_path, storage_root)
    orphan.unlink()

    # 2. Checksum / size mismatch detection
    full_path.write_bytes(b"altered-content")
    with pytest.raises(AssertionError, match="mismatch for"):
        assert_consistent(db_path, storage_root)

    # 3. Missing file detection
    full_path.unlink()
    with pytest.raises(AssertionError, match="Missing file in storage"):
        assert_consistent(db_path, storage_root)


def test_legacy_factories_and_synthetic_files(tmp_path: Path) -> None:
    """Verify legacy catalog factories and synthetic storage generator."""
    # Legacy v1
    v1 = create_legacy_v1_catalog()
    assert "disciplines" in v1
    assert "groups" in v1

    # Legacy v2
    v2 = create_legacy_v2_catalog()
    assert v2["version"] == 2
    assert "subjects" in v2

    # Legacy v3
    v3 = create_legacy_v3_catalog()
    assert v3["version"] == 3
    assert v3["unassigned_subject_id"] == str(SYSTEM_UNASSIGNED_SUBJECT_ID)

    # Synthetic storage files
    storage = tmp_path / "storage"
    files = create_synthetic_storage_files(storage)
    assert files["historical_zip_1"].is_file()
    assert files["historical_zip_2"].is_file()
    assert files["historical_zip_2"].name.startswith("+")
    assert files["empty_file"].stat().st_size == 0
    assert files["service_gitignore"].is_file()
    assert files["secret_zip"].name.startswith(".")


def test_mapping_generator() -> None:
    """Verify mapping generator produces valid v2 structure with default and archived entities."""
    mapping = create_test_mapping()
    assert mapping["version"] == 1
    assert mapping["entities"] == {"groups": [], "subjects": [], "topics": []}
    assert len(mapping["students"]) == 2
    assert len(mapping["files"]) > 0

    # With archived entities
    archived_map = create_mapping_with_archived(
        archived_groups=[{"id": canonical_uuid("arch-group"), "name": "OldGroup"}],
        archived_topics=[{"id": canonical_uuid("arch-topic"), "name": "OldTopic", "subject_id": S1_ID}],
    )
    assert any(g["name"] == "OldGroup" and g.get("archived_at") for g in archived_map["entities"]["groups"])
    assert any(t["name"] == "OldTopic" and t.get("archived_at") for t in archived_map["entities"]["topics"])


def test_audit_env_stores(audit_env: AuditEnv) -> None:
    """Verify audit_env initializes and provides all domain store instances."""
    db_path = audit_env.populate_canonical()

    cat_store = audit_env.get_catalog_store()
    access_store = audit_env.get_access_store()
    assignment_store = audit_env.get_assignment_store()
    sub_store = audit_env.get_submission_store()

    snapshot = cat_store.snapshot()
    assert any(g.name == G1_NAME for g in snapshot.groups)
    assert any(s.name == S1_NAME for s in snapshot.subjects)

    access_g1 = access_store.get(G1_ID)
    assert access_g1 is not None
    assert access_g1.is_open is True

    assignments = assignment_store.list_for_group(G1_ID)
    assert len(assignments) == 2

    assert sub_store.db_path == db_path
