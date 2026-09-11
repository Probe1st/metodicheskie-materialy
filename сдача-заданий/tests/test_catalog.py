from pathlib import Path

from app.catalog import load_catalog


def test_catalog_accepts_known_work() -> None:
    catalog = load_catalog(Path("catalog.json"))

    assert catalog.contains(
        "ИС 25/9-1П",
        "Архитектура аппаратных средств",
        "Логические элементы. Базовые схемы",
    )


def test_catalog_rejects_work_from_another_discipline() -> None:
    catalog = load_catalog(Path("catalog.json"))

    assert not catalog.contains(
        "ИС 25/9-1П",
        "Архитектура аппаратных средств",
        "паспорт качества учебного сервиса",
    )
