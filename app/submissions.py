"""Submissions and student domain logic for homework-submissions v2."""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import unicodedata
from collections.abc import Sequence
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
from app.student_names import (
    StudentNameResult,
    _are_fuzzy_similar,
    normalize_student_name,
    student_match_key,
    student_sorted_tokens,
)


@dataclass(frozen=True, slots=True)
class Student:
    id: UUID
    group_id: UUID
    display_name: str
    archived_at: datetime | None


@dataclass(frozen=True, slots=True)
class StudentSummary:
    id: UUID
    group_id: UUID
    group_name: str
    display_name: str
    archived_at: datetime | None
    submissions_count: int


@dataclass(frozen=True, slots=True)
class MergeResult:
    target_student: Student
    merged_source_ids: tuple[UUID, ...]
    moved_submissions: int
    total_submissions: int


@dataclass(frozen=True, slots=True)
class SplitResult:
    source_student_id: UUID
    target_students: tuple[Student, ...]
    moved_submissions: int
    copied_submissions: int


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

    def create_student_with_info(
        self, group_id: UUID | str, display_name: str
    ) -> tuple[Student, StudentNameResult]:
        if not isinstance(display_name, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise SubmissionValidationError("display_name must be a string")
        try:
            name_result = normalize_student_name(display_name)
        except (ValueError, TypeError) as err:
            raise SubmissionValidationError(str(err)) from err

        normalized = name_result.display_name
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

        student = Student(
            id=student_id,
            group_id=group_uuid,
            display_name=normalized,
            archived_at=None,
        )
        return student, name_result

    def create_student(self, group_id: UUID | str, display_name: str) -> Student:
        student, _ = self.create_student_with_info(group_id, display_name)
        return student

    def get_student(self, student_id: UUID | str) -> Student | None:
        student_uuid = UUID(str(student_id))
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, group_id, display_name, archived_at FROM students WHERE id = ?",
                (str(student_uuid),),
            ).fetchone()
            if row is None:
                return None
            return Student(
                id=UUID(row["id"]),
                group_id=UUID(row["group_id"]),
                display_name=row["display_name"],
                archived_at=_parse_iso_utc(row["archived_at"]),
            )

    def list_students_with_stats(
        self,
        group_id: UUID | str | None = None,
        include_archived: bool = True,
    ) -> list[StudentSummary]:
        conditions: list[str] = []
        params: list[Any] = []
        if group_id is not None:
            conditions.append("st.group_id = ?")
            params.append(str(UUID(str(group_id))))
        if not include_archived:
            conditions.append("st.archived_at IS NULL")
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
        SELECT
            st.id, st.group_id, g.name AS group_name, st.display_name, st.archived_at,
            COUNT(s.id) AS submissions_count
        FROM students st
        JOIN groups g ON g.id = st.group_id
        LEFT JOIN submissions s ON s.student_id = st.id
        {where_clause}
        GROUP BY st.id, st.group_id, g.name, st.display_name, st.archived_at
        ORDER BY g.name, st.display_name, st.id
        """
        with connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                StudentSummary(
                    id=UUID(r["id"]),
                    group_id=UUID(r["group_id"]),
                    group_name=r["group_name"],
                    display_name=r["display_name"],
                    archived_at=_parse_iso_utc(r["archived_at"]),
                    submissions_count=int(r["submissions_count"]),
                )
                for r in rows
            ]

    def find_matching_students(
        self,
        group_id: UUID | str,
        display_name: str,
        include_archived: bool = False,
    ) -> tuple[list[Student], list[Student]]:
        """Find exact and fuzzy matching students in the same group."""
        try:
            norm = normalize_student_name(display_name)
        except (ValueError, TypeError):
            return [], []

        if norm.is_paired:
            return [], []

        target_key = student_match_key(norm.display_name)
        target_tokens = student_sorted_tokens(norm.display_name)
        students = self.list_students(group_id, include_archived=include_archived)

        exact: list[Student] = []
        fuzzy: list[Student] = []
        for st in students:
            try:
                st_norm = normalize_student_name(st.display_name)
                st_disp = st_norm.display_name
                st_paired = st_norm.is_paired
            except ValueError:
                st_disp = st.display_name
                st_paired = False

            if st_paired:
                continue

            st_key = student_match_key(st_disp)
            st_tokens = student_sorted_tokens(st_disp)
            if st_key == target_key or (len(target_tokens) >= 2 and st_tokens == target_tokens):
                exact.append(st)
                continue

            similar, _, _ = _are_fuzzy_similar(norm.display_name, st_disp)
            if similar:
                fuzzy.append(st)

        return exact, fuzzy

    def rename_student(
        self,
        student_id: UUID | str,
        new_name: str,
        *,
        normalize: bool = True,
    ) -> tuple[Student, StudentNameResult | None]:
        if not isinstance(new_name, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise SubmissionValidationError("display_name must be a string")

        name_result: StudentNameResult | None = None
        if normalize:
            try:
                name_result = normalize_student_name(new_name)
            except (ValueError, TypeError) as err:
                raise SubmissionValidationError(str(err)) from err
            cleaned = name_result.display_name
        else:
            cleaned = unicodedata.normalize("NFC", new_name.strip())
            if not cleaned:
                raise SubmissionValidationError("display_name cannot be empty")

        student_uuid = UUID(str(student_id))
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, group_id, display_name, archived_at FROM students WHERE id = ?",
                (str(student_uuid),),
            ).fetchone()
            if row is None:
                raise SubmissionNotFoundError(f"Student {student_uuid} not found")

            conn.execute(
                "UPDATE students SET display_name = ? WHERE id = ?",
                (cleaned, str(student_uuid)),
            )
            conn.commit()
            return (
                Student(
                    id=UUID(row["id"]),
                    group_id=UUID(row["group_id"]),
                    display_name=cleaned,
                    archived_at=_parse_iso_utc(row["archived_at"]),
                ),
                name_result,
            )

    def set_student_archived(self, student_id: UUID | str, archived: bool) -> Student:
        student_uuid = UUID(str(student_id))
        archived_at_iso = datetime.now(timezone.utc).isoformat() if archived else None
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, group_id, display_name, archived_at FROM students WHERE id = ?",
                (str(student_uuid),),
            ).fetchone()
            if row is None:
                raise SubmissionNotFoundError(f"Student {student_uuid} not found")

            if archived and row["archived_at"] is not None:
                archived_at_iso = row["archived_at"]

            conn.execute(
                "UPDATE students SET archived_at = ? WHERE id = ?",
                (archived_at_iso, str(student_uuid)),
            )
            conn.commit()
            return Student(
                id=UUID(row["id"]),
                group_id=UUID(row["group_id"]),
                display_name=row["display_name"],
                archived_at=_parse_iso_utc(archived_at_iso),
            )

    @staticmethod
    def _reassign_and_renumber_submissions(
        conn: sqlite3.Connection,
        target_student_id: str,
        source_student_ids: Sequence[str],
    ) -> int:
        """Move all submissions from source_student_ids to target_student_id, renumbering attempts per topic."""
        if not source_student_ids:
            return 0
        all_ids = [target_student_id, *source_student_ids]
        all_ph = ",".join("?" for _ in all_ids)
        src_ph = ",".join("?" for _ in source_student_ids)

        moved_count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM submissions WHERE student_id IN ({src_ph})",
                list(source_student_ids),
            ).fetchone()[0]
        )
        if moved_count == 0:
            return 0

        topic_rows = conn.execute(
            f"SELECT DISTINCT topic_id FROM submissions WHERE student_id IN ({src_ph})",
            list(source_student_ids),
        ).fetchall()

        for trow in topic_rows:
            topic_id = trow["topic_id"]
            subs = conn.execute(
                f"""
                SELECT id, attempt_number
                FROM submissions
                WHERE topic_id = ? AND student_id IN ({all_ph})
                ORDER BY created_at ASC, attempt_number ASC, id ASC
                """,
                [topic_id, *all_ids],
            ).fetchall()
            if not subs:
                continue

            offset = max(int(r["attempt_number"]) for r in subs) + 1000
            for idx, sub_row in enumerate(subs, start=1):
                conn.execute(
                    "UPDATE submissions SET student_id = ?, attempt_number = ? WHERE id = ?",
                    (target_student_id, offset + idx, sub_row["id"]),
                )
            for idx, sub_row in enumerate(subs, start=1):
                conn.execute(
                    "UPDATE submissions SET attempt_number = ? WHERE id = ?",
                    (idx, sub_row["id"]),
                )

        return moved_count

    def merge_students(
        self,
        group_id: UUID | str,
        target_student_id: UUID | str,
        source_student_ids: Sequence[UUID | str],
        target_display_name: str | None = None,
    ) -> MergeResult:
        """Merge source_student_ids into target_student_id within group_id atomically."""
        group_uuid = UUID(str(group_id))
        target_uuid = UUID(str(target_student_id))
        seen_sources: list[UUID] = []
        for sid in source_student_ids:
            u = UUID(str(sid))
            if u == target_uuid:
                raise SubmissionValidationError("target_student_id cannot be in source_student_ids")
            if u not in seen_sources:
                seen_sources.append(u)

        if not seen_sources:
            raise SubmissionValidationError("source_student_ids cannot be empty")

        normalized_target_name: str | None = None
        if target_display_name is not None:
            try:
                normalized_target_name = normalize_student_name(target_display_name).display_name
            except (ValueError, TypeError) as err:
                raise SubmissionValidationError(str(err)) from err

        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE;")

            grp_row = conn.execute("SELECT id FROM groups WHERE id = ?", (str(group_uuid),)).fetchone()
            if grp_row is None:
                raise SubmissionNotFoundError(f"Group {group_uuid} not found")

            all_uuids = [target_uuid, *seen_sources]
            ph = ",".join("?" for _ in all_uuids)
            rows = conn.execute(
                f"SELECT id, group_id, display_name, archived_at FROM students WHERE id IN ({ph})",
                [str(u) for u in all_uuids],
            ).fetchall()
            row_map = {r["id"]: r for r in rows}

            for u in all_uuids:
                r = row_map.get(str(u))
                if r is None:
                    raise SubmissionNotFoundError(f"Student {u} not found")
                if r["group_id"] != str(group_uuid):
                    raise SubmissionValidationError(
                        f"Student {u} belongs to group {r['group_id']}, expected {group_uuid}"
                    )

            target_row = row_map[str(target_uuid)]
            any_active = any(row_map[str(u)]["archived_at"] is None for u in all_uuids)
            final_archived = None if any_active else target_row["archived_at"]
            final_name = normalized_target_name or target_row["display_name"]

            source_strs = [str(u) for u in seen_sources]
            moved_count = self._reassign_and_renumber_submissions(conn, str(target_uuid), source_strs)

            conn.execute(
                "UPDATE students SET display_name = ?, archived_at = ? WHERE id = ?",
                (final_name, final_archived, str(target_uuid)),
            )

            src_ph = ",".join("?" for _ in source_strs)
            conn.execute(f"DELETE FROM students WHERE id IN ({src_ph})", source_strs)

            total_subs = int(
                conn.execute(
                    "SELECT COUNT(*) FROM submissions WHERE student_id = ?",
                    (str(target_uuid),),
                ).fetchone()[0]
            )
            conn.commit()

        return MergeResult(
            target_student=Student(
                id=target_uuid,
                group_id=group_uuid,
                display_name=final_name,
                archived_at=_parse_iso_utc(final_archived),
            ),
            merged_source_ids=tuple(seen_sources),
            moved_submissions=moved_count,
            total_submissions=total_subs,
        )

    def split_paired_student(
        self,
        group_id: UUID | str,
        source_student_id: UUID | str,
        targets: Sequence[tuple[UUID | str | None, str]],
    ) -> SplitResult:
        """Split a paired student record onto 1 or more individual students, preserving files."""
        group_uuid = UUID(str(group_id))
        source_uuid = UUID(str(source_student_id))
        if not targets:
            raise SubmissionValidationError("At least one target student is required")

        parsed_targets: list[tuple[UUID | None, str]] = []
        seen_keys: set[tuple[str, str]] = set()
        for raw_id, raw_name in targets:
            t_uuid = UUID(str(raw_id)) if raw_id else None
            try:
                norm_res = normalize_student_name(raw_name)
            except (ValueError, TypeError) as err:
                raise SubmissionValidationError(str(err)) from err
            if norm_res.is_paired:
                raise SubmissionValidationError(f"Target name cannot be paired: {raw_name!r}")
            dedup_key = (str(t_uuid) if t_uuid else "", student_match_key(norm_res.display_name))
            if dedup_key not in seen_keys:
                seen_keys.add(dedup_key)
                parsed_targets.append((t_uuid, norm_res.display_name))

        pending_dir = ensure_pending_dir(self.storage_root)
        staged_temp_files: list[Path] = []
        published_files: list[Path] = []
        committed = False

        try:
            with connect(self.db_path) as conn:
                conn.execute("BEGIN IMMEDIATE;")

                src_row = conn.execute(
                    "SELECT id, group_id, display_name, archived_at FROM students WHERE id = ?",
                    (str(source_uuid),),
                ).fetchone()
                if src_row is None:
                    raise SubmissionNotFoundError(f"Student {source_uuid} not found")
                if src_row["group_id"] != str(group_uuid):
                    raise SubmissionValidationError("Source student does not belong to target group")

                existing_rows = conn.execute(
                    "SELECT id, display_name, archived_at FROM students WHERE group_id = ? AND id != ?",
                    (str(group_uuid), str(source_uuid)),
                ).fetchall()

                resolved_targets: list[Student] = []
                for req_id, req_name in parsed_targets:
                    if req_id is not None:
                        if req_id == source_uuid:
                            raise SubmissionValidationError("Target student cannot be the paired source student")
                        found = next((r for r in existing_rows if r["id"] == str(req_id)), None)
                        if found is None:
                            raise SubmissionNotFoundError(f"Target student {req_id} not found in group {group_uuid}")
                        if found["archived_at"] is not None:
                            conn.execute("UPDATE students SET archived_at = NULL WHERE id = ?", (str(req_id),))
                        resolved_targets.append(
                            Student(
                                id=req_id,
                                group_id=group_uuid,
                                display_name=found["display_name"],
                                archived_at=None,
                            )
                        )
                    else:
                        req_key = student_match_key(req_name)
                        matched_row = next(
                            (
                                r
                                for r in existing_rows
                                if r["archived_at"] is None and student_match_key(r["display_name"]) == req_key
                            ),
                            None,
                        )
                        if matched_row is not None:
                            resolved_targets.append(
                                Student(
                                    id=UUID(matched_row["id"]),
                                    group_id=group_uuid,
                                    display_name=matched_row["display_name"],
                                    archived_at=None,
                                )
                            )
                        else:
                            new_id = uuid4()
                            conn.execute(
                                "INSERT INTO students (id, group_id, display_name, archived_at) VALUES (?, ?, ?, NULL)",
                                (str(new_id), str(group_uuid), req_name),
                            )
                            resolved_targets.append(
                                Student(
                                    id=new_id,
                                    group_id=group_uuid,
                                    display_name=req_name,
                                    archived_at=None,
                                )
                            )

                # Deduplicate resolved target UUIDs preserving order
                unique_targets: list[Student] = []
                seen_target_ids: set[UUID] = set()
                for st in resolved_targets:
                    if st.id not in seen_target_ids:
                        seen_target_ids.add(st.id)
                        unique_targets.append(st)

                # Fetch source submissions + files BEFORE moving them
                src_subs = conn.execute(
                    """
                    SELECT s.id, s.topic_id, s.assignment_id, s.attempt_number,
                           s.review_status, s.reviewed_at, s.created_at,
                           sf.original_filename, sf.stored_path, sf.size_bytes, sf.sha256
                    FROM submissions s
                    JOIN submission_files sf ON sf.submission_id = s.id
                    WHERE s.student_id = ?
                    ORDER BY s.created_at ASC, s.attempt_number ASC, s.id ASC
                    """,
                    (str(source_uuid),),
                ).fetchall()

                copied_count = 0
                # Clone submissions + files for targets 2..N first
                for extra_target in unique_targets[1:]:
                    for sub_row in src_subs:
                        src_file_path = resolve_safe_path(self.storage_root, sub_row["stored_path"])
                        if not src_file_path.is_file():
                            raise FileNotFoundError(f"Source file missing on disk: {src_file_path}")

                        new_sub_id = uuid4()
                        new_file_id = uuid4()
                        suffix = clean_extension(sub_row["original_filename"])
                        new_rel_path = f"{group_uuid}/{sub_row['topic_id']}/{new_sub_id}{suffix}"
                        full_target_path = self.storage_root / str(group_uuid) / str(sub_row["topic_id"]) / f"{new_sub_id}{suffix}"
                        full_target_path.parent.mkdir(parents=True, exist_ok=True)

                        temp_file = pending_dir / f"{uuid4()}.tmp"
                        staged_temp_files.append(temp_file)
                        shutil.copyfile(src_file_path, temp_file)
                        with temp_file.open("rb") as temp_fh:
                            os.fsync(temp_fh.fileno())
                        os.replace(temp_file, full_target_path)
                        published_files.append(full_target_path)
                        _fsync_dir(full_target_path.parent)

                        next_attempt = int(
                            conn.execute(
                                "SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM submissions WHERE student_id = ? AND topic_id = ?",
                                (str(extra_target.id), sub_row["topic_id"]),
                            ).fetchone()[0]
                        )

                        conn.execute(
                            """
                            INSERT INTO submissions (
                                id, student_id, group_id, topic_id, assignment_id,
                                attempt_number, review_status, reviewed_at, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                str(new_sub_id),
                                str(extra_target.id),
                                str(group_uuid),
                                sub_row["topic_id"],
                                sub_row["assignment_id"],
                                next_attempt,
                                sub_row["review_status"],
                                sub_row["reviewed_at"],
                                sub_row["created_at"],
                            ),
                        )
                        conn.execute(
                            """
                            INSERT INTO submission_files (
                                id, submission_id, original_filename, stored_path, size_bytes, sha256
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                str(new_file_id),
                                str(new_sub_id),
                                sub_row["original_filename"],
                                new_rel_path,
                                int(sub_row["size_bytes"]),
                                sub_row["sha256"],
                            ),
                        )
                        copied_count += 1

                # Move original submissions to primary target (target #1)
                primary_target = unique_targets[0]
                moved_count = self._reassign_and_renumber_submissions(
                    conn, str(primary_target.id), [str(source_uuid)]
                )
                conn.execute("DELETE FROM students WHERE id = ?", (str(source_uuid),))
                conn.commit()
                committed = True
        except BaseException:
            if not committed:
                for pf in published_files:
                    try:
                        pf.unlink(missing_ok=True)
                    except OSError:
                        pass
                for tf in staged_temp_files:
                    cleanup_pending_file(tf)
            raise

        return SplitResult(
            source_student_id=source_uuid,
            target_students=tuple(unique_targets),
            moved_submissions=moved_count,
            copied_submissions=copied_count,
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
