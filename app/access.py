"""Group access control for SQLite v2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from werkzeug.security import check_password_hash, generate_password_hash

from .database import connect


@dataclass(frozen=True, slots=True)
class GroupAccess:
    group_id: UUID
    is_open: bool
    closes_at: datetime | None
    version: int

    def __init__(
        self,
        group_id: UUID | str,
        is_open: bool,
        closes_at: datetime | None,
        version: int,
    ):
        object.__setattr__(
            self,
            "group_id",
            group_id if isinstance(group_id, UUID) else UUID(str(group_id)),
        )
        object.__setattr__(self, "is_open", bool(is_open))
        object.__setattr__(self, "closes_at", closes_at)
        object.__setattr__(self, "version", int(version))


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        value = value.replace(tzinfo=local_tz)
    return value.astimezone(timezone.utc)


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class AccessStore:
    def __init__(self, db_path: Path | str, groups: object = None):
        self.db_path = Path(db_path)

    @staticmethod
    def _to_uuid(val: UUID | str) -> UUID:
        return val if isinstance(val, UUID) else UUID(str(val))

    def get(self, group_id: UUID | str) -> GroupAccess | None:
        u = self._to_uuid(group_id)
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT ga.group_id, ga.is_open, ga.closes_at_utc, ga.version
                FROM group_access ga
                JOIN groups g ON g.id = ga.group_id
                WHERE ga.group_id = ? AND g.archived_at IS NULL;
                """,
                (str(u),),
            ).fetchone()
            if not row:
                return None
            return GroupAccess(
                group_id=u,
                is_open=bool(row["is_open"]),
                closes_at=_parse_iso_utc(row["closes_at_utc"]),
                version=int(row["version"]),
            )

    def configure(
        self,
        group_id: UUID | str,
        is_open: bool | str | None,
        password: str | bool | None = None,
        closes_at: datetime | None = None,
    ) -> GroupAccess:
        # Handle parameter order (group_id, password, is_open, closes_at) vs (group_id, is_open, password, closes_at)
        if isinstance(is_open, (str, type(None))) and isinstance(password, bool):
            actual_password: str | None = is_open
            actual_is_open: bool = password
        else:
            actual_is_open = bool(is_open)
            actual_password = password if isinstance(password, str) else None

        u = self._to_uuid(group_id)
        utc_closes_at = _to_utc(closes_at)
        iso_closes_at = utc_closes_at.isoformat() if utc_closes_at else None

        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT password_hash, version FROM group_access WHERE group_id = ?;",
                (str(u),),
            ).fetchone()
            if not row:
                raise ValueError(f"Group access not found for {group_id}")

            if actual_password is None:
                new_hash = row["password_hash"]
            elif actual_password == "":
                new_hash = None
            else:
                new_hash = generate_password_hash(actual_password)

            new_version = int(row["version"]) + 1
            conn.execute(
                """
                UPDATE group_access
                SET is_open = ?, password_hash = ?, closes_at_utc = ?, version = ?
                WHERE group_id = ?;
                """,
                (
                    1 if actual_is_open else 0,
                    new_hash,
                    iso_closes_at,
                    new_version,
                    str(u),
                ),
            )

        return GroupAccess(
            group_id=u,
            is_open=actual_is_open,
            closes_at=utc_closes_at,
            version=new_version,
        )

    def authorize(
        self,
        group_id: UUID | str,
        password: str,
        now: datetime | None = None,
    ) -> tuple[bool, GroupAccess | None]:
        u = self._to_uuid(group_id)
        current_time = _to_utc(now) or datetime.now(timezone.utc)

        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT ga.password_hash, ga.is_open, ga.closes_at_utc, ga.version, g.archived_at
                FROM group_access ga
                JOIN groups g ON g.id = ga.group_id
                WHERE ga.group_id = ?;
                """,
                (str(u),),
            ).fetchone()
            if not row or row["archived_at"] is not None:
                return False, None

            if not row["is_open"]:
                return False, None

            if row["closes_at_utc"]:
                deadline = _parse_iso_utc(row["closes_at_utc"])
                if deadline and current_time >= deadline:
                    return False, None

            if not row["password_hash"] or not check_password_hash(
                row["password_hash"], password
            ):
                return False, None

            access = GroupAccess(
                group_id=u,
                is_open=bool(row["is_open"]),
                closes_at=_parse_iso_utc(row["closes_at_utc"]),
                version=int(row["version"]),
            )
            return True, access

    def permits(
        self,
        group_id: UUID | str,
        expected_version: int,
        now: datetime | None = None,
    ) -> bool:
        u = self._to_uuid(group_id)
        current_time = _to_utc(now) or datetime.now(timezone.utc)

        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT ga.is_open, ga.closes_at_utc, ga.version, g.archived_at
                FROM group_access ga
                JOIN groups g ON g.id = ga.group_id
                WHERE ga.group_id = ?;
                """,
                (str(u),),
            ).fetchone()
            if not row or row["archived_at"] is not None:
                return False

            if not row["is_open"]:
                return False

            if int(row["version"]) != expected_version:
                return False

            if row["closes_at_utc"]:
                deadline = _parse_iso_utc(row["closes_at_utc"])
                if deadline and current_time >= deadline:
                    return False

            return True
