"""Audit test suite for crash recovery, fault injection, and filesystem safety.

Covers CRASH-01 through CRASH-03, and FS-01 through FS-02.
Validates targeted I/O failure injection, commit boundaries, hard crash recovery (os._exit),
storage path traversal, symlink escapes, and the read-only storage reconciliation tool.
"""

from __future__ import annotations

import errno
import io
import os
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Self
from uuid import uuid4

import pytest

import app.submissions
from app.storage import ensure_pending_dir, resolve_safe_path
from tests.audit.conftest import AuditEnv
from tests.audit.support import (
    A1_ID,
    G1_ACCESS_VERSION,
    G1_ID,
    STUDENT1_ID,
    assert_consistent,
    read_db_state,
    run_cli,
    snapshot_tree,
)
from tools.reconcile_storage import reconcile_storage

# ===========================================================================
# CRASH-01: Targeted I/O Fault Injection
# ===========================================================================

@pytest.mark.parametrize("case_id", ["CRASH-01-01"])
def test_crash_01_01_stream_read_io_error(case_id: str, audit_env: AuditEnv) -> None:
    """CRASH-01-01: Simulated EIO error while reading incoming file stream.

    Must catch error, unlink temporary file in .pending, and leave DB untouched.
    """
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()

    class FailingStream(io.RawIOBase):
        def readable(self) -> bool:
            return True

        def read(self, size: int = -1) -> bytes:
            raise OSError(errno.EIO, "Simulated EIO read error from network/disk")

    with pytest.raises(OSError, match="Simulated EIO read error"):
        sub_store.submit(
            group_id=G1_ID,
            student_id=STUDENT1_ID,
            assignment_id=A1_ID,
            source=FailingStream(),  # type: ignore[arg-type]
            original_filename="fault_stream.zip",
            access_version=G1_ACCESS_VERSION,
        )

    # Database untouched
    state = read_db_state(audit_env.db_path)
    assert state["tables"]["submissions"]["row_count"] == 0

    # No leftover temporary files in .pending
    pending_dir = audit_env.storage / ".pending"
    if pending_dir.exists():
        tmp_files = list(pending_dir.glob("*.tmp"))
        assert len(tmp_files) == 0, f"Leftover temp files found: {tmp_files}"


@pytest.mark.parametrize("case_id", ["CRASH-01-01"])
def test_crash_01_01_pending_fsync_enospc_error(
    case_id: str, audit_env: AuditEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CRASH-01-01: Simulated ENOSPC (disk full) during os.fsync on pending file."""
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()

    def faulty_fsync(fd: int) -> None:
        raise OSError(errno.ENOSPC, "Simulated ENOSPC: No space left on device")

    monkeypatch.setattr(os, "fsync", faulty_fsync)

    with pytest.raises(OSError, match="No space left on device"):
        sub_store.submit(
            group_id=G1_ID,
            student_id=STUDENT1_ID,
            assignment_id=A1_ID,
            source=io.BytesIO(b"Data waiting for fsync"),
            original_filename="fault_fsync.zip",
            access_version=G1_ACCESS_VERSION,
        )

    state = read_db_state(audit_env.db_path)
    assert state["tables"]["submissions"]["row_count"] == 0

    pending_dir = audit_env.storage / ".pending"
    if pending_dir.exists():
        assert len(list(pending_dir.glob("*.tmp"))) == 0


@pytest.mark.parametrize("case_id", ["CRASH-01-02"])
def test_crash_01_02_replace_eacces_error(
    case_id: str, audit_env: AuditEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CRASH-01-02: Simulated EACCES during os.replace to published location."""
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()

    def faulty_replace(src: Path | str, dst: Path | str) -> None:
        raise OSError(errno.EACCES, "Simulated EACCES: Permission denied on rename")

    monkeypatch.setattr(os, "replace", faulty_replace)

    with pytest.raises(OSError, match="Permission denied on rename"):
        sub_store.submit(
            group_id=G1_ID,
            student_id=STUDENT1_ID,
            assignment_id=A1_ID,
            source=io.BytesIO(b"Data before replace"),
            original_filename="fault_replace.zip",
            access_version=G1_ACCESS_VERSION,
        )

    state = read_db_state(audit_env.db_path)
    assert state["tables"]["submissions"]["row_count"] == 0
    assert_consistent(audit_env.db_path, audit_env.storage)


@pytest.mark.parametrize("case_id", ["CRASH-01-02"])
def test_crash_01_02_database_insert_failure_rolls_back_moved_file(
    case_id: str, audit_env: AuditEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CRASH-01-02: Failure on DB INSERT unlinks moved target file to prevent orphan files."""
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()

    real_connect = app.submissions.connect

    class FailingInsertWrapper:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn

        def __getattr__(self, name: str) -> Any:
            return getattr(self._conn, name)

        def __enter__(self) -> Self:
            self._conn.__enter__()
            return self

        def __exit__(self, *args: object) -> Any:
            return self._conn.__exit__(*args)

        def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
            if "INSERT INTO submission_files" in sql:
                raise sqlite3.OperationalError("Simulated DB error on submission_files INSERT")
            return self._conn.execute(sql, *args, **kwargs)

    @contextmanager
    def faulty_connect(path: Any) -> Any:
        with real_connect(path) as conn:
            yield FailingInsertWrapper(conn)

    monkeypatch.setattr(app.submissions, "connect", faulty_connect)

    with pytest.raises(sqlite3.OperationalError, match="Simulated DB error on submission_files INSERT"):
        sub_store.submit(
            group_id=G1_ID,
            student_id=STUDENT1_ID,
            assignment_id=A1_ID,
            source=io.BytesIO(b"Data before DB fail"),
            original_filename="fault_db_insert.zip",
            access_version=G1_ACCESS_VERSION,
        )

    # DB was rolled back
    state = read_db_state(audit_env.db_path)
    assert state["tables"]["submissions"]["row_count"] == 0
    assert state["tables"]["submission_files"]["row_count"] == 0

    # Target file must have been unlinked; no orphan file created
    assert_consistent(audit_env.db_path, audit_env.storage)


# ===========================================================================
# CRASH-02: Unknown / Post-Commit Outcome
# ===========================================================================

@pytest.mark.parametrize("case_id", ["CRASH-02-01"])
def test_crash_02_01_post_commit_error_preserves_file(
    case_id: str, audit_env: AuditEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CRASH-02-01: Crucial data-safety invariant: if commit succeeded, file MUST NOT be deleted."""
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()

    # We hook SubmissionStore._submission_exists or post-commit return to simulate failure after commit
    payload = b"Crucial submitted payload that must be preserved"

    real_connect = app.submissions.connect

    class PostCommitFailingWrapper:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn

        def __getattr__(self, name: str) -> Any:
            return getattr(self._conn, name)

        def __enter__(self) -> Self:
            self._conn.__enter__()
            return self

        def __exit__(self, *args: object) -> Any:
            return self._conn.__exit__(*args)

        def commit(self) -> None:
            self._conn.commit()
            raise RuntimeError("Simulated transient network/driver error right after commit")

    @contextmanager
    def faulty_commit_connect(path: Any) -> Any:
        with real_connect(path) as conn:
            yield PostCommitFailingWrapper(conn)

    monkeypatch.setattr(app.submissions, "connect", faulty_commit_connect)

    with pytest.raises(RuntimeError, match="right after commit"):
        sub_store.submit(
            group_id=G1_ID,
            student_id=STUDENT1_ID,
            assignment_id=A1_ID,
            source=io.BytesIO(payload),
            original_filename="committed_safe.zip",
            access_version=G1_ACCESS_VERSION,
        )

    # Invariant: Because commit succeeded and self._submission_exists confirmed it,
    # the committed file MUST remain in storage and DB must retain the rows!
    state = read_db_state(audit_env.db_path)
    assert state["tables"]["submissions"]["row_count"] == 1
    assert state["tables"]["submission_files"]["row_count"] == 1

    stored_path = state["tables"]["submission_files"]["rows"][0]["stored_path"]
    full_path = audit_env.storage / stored_path
    assert full_path.is_file(), "Committed file was deleted from storage! Data loss invariant violated."
    assert full_path.read_bytes() == payload

    assert_consistent(audit_env.db_path, audit_env.storage)


# ===========================================================================
# CRASH-03: Hard Crash Recovery (Subprocess with os._exit)
# ===========================================================================

@pytest.mark.parametrize(
    ("case_id", "checkpoint", "expected_code"),
    [
        ("CRASH-03-01", "after_pending_fsync", 101),
        ("CRASH-03-01", "after_replace", 102),
        ("CRASH-03-01", "between_inserts", 103),
        ("CRASH-03-01", "after_commit", 104),
    ],
)
def test_crash_03_01_hard_crash_recovery(
    case_id: str, checkpoint: str, expected_code: int, audit_env: AuditEnv
) -> None:
    """CRASH-03-01: Hard crash (os._exit) at different lifecycle stages.

    Verifies WAL recovery, DB integrity, and diagnosis by reconcile_storage.
    """
    audit_env.populate_canonical()

    hw_root = str(Path(__file__).resolve().parents[2])
    db_str = str(audit_env.db_path)
    storage_str = str(audit_env.storage)
    gid_str = str(G1_ID)
    stid_str = str(STUDENT1_ID)
    aid_str = str(A1_ID)

    script = f"""
import io
import os
import sqlite3
import sys

sys.path.insert(0, {hw_root!r})
from app.submissions import SubmissionStore
from uuid import UUID

sub_store = SubmissionStore({db_str!r}, {storage_str!r})
checkpoint = {checkpoint!r}

if checkpoint == "after_pending_fsync":
    real_fsync = os.fsync
    def hooked_fsync(fd):
        real_fsync(fd)
        os._exit(101)
    os.fsync = hooked_fsync

elif checkpoint == "after_replace":
    real_replace = os.replace
    def hooked_replace(src, dst):
        real_replace(src, dst)
        os._exit(102)
    os.replace = hooked_replace

elif checkpoint == "between_inserts":
    from contextlib import contextmanager
    import app.submissions
    real_conn_fn = app.submissions.connect
    class CrashBetweenWrapper:
        def __init__(self, conn):
            self._conn = conn
        def __getattr__(self, name):
            return getattr(self._conn, name)
        def __enter__(self):
            self._conn.__enter__()
            return self
        def __exit__(self, *args):
            return self._conn.__exit__(*args)
        def execute(self, sql, *args, **kwargs):
            res = self._conn.execute(sql, *args, **kwargs)
            if "INSERT INTO submissions (" in sql:
                os._exit(103)
            return res
    @contextmanager
    def wrapped_connect(path):
        with real_conn_fn(path) as conn:
            yield CrashBetweenWrapper(conn)
    app.submissions.connect = wrapped_connect
elif checkpoint == "after_commit":
    from contextlib import contextmanager
    import app.submissions
    real_conn_fn = app.submissions.connect
    class CrashCommitWrapper:
        def __init__(self, conn):
            self._conn = conn
        def __getattr__(self, name):
            return getattr(self._conn, name)
        def __enter__(self):
            self._conn.__enter__()
            return self
        def __exit__(self, *args):
            return self._conn.__exit__(*args)
        def commit(self):
            self._conn.commit()
            os._exit(104)
    @contextmanager
    def wrapped_connect(path):
        with real_conn_fn(path) as conn:
            yield CrashCommitWrapper(conn)
    app.submissions.connect = wrapped_connect

sub_store.submit(
    group_id=UUID({gid_str!r}),
    student_id=UUID({stid_str!r}),
    assignment_id=UUID({aid_str!r}),
    source=io.BytesIO(b"Hard crash payload test"),
    original_filename="crash_test.zip",
    access_version={G1_ACCESS_VERSION},
)
"""

    p = subprocess.Popen([sys.executable, "-c", script], cwd=str(audit_env.root))
    ret_code = p.wait()
    assert ret_code == expected_code, f"Expected checkpoint exit {expected_code}, got {ret_code}"

    # Reopen database and verify SQLite WAL recovery and PRAGMA integrity
    state = read_db_state(audit_env.db_path)
    assert state["integrity_check"] == ["ok"]
    assert state["foreign_key_check"] == []

    # Run reconcile_storage to audit consistency
    report = reconcile_storage(audit_env.db_path, audit_env.storage)

    if checkpoint == "after_pending_fsync":
        # Process died before rename; DB has 0 rows, .pending has leftover file
        assert state["tables"]["submissions"]["row_count"] == 0
        assert len(report["pending_files"]) >= 1

    elif checkpoint == "after_replace":
        # Process died after rename but before commit; file is orphan on disk
        assert state["tables"]["submissions"]["row_count"] == 0
        assert len(report["orphaned_files"]) >= 1

    elif checkpoint == "between_inserts":
        # Process died before commit; SQLite WAL auto-rolled back uncommitted first insert
        assert state["tables"]["submissions"]["row_count"] == 0
        assert len(report["orphaned_files"]) >= 1

    elif checkpoint == "after_commit":
        # Process committed before exit; DB and storage are completely consistent
        assert state["tables"]["submissions"]["row_count"] == 1
        assert report["status"] == "ok"
        assert_consistent(audit_env.db_path, audit_env.storage)


# ===========================================================================
# FS-01: Storage Path Traversal & Symlinks
# ===========================================================================

@pytest.mark.parametrize(
    ("case_id", "rel_path"),
    [
        ("FS-01-01", ".."),
        ("FS-01-01", "../sentinel.txt"),
        ("FS-01-01", "group/../../sentinel.txt"),
        ("FS-01-01", "group/topic/../../../../sentinel.txt"),
        ("FS-01-01", "/etc/passwd"),
        ("FS-01-01", ""),
        ("FS-01-01", "."),
    ],
)
def test_fs_01_01_resolve_safe_path_rejects_traversal(
    case_id: str, rel_path: str, audit_env: AuditEnv
) -> None:
    """FS-01-01: resolve_safe_path() strictly rejects traversal escaping storage root."""
    storage_root = audit_env.storage
    sentinel = audit_env.sentinel / "sentinel.txt"
    sentinel.write_text("TOP_SECRET_SENTINEL", encoding="utf-8")

    with pytest.raises(ValueError, match="Path escapes storage root"):
        resolve_safe_path(storage_root, rel_path)

    # Sentinel file intact
    assert sentinel.read_text(encoding="utf-8") == "TOP_SECRET_SENTINEL"


@pytest.mark.parametrize("case_id", ["FS-01-02"])
def test_fs_01_02_resolve_safe_path_rejects_symlink_escape(
    case_id: str, audit_env: AuditEnv
) -> None:
    """FS-01-02: resolve_safe_path() rejects symlinks that escape storage root."""
    storage_root = audit_env.storage
    sentinel = audit_env.sentinel / "secret_target.txt"
    sentinel.write_text("DO_NOT_LEAK", encoding="utf-8")

    # Symlink inside storage pointing outside
    escape_link = storage_root / "leak_symlink"
    os.symlink(str(sentinel), str(escape_link))

    with pytest.raises(ValueError, match="Path escapes storage root"):
        resolve_safe_path(storage_root, "leak_symlink")

    # Safe symlink pointing within storage is allowed
    internal_target = storage_root / "internal_file.txt"
    internal_target.write_text("SAFE_INTERNAL", encoding="utf-8")
    safe_link = storage_root / "safe_symlink"
    os.symlink(str(internal_target), str(safe_link))

    resolved = resolve_safe_path(storage_root, "safe_symlink")
    assert resolved == internal_target.resolve()


# ===========================================================================
# FS-02: Reconcile Storage Tool Verification
# ===========================================================================

@pytest.mark.parametrize("case_id", ["FS-02-01"])
def test_fs_02_01_reconcile_storage_clean_state(case_id: str, audit_env: AuditEnv) -> None:
    """FS-02-01: reconcile_storage returns status='ok' when DB and storage are in 1:1 agreement."""
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()

    # Create submission
    sub_store.submit(
        group_id=G1_ID,
        student_id=STUDENT1_ID,
        assignment_id=A1_ID,
        source=io.BytesIO(b"Valid submission content"),
        original_filename="valid.zip",
        access_version=G1_ACCESS_VERSION,
    )

    report = reconcile_storage(audit_env.db_path, audit_env.storage)
    assert report["status"] == "ok"
    assert report["missing_files"] == []
    assert report["checksum_mismatches"] == []
    assert report["size_mismatches"] == []
    assert report["orphaned_files"] == []
    assert report["pending_files"] == []


@pytest.mark.parametrize("case_id", ["FS-02-02"])
def test_fs_02_02_reconcile_storage_discrepancy_detection_and_readonly(
    case_id: str, audit_env: AuditEnv
) -> None:
    """FS-02-02: reconcile_storage detects discrepancies and is strictly read-only."""
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()

    payload = b"Sample submission data for reconciliation"
    _sub, _sfile = sub_store.submit(
        group_id=G1_ID,
        student_id=STUDENT1_ID,
        assignment_id=A1_ID,
        source=io.BytesIO(payload),
        original_filename="report.zip",
        access_version=G1_ACCESS_VERSION,
    )

    # 1. Create an orphaned regular file
    orphan_file = audit_env.storage / "orphan_upload.zip"
    orphan_file.write_bytes(b"Orphan bytes")

    # 2. Create leftover pending file
    pending_dir = ensure_pending_dir(audit_env.storage)
    pending_file = pending_dir / f"{uuid4()}.tmp"
    pending_file.write_bytes(b"Abandoned pending upload")

    # Capture filesystem and DB snapshot BEFORE running reconcile
    snap_before = snapshot_tree(audit_env.storage)
    db_before = read_db_state(audit_env.db_path)

    # Run library reconcile
    report = reconcile_storage(audit_env.db_path, audit_env.storage)
    assert report["status"] == "discrepancies_found"
    assert "orphan_upload.zip" in report["orphaned_files"]
    assert any(pending_file.name in p for p in report["pending_files"])

    # Run CLI reconcile tool
    proc = run_cli(
        [
            sys.executable,
            "-m",
            "tools.reconcile_storage",
            "--database",
            str(audit_env.db_path),
            "--storage",
            str(audit_env.storage),
        ],
        cwd=audit_env.root,
    )
    assert proc.returncode == 1, "CLI reconcile must exit non-zero when discrepancies exist"

    # Verify strictly read-only property: snapshots must be completely identical
    snap_after = snapshot_tree(audit_env.storage)
    db_after = read_db_state(audit_env.db_path)

    assert snap_before == snap_after, "reconcile_storage modified the storage filesystem!"
    assert db_before == db_after, "reconcile_storage modified the database!"
