from __future__ import annotations

import json
from pathlib import Path

import pytest
from tools.migrate_storage import (
    clean,
    dry_run_migration,
    execute_migration,
    load_catalog_entities,
    main,
    parse_legacy_directory,
    plan_migrations,
    rollback_migration,
    update_reports,
)


@pytest.fixture
def sample_catalog(tmp_path: Path) -> Path:
    catalog_data = {
        "version": 3,
        "groups": [
            {"id": "g1", "name": "ИС 25/9-3В"},
            {"id": "g2", "name": "ИС 24/9-3П"},
            {"id": "g3", "name": "ИС 25"},  # shorter prefix to test greedy matching
        ],
        "subjects": [
            {"id": "s1", "name": "Архитектура аппаратных средств"},
            {"id": "s2", "name": "Разработка программных модулей"},
            {"id": "s3", "name": "Разработка"},  # shorter prefix
        ],
        "topics": [],
    }
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog_data, ensure_ascii=False), encoding="utf-8")
    return catalog_path


def test_clean_function():
    # Slash replacement
    assert clean("ИС 25/9-3В") == "ИС 25／9-3В"
    assert clean("A\\B/C") == "A／B／C"

    # Invalid characters * ? " < > | : removed
    assert clean('Test: "hello"? *world* <1>|2') == "Test hello world 12"

    # Whitespace normalized
    assert clean("  Subject   Name  \t\n") == "Subject Name"

    # Invalid components
    with pytest.raises(ValueError):
        clean("")
    with pytest.raises(ValueError):
        clean(".")
    with pytest.raises(ValueError):
        clean("..")


def test_load_catalog_entities(sample_catalog: Path):
    groups, subjects = load_catalog_entities(sample_catalog)
    # Greedy prefix ordering: longer names come first
    assert groups[0] == "ИС 25／9-3В"
    assert "ИС 25" in groups
    assert groups.index("ИС 25／9-3В") < groups.index("ИС 25")

    assert subjects[0] == "Архитектура аппаратных средств"
    assert subjects.index("Разработка программных модулей") < subjects.index("Разработка")


def test_parse_legacy_directory_standard_and_corner_cases(tmp_path: Path, sample_catalog: Path):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    groups, subjects = load_catalog_entities(sample_catalog)

    # 1. Standard directory
    d1 = "ИС 25／9-3В-Архитектура аппаратных средств-Виды памяти"
    res1 = parse_legacy_directory(d1, storage_dir, groups, subjects)
    assert res1 is not None
    assert res1.group == "ИС 25／9-3В"
    assert res1.subject == "Архитектура аппаратных средств"
    assert res1.work == "Виды памяти"
    assert res1.target_dir == storage_dir / "ИС 25／9-3В" / "Архитектура аппаратных средств" / "Виды памяти"

    # 2. Work name with hyphens
    d2 = "ИС 24／9-3П-Разработка программных модулей-Music theory - Minor or Major chords"
    res2 = parse_legacy_directory(d2, storage_dir, groups, subjects)
    assert res2 is not None
    assert res2.group == "ИС 24／9-3П"
    assert res2.subject == "Разработка программных модулей"
    assert res2.work == "Music theory - Minor or Major chords"
    assert res2.target_dir == storage_dir / "ИС 24／9-3П" / "Разработка программных модулей" / "Music theory - Minor or Major chords"

    # 3. Already hierarchical or non-matching directory
    d3 = "ИС 25／9-3В"
    assert parse_legacy_directory(d3, storage_dir, groups, subjects) is None

    d4 = "Random-NonMatching-Dir"
    assert parse_legacy_directory(d4, storage_dir, groups, subjects) is None


def test_plan_migrations_and_conflict_detection(tmp_path: Path, sample_catalog: Path):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    groups, subjects = load_catalog_entities(sample_catalog)

    legacy_dir = storage_dir / "ИС 25／9-3В-Архитектура аппаратных средств-Виды памяти"
    legacy_dir.mkdir(parents=True)
    file1 = legacy_dir / "Иванов Иван 1.py"
    file1.write_text("print('test')", encoding="utf-8")

    migrations, file_moves, conflicts = plan_migrations(storage_dir, groups, subjects)
    assert len(migrations) == 1
    assert len(file_moves) == 1
    assert len(conflicts) == 0

    # Introduce conflict: target file already exists
    target_dir = storage_dir / "ИС 25／9-3В" / "Архитектура аппаратных средств" / "Виды памяти"
    target_dir.mkdir(parents=True)
    existing_target = target_dir / "Иванов Иван 1.py"
    existing_target.write_text("existing", encoding="utf-8")

    _, _, conflicts_after = plan_migrations(storage_dir, groups, subjects)
    assert len(conflicts_after) == 1
    assert "Target file already exists" in conflicts_after[0]


def test_dry_run_migration(tmp_path: Path, sample_catalog: Path):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    manifest_path = tmp_path / "manifest.json"

    legacy_dir = storage_dir / "ИС 25／9-3В-Архитектура аппаратных средств-Виды памяти"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "file1.txt").write_text("content1", encoding="utf-8")

    res = dry_run_migration(storage_dir, sample_catalog, manifest_path)
    assert res["mode"] == "dry-run"
    assert res["directories_count"] == 1
    assert res["files_count"] == 1
    assert len(res["conflicts"]) == 0

    # Ensure dry run did not touch the filesystem
    assert legacy_dir.exists()
    assert (legacy_dir / "file1.txt").exists()
    assert not manifest_path.exists()


def test_execute_migration_and_rollback(tmp_path: Path, sample_catalog: Path):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    manifest_path = tmp_path / "manifest.json"

    # Setup 2 legacy directories with files
    legacy_dir1 = storage_dir / "ИС 25／9-3В-Архитектура аппаратных средств-Виды памяти"
    legacy_dir1.mkdir(parents=True)
    f1 = legacy_dir1 / "+Иванов Иван 1.py"
    f1.write_text("code1", encoding="utf-8")

    legacy_dir2 = storage_dir / "ИС 24／9-3П-Разработка программных модулей-Music theory - Minor or Major chords"
    legacy_dir2.mkdir(parents=True)
    f2 = legacy_dir2 / "Петров Петр 1.py"
    f2.write_text("code2", encoding="utf-8")

    # Setup report file
    report_file = tmp_path / "report-IS25.md"
    report_content = (
        f"# Отчет\n"
        f"Каталог: storage/{legacy_dir1.name}\n"
        f"Другой каталог: storage/{legacy_dir2.name}\n"
    )
    report_file.write_text(report_content, encoding="utf-8")

    # 1. Execute
    exec_res = execute_migration(
        storage_dir=storage_dir,
        catalog_path=sample_catalog,
        manifest_path=manifest_path,
        update_reports_flag=True,
        custom_reports_dir=tmp_path,
    )

    assert len(exec_res["directories"]) == 2
    assert len(exec_res["files"]) == 2
    assert manifest_path.is_file()

    # Legacy directories must be removed
    assert not legacy_dir1.exists()
    assert not legacy_dir2.exists()

    # Hierarchical targets must exist
    target1 = storage_dir / "ИС 25／9-3В" / "Архитектура аппаратных средств" / "Виды памяти"
    target2 = storage_dir / "ИС 24／9-3П" / "Разработка программных модулей" / "Music theory - Minor or Major chords"
    assert (target1 / "+Иванов Иван 1.py").read_text(encoding="utf-8") == "code1"
    assert (target2 / "Петров Петр 1.py").read_text(encoding="utf-8") == "code2"

    # Report file updated
    updated_report = report_file.read_text(encoding="utf-8")
    assert "storage/ИС 25／9-3В/Архитектура аппаратных средств/Виды памяти" in updated_report
    assert "storage/ИС 24／9-3П/Разработка программных модулей/Music theory - Minor or Major chords" in updated_report

    # 2. Rollback
    rb_res = rollback_migration(
        manifest_path=manifest_path,
        storage_dir=storage_dir,
        custom_reports_dir=tmp_path,
        dry_run=False,
    )
    assert rb_res["status"] == "rolled_back"
    assert rb_res["restored_files"] == 2

    # Files must be back in legacy flat structure
    assert legacy_dir1.exists()
    assert (legacy_dir1 / "+Иванов Иван 1.py").read_text(encoding="utf-8") == "code1"
    assert legacy_dir2.exists()
    assert (legacy_dir2 / "Петров Петр 1.py").read_text(encoding="utf-8") == "code2"

    # Hierarchical empty directories should be cleaned up
    assert not target1.exists()
    assert not target2.exists()

    # Report file reverted
    reverted_report = report_file.read_text(encoding="utf-8")
    assert legacy_dir1.name in reverted_report
    assert legacy_dir2.name in reverted_report

    # Manifest should be removed after rollback
    assert not manifest_path.exists()


def test_update_reports_helper(tmp_path: Path):
    report_file = tmp_path / "report-test.md"
    legacy_name = "ИС 25／9-3В-Архитектура аппаратных средств-Виды памяти"
    hierarchical_rel = "ИС 25／9-3В/Архитектура аппаратных средств/Виды памяти"
    report_file.write_text(f"path: {legacy_name}\nother line\n", encoding="utf-8")

    dirs = [{"legacy_name": legacy_name, "hierarchical_rel": hierarchical_rel}]

    # Update
    res = update_reports([report_file], dirs, reverse=False, dry_run=False)
    assert len(res) == 1
    assert hierarchical_rel in report_file.read_text(encoding="utf-8")

    # Reverse
    rev_res = update_reports([report_file], dirs, reverse=True, dry_run=False)
    assert len(rev_res) == 1
    assert legacy_name in report_file.read_text(encoding="utf-8")


def test_cli_flags(tmp_path: Path, sample_catalog: Path):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    manifest_path = tmp_path / "manifest.json"

    legacy_dir = storage_dir / "ИС 25／9-3В-Архитектура аппаратных средств-Виды памяти"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "test.py").write_text("pass", encoding="utf-8")

    # Test dry run CLI
    code_dry = main([
        "--storage-dir", str(storage_dir),
        "--catalog", str(sample_catalog),
        "--manifest", str(manifest_path),
        "--dry-run",
    ])
    assert code_dry == 0
    assert not manifest_path.exists()

    # Test execute CLI
    code_exec = main([
        "--storage-dir", str(storage_dir),
        "--catalog", str(sample_catalog),
        "--manifest", str(manifest_path),
        "--execute",
    ])
    assert code_exec == 0
    assert manifest_path.exists()

    # Test rollback CLI
    code_rb = main([
        "--storage-dir", str(storage_dir),
        "--catalog", str(sample_catalog),
        "--manifest", str(manifest_path),
        "--rollback",
    ])
    assert code_rb == 0
    assert legacy_dir.exists()
