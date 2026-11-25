"""Class assignments management for SQLite v2."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar
from uuid import UUID

from .database import connect, validate_lesson_date, validate_pair_number

T = TypeVar("T")

_ASSIGNMENT_SELECT = """
SELECT ca.id, ca.group_id, ca.topic_id, t.subject_id, g.name AS group_name,
       ca.lesson_date, ca.pair_number, ca.created_at, ca.updated_at, ca.archived_at
FROM class_assignments ca
JOIN topics t ON t.id = ca.topic_id
JOIN groups g ON g.id = ca.group_id
"""


@dataclass(frozen=True, slots=True)
class Assignment:
    id: UUID
    group_id: UUID
    topic_id: UUID
    subject_id: UUID
    lesson_date: str  # YYYY-MM-DD
    pair_number: int  # 1..6
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    group_name: str | None = None


class AssignmentInUseError(ValueError):
    """Raised when attempting to change the topic_id of an assignment with submissions."""


class AssignmentConflictError(ValueError):
    """Raised when an active assignment already exists for (group_id, lesson_date, pair_number)."""


class AssignmentNotFoundError(ValueError):
    """Raised when an assignment ID is not found."""


class AssignmentValidationError(ValueError):
    """Raised when input parameters fail domain validation."""


def _parse_datetime(val: str | None) -> datetime | None:
    if not val:
        return None
    dt = datetime.fromisoformat(val)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class AssignmentStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    @staticmethod
    def _to_uuid(val: UUID | str) -> UUID:
        return val if isinstance(val, UUID) else UUID(str(val))

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Open a managed SQLite connection for multi-store atomic operations."""
        with connect(self.db_path) as conn:
            yield conn

    def _with_conn(
        self,
        conn: sqlite3.Connection | None,
        fn: Callable[[sqlite3.Connection], T],
    ) -> T:
        if conn is not None:
            return fn(conn)
        with connect(self.db_path) as local_conn:
            return fn(local_conn)

    @classmethod
    def _row_to_assignment(cls, row: sqlite3.Row) -> Assignment:
        keys = row.keys()
        return Assignment(
            id=UUID(row["id"]),
            group_id=UUID(row["group_id"]),
            topic_id=UUID(row["topic_id"]),
            subject_id=UUID(row["subject_id"]),
            lesson_date=row["lesson_date"],
            pair_number=int(row["pair_number"]),
            created_at=_parse_datetime(row["created_at"]) or datetime.now(timezone.utc),
            updated_at=_parse_datetime(row["updated_at"]) or datetime.now(timezone.utc),
            archived_at=_parse_datetime(row["archived_at"]),
            group_name=row["group_name"] if "group_name" in keys else None,
        )

    def create(
        self,
        group_id: UUID | str,
        topic_id: UUID | str,
        lesson_date_or_topic: str | UUID,
        pair_number_or_date: int | str,
        pair_number: int | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> Assignment:
        # Handle 5-arg signature (group_id, subject_id, topic_id, lesson_date, pair_number)
        if pair_number is not None:
            actual_topic_id = self._to_uuid(lesson_date_or_topic)
            actual_lesson_date = str(pair_number_or_date)
            actual_pair_number = pair_number
        else:
            actual_topic_id = self._to_uuid(topic_id)
            actual_lesson_date = str(lesson_date_or_topic)
            actual_pair_number = int(pair_number_or_date) if not isinstance(pair_number_or_date, bool) else -1

        try:
            valid_date = validate_lesson_date(actual_lesson_date)
        except (ValueError, TypeError) as e:
            raise AssignmentValidationError(str(e)) from e

        try:
            valid_pair = validate_pair_number(actual_pair_number)
        except (ValueError, TypeError) as e:
            raise AssignmentValidationError(str(e)) from e

        grp_uuid = self._to_uuid(group_id)
        now_iso = self._now_iso()
        new_id = uuid.uuid4()

        def _execute(c: sqlite3.Connection) -> Assignment:
            grp = c.execute(
                "SELECT id, name, archived_at FROM groups WHERE id = ?;",
                (str(grp_uuid),),
            ).fetchone()
            if not grp:
                raise AssignmentNotFoundError(f"Group {grp_uuid} not found")
            if grp["archived_at"] is not None:
                raise AssignmentValidationError(f"Group {grp_uuid} is archived")

            top = c.execute(
                "SELECT id, subject_id, archived_at FROM topics WHERE id = ?;",
                (str(actual_topic_id),),
            ).fetchone()
            if not top:
                raise AssignmentNotFoundError(f"Topic {actual_topic_id} not found")
            if top["archived_at"] is not None:
                raise AssignmentValidationError(f"Topic {actual_topic_id} is archived")

            subject_uuid = UUID(top["subject_id"])

            conflict = c.execute(
                """
                SELECT id FROM class_assignments
                WHERE group_id = ? AND lesson_date = ? AND pair_number = ? AND archived_at IS NULL;
                """,
                (str(grp_uuid), valid_date, valid_pair),
            ).fetchone()
            if conflict:
                raise AssignmentConflictError(
                    f"Active assignment already exists for ({grp_uuid}, {valid_date}, {valid_pair})"
                )

            c.execute(
                """
                INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at, archived_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL);
                """,
                (
                    str(new_id),
                    str(grp_uuid),
                    str(actual_topic_id),
                    valid_date,
                    valid_pair,
                    now_iso,
                    now_iso,
                ),
            )

            now_dt = _parse_datetime(now_iso) or datetime.now(timezone.utc)
            return Assignment(
                id=new_id,
                group_id=grp_uuid,
                topic_id=actual_topic_id,
                subject_id=subject_uuid,
                lesson_date=valid_date,
                pair_number=valid_pair,
                created_at=now_dt,
                updated_at=now_dt,
                archived_at=None,
                group_name=grp["name"],
            )

        return self._with_conn(conn, _execute)

    def update(
        self,
        assignment_id: UUID | str,
        lesson_date: str,
        pair_number: int,
        topic_id_or_subject: UUID | str,
        topic_id: UUID | str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> Assignment:
        # Support both (assignment_id, lesson_date, pair_number, topic_id)
        # and 5-arg (assignment_id, lesson_date, pair_number, subject_id, topic_id)
        if topic_id is not None:
            actual_topic_id = self._to_uuid(topic_id)
        else:
            actual_topic_id = self._to_uuid(topic_id_or_subject)

        try:
            valid_date = validate_lesson_date(lesson_date)
        except (ValueError, TypeError) as e:
            raise AssignmentValidationError(str(e)) from e

        try:
            valid_pair = validate_pair_number(pair_number)
        except (ValueError, TypeError) as e:
            raise AssignmentValidationError(str(e)) from e

        asgn_uuid = self._to_uuid(assignment_id)
        now_iso = self._now_iso()

        def _execute(c: sqlite3.Connection) -> Assignment:
            cur = c.execute(
                """
                SELECT ca.id, ca.group_id, ca.topic_id, ca.lesson_date, ca.pair_number,
                       ca.created_at, ca.updated_at, ca.archived_at,
                       t.subject_id, g.name AS group_name
                FROM class_assignments ca
                JOIN topics t ON t.id = ca.topic_id
                JOIN groups g ON g.id = ca.group_id
                WHERE ca.id = ?;
                """,
                (str(asgn_uuid),),
            ).fetchone()
            if not cur:
                raise AssignmentNotFoundError(f"Assignment {asgn_uuid} not found")

            # Check if topic_id changed
            if str(actual_topic_id) != cur["topic_id"]:
                sub_count = c.execute(
                    "SELECT COUNT(*) FROM submissions WHERE assignment_id = ?;",
                    (str(asgn_uuid),),
                ).fetchone()[0]
                if sub_count > 0:
                    raise AssignmentInUseError(
                        "Cannot change topic of assignment with submissions"
                    )

                new_top = c.execute(
                    "SELECT id, subject_id, archived_at FROM topics WHERE id = ?;",
                    (str(actual_topic_id),),
                ).fetchone()
                if not new_top:
                    raise AssignmentNotFoundError(f"Topic {actual_topic_id} not found")
                if new_top["archived_at"] is not None:
                    raise AssignmentValidationError(f"Topic {actual_topic_id} is archived")
                subject_uuid = UUID(new_top["subject_id"])
            else:
                subject_uuid = UUID(cur["subject_id"])

            group_id_str = cur["group_id"]
            conflict = c.execute(
                """
                SELECT id FROM class_assignments
                WHERE group_id = ? AND lesson_date = ? AND pair_number = ? AND id != ? AND archived_at IS NULL;
                """,
                (group_id_str, valid_date, valid_pair, str(asgn_uuid)),
            ).fetchone()
            if conflict:
                raise AssignmentConflictError(
                    f"Active assignment already exists for slot ({group_id_str}, {valid_date}, {valid_pair})"
                )

            c.execute(
                """
                UPDATE class_assignments
                SET lesson_date = ?, pair_number = ?, topic_id = ?, updated_at = ?
                WHERE id = ?;
                """,
                (
                    valid_date,
                    valid_pair,
                    str(actual_topic_id),
                    now_iso,
                    str(asgn_uuid),
                ),
            )

            return Assignment(
                id=asgn_uuid,
                group_id=UUID(group_id_str),
                topic_id=actual_topic_id,
                subject_id=subject_uuid,
                lesson_date=valid_date,
                pair_number=valid_pair,
                created_at=_parse_datetime(cur["created_at"]) or datetime.now(timezone.utc),
                updated_at=_parse_datetime(now_iso) or datetime.now(timezone.utc),
                archived_at=_parse_datetime(cur["archived_at"]),
                group_name=cur["group_name"],
            )

        return self._with_conn(conn, _execute)

    def archive(self, assignment_id: UUID | str, conn: sqlite3.Connection | None = None) -> None:
        u = self._to_uuid(assignment_id)

        def _execute(c: sqlite3.Connection) -> None:
            row = c.execute(
                "SELECT id, archived_at FROM class_assignments WHERE id = ?;",
                (str(u),),
            ).fetchone()
            if not row:
                raise AssignmentNotFoundError(f"Assignment {assignment_id} not found")

            if row["archived_at"] is None:
                now_iso = self._now_iso()
                c.execute(
                    "UPDATE class_assignments SET archived_at = ? WHERE id = ?;",
                    (now_iso, str(u)),
                )

        self._with_conn(conn, _execute)

    delete = archive

    def get(self, assignment_id: UUID | str, conn: sqlite3.Connection | None = None) -> Assignment | None:
        u = self._to_uuid(assignment_id)

        def _execute(c: sqlite3.Connection) -> Assignment | None:
            row = c.execute(
                f"{_ASSIGNMENT_SELECT} WHERE ca.id = ?;",
                (str(u),),
            ).fetchone()
            return self._row_to_assignment(row) if row else None

        return self._with_conn(conn, _execute)

    def get_ids_for_topic(
        self, topic_id: UUID | str, conn: sqlite3.Connection | None = None
    ) -> tuple[UUID, ...]:
        tu = self._to_uuid(topic_id)
        sql = "SELECT id FROM class_assignments WHERE topic_id = ? AND archived_at IS NULL;"
        return self._with_conn(
            conn, lambda c: tuple(UUID(r["id"]) for r in c.execute(sql, (str(tu),)).fetchall())
        )

    def get_ids_for_subject(
        self, subject_id: UUID | str, conn: sqlite3.Connection | None = None
    ) -> tuple[UUID, ...]:
        su = self._to_uuid(subject_id)
        sql = """
        SELECT ca.id
        FROM class_assignments ca
        JOIN topics t ON t.id = ca.topic_id
        WHERE t.subject_id = ? AND ca.archived_at IS NULL;
        """
        return self._with_conn(
            conn, lambda c: tuple(UUID(r["id"]) for r in c.execute(sql, (str(su),)).fetchall())
        )

    def get_ids_for_group(
        self, group_id: UUID | str, conn: sqlite3.Connection | None = None
    ) -> tuple[UUID, ...]:
        gu = self._to_uuid(group_id)
        sql = "SELECT id FROM class_assignments WHERE group_id = ? AND archived_at IS NULL;"
        return self._with_conn(
            conn, lambda c: tuple(UUID(r["id"]) for r in c.execute(sql, (str(gu),)).fetchall())
        )

    def list_for_group(self, group_id: UUID | str) -> list[Assignment]:
        u = self._to_uuid(group_id)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                {_ASSIGNMENT_SELECT}
                WHERE ca.group_id = ? AND ca.archived_at IS NULL
                ORDER BY ca.lesson_date ASC, ca.pair_number ASC;
                """,
                (str(u),),
            ).fetchall()
            return [self._row_to_assignment(r) for r in rows]

    def list_for_group_and_subject(
        self, group_id: UUID | str, subject_id: UUID | str
    ) -> list[Assignment]:
        gu = self._to_uuid(group_id)
        su = self._to_uuid(subject_id)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                {_ASSIGNMENT_SELECT}
                WHERE ca.group_id = ? AND t.subject_id = ? AND ca.archived_at IS NULL
                ORDER BY ca.lesson_date ASC, ca.pair_number ASC;
                """,
                (str(gu), str(su)),
            ).fetchall()
            return [self._row_to_assignment(r) for r in rows]

    def list_all(self) -> list[Assignment]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                {_ASSIGNMENT_SELECT}
                WHERE ca.archived_at IS NULL
                ORDER BY ca.lesson_date ASC, ca.pair_number ASC;
                """
            ).fetchall()
            return [self._row_to_assignment(r) for r in rows]

    def reassign_subject_for_topics(
        self,
        topic_ids: Sequence[UUID],
        new_subject_id: UUID,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """Compatibility helper: in SQLite v2 subject_id lives on topics, not class_assignments."""
        if not topic_ids:
            return 0
        str_ids = [str(uid) for uid in topic_ids]
        placeholders = ",".join("?" for _ in str_ids)
        sql = f"SELECT COUNT(*) FROM class_assignments WHERE topic_id IN ({placeholders});"
        return int(self._with_conn(conn, lambda c: c.execute(sql, str_ids).fetchone()[0]))
