from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CatalogItem:
    id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class Topic:
    id: UUID
    name: str
    subject_id: UUID


@dataclass(frozen=True, slots=True)
class Catalog:
    groups: tuple[CatalogItem, ...]
    subjects: tuple[CatalogItem, ...]
    topics: tuple[Topic, ...]
    unassigned_subject_id: UUID

    def contains(self, group: str, subject: str, topic: str) -> bool:
        selected = next((item for item in self.subjects if item.name.casefold() == subject.casefold()), None)
        return (
            selected is not None
            and any(item.name.casefold() == group.casefold() for item in self.groups)
            and any(item.subject_id == selected.id and item.name.casefold() == topic.casefold()
                    for item in self.topics)
        )


class CatalogItemNotFound(ValueError):
    pass


class CatalogNameConflict(ValueError):
    def __init__(self, names: str | list[str]):
        self.names = (names,) if isinstance(names, str) else tuple(dict.fromkeys(names))
        super().__init__(", ".join(self.names))


class CatalogStore(Protocol):
    def snapshot(self) -> Catalog: ...
    def list_groups(self) -> tuple[CatalogItem, ...]: ...
    def create_group(self, name: str) -> CatalogItem: ...
    def rename_group(self, item_id: UUID, name: str) -> CatalogItem: ...
    def delete_group(self, item_id: UUID) -> CatalogItem: ...
    def list_subjects(self) -> tuple[CatalogItem, ...]: ...
    def create_subject(self, name: str) -> CatalogItem: ...
    def rename_subject(self, item_id: UUID, name: str) -> CatalogItem: ...
    def delete_subject(self, item_id: UUID) -> CatalogItem: ...
    def list_topics(self) -> tuple[Topic, ...]: ...
    def create_topic(self, name: str, subject_id: UUID) -> Topic: ...
    def update_topic(self, item_id: UUID, name: str, subject_id: UUID) -> Topic: ...
    def move_topics(self, item_ids: tuple[UUID, ...], subject_id: UUID) -> tuple[Topic, ...]: ...
    def delete_topic(self, item_id: UUID) -> Topic: ...


def create_catalog_store(config: Mapping[str, object]) -> CatalogStore:
    provider = config.get('CATALOG_PROVIDER', 'json')
    if provider != 'json':
        raise ValueError(f'Unsupported CATALOG_PROVIDER: {provider}; only json is installed')
    from .json_catalog import JsonCatalogStore
    return JsonCatalogStore(Path(config['CATALOG_PATH']))
