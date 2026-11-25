"""Tests for Catalog data model and Catalog.contains logic."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.catalog_store import Catalog, CatalogItem, Topic, create_catalog_store
from app.database import initialize


def test_catalog_dataclass_contains_directly():
    g1_id = uuid4()
    s1_id = uuid4()
    s2_id = uuid4()
    t1_id = uuid4()
    t2_id = uuid4()

    catalog = Catalog(
        groups=[CatalogItem(g1_id, "ИС 25/9-1П")],
        subjects=[
            CatalogItem(s1_id, "Обеспечение качества"),
            CatalogItem(s2_id, "Архитектура ЭВМ"),
        ],
        topics=[
            Topic(t1_id, s1_id, "Паспорт качества"),
            Topic(t2_id, s2_id, "Логические схемы"),
        ],
    )

    # Valid combination
    assert catalog.contains("ИС 25/9-1П", "Обеспечение качества", "Паспорт качества")

    # Casefold matching
    assert catalog.contains("ис 25/9-1п", "обеспечение качества", "паспорт качества")

    # Topic from another subject
    assert not catalog.contains("ИС 25/9-1П", "Архитектура ЭВМ", "Паспорт качества")

    # Unknown group
    assert not catalog.contains("Неизвестная группа", "Обеспечение качества", "Паспорт качества")

    # Unknown topic
    assert not catalog.contains("ИС 25/9-1П", "Обеспечение качества", "Несуществующая тема")


def test_catalog_store_snapshot_contains(tmp_path: Path):
    db_path = tmp_path / "app_v2.sqlite3"
    initialize(db_path)

    store = create_catalog_store({"DATABASE_PATH": db_path})
    g = store.create_group("ИС 25/9-1П")
    s1 = store.create_subject("Информатика")
    s2 = store.create_subject("Физика")
    store.create_topic(s1.id, "Базы данных")
    store.create_topic(s2.id, "Механика")

    snapshot = store.snapshot()

    assert snapshot.contains(g.name, "Информатика", "Базы данных")
    assert snapshot.contains("ис 25/9-1п", "информатика", "базы данных")
    assert not snapshot.contains(g.name, "Физика", "Базы данных")
    assert not snapshot.contains(g.name, "Информатика", "Механика")
