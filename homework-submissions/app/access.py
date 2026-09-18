from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from werkzeug.security import check_password_hash, generate_password_hash


@dataclass(frozen=True, slots=True)
class GroupAccess:
    group: str
    is_open: bool
    closes_at: datetime | None
    version: int


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        value = value.replace(tzinfo=local_tz)
    return value.astimezone(UTC)


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(UTC)


class AccessStore:
    def __init__(self, path: Path, groups: Iterable[str]):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)

        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS group_access (
                    group_name TEXT PRIMARY KEY,
                    password_hash TEXT,
                    is_open INTEGER NOT NULL DEFAULT 0,
                    closes_at_utc TEXT,
                    version INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            for group in groups:
                conn.execute(
                    """
                    INSERT INTO group_access (group_name, password_hash, is_open, closes_at_utc, version)
                    VALUES (?, NULL, 0, NULL, 1)
                    ON CONFLICT(group_name) DO NOTHING
                    """,
                    (group,),
                )
            conn.commit()

        self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def configure(
        self,
        group: str,
        password: str | None,
        is_open: bool,
        closes_at: datetime | None,
    ) -> None:
        utc_closes_at = _to_utc(closes_at)
        serialized_deadline = utc_closes_at.isoformat() if utc_closes_at else None

        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT password_hash, version FROM group_access WHERE group_name = ?",
                (group,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"Unknown group: {group}")

            current_hash = row["password_hash"]
            next_hash = generate_password_hash(password) if password else current_hash
            next_version = int(row["version"]) + 1

            conn.execute(
                """
                UPDATE group_access
                SET password_hash = ?, is_open = ?, closes_at_utc = ?, version = ?
                WHERE group_name = ?
                """,
                (next_hash, 1 if is_open else 0, serialized_deadline, next_version, group),
            )
            conn.commit()

    def authorize(self, group: str, password: str, now: datetime | None = None) -> int | None:
        if not password:
            return None

        current_time = _to_utc(now) or datetime.now(UTC)

        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT password_hash, is_open, closes_at_utc, version
                FROM group_access
                WHERE group_name = ?
                """,
                (group,),
            )
            row = cursor.fetchone()

        if row is None or not row["is_open"] or not row["password_hash"]:
            return None

        deadline = _parse_iso_utc(row["closes_at_utc"])
        if deadline and current_time >= deadline:
            return None

        if not check_password_hash(row["password_hash"], password):
            return None

        return int(row["version"])

    def permits(self, group: str, version: int, now: datetime | None = None) -> bool:
        current_time = _to_utc(now) or datetime.now(UTC)

        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT is_open, closes_at_utc, version
                FROM group_access
                WHERE group_name = ?
                """,
                (group,),
            )
            row = cursor.fetchone()

        if row is None:
            return False

        if int(row["version"]) != version or not row["is_open"]:
            return False

        deadline = _parse_iso_utc(row["closes_at_utc"])
        if deadline and current_time >= deadline:
            return False

        return True

    def groups(self) -> tuple[GroupAccess, ...]:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT group_name, is_open, closes_at_utc, version
                FROM group_access
                ORDER BY group_name
                """
            )
            rows = cursor.fetchall()

        return tuple(
            GroupAccess(
                group=row["group_name"],
                is_open=bool(row["is_open"]),
                closes_at=_parse_iso_utc(row["closes_at_utc"]),
                version=int(row["version"]),
            )
            for row in rows
        )
