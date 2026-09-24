import stat
from datetime import UTC, datetime
from pathlib import Path

from app.access import AccessStore


def test_private_state_uses_owner_only_permissions(tmp_path: Path):
    database = tmp_path / ".private" / "access.sqlite3"
    AccessStore(database, ["Группа 1"])

    assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.stat().st_mode) == 0o600


def test_group_access_requires_open_state_password_and_future_deadline(tmp_path: Path):
    store = AccessStore(tmp_path / ".private" / "access.sqlite3", ["Группа 1"])
    deadline = datetime(2026, 9, 18, 15, tzinfo=UTC)
    store.configure("Группа 1", "1234", True, deadline)

    assert store.authorize("Группа 1", "wrong", datetime(2026, 9, 18, 14, tzinfo=UTC)) is None
    version = store.authorize("Группа 1", "1234", datetime(2026, 9, 18, 14, tzinfo=UTC))
    assert version is not None
    assert store.permits("Группа 1", version, datetime(2026, 9, 18, 14, tzinfo=UTC))
    assert not store.permits("Группа 1", version, deadline)


def test_reconfiguring_group_invalidates_previous_student_session(tmp_path: Path):
    store = AccessStore(tmp_path / ".private" / "access.sqlite3", ["Группа 1"])
    store.configure("Группа 1", "1234", True, None)
    version = store.authorize("Группа 1", "1234")
    store.configure("Группа 1", "5678", True, None)

    assert version is not None
    assert not store.permits("Группа 1", version)
    assert store.authorize("Группа 1", "1234") is None
    assert store.authorize("Группа 1", "5678") is not None


def test_renaming_group_preserves_access_but_invalidates_previous_session(tmp_path: Path):
    store = AccessStore(tmp_path / ".private" / "access.sqlite3", ["Группа 1"])
    store.configure("Группа 1", "1234", True, None)
    version = store.authorize("Группа 1", "1234")

    store.rename_group("Группа 1", "Группа 2")

    assert not store.permits("Группа 1", version)
    assert not store.permits("Группа 2", version)
    assert store.authorize("Группа 2", "1234") is not None


def test_deleting_group_removes_credentials(tmp_path: Path):
    store = AccessStore(tmp_path / ".private" / "access.sqlite3", ["Группа 1"])
    store.configure("Группа 1", "1234", True, None)
    store.delete_group("Группа 1")
    assert store.authorize("Группа 1", "1234") is None
