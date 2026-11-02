from __future__ import annotations

import logging
import sqlite3
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

TASK_CONTENT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS task_attachments (
    id TEXT PRIMARY KEY,
    original_name TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_contents (
    owner_kind TEXT NOT NULL CHECK (owner_kind IN ('topic', 'lesson')),
    owner_id TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    description_format TEXT NOT NULL CHECK (description_format IN ('html', 'md', 'txt')),
    attachment_id TEXT REFERENCES task_attachments(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (owner_kind, owner_id)
);

CREATE INDEX IF NOT EXISTS idx_task_contents_attachment
    ON task_contents(attachment_id);
"""

VALID_OWNER_KINDS = frozenset({"topic", "lesson"})
VALID_DESCRIPTION_FORMATS = frozenset({"html", "md", "txt"})


class TaskContentValidationError(ValueError):
    """Raised when task content inputs fail validation."""


class TaskContentNotFoundError(ValueError):
    """Raised when task content or attachment is not found."""


@dataclass(frozen=True, slots=True)
class TaskAttachment:
    id: UUID
    original_name: str
    size_bytes: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TaskContent:
    owner_kind: str  # "topic" | "lesson"
    owner_id: UUID
    description: str
    description_format: str  # "html" | "md" | "txt"
    attachment_id: UUID | None
    updated_at: datetime


def init_task_content_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(TASK_CONTENT_SCHEMA_SQL)


class TaskContentStore:
    def __init__(self, db_path: Path, attachments_dir: Path):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.chmod(0o700)

        self.attachments_dir = Path(attachments_dir).resolve()
        self.attachments_dir.mkdir(parents=True, exist_ok=True)
        self.attachments_dir.chmod(0o700)

        with self._connect() as conn:
            init_task_content_schema(conn)
            conn.commit()

        self.db_path.chmod(0o600)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            yield conn
        finally:
            conn.close()

    def _row_to_attachment(self, row: sqlite3.Row) -> TaskAttachment:
        return TaskAttachment(
            id=UUID(row["id"]),
            original_name=row["original_name"],
            size_bytes=int(row["size_bytes"]),
            created_at=datetime.fromisoformat(row["created_at"]).astimezone(UTC),
        )

    def _row_to_content(self, row: sqlite3.Row) -> TaskContent:
        raw_att = row["attachment_id"]
        return TaskContent(
            owner_kind=row["owner_kind"],
            owner_id=UUID(row["owner_id"]),
            description=row["description"],
            description_format=row["description_format"],
            attachment_id=UUID(raw_att) if raw_att else None,
            updated_at=datetime.fromisoformat(row["updated_at"]).astimezone(UTC),
        )

    def get_attachment_path(self, attachment_id: UUID) -> Path:
        return self.attachments_dir / str(attachment_id)

    def get_attachment(self, attachment_id: UUID, conn: sqlite3.Connection | None = None) -> TaskAttachment | None:
        if conn is not None:
            row = conn.execute(
                "SELECT * FROM task_attachments WHERE id = ?",
                (str(attachment_id),),
            ).fetchone()
            return self._row_to_attachment(row) if row else None

        with self._connect() as local_conn:
            row = local_conn.execute(
                "SELECT * FROM task_attachments WHERE id = ?",
                (str(attachment_id),),
            ).fetchone()
            return self._row_to_attachment(row) if row else None

    def get(self, owner_kind: str, owner_id: UUID, conn: sqlite3.Connection | None = None) -> TaskContent | None:
        if owner_kind not in VALID_OWNER_KINDS:
            raise TaskContentValidationError(f"Invalid owner_kind: {owner_kind!r}")
        sql = "SELECT * FROM task_contents WHERE owner_kind = ? AND owner_id = ?"
        params = (owner_kind, str(owner_id))

        if conn is not None:
            row = conn.execute(sql, params).fetchone()
            return self._row_to_content(row) if row else None

        with self._connect() as local_conn:
            row = local_conn.execute(sql, params).fetchone()
            return self._row_to_content(row) if row else None

    def write_attachment_file(self, attachment_id: UUID, file_data: BinaryIO | bytes) -> int:
        """Fully writes the attachment file to disk before transactional DB registration.
        Returns the number of bytes written.
        """
        dest_path = self.get_attachment_path(attachment_id)
        try:
            if isinstance(file_data, bytes):
                dest_path.write_bytes(file_data)
                size_bytes = len(file_data)
            else:
                bytes_count = 0
                with open(dest_path, "wb") as f:
                    while chunk := file_data.read(64 * 1024):
                        f.write(chunk)
                        bytes_count += len(chunk)
                size_bytes = bytes_count
            dest_path.chmod(0o600)
            return size_bytes
        except Exception:
            self.discard_attachment_file(attachment_id)
            raise

    def discard_attachment_file(self, attachment_id: UUID) -> None:
        """Removes an uncommitted or discarded physical file from disk."""
        target = self.get_attachment_path(attachment_id)
        try:
            if target.is_file():
                target.unlink()
        except OSError as err:
            logger.error("Failed to discard attachment file %s: %s", attachment_id, err)

    def register_attachment(
        self,
        attachment_id: UUID,
        original_name: str,
        size_bytes: int,
        conn: sqlite3.Connection | None = None,
    ) -> TaskAttachment:
        cleaned_name = original_name.strip()
        if not cleaned_name:
            raise TaskContentValidationError("Attachment original_name cannot be empty.")
        if size_bytes < 0:
            raise TaskContentValidationError("Attachment size_bytes cannot be negative.")

        now_iso = datetime.now(UTC).isoformat()

        def _do_insert(c: sqlite3.Connection):
            c.execute(
                """
                INSERT INTO task_attachments (id, original_name, size_bytes, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (str(attachment_id), cleaned_name, size_bytes, now_iso),
            )

        if conn is not None:
            _do_insert(conn)
        else:
            with self._connect() as local_conn:
                _do_insert(local_conn)
                local_conn.commit()

        return TaskAttachment(
            id=attachment_id,
            original_name=cleaned_name,
            size_bytes=size_bytes,
            created_at=datetime.fromisoformat(now_iso).astimezone(UTC),
        )

    def save_attachment(
        self,
        original_name: str,
        file_data: BinaryIO | bytes,
        conn: sqlite3.Connection | None = None,
    ) -> TaskAttachment:
        """Helper to create UUID, write file and register metadata in one call."""
        attachment_id = uuid4()
        size_bytes = self.write_attachment_file(attachment_id, file_data)
        try:
            return self.register_attachment(attachment_id, original_name, size_bytes, conn=conn)
        except Exception:
            self.discard_attachment_file(attachment_id)
            raise

    def set_content(
        self,
        owner_kind: str,
        owner_id: UUID,
        description: str,
        description_format: str,
        attachment_id: UUID | None,
        conn: sqlite3.Connection | None = None,
    ) -> TaskContent:
        if owner_kind not in VALID_OWNER_KINDS:
            raise TaskContentValidationError(f"Invalid owner_kind: {owner_kind!r}. Expected 'topic' or 'lesson'.")
        if description_format not in VALID_DESCRIPTION_FORMATS:
            raise TaskContentValidationError(
                f"Invalid description_format: {description_format!r}. Expected 'html', 'md', or 'txt'."
            )
        if not isinstance(description, str):
            raise TaskContentValidationError("description must be a string.")

        now_iso = datetime.now(UTC).isoformat()
        att_str = str(attachment_id) if attachment_id else None

        def _execute(c: sqlite3.Connection) -> tuple[TaskContent, list[UUID]]:
            # Verify attachment exists if provided
            if attachment_id is not None:
                att_row = c.execute(
                    "SELECT id FROM task_attachments WHERE id = ?",
                    (att_str,),
                ).fetchone()
                if att_row is None:
                    raise TaskContentValidationError(f"Attachment {attachment_id} not found.")

            # Find old attachment if any
            old_row = c.execute(
                "SELECT attachment_id FROM task_contents WHERE owner_kind = ? AND owner_id = ?",
                (owner_kind, str(owner_id)),
            ).fetchone()
            old_att_id: UUID | None = UUID(old_row["attachment_id"]) if (old_row and old_row["attachment_id"]) else None

            # Upsert task content
            c.execute(
                """
                INSERT INTO task_contents (owner_kind, owner_id, description, description_format, attachment_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_kind, owner_id) DO UPDATE SET
                    description = excluded.description,
                    description_format = excluded.description_format,
                    attachment_id = excluded.attachment_id,
                    updated_at = excluded.updated_at
                """,
                (owner_kind, str(owner_id), description, description_format, att_str, now_iso),
            )

            # Check if old attachment is orphaned
            orphaned_files: list[UUID] = []
            if old_att_id is not None and old_att_id != attachment_id:
                ref_count = c.execute(
                    "SELECT COUNT(*) as cnt FROM task_contents WHERE attachment_id = ?",
                    (str(old_att_id),),
                ).fetchone()["cnt"]
                if ref_count == 0:
                    c.execute("DELETE FROM task_attachments WHERE id = ?", (str(old_att_id),))
                    orphaned_files.append(old_att_id)

            item = TaskContent(
                owner_kind=owner_kind,
                owner_id=owner_id,
                description=description,
                description_format=description_format,
                attachment_id=attachment_id,
                updated_at=datetime.fromisoformat(now_iso).astimezone(UTC),
            )
            return item, orphaned_files

        if conn is not None:
            item, orphaned = _execute(conn)
            # Physical files removed after commit by caller or caller can rely on _cleanup_files
            for orphan_id in orphaned:
                self.discard_attachment_file(orphan_id)
            return item
        else:
            with self._connect() as local_conn:
                item, orphaned = _execute(local_conn)
                local_conn.commit()
            for orphan_id in orphaned:
                self.discard_attachment_file(orphan_id)
            return item

    def delete_content(
        self,
        owner_kind: str,
        owner_id: UUID,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        if owner_kind not in VALID_OWNER_KINDS:
            raise TaskContentValidationError(f"Invalid owner_kind: {owner_kind!r}")

        def _execute(c: sqlite3.Connection) -> list[UUID]:
            old_row = c.execute(
                "SELECT attachment_id FROM task_contents WHERE owner_kind = ? AND owner_id = ?",
                (owner_kind, str(owner_id)),
            ).fetchone()
            if old_row is None:
                return []

            old_att_id: UUID | None = UUID(old_row["attachment_id"]) if old_row["attachment_id"] else None
            c.execute(
                "DELETE FROM task_contents WHERE owner_kind = ? AND owner_id = ?",
                (owner_kind, str(owner_id)),
            )

            orphaned: list[UUID] = []
            if old_att_id is not None:
                cnt = c.execute(
                    "SELECT COUNT(*) as cnt FROM task_contents WHERE attachment_id = ?",
                    (str(old_att_id),),
                ).fetchone()["cnt"]
                if cnt == 0:
                    c.execute("DELETE FROM task_attachments WHERE id = ?", (str(old_att_id),))
                    orphaned.append(old_att_id)
            return orphaned

        if conn is not None:
            orphaned = _execute(conn)
            for orphan_id in orphaned:
                self.discard_attachment_file(orphan_id)
        else:
            with self._connect() as local_conn:
                orphaned = _execute(local_conn)
                local_conn.commit()
            for orphan_id in orphaned:
                self.discard_attachment_file(orphan_id)

    def delete_contents_for_owners(
        self,
        owner_kind: str,
        owner_ids: Sequence[UUID],
        conn: sqlite3.Connection | None = None,
    ) -> None:
        if not owner_ids:
            return
        if owner_kind not in VALID_OWNER_KINDS:
            raise TaskContentValidationError(f"Invalid owner_kind: {owner_kind!r}")

        def _execute(c: sqlite3.Connection) -> list[UUID]:
            str_ids = [str(uid) for uid in owner_ids]
            placeholders = ",".join("?" for _ in str_ids)
            query = f"SELECT attachment_id FROM task_contents WHERE owner_kind = ? AND owner_id IN ({placeholders})"
            rows = c.execute(query, [owner_kind, *str_ids]).fetchall()

            att_ids_to_check: set[UUID] = set()
            for r in rows:
                if r["attachment_id"]:
                    att_ids_to_check.add(UUID(r["attachment_id"]))

            delete_query = f"DELETE FROM task_contents WHERE owner_kind = ? AND owner_id IN ({placeholders})"
            c.execute(delete_query, [owner_kind, *str_ids])

            orphaned: list[UUID] = []
            for att_id in att_ids_to_check:
                cnt = c.execute(
                    "SELECT COUNT(*) as cnt FROM task_contents WHERE attachment_id = ?",
                    (str(att_id),),
                ).fetchone()["cnt"]
                if cnt == 0:
                    c.execute("DELETE FROM task_attachments WHERE id = ?", (str(att_id),))
                    orphaned.append(att_id)
            return orphaned

        if conn is not None:
            orphaned = _execute(conn)
            for orphan_id in orphaned:
                self.discard_attachment_file(orphan_id)
        else:
            with self._connect() as local_conn:
                orphaned = _execute(local_conn)
                local_conn.commit()
            for orphan_id in orphaned:
                self.discard_attachment_file(orphan_id)
