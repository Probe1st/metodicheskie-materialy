from pathlib import Path

from app.catalog_store import create_catalog_store


def test_catalog_accepts_independent_topics(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "catalog.json"
    path = tmp_path / "catalog.json"
    path.write_bytes(source.read_bytes())
    catalog = create_catalog_store({"CATALOG_PROVIDER": "json", "CATALOG_PATH": path}).snapshot()

    assert catalog.contains(
        "ИС 25/9-1П",
        "Архитектура аппаратных средств",
        "паспорт качества учебного сервиса",
    )
    assert not catalog.contains("unknown", "Архитектура аппаратных средств", "паспорт качества учебного сервиса")
