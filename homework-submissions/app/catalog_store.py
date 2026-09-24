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
class Catalog:
    groups: tuple[CatalogItem, ...]
    subjects: tuple[CatalogItem, ...]
    topics: tuple[CatalogItem, ...]

    def contains(self, group: str, subject: str, topic: str) -> bool:
        return all(
            any(item.name.casefold() == name.casefold() for item in items)
            for name, items in ((group, self.groups), (subject, self.subjects), (topic, self.topics))
        )


class CatalogItemNotFound(ValueError):
    pass


class CatalogNameConflict(ValueError):
    pass


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
    def list_topics(self) -> tuple[CatalogItem, ...]: ...
    def create_topic(self, name: str) -> CatalogItem: ...
    def rename_topic(self, item_id: UUID, name: str) -> CatalogItem: ...
    def delete_topic(self, item_id: UUID) -> CatalogItem: ...


def create_catalog_store(config: Mapping[str, object]) -> CatalogStore:
    provider = config.get('CATALOG_PROVIDER', 'json')
    if provider != 'json':
        raise ValueError(f'Unsupported CATALOG_PROVIDER: {provider}; only json is installed')
    from .json_catalog import JsonCatalogStore
    return JsonCatalogStore(Path(config['CATALOG_PATH']))
