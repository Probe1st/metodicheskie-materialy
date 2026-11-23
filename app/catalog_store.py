"""Catalog store interface, data classes, and factory for SQLite v2."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CatalogItem:
    id: UUID
    name: str

    def __init__(self, id: UUID | str, name: str):
        object.__setattr__(self, "id", id if isinstance(id, UUID) else UUID(str(id)))
        object.__setattr__(self, "name", str(name))


@dataclass(frozen=True, slots=True)
class Topic:
    id: UUID
    subject_id: UUID
    name: str

    def __init__(
        self,
        id: UUID | str,
        subject_id: UUID | str,
        name: str | UUID = "",
    ):
        # Allow both Topic(id, subject_id, name) and legacy Topic(id, name, subject_id)
        if isinstance(subject_id, str) and isinstance(name, UUID):
            object.__setattr__(self, "id", id if isinstance(id, UUID) else UUID(str(id)))
            object.__setattr__(self, "name", subject_id)
            object.__setattr__(self, "subject_id", name)
        else:
            object.__setattr__(self, "id", id if isinstance(id, UUID) else UUID(str(id)))
            object.__setattr__(
                self,
                "subject_id",
                subject_id if isinstance(subject_id, UUID) else UUID(str(subject_id)),
            )
            object.__setattr__(self, "name", str(name))


@dataclass(frozen=True, slots=True)
class Catalog:
    groups: list[CatalogItem]
    subjects: list[CatalogItem]
    topics: list[Topic]
    unassigned_subject_id: UUID | None = None

    def contains(self, group: str, subject: str, topic: str) -> bool:
        selected = next(
            (item for item in self.subjects if item.name.casefold() == subject.casefold()),
            None,
        )
        return (
            selected is not None
            and any(item.name.casefold() == group.casefold() for item in self.groups)
            and any(
                item.subject_id == selected.id and item.name.casefold() == topic.casefold()
                for item in self.topics
            )
        )


class CatalogError(Exception):
    """Base exception for catalog store errors."""


class CatalogNotFoundError(CatalogError, ValueError):
    """Raised when an item is not found in the catalog."""


CatalogItemNotFound = CatalogNotFoundError  # Backward compatibility alias


class CatalogConflictError(CatalogError, ValueError):
    """Raised when a name or key conflict occurs."""

    def __init__(self, names: str | list[str] | tuple[str, ...]):
        if isinstance(names, str):
            self.names = (names,)
        else:
            self.names = tuple(dict.fromkeys(names))
        super().__init__(", ".join(self.names))


CatalogNameConflict = CatalogConflictError  # Backward compatibility alias


class CatalogOperationForbiddenError(CatalogError, ValueError):
    """Raised when an operation on a catalog entity is forbidden (e.g. system subject)."""


class CatalogValidationError(CatalogError, ValueError):
    """Raised when catalog input validation fails."""


class CatalogStore(ABC):
    @abstractmethod
    def snapshot(self) -> Catalog: ...

    @abstractmethod
    def list_groups(self, include_archived: bool = False) -> list[CatalogItem]: ...

    @abstractmethod
    def get_group(self, item_id: UUID) -> CatalogItem | None: ...

    @abstractmethod
    def create_group(self, name: str) -> CatalogItem: ...

    @abstractmethod
    def rename_group(self, item_id: UUID, new_name: str) -> CatalogItem: ...

    @abstractmethod
    def archive_group(self, item_id: UUID) -> CatalogItem: ...

    @abstractmethod
    def list_subjects(self, include_archived: bool = False) -> list[CatalogItem]: ...

    @abstractmethod
    def get_subject(self, item_id: UUID) -> CatalogItem | None: ...

    @abstractmethod
    def create_subject(self, name: str) -> CatalogItem: ...

    @abstractmethod
    def rename_subject(self, item_id: UUID, new_name: str) -> CatalogItem: ...

    @abstractmethod
    def archive_subject(self, item_id: UUID) -> CatalogItem: ...

    @abstractmethod
    def list_topics(
        self, subject_id: UUID | None = None, include_archived: bool = False
    ) -> list[Topic]: ...

    @abstractmethod
    def get_topic(self, item_id: UUID) -> Topic | None: ...

    @abstractmethod
    def create_topic(self, subject_id: UUID, name: str) -> Topic: ...

    @abstractmethod
    def rename_topic(self, item_id: UUID, new_name: str) -> Topic: ...

    @abstractmethod
    def archive_topic(self, item_id: UUID) -> Topic: ...

    @abstractmethod
    def move_topics(self, topic_ids: list[UUID], target_subject_id: UUID) -> None: ...


def create_catalog_store(config: Mapping[str, object]) -> CatalogStore:
    if "DATABASE_PATH" not in config:
        raise ValueError("Missing DATABASE_PATH in config for catalog store")

    db_path = Path(str(config["DATABASE_PATH"]))
    if not db_path.is_absolute():
        private_dir = Path(str(config.get("PRIVATE_DATA_DIR", ".private")))
        try:
            db_path.relative_to(private_dir)
        except ValueError:
            db_path = private_dir / db_path
    from .sqlite_catalog import SqliteCatalogStore

    return SqliteCatalogStore(db_path)
