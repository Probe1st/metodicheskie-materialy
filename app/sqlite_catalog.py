"""SQLite v2 implementation of the CatalogStore interface."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from .catalog_store import (
    Catalog,
    CatalogConflictError,
    CatalogItem,
    CatalogNotFoundError,
    CatalogOperationForbiddenError,
    CatalogStore,
    CatalogValidationError,
    Topic,
)
from .database import connect, normalize_name_key


class SqliteCatalogStore(CatalogStore):
    def __init__(self, database_path: Path | str):
        self.database_path = Path(database_path)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _to_uuid(val: UUID | str) -> UUID:
        return val if isinstance(val, UUID) else UUID(str(val))

    def snapshot(self) -> Catalog:
        with connect(self.database_path) as conn:
            group_rows = conn.execute(
                "SELECT id, name FROM groups WHERE archived_at IS NULL ORDER BY name;"
            ).fetchall()
            groups = [CatalogItem(UUID(r["id"]), r["name"]) for r in group_rows]

            subject_rows = conn.execute(
                "SELECT id, name FROM subjects WHERE archived_at IS NULL ORDER BY is_system DESC, name;"
            ).fetchall()
            subjects = [CatalogItem(UUID(r["id"]), r["name"]) for r in subject_rows]

            topic_rows = conn.execute(
                "SELECT id, subject_id, name FROM topics WHERE archived_at IS NULL ORDER BY name;"
            ).fetchall()
            topics = [
                Topic(UUID(r["id"]), UUID(r["subject_id"]), r["name"]) for r in topic_rows
            ]

            sys_row = conn.execute(
                "SELECT id FROM subjects WHERE is_system = 1;"
            ).fetchone()
            unassigned_id = UUID(sys_row["id"]) if sys_row else None

        return Catalog(
            groups=groups,
            subjects=subjects,
            topics=topics,
            unassigned_subject_id=unassigned_id,
        )

    # --- Groups ---

    def list_groups(self, include_archived: bool = False) -> list[CatalogItem]:
        with connect(self.database_path) as conn:
            if include_archived:
                rows = conn.execute("SELECT id, name FROM groups ORDER BY name;").fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, name FROM groups WHERE archived_at IS NULL ORDER BY name;"
                ).fetchall()
            return [CatalogItem(UUID(r["id"]), r["name"]) for r in rows]

    def get_group(
        self, item_id: UUID | str, include_archived: bool = False
    ) -> CatalogItem | None:
        u = self._to_uuid(item_id)
        with connect(self.database_path) as conn:
            if include_archived:
                row = conn.execute(
                    "SELECT id, name FROM groups WHERE id = ?;", (str(u),)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id, name FROM groups WHERE id = ? AND archived_at IS NULL;",
                    (str(u),),
                ).fetchone()
            if not row:
                return None
            return CatalogItem(UUID(row["id"]), row["name"])

    def create_group(self, name: str) -> CatalogItem:
        try:
            name_key = normalize_name_key(name)
        except (ValueError, TypeError) as e:
            raise CatalogValidationError(str(e)) from e

        cleaned_name = name.strip()
        new_id = uuid.uuid4()
        with connect(self.database_path) as conn:
            try:
                conn.execute(
                    "INSERT INTO groups (id, name, name_key, archived_at) VALUES (?, ?, ?, NULL);",
                    (str(new_id), cleaned_name, name_key),
                )
                conn.execute(
                    """
                    INSERT INTO group_access (group_id, password_hash, is_open, closes_at_utc, version)
                    VALUES (?, NULL, 0, NULL, 1);
                    """,
                    (str(new_id),),
                )
            except sqlite3.IntegrityError as e:
                raise CatalogConflictError(f"Group '{cleaned_name}' already exists") from e

        return CatalogItem(new_id, cleaned_name)

    def rename_group(self, item_id: UUID | str, new_name: str) -> CatalogItem:
        u = self._to_uuid(item_id)
        try:
            name_key = normalize_name_key(new_name)
        except (ValueError, TypeError) as e:
            raise CatalogValidationError(str(e)) from e

        cleaned_name = new_name.strip()
        with connect(self.database_path) as conn:
            row = conn.execute(
                "SELECT id, name, archived_at FROM groups WHERE id = ?;", (str(u),)
            ).fetchone()
            if not row:
                raise CatalogNotFoundError(f"Group {item_id} not found")
            if row["archived_at"] is not None:
                raise CatalogOperationForbiddenError("Cannot rename archived group")

            try:
                conn.execute(
                    "UPDATE groups SET name = ?, name_key = ? WHERE id = ?;",
                    (cleaned_name, name_key, str(u)),
                )
                conn.execute(
                    "UPDATE group_access SET version = version + 1 WHERE group_id = ?;",
                    (str(u),),
                )
            except sqlite3.IntegrityError as e:
                raise CatalogConflictError(f"Group '{cleaned_name}' already exists") from e

        return CatalogItem(u, cleaned_name)

    def archive_group(self, item_id: UUID | str) -> CatalogItem:
        u = self._to_uuid(item_id)
        with connect(self.database_path) as conn:
            row = conn.execute(
                "SELECT id, name, archived_at FROM groups WHERE id = ?;", (str(u),)
            ).fetchone()
            if not row:
                raise CatalogNotFoundError(f"Group {item_id} not found")
            if row["archived_at"] is not None:
                return CatalogItem(u, row["name"])

            now = self._now_iso()
            conn.execute(
                "UPDATE groups SET archived_at = ? WHERE id = ?;", (now, str(u))
            )
            conn.execute(
                "UPDATE students SET archived_at = ? WHERE group_id = ? AND archived_at IS NULL;",
                (now, str(u)),
            )
            conn.execute(
                """
                UPDATE class_assignments
                SET archived_at = ?
                WHERE group_id = ? AND archived_at IS NULL;
                """,
                (now, str(u)),
            )
            conn.execute(
                """
                UPDATE group_access
                SET is_open = 0, closes_at_utc = NULL, version = version + 1
                WHERE group_id = ?;
                """,
                (str(u),),
            )

        return CatalogItem(u, row["name"])

    # --- Subjects ---

    def list_subjects(self, include_archived: bool = False) -> list[CatalogItem]:
        with connect(self.database_path) as conn:
            if include_archived:
                rows = conn.execute(
                    "SELECT id, name FROM subjects ORDER BY is_system DESC, name;"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, name FROM subjects WHERE archived_at IS NULL ORDER BY is_system DESC, name;"
                ).fetchall()
            return [CatalogItem(UUID(r["id"]), r["name"]) for r in rows]

    def get_subject(
        self, item_id: UUID | str, include_archived: bool = False
    ) -> CatalogItem | None:
        u = self._to_uuid(item_id)
        with connect(self.database_path) as conn:
            if include_archived:
                row = conn.execute(
                    "SELECT id, name FROM subjects WHERE id = ?;", (str(u),)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id, name FROM subjects WHERE id = ? AND archived_at IS NULL;",
                    (str(u),),
                ).fetchone()
            if not row:
                return None
            return CatalogItem(UUID(row["id"]), row["name"])

    def create_subject(self, name: str) -> CatalogItem:
        try:
            name_key = normalize_name_key(name)
        except (ValueError, TypeError) as e:
            raise CatalogValidationError(str(e)) from e

        cleaned_name = name.strip()
        new_id = uuid.uuid4()
        with connect(self.database_path) as conn:
            try:
                conn.execute(
                    "INSERT INTO subjects (id, name, name_key, is_system, archived_at) VALUES (?, ?, ?, 0, NULL);",
                    (str(new_id), cleaned_name, name_key),
                )
            except sqlite3.IntegrityError as e:
                raise CatalogConflictError(f"Subject '{cleaned_name}' already exists") from e

        return CatalogItem(new_id, cleaned_name)

    def rename_subject(self, item_id: UUID | str, new_name: str) -> CatalogItem:
        u = self._to_uuid(item_id)
        try:
            name_key = normalize_name_key(new_name)
        except (ValueError, TypeError) as e:
            raise CatalogValidationError(str(e)) from e

        cleaned_name = new_name.strip()
        with connect(self.database_path) as conn:
            row = conn.execute(
                "SELECT id, name, is_system, archived_at FROM subjects WHERE id = ?;",
                (str(u),),
            ).fetchone()
            if not row:
                raise CatalogNotFoundError(f"Subject {item_id} not found")
            if row["is_system"] == 1:
                raise CatalogOperationForbiddenError("Cannot rename system subject")
            if row["archived_at"] is not None:
                raise CatalogOperationForbiddenError("Cannot rename archived subject")

            try:
                conn.execute(
                    "UPDATE subjects SET name = ?, name_key = ? WHERE id = ?;",
                    (cleaned_name, name_key, str(u)),
                )
            except sqlite3.IntegrityError as e:
                raise CatalogConflictError(f"Subject '{cleaned_name}' already exists") from e

        return CatalogItem(u, cleaned_name)

    def archive_subject(self, item_id: UUID | str) -> CatalogItem:
        u = self._to_uuid(item_id)
        with connect(self.database_path) as conn:
            row = conn.execute(
                "SELECT id, name, is_system, archived_at FROM subjects WHERE id = ?;",
                (str(u),),
            ).fetchone()
            if not row:
                raise CatalogNotFoundError(f"Subject {item_id} not found")
            if row["is_system"] == 1:
                raise CatalogOperationForbiddenError("Cannot archive system subject")
            if row["archived_at"] is not None:
                return CatalogItem(u, row["name"])

            now = self._now_iso()
            conn.execute(
                "UPDATE subjects SET archived_at = ? WHERE id = ?;", (now, str(u))
            )
            conn.execute(
                "UPDATE topics SET archived_at = ? WHERE subject_id = ? AND archived_at IS NULL;",
                (now, str(u)),
            )
            conn.execute(
                """
                UPDATE class_assignments
                SET archived_at = ?
                WHERE topic_id IN (SELECT id FROM topics WHERE subject_id = ?)
                  AND archived_at IS NULL;
                """,
                (now, str(u)),
            )

        return CatalogItem(u, row["name"])

    # --- Topics ---

    def list_topics(
        self, subject_id: UUID | str | None = None, include_archived: bool = False
    ) -> list[Topic]:
        with connect(self.database_path) as conn:
            query = "SELECT id, subject_id, name FROM topics"
            conditions = []
            params = []

            if subject_id is not None:
                conditions.append("subject_id = ?")
                params.append(str(self._to_uuid(subject_id)))

            if not include_archived:
                conditions.append("archived_at IS NULL")

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " ORDER BY name;"
            rows = conn.execute(query, params).fetchall()
            return [
                Topic(UUID(r["id"]), UUID(r["subject_id"]), r["name"]) for r in rows
            ]

    def get_topic(
        self, item_id: UUID | str, include_archived: bool = False
    ) -> Topic | None:
        u = self._to_uuid(item_id)
        with connect(self.database_path) as conn:
            if include_archived:
                row = conn.execute(
                    "SELECT id, subject_id, name FROM topics WHERE id = ?;", (str(u),)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id, subject_id, name FROM topics WHERE id = ? AND archived_at IS NULL;",
                    (str(u),),
                ).fetchone()
            if not row:
                return None
            return Topic(UUID(row["id"]), UUID(row["subject_id"]), row["name"])

    def create_topic(
        self,
        subject_id: UUID | str,
        name: str | UUID,
    ) -> Topic:
        # Support both create_topic(subject_id, name) and create_topic(name, subject_id)
        if isinstance(subject_id, str) and isinstance(name, UUID):
            subject_id, name = name, subject_id

        sub_uuid = self._to_uuid(subject_id)
        str_name = str(name)

        try:
            name_key = normalize_name_key(str_name)
        except (ValueError, TypeError) as e:
            raise CatalogValidationError(str(e)) from e

        cleaned_name = str_name.strip()
        new_id = uuid.uuid4()
        with connect(self.database_path) as conn:
            subj = conn.execute(
                "SELECT id, archived_at FROM subjects WHERE id = ?;", (str(sub_uuid),)
            ).fetchone()
            if not subj:
                raise CatalogNotFoundError(f"Subject {sub_uuid} not found")
            if subj["archived_at"] is not None:
                raise CatalogOperationForbiddenError(
                    "Cannot add topic to archived subject"
                )

            try:
                conn.execute(
                    """
                    INSERT INTO topics (id, subject_id, name, name_key, archived_at)
                    VALUES (?, ?, ?, ?, NULL);
                    """,
                    (str(new_id), str(sub_uuid), cleaned_name, name_key),
                )
            except sqlite3.IntegrityError as e:
                raise CatalogConflictError(
                    f"Topic '{cleaned_name}' already exists in subject"
                ) from e

        return Topic(new_id, sub_uuid, cleaned_name)

    def rename_topic(self, item_id: UUID | str, new_name: str) -> Topic:
        return self.update_topic(item_id, new_name, subject_id=None)

    def update_topic(
        self,
        item_id: UUID | str,
        new_name: str,
        subject_id: UUID | str | None = None,
    ) -> Topic:
        u = self._to_uuid(item_id)
        try:
            name_key = normalize_name_key(new_name)
        except (ValueError, TypeError) as e:
            raise CatalogValidationError(str(e)) from e

        cleaned_name = new_name.strip()
        target_sub_uuid = self._to_uuid(subject_id) if subject_id is not None else None

        with connect(self.database_path) as conn:
            row = conn.execute(
                "SELECT id, subject_id, name, archived_at FROM topics WHERE id = ?;",
                (str(u),),
            ).fetchone()
            if not row:
                raise CatalogNotFoundError(f"Topic {item_id} not found")
            if row["archived_at"] is not None:
                raise CatalogOperationForbiddenError("Cannot rename archived topic")

            final_sub_uuid = target_sub_uuid or UUID(row["subject_id"])
            if str(final_sub_uuid) != row["subject_id"]:
                subj = conn.execute(
                    "SELECT id, archived_at FROM subjects WHERE id = ?;",
                    (str(final_sub_uuid),),
                ).fetchone()
                if not subj:
                    raise CatalogNotFoundError(f"Subject {final_sub_uuid} not found")
                if subj["archived_at"] is not None:
                    raise CatalogOperationForbiddenError(
                        "Cannot move topic to archived subject"
                    )

            try:
                conn.execute(
                    "UPDATE topics SET name = ?, name_key = ?, subject_id = ? WHERE id = ?;",
                    (cleaned_name, name_key, str(final_sub_uuid), str(u)),
                )
            except sqlite3.IntegrityError as e:
                raise CatalogConflictError(
                    [cleaned_name]
                ) from e

        return Topic(u, final_sub_uuid, cleaned_name)

    def archive_topic(self, item_id: UUID | str) -> Topic:
        u = self._to_uuid(item_id)
        with connect(self.database_path) as conn:
            row = conn.execute(
                "SELECT id, subject_id, name, archived_at FROM topics WHERE id = ?;",
                (str(u),),
            ).fetchone()
            if not row:
                raise CatalogNotFoundError(f"Topic {item_id} not found")
            if row["archived_at"] is not None:
                return Topic(u, UUID(row["subject_id"]), row["name"])

            now = self._now_iso()
            conn.execute(
                "UPDATE topics SET archived_at = ? WHERE id = ?;", (now, str(u))
            )
            conn.execute(
                """
                UPDATE class_assignments
                SET archived_at = ?
                WHERE topic_id = ? AND archived_at IS NULL;
                """,
                (now, str(u)),
            )

        return Topic(u, UUID(row["subject_id"]), row["name"])

    def move_topics(
        self, topic_ids: list[UUID] | tuple[UUID, ...], target_subject_id: UUID | str
    ) -> None:
        if not topic_ids:
            return

        target_uuid = self._to_uuid(target_subject_id)
        topic_uuids = [self._to_uuid(t) for t in topic_ids]

        with connect(self.database_path) as conn:
            target = conn.execute(
                "SELECT id, archived_at FROM subjects WHERE id = ?;",
                (str(target_uuid),),
            ).fetchone()
            if not target:
                raise CatalogNotFoundError(
                    f"Target subject {target_subject_id} not found"
                )
            if target["archived_at"] is not None:
                raise CatalogOperationForbiddenError(
                    "Cannot move topics to archived subject"
                )

            placeholders = ",".join("?" for _ in topic_uuids)
            moving_rows = conn.execute(
                f"SELECT id, subject_id, name, name_key, archived_at FROM topics WHERE id IN ({placeholders});",
                [str(u) for u in topic_uuids],
            ).fetchall()
            found_ids = {r["id"] for r in moving_rows}

            for u in topic_uuids:
                if str(u) not in found_ids:
                    raise CatalogNotFoundError(f"Topic {u} not found")

            for r in moving_rows:
                if r["archived_at"] is not None:
                    raise CatalogOperationForbiddenError(
                        f"Cannot move archived topic {r['id']}"
                    )

            moving_keys = [r["name_key"] for r in moving_rows]
            if len(moving_keys) != len(set(moving_keys)):
                raise CatalogConflictError("Duplicate topic names among moving topics")

            # Check collisions with existing topics in target subject
            existing_target_topics = conn.execute(
                f"""
                SELECT name, name_key FROM topics
                WHERE subject_id = ? AND id NOT IN ({placeholders});
                """,
                [str(target_uuid)] + [str(u) for u in topic_uuids],
            ).fetchall()
            target_key_set = {r["name_key"] for r in existing_target_topics}

            conflicts = [r["name"] for r in moving_rows if r["name_key"] in target_key_set]
            if conflicts:
                raise CatalogConflictError(conflicts)

            for u in topic_uuids:
                conn.execute(
                    "UPDATE topics SET subject_id = ? WHERE id = ?;",
                    (str(target_uuid), str(u)),
                )
