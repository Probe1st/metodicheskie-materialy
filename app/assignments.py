from __future__ import annotations

import re
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class Assignment:
    id: UUID
    group_name: str
    subject_id: UUID
    topic_id: UUID
    lesson_date: str  # YYYY-MM-DD
    pair_number: int  # 1..6
    created_at: datetime
    updated_at: datetime

class AssignmentConflictError(ValueError):
    """Raised when an assignment already exists for (group_name, lesson_date, pair_number)."""


class AssignmentNotFoundError(ValueError):
    """Raised when an assignment ID is not found."""


class AssignmentValidationError(ValueError):
    """Raised when input parameters fail domain validation."""


_DATE_REGEX = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$")


def _validate_lesson_date(lesson_date: str) -> str:
    if not isinstance(lesson_date, str):
        raise AssignmentValidationError(f"Invalid lesson_date type: {type(lesson_date)!r}. Expected str.")
    cleaned = lesson_date.strip()
    if not _DATE_REGEX.match(cleaned):
        raise AssignmentValidationError(f"Invalid lesson_date format: {lesson_date!r}. Expected YYYY-MM-DD.")
    try:
        datetime.strptime(cleaned, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as err:
        raise AssignmentValidationError(f"Invalid calendar date: {lesson_date!r}") from err
    return cleaned

def _validate_pair_number(pair_number: int) -> int:
    if isinstance(pair_number, bool) or not isinstance(pair_number, int) or pair_number < 1 or pair_number > 6:
        raise AssignmentValidationError(f"Invalid pair_number: {pair_number!r}. Must be an integer between 1 and 6.")
    return pair_number


class AssignmentStore:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)

        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS class_assignments (
                    id TEXT PRIMARY KEY,
                    group_name TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    lesson_date TEXT NOT NULL,
                    pair_number INTEGER NOT NULL CHECK (pair_number BETWEEN 1 AND 6),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(group_name, lesson_date, pair_number)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_assignments_group_subject
                ON class_assignments(group_name, subject_id, lesson_date)
                """
            )
            conn.commit()

        self.path.chmod(0o600)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            yield conn
        finally:
            conn.close()

    def _row_to_assignment(self, row: sqlite3.Row) -> Assignment:
        return Assignment(
            id=UUID(row["id"]),
            group_name=row["group_name"],
            subject_id=UUID(row["subject_id"]),
            topic_id=UUID(row["topic_id"]),
            lesson_date=row["lesson_date"],
            pair_number=int(row["pair_number"]),
            created_at=datetime.fromisoformat(row["created_at"]).astimezone(UTC),
            updated_at=datetime.fromisoformat(row["updated_at"]).astimezone(UTC),
        )

    def create(
        self,
        group_name: str,
        subject_id: UUID,
        topic_id: UUID,
        lesson_date: str,
        pair_number: int,
    ) -> Assignment:
        if not isinstance(group_name, str) or not group_name.strip():
            raise AssignmentValidationError("group_name cannot be empty.")
        cleaned_group = group_name.strip()
        valid_date = _validate_lesson_date(lesson_date)
        valid_pair = _validate_pair_number(pair_number)

        assignment_id = uuid4()
        now_iso = datetime.now(UTC).isoformat()

        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO class_assignments (
                        id, group_name, subject_id, topic_id, lesson_date, pair_number, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(assignment_id),
                        cleaned_group,
                        str(subject_id),
                        str(topic_id),
                        valid_date,
                        valid_pair,
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as err:
                raise AssignmentConflictError(
                    f"Пара {valid_pair} на дату {valid_date} для группы {cleaned_group} уже занята."
                ) from err

        item = self.get(assignment_id)
        assert item is not None
        return item

    def update(
        self,
        assignment_id: UUID,
        lesson_date: str,
        pair_number: int,
        topic_id: UUID,
    ) -> Assignment:
        valid_date = _validate_lesson_date(lesson_date)
        valid_pair = _validate_pair_number(pair_number)
        now_iso = datetime.now(UTC).isoformat()

        with self._connect() as conn:
            row = conn.execute(
                "SELECT group_name FROM class_assignments WHERE id = ?",
                (str(assignment_id),),
            ).fetchone()
            if row is None:
                raise AssignmentNotFoundError(f"Assignment {assignment_id} not found.")

            try:
                conn.execute(
                    """
                    UPDATE class_assignments
                    SET lesson_date = ?, pair_number = ?, topic_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        valid_date,
                        valid_pair,
                        str(topic_id),
                        now_iso,
                        str(assignment_id),
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as err:
                raise AssignmentConflictError(
                    f"Пара {valid_pair} на дату {valid_date} уже занята."
                ) from err

        item = self.get(assignment_id)
        assert item is not None
        return item

    def delete(self, assignment_id: UUID) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM class_assignments WHERE id = ?",
                (str(assignment_id),),
            )
            conn.commit()

    def get(self, assignment_id: UUID) -> Assignment | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM class_assignments WHERE id = ?",
                (str(assignment_id),),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_assignment(row)

    def list_for_group(self, group_name: str) -> tuple[Assignment, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM class_assignments
                WHERE group_name = ?
                ORDER BY lesson_date DESC, pair_number ASC
                """,
                (group_name.strip(),),
            ).fetchall()
            return tuple(self._row_to_assignment(r) for r in rows)

    def list_for_group_and_subject(
        self, group_name: str, subject_id: UUID
    ) -> tuple[Assignment, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM class_assignments
                WHERE group_name = ? AND subject_id = ?
                ORDER BY lesson_date DESC, pair_number ASC
                """,
                (group_name.strip(), str(subject_id)),
            ).fetchall()
            return tuple(self._row_to_assignment(r) for r in rows)

    def list_all(self) -> tuple[Assignment, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM class_assignments
                ORDER BY lesson_date DESC, pair_number ASC
                """
            ).fetchall()
            return tuple(self._row_to_assignment(r) for r in rows)

    def delete_subject(self, subject_id: UUID) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM class_assignments WHERE subject_id = ?",
                (str(subject_id),),
            )
            conn.commit()

    def delete_topic(self, topic_id: UUID) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM class_assignments WHERE topic_id = ?",
                (str(topic_id),),
            )
            conn.commit()

    def rename_group(self, old_name: str, new_name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE class_assignments SET group_name = ? WHERE group_name = ?",
                (new_name.strip(), old_name.strip()),
            )
            conn.commit()

    def delete_group(self, name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM class_assignments WHERE group_name = ?",
                (name.strip(),),
            )
            conn.commit()
