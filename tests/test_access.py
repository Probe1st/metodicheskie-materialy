"""Tests for SQLite v2 group access control."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.access import AccessStore, GroupAccess
from app.database import initialize
from app.sqlite_catalog import SqliteCatalogStore


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "app_v2.sqlite3"
    initialize(path)
    return path


@pytest.fixture
def catalog(db_path: Path) -> SqliteCatalogStore:
    return SqliteCatalogStore(db_path)


@pytest.fixture
def access_store(db_path: Path) -> AccessStore:
    return AccessStore(db_path)


def test_group_access_dataclass_and_get(
    catalog: SqliteCatalogStore, access_store: AccessStore
):
    group = catalog.create_group("Группа-101")
    access = access_store.get(group.id)

    assert access is not None
    assert isinstance(access, GroupAccess)
    assert access.group_id == group.id
    assert access.is_open is False
    assert access.closes_at is None
    assert access.version == 1


def test_configure_and_authorize_lifecycle(
    catalog: SqliteCatalogStore, access_store: AccessStore
):
    group = catalog.create_group("Группа-102")
    future_deadline = datetime.now(timezone.utc) + timedelta(hours=2)

    # Configure access
    updated = access_store.configure(
        group_id=group.id,
        is_open=True,
        password="secret_pass",
        closes_at=future_deadline,
    )

    assert updated.is_open is True
    assert updated.version == 2
    assert updated.closes_at is not None

    # Failed authorization: wrong password
    ok, auth_access = access_store.authorize(group.id, "wrong_password")
    assert ok is False
    assert auth_access is None

    # Successful authorization
    ok, auth_access = access_store.authorize(group.id, "secret_pass")
    assert ok is True
    assert auth_access is not None
    assert auth_access.group_id == group.id
    assert auth_access.version == 2

    # Permits checks
    assert access_store.permits(group.id, expected_version=2) is True
    assert access_store.permits(group.id, expected_version=1) is False


def test_reconfiguring_invalidates_previous_session(
    catalog: SqliteCatalogStore, access_store: AccessStore
):
    group = catalog.create_group("Группа-103")
    access_store.configure(group.id, is_open=True, password="pass1")
    ok, auth1 = access_store.authorize(group.id, "pass1")
    assert ok is True
    assert auth1 is not None
    v1 = auth1.version

    # Reconfigure with new password
    access_store.configure(group.id, is_open=True, password="pass2")

    # Old version is invalidated
    assert access_store.permits(group.id, expected_version=v1) is False

    # Old password no longer works
    ok, _ = access_store.authorize(group.id, "pass1")
    assert ok is False

    # New password works
    ok, auth2 = access_store.authorize(group.id, "pass2")
    assert ok is True
    assert auth2 is not None
    assert auth2.version > v1
    assert access_store.permits(group.id, expected_version=auth2.version) is True


def test_closed_state_and_past_deadline(
    catalog: SqliteCatalogStore, access_store: AccessStore
):
    group = catalog.create_group("Группа-104")

    # Closed state
    access_store.configure(group.id, is_open=False, password="pass")
    ok, _ = access_store.authorize(group.id, "pass")
    assert ok is False
    assert access_store.permits(group.id, expected_version=2) is False

    # Expired deadline
    past_deadline = datetime.now(timezone.utc) - timedelta(minutes=10)
    access_store.configure(
        group.id, is_open=True, password="pass", closes_at=past_deadline
    )
    ok, _ = access_store.authorize(group.id, "pass")
    assert ok is False
    assert access_store.permits(group.id, expected_version=3) is False


def test_archived_group_revokes_access(
    catalog: SqliteCatalogStore, access_store: AccessStore
):
    group = catalog.create_group("Группа-105")
    access_store.configure(group.id, is_open=True, password="pass")
    ok, auth = access_store.authorize(group.id, "pass")
    assert ok is True
    assert auth is not None

    catalog.archive_group(group.id)

    # Cannot authorize or permit on archived group
    ok, _ = access_store.authorize(group.id, "pass")
    assert ok is False
    assert access_store.permits(group.id, expected_version=auth.version) is False
    assert access_store.get(group.id) is None


def test_renaming_group_invalidates_session(
    catalog: SqliteCatalogStore, access_store: AccessStore
):
    group = catalog.create_group("Группа-106")
    access_store.configure(group.id, is_open=True, password="pass")
    ok, auth = access_store.authorize(group.id, "pass")
    assert ok is True
    assert auth is not None
    old_version = auth.version

    # Rename group increments version
    catalog.rename_group(group.id, "Группа-106-Новая")

    # Old version is no longer permitted
    assert access_store.permits(group.id, expected_version=old_version) is False

    # Authorizing again retrieves updated version
    ok, new_auth = access_store.authorize(group.id, "pass")
    assert ok is True
    assert new_auth is not None
    assert new_auth.version > old_version
    assert access_store.permits(group.id, expected_version=new_auth.version) is True
