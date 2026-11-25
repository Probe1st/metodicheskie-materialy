"""Submissions and student domain logic for homework-submissions v2."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID, uuid4

from app.database import connect
from app.storage import (
    clean_extension,
    cleanup_pending_file,
    ensure_pending_dir,
    resolve_safe_path,
)


@dataclass(frozen=True, slots=True)
class Student:
    id: UUID
    group_id: UUID
    display_name: str
    archived_at: datetime | None


@dataclass(frozen=True, slots=True)
class Submission:
    id: UUID
    student_id: UUID
    group_id: UUID
    topic_id: UUID
    assignment_id: UUID | None
    attempt_number: int
    review_status: str
    reviewed_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SubmissionFile:
    id: UUID
    submission_id: UUID
    original_filename: str
    stored_path: str
    size_bytes: int
    sha256: str | None


@dataclass(frozen=True, slots=True)
class SubmissionDetail:
    id: UUID
    student_id: UUID
    group_id: UUID
    topic_id: UUID
    assignment_id: UUID | None
    attempt_number: int
    review_status: str
    reviewed_at: datetime | None
    created_at: datetime
    student_display_name: str
    group_name: str
    topic_name: str
    lesson_date: str | None
    pair_number: int | None
    original_filename: str
    stored_path: str
    size_bytes: int
    sha256: str | None

    @property
    def display_name(self) -> str:
        return self.student_display_name

    @property
    def student_name(self) -> str:
        return self.student_display_name

    @property
    def assignment_date(self) -> str | None:
        return self.lesson_date

    @property
    def assignment_pair(self) -> int | None:
        return self.pair_number


class SubmissionError(Exception):
    """Base exception for submission operations."""


class SubmissionAccessError(SubmissionError):
    """Raised for 403 authorization / deadline / version / group mismatch."""


class SubmissionValidationError(SubmissionError):
    """Raised for 400 validation issues."""


class SubmissionNotFoundError(SubmissionError):
    """Raised for 404 missing records."""


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


class SubmissionStore:
    def __init__(self, db_path: Path | str, storage_root: Path | str):
        self.db_path = Path(db_path).resolve()
        self.storage_root = Path(storage_root).resolve()

    def list_students(self, group_id: UUID | str, include_archived: bool = False) -> list[Student]:
        group_uuid = UUID(str(group_id))
        query = "SELECT id, group_id, display_name, archived_at FROM students WHERE group_id = ?"
        params: list[Any] = [str(group_uuid)]
        if not include_archived:
            query += " AND archived_at IS NULL"
        query += " ORDER BY display_name, id"

        with connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                Student(
                    id=UUID(row["id"]),
                    group_id=UUID(row["group_id"]),
                    display_name=row["display_name"],
                    archived_at=_parse_iso_utc(row["archived_at"]),
                )
                for row in rows
            ]

    def create_student(self, group_id: UUID | str, display_name: str) -> Student:
        if not isinstance(display_name, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise SubmissionValidationError("display_name must be a string")
        normalized = unicodedata.normalize("NFC", display_name.strip())
        if not normalized:
            raise SubmissionValidationError("display_name cannot be empty")

        group_uuid = UUID(str(group_id))
        with connect(self.db_path) as conn:
            group_row = conn.execute(
                "SELECT id, archived_at FROM groups WHERE id = ?",
                (str(group_uuid),),
            ).fetchone()
            if group_row is None:
                raise SubmissionNotFoundError(f"Group {group_uuid} not found")
            if group_row["archived_at"] is not None:
                raise SubmissionValidationError(f"Group {group_uuid} is archived")

            student_id = uuid4()
            conn.execute(
                "INSERT INTO students (id, group_id, display_name, archived_at) VALUES (?, ?, ?, NULL)",
                (str(student_id), str(group_uuid), normalized),
            )
            conn.commit()

        return Student(
            id=student_id,
            group_id=group_uuid,
            display_name=normalized,
            archived_at=None,
        )

    def submit(
        self,
        group_id: UUID | str,
        student_id: UUID | str,
        assignment_id: UUID | str,
        source: BinaryIO,
        original_filename: str,
        access_version: int,
    ) -> tuple[Submission, SubmissionFile]:
        group_uuid = UUID(str(group_id))
        student_uuid = UUID(str(student_id))
        assignment_uuid = UUID(str(assignment_id))

        # Phase 1: Pre-flight validation (fast reject before reading stream)
        with connect(self.db_path) as conn:
            # 1. Group exists and active
            group_row = conn.execute(
                "SELECT id, archived_at FROM groups WHERE id = ?",
                (str(group_uuid),),
            ).fetchone()
            if group_row is None:
                raise SubmissionNotFoundError(f"Group {group_uuid} not found")
            if group_row["archived_at"] is not None:
                raise SubmissionAccessError(f"Group {group_uuid} is archived")

            # 2. Group access open, not expired, version matches
            access_row = conn.execute(
                "SELECT is_open, closes_at_utc, version FROM group_access WHERE group_id = ?",
                (str(group_uuid),),
            ).fetchone()
            if access_row is None:
                raise SubmissionAccessError(f"Group access configuration not found for group {group_uuid}")
            if int(access_row["is_open"]) != 1:
                raise SubmissionAccessError(f"Submissions are closed for group {group_uuid}")
            if access_row["version"] != access_version:
                raise SubmissionAccessError(
                    f"Access version mismatch for group {group_uuid}: expected {access_version}, got {access_row['version']}"
                )
            if access_row["closes_at_utc"]:
                closes_at = _parse_iso_utc(access_row["closes_at_utc"])
                if closes_at and datetime.now(timezone.utc) > closes_at:
                    raise SubmissionAccessError(f"Submission deadline has passed ({access_row['closes_at_utc']})")

            # 3. Student exists, belongs to group, active
            student_row = conn.execute(
                "SELECT id, group_id, archived_at FROM students WHERE id = ?",
                (str(student_uuid),),
            ).fetchone()
            if student_row is None:
                raise SubmissionNotFoundError(f"Student {student_uuid} not found")
            if str(student_row["group_id"]) != str(group_uuid):
                raise SubmissionAccessError(f"Student {student_uuid} does not belong to group {group_uuid}")
            if student_row["archived_at"] is not None:
                raise SubmissionAccessError(f"Student {student_uuid} is archived")

            # 4. Assignment exists, belongs to group, active
            assignment_row = conn.execute(
                "SELECT id, group_id, topic_id, archived_at FROM class_assignments WHERE id = ?",
                (str(assignment_uuid),),
            ).fetchone()
            if assignment_row is None:
                raise SubmissionNotFoundError(f"Assignment {assignment_uuid} not found")
            if str(assignment_row["group_id"]) != str(group_uuid):
                raise SubmissionAccessError(f"Assignment {assignment_uuid} does not belong to group {group_uuid}")
            if assignment_row["archived_at"] is not None:
                raise SubmissionAccessError(f"Assignment {assignment_uuid} is archived")

            topic_uuid = UUID(assignment_row["topic_id"])

            # 5. Topic and subject active
            topic_subject_row = conn.execute(
                """
                SELECT t.id AS topic_id, t.archived_at AS topic_archived,
                       s.id AS subject_id, s.archived_at AS subject_archived
                FROM topics t
                JOIN subjects s ON s.id = t.subject_id
                WHERE t.id = ?
                """,
                (str(topic_uuid),),
            ).fetchone()
            if topic_subject_row is None or topic_subject_row["topic_archived"] is not None:
                raise SubmissionAccessError(f"Topic {topic_uuid} not found or is archived")
            if topic_subject_row["subject_archived"] is not None:
                raise SubmissionAccessError(f"Subject {topic_subject_row['subject_id']} is archived")

        # Phase 2: Stream file to temporary file in .pending
        pending_dir = ensure_pending_dir(self.storage_root)
        temp_file = pending_dir / f"{uuid4()}.tmp"
        hasher = hashlib.sha256()
        size_bytes = 0

        try:
            with temp_file.open("wb") as f:
                while True:
                    chunk = source.read(65536)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    size_bytes += len(chunk)
                    f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
            sha256_hex = hasher.hexdigest()
        except BaseException:
            cleanup_pending_file(temp_file)
            raise

        # Phase 3: Immediate transaction & atomic publish
        clean_orig_name = Path(original_filename).name or "submission"
        suffix = clean_extension(clean_orig_name)
        submission_id = uuid4()
        file_id = uuid4()
        relative_stored_path = f"{group_uuid}/{topic_uuid}/{submission_id}{suffix}"
        full_target_path = self.storage_root / str(group_uuid) / str(topic_uuid) / f"{submission_id}{suffix}"
        full_target_parent = full_target_path.parent

        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.isoformat()

        replaced = False
        committed = False

        try:
            with connect(self.db_path) as conn:
                conn.execute("BEGIN IMMEDIATE;")

                # Re-verify within transaction
                re_group = conn.execute(
                    """
                    SELECT g.archived_at AS group_archived,
                           a.is_open, a.closes_at_utc, a.version
                    FROM groups g
                    LEFT JOIN group_access a ON a.group_id = g.id
                    WHERE g.id = ?
                    """,
                    (str(group_uuid),),
                ).fetchone()
                if re_group is None or re_group["group_archived"] is not None:
                    raise SubmissionAccessError(f"Group {group_uuid} is not available or archived")
                if (
                    re_group["is_open"] is None
                    or int(re_group["is_open"]) != 1
                    or re_group["version"] != access_version
                ):
                    raise SubmissionAccessError(f"Group {group_uuid} access closed or version mismatch")
                if re_group["closes_at_utc"]:
                    closes = _parse_iso_utc(re_group["closes_at_utc"])
                    if closes and datetime.now(timezone.utc) > closes:
                        raise SubmissionAccessError("Submission deadline has passed")

                re_student = conn.execute(
                    "SELECT id, group_id, archived_at FROM students WHERE id = ?",
                    (str(student_uuid),),
                ).fetchone()
                if (
                    re_student is None
                    or str(re_student["group_id"]) != str(group_uuid)
                    or re_student["archived_at"] is not None
                ):
                    raise SubmissionAccessError("Student validation failed during publish")

                re_assignment = conn.execute(
                    "SELECT id, group_id, topic_id, archived_at FROM class_assignments WHERE id = ?",
                    (str(assignment_uuid),),
                ).fetchone()
                if (
                    re_assignment is None
                    or str(re_assignment["group_id"]) != str(group_uuid)
                    or re_assignment["archived_at"] is not None
                ):
                    raise SubmissionAccessError("Assignment validation failed during publish")

                # Calculate next attempt_number
                cur = conn.execute(
                    "SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM submissions WHERE student_id = ? AND topic_id = ?",
                    (str(student_uuid), str(topic_uuid)),
                )
                attempt_number = cur.fetchone()[0]

                # Ensure target parent dir exists
                full_target_parent.mkdir(parents=True, exist_ok=True)

                # Atomic replace and directory fsync
                os.replace(temp_file, full_target_path)
                replaced = True
                _fsync_dir(full_target_parent)

                # Insert DB records
                conn.execute(
                    """
                    INSERT INTO submissions (
                        id, student_id, group_id, topic_id, assignment_id,
                        attempt_number, review_status, reviewed_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(submission_id),
                        str(student_uuid),
                        str(group_uuid),
                        str(topic_uuid),
                        str(assignment_uuid),
                        attempt_number,
                        "pending",
                        None,
                        now_iso,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO submission_files (
                        id, submission_id, original_filename, stored_path, size_bytes, sha256
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(file_id),
                        str(submission_id),
                        clean_orig_name,
                        relative_stored_path,
                        size_bytes,
                        sha256_hex,
                    ),
                )

                try:
                    conn.commit()
                    committed = True
                except Exception:
                    # Commit failed with unknown outcome; inspect DB in new connection
                    if self._submission_exists(submission_id):
                        committed = True
                    raise

        except BaseException:
            if not committed:
                if replaced:
                    try:
                        full_target_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                cleanup_pending_file(temp_file)
            raise

        submission = Submission(
            id=submission_id,
            student_id=student_uuid,
            group_id=group_uuid,
            topic_id=topic_uuid,
            assignment_id=assignment_uuid,
            attempt_number=attempt_number,
            review_status="pending",
            reviewed_at=None,
            created_at=now_utc,
        )
        submission_file = SubmissionFile(
            id=file_id,
            submission_id=submission_id,
            original_filename=clean_orig_name,
            stored_path=relative_stored_path,
            size_bytes=size_bytes,
            sha256=sha256_hex,
        )
        return submission, submission_file

    def _submission_exists(self, submission_id: UUID) -> bool:
        try:
            with connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT 1 FROM submissions WHERE id = ?",
                    (str(submission_id),),
                ).fetchone()
                return row is not None
        except (sqlite3.Error, OSError):
            return False

    def count_submissions(
        self,
        group_id: UUID | str | None = None,
        topic_id: UUID | str | None = None,
    ) -> int:
        conditions: list[str] = []
        params: list[Any] = []

        if group_id is not None:
            conditions.append("group_id = ?")
            params.append(str(UUID(str(group_id))))
        if topic_id is not None:
            conditions.append("topic_id = ?")
            params.append(str(UUID(str(topic_id))))

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT COUNT(*) FROM submissions {where_clause};"

        with connect(self.db_path) as conn:
            return int(conn.execute(query, params).fetchone()[0])

    def list_submissions(
        self,
        group_id: UUID | str | None = None,
        topic_id: UUID | str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[SubmissionDetail]:
        conditions: list[str] = []
        params: list[Any] = []

        if group_id is not None:
            conditions.append("s.group_id = ?")
            params.append(str(UUID(str(group_id))))
        if topic_id is not None:
            conditions.append("s.topic_id = ?")
            params.append(str(UUID(str(topic_id))))

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        pagination_clause = ""
        if limit is not None:
            pagination_clause += " LIMIT ?"
            params.append(int(limit))
            if offset is not None:
                pagination_clause += " OFFSET ?"
                params.append(int(offset))

        query = f"""
        SELECT
            s.id, s.student_id, s.group_id, s.topic_id, s.assignment_id,
            s.attempt_number, s.review_status, s.reviewed_at, s.created_at,
            st.display_name AS student_display_name,
            g.name AS group_name,
            t.name AS topic_name,
            ca.lesson_date,
            ca.pair_number,
            sf.original_filename,
            sf.stored_path,
            sf.size_bytes,
            sf.sha256
        FROM submissions s
        JOIN students st ON st.id = s.student_id
        JOIN groups g ON g.id = s.group_id
        JOIN topics t ON t.id = s.topic_id
        LEFT JOIN class_assignments ca ON ca.id = s.assignment_id
        JOIN submission_files sf ON sf.submission_id = s.id
        {where_clause}
        ORDER BY s.created_at DESC, s.id DESC
        {pagination_clause}
        """

        with connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                SubmissionDetail(
                    id=UUID(row["id"]),
                    student_id=UUID(row["student_id"]),
                    group_id=UUID(row["group_id"]),
                    topic_id=UUID(row["topic_id"]),
                    assignment_id=UUID(row["assignment_id"]) if row["assignment_id"] else None,
                    attempt_number=int(row["attempt_number"]),
                    review_status=row["review_status"],
                    reviewed_at=_parse_iso_utc(row["reviewed_at"]),
                    created_at=_parse_iso_utc(row["created_at"]) or datetime.now(timezone.utc),
                    student_display_name=row["student_display_name"],
                    group_name=row["group_name"],
                    topic_name=row["topic_name"],
                    lesson_date=row["lesson_date"],
                    pair_number=int(row["pair_number"]) if row["pair_number"] is not None else None,
                    original_filename=row["original_filename"],
                    stored_path=row["stored_path"],
                    size_bytes=int(row["size_bytes"]),
                    sha256=row["sha256"],
                )
                for row in rows
            ]

    def get_file(self, submission_id: UUID | str) -> tuple[Path, str]:
        sub_uuid = UUID(str(submission_id))
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT sf.stored_path, sf.original_filename
                FROM submission_files sf
                WHERE sf.submission_id = ?
                """,
                (str(sub_uuid),),
            ).fetchone()

        if row is None:
            raise SubmissionNotFoundError(f"Submission {sub_uuid} not found")

        stored_path = row["stored_path"]
        original_filename = row["original_filename"]

        full_path = resolve_safe_path(self.storage_root, stored_path)
        if not full_path.is_file():
            raise FileNotFoundError(f"File not found on disk: {full_path}")

        return full_path, original_filename

    def set_review_status(self, submission_id: UUID | str, status: str) -> tuple[str, str | None]:
        if status not in ("pending", "reviewed"):
            raise SubmissionValidationError(f"Invalid review status: {status!r}. Must be 'pending' or 'reviewed'.")

        sub_uuid = UUID(str(submission_id))
        if status == "reviewed":
            reviewed_at = datetime.now(timezone.utc).isoformat()
        else:
            reviewed_at = None

        with connect(self.db_path) as conn:
            cur = conn.execute(
                """
                UPDATE submissions
                SET review_status = ?, reviewed_at = ?
                WHERE id = ?
                """,
                (status, reviewed_at, str(sub_uuid)),
            )
            if cur.rowcount == 0:
                raise SubmissionNotFoundError(f"Submission {sub_uuid} not found")
            conn.commit()

        return status, reviewed_at
