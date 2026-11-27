"""Tests for student DB cleanup analysis and offline normalization/merge CLI."""

from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

from app.access import AccessStore
from app.assignments import AssignmentStore
from app.database import connect, initialize, validate
from app.sqlite_catalog import SqliteCatalogStore
from app.submissions import SubmissionStore
from tools.reconcile_storage import reconcile_storage


def _seed_production_like_db(tmp_path: Path) -> dict[str, object]:
    db_path = tmp_path / "prod.sqlite3"
    storage_root = tmp_path / "storage"
    initialize(db_path)
    storage_root.mkdir(parents=True, exist_ok=True)

    cat = SqliteCatalogStore(db_path)
    group = cat.create_group("ИС 25/9-1П")
    subj = cat.create_subject("Архитектура аппаратных средств")
    t1 = cat.create_topic(subj.id, "Порты")
    t2 = cat.create_topic(subj.id, "Процессоры")

    AccessStore(db_path).configure(group.id, is_open=True, password="123")
    asgn_store = AssignmentStore(db_path)
    a1 = asgn_store.create(group.id, t1.id, "2026-10-01", 1)
    a2 = asgn_store.create(group.id, t2.id, "2026-10-02", 2)

    sub_store = SubmissionStore(db_path, storage_root)

    # Insert dirty legacy production-like student rows directly into SQLite
    raw_names = [
        "Колотович Олег",
        "колотович олег",
        "ДУДИНОВ КИРИЛЛ АЛЕКСЕЕВИЧ",
        "Дудинов Кирилл Алексеевич",
        "Мельников Богдан Артемович",
        "Мельников Богдан Артеомвич",
        "Мельников Богдан Артёмович",
        "Мельников Богдан Артемовис",
        "Мельников Бгодан",  # typo in first name -> fuzzy candidate
        "Шахлина Дарья0",
        "Бронников Антон Анарович 1.drawio",
        "Манько Еремей 1. manko.drawio",
        "Мухетдинов Кирилл Андреевич 1.А. Практическая работа №",
        "Чепак Савелий Павлович 1. Чепак Рупо",
        "Чепак Савелий Павлович 1. Порты SCSI и IDE. Чепак РУПО",
        "Гасанова Лейла Хаял кызы 1. ИС 25 9",
        "Бобоназарзода Мухаммадсаид Торихи 1. Основные характеристики процессоров.pdf Бобоназарзода Мухаммадсаид ИС 25 9",
        "Гилюк Даниил",
        "Гилюк Даниил и Бондаренко Владислав",
    ]
    student_ids: dict[str, str] = {}
    with connect(db_path) as conn:
        for name in raw_names:
            sid = str(uuid4())
            student_ids[name] = sid
            conn.execute(
                "INSERT INTO students (id, group_id, display_name, archived_at) VALUES (?, ?, ?, NULL)",
                (sid, str(group.id), name),
            )

    # Create submissions for every dirty student so link preservation is thoroughly tested
    for idx, name in enumerate(raw_names):
        sid = student_ids[name]
        asgn_id = a1.id if idx % 2 == 0 else a2.id
        sub, _ = sub_store.submit(
            group.id,
            UUID(sid),
            asgn_id,
            io.BytesIO(f"work-{idx}".encode()),
            f"file_{idx}.pdf",
            2,
        )
        if idx % 3 == 0:
            sub_store.set_review_status(sub.id, "reviewed")

    # Give Колотович Олег and колотович олег colliding submissions on same topic t1
    sub_store.submit(group.id, UUID(student_ids["колотович олег"]), a1.id, io.BytesIO(b"extra-k"), "k.pdf", 2)

    return {
        "db_path": db_path,
        "storage_root": storage_root,
        "group": group,
        "student_ids": student_ids,
        "total_initial_submissions": len(raw_names) + 1,
    }


def test_analyze_detects_all_production_issue_categories(tmp_path: Path) -> None:
    env = _seed_production_like_db(tmp_path)
    db_path = env["db_path"]
    plan_path = tmp_path / "plan.json"
    repo_root = Path(__file__).resolve().parents[1]

    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.normalize_students",
            "analyze",
            "--database",
            str(db_path),
            "--output",
            str(plan_path),
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert plan_path.is_file()

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    summary = plan["summary"]
    assert summary["exact_merge_groups_count"] == 4  # Колотович, Дудинов, Мельников(4), Чепак(2)
    assert summary["renames_only_count"] == 6  # Шахлина, Бронников, Манько, Мухетдинов, Гасанова, Бобоназарзода
    assert summary["paired_students_count"] == 1  # Гилюк Даниил и Бондаренко Владислав
    assert summary["fuzzy_candidate_groups_count"] >= 1  # Мельников Богдан <-> Мельников Бгодан


def test_apply_auto_with_dry_run_and_backup_preserves_all_links(tmp_path: Path) -> None:
    env = _seed_production_like_db(tmp_path)
    db_path: Path = env["db_path"]  # type: ignore[assignment]
    storage_root: Path = env["storage_root"]  # type: ignore[assignment]
    backup_dir = tmp_path / "backups"
    repo_root = Path(__file__).resolve().parents[1]

    # 1. Dry-run must NOT mutate DB or create backups
    dry = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.normalize_students",
            "apply",
            "--database",
            str(db_path),
            "--storage",
            str(storage_root),
            "--auto",
            "--split-paired",
            "--dry-run",
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert dry.returncode == 0, dry.stderr
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 19

    # 2. Real apply with --auto --split-paired --backup-dir
    ap = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.normalize_students",
            "apply",
            "--database",
            str(db_path),
            "--storage",
            str(storage_root),
            "--backup-dir",
            str(backup_dir),
            "--auto",
            "--split-paired",
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert ap.returncode == 0, ap.stderr
    validate(db_path)

    # Backup must exist with manifest
    backup_runs = list(backup_dir.iterdir())
    assert len(backup_runs) == 1
    assert (backup_runs[0] / "manifest.json").is_file()
    assert (backup_runs[0] / db_path.name).is_file()

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        fk = conn.execute("PRAGMA foreign_key_check;").fetchall()
        assert fk == []

        names = {r["display_name"] for r in conn.execute("SELECT display_name FROM students").fetchall()}
        expected_clean_names = {
            "Колотович Олег",
            "Дудинов Кирилл",
            "Мельников Богдан",
            "Мельников Бгодан",  # fuzzy candidate untouched by --auto unless approved
            "Шахлина Дарья",
            "Бронников Антон",
            "Манько Еремей",
            "Мухетдинов Кирилл",
            "Чепак Савелий",
            "Гасанова Лейла",
            "Бобоназарзода Мухаммадсаид",
            "Гилюк Даниил",
            "Бондаренко Владислав",
        }
        assert names == expected_clean_names

        # Paired submission was cloned so total submissions increased by 1
        sub_count = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        assert sub_count == int(env["total_initial_submissions"]) + 1  # type: ignore[arg-type]

        # Колотович Олег has all 3 submissions (2 on t1 + 1 on t2? Wait: idx 0, 1 + extra)
        k_row = conn.execute("SELECT id FROM students WHERE display_name = 'Колотович Олег'").fetchone()
        k_subs = conn.execute(
            "SELECT topic_id, attempt_number FROM submissions WHERE student_id = ? ORDER BY topic_id, attempt_number",
            (k_row["id"],),
        ).fetchall()
        assert len(k_subs) == 3

        # Мельников Богдан absorbed all 4 patronymic variants -> 4 submissions
        m_row = conn.execute("SELECT id FROM students WHERE display_name = 'Мельников Богдан'").fetchone()
        m_count = conn.execute("SELECT COUNT(*) FROM submissions WHERE student_id = ?", (m_row["id"],)).fetchone()[0]
        assert m_count == 4

    assert reconcile_storage(db_path, storage_root)["status"] == "ok"


def test_apply_plan_with_approved_fuzzy_merge(tmp_path: Path) -> None:
    env = _seed_production_like_db(tmp_path)
    db_path: Path = env["db_path"]  # type: ignore[assignment]
    storage_root: Path = env["storage_root"]  # type: ignore[assignment]
    plan_path = tmp_path / "plan.json"
    backup_dir = tmp_path / "backups"
    repo_root = Path(__file__).resolve().parents[1]

    subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.normalize_students",
            "analyze",
            "--database",
            str(db_path),
            "--output",
            str(plan_path),
        ],
        cwd=str(repo_root),
        check=True,
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    for grp in plan["groups"]:
        for fc in grp["fuzzy_candidates"]:
            fc["approved"] = True
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.normalize_students",
            "apply",
            "--database",
            str(db_path),
            "--storage",
            str(storage_root),
            "--backup-dir",
            str(backup_dir),
            "--plan",
            str(plan_path),
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr

    with sqlite3.connect(db_path) as conn:
        names = {r[0] for r in conn.execute("SELECT display_name FROM students").fetchall()}
        assert "Мельников Бгодан" not in names
        assert "Мельников Богдан" in names
        m_id = conn.execute("SELECT id FROM students WHERE display_name = 'Мельников Богдан'").fetchone()[0]
        assert conn.execute("SELECT COUNT(*) FROM submissions WHERE student_id = ?", (m_id,)).fetchone()[0] == 5
    assert reconcile_storage(db_path, storage_root)["status"] == "ok"
