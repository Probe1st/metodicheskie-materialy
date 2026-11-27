"""Tests for /admin/students web routes and student normalization/merge UI endpoints."""

from __future__ import annotations

import sqlite3
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from flask.testing import FlaskClient

from app.access import AccessStore
from app.assignments import AssignmentStore
from app.database import connect, initialize
from app.sqlite_catalog import SqliteCatalogStore
from app.submissions import Student, SubmissionStore
from app.web import create_app


def _insert_legacy_student(db_path: Path, group_id: UUID, display_name: str) -> Student:
    sid = uuid4()
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO students (id, group_id, display_name, archived_at) VALUES (?, ?, ?, NULL)",
            (str(sid), str(group_id), display_name),
        )
        conn.commit()
    return Student(id=sid, group_id=group_id, display_name=display_name, archived_at=None)


@pytest.fixture
def admin_env(tmp_path: Path):
    db_path = tmp_path / "app_v2.sqlite3"
    initialize(db_path)
    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True, exist_ok=True)

    catalog = SqliteCatalogStore(db_path)
    group = catalog.create_group("ИС 25 9")
    other_group = catalog.create_group("ИС 25 11")
    subject = catalog.create_subject("Информатика")
    topic = catalog.create_topic(subject.id, "Практическая работа 1")

    AccessStore(db_path).configure(group.id, is_open=True, password="secret")
    AccessStore(db_path).configure(other_group.id, is_open=True, password="secret")

    assignments = AssignmentStore(db_path)
    a1 = assignments.create(group.id, topic.id, "2026-11-01", 1)
    a2 = assignments.create(group.id, topic.id, "2026-11-02", 2)

    store = SubmissionStore(db_path, storage_root)
    acc = AccessStore(db_path).get(group.id)
    assert acc is not None

    # Insert dirty legacy rows directly into SQLite (mimicking unnormalized production DB)
    st_upper = _insert_legacy_student(db_path, group.id, "Колотович Олег")
    st_lower = _insert_legacy_student(db_path, group.id, "колотович олег")
    st_tail = _insert_legacy_student(db_path, group.id, "Манько Еремей 1. manko.drawio")
    st_fuzzy_a = _insert_legacy_student(db_path, group.id, "Антонова Дарья")
    st_fuzzy_b = _insert_legacy_student(db_path, group.id, "Антонова Даря")
    st_paired = _insert_legacy_student(db_path, group.id, "Гилюк Даниил и Бондаренко Владислав")
    st_double = _insert_legacy_student(db_path, group.id, "Иванов-Петров Анна-Мария")
    st_other_group = _insert_legacy_student(db_path, other_group.id, "Сидоров Петр")

    store.submit(group.id, st_upper.id, a1.id, BytesIO(b"upper file"), "u.zip", acc.version)
    store.submit(group.id, st_lower.id, a2.id, BytesIO(b"lower file"), "l.zip", acc.version)
    store.submit(group.id, st_tail.id, a1.id, BytesIO(b"tail file"), "manko.drawio", acc.version)
    store.submit(group.id, st_paired.id, a1.id, BytesIO(b"pair work"), "pair.zip", acc.version)

    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "ADMIN_PASSWORD": "admin-password",
        "DATABASE_PATH": db_path,
        "STORAGE_ROOT": storage_root,
        "MAX_UPLOAD_MB": 50,
        "WTF_CSRF_ENABLED": False,
    })

    return {
        "app": app,
        "client": app.test_client(),
        "db_path": db_path,
        "storage_root": storage_root,
        "store": store,
        "group": group,
        "other_group": other_group,
        "students": {
            "upper": st_upper,
            "lower": st_lower,
            "tail": st_tail,
            "fuzzy_a": st_fuzzy_a,
            "fuzzy_b": st_fuzzy_b,
            "paired": st_paired,
            "double": st_double,
            "other_group": st_other_group,
        },
    }


def _login_admin(client: FlaskClient) -> None:
    res = client.post("/admin/login", data={"password": "admin-password"})
    assert res.status_code == 204


def test_admin_students_requires_admin_auth(admin_env):
    client: FlaskClient = admin_env["client"]
    gid = admin_env["group"].id
    sid = admin_env["students"]["upper"].id

    assert client.get("/admin/students").status_code == 403
    assert client.post("/admin/students/normalize-group", json={"group_id": str(gid)}).status_code == 403
    assert client.post("/admin/students/merge", json={"group_id": str(gid)}).status_code == 403
    assert client.post(f"/admin/students/{sid}/rename", json={"display_name": "Тест Тест"}).status_code == 403
    assert client.post(f"/admin/students/{sid}/split", json={"group_id": str(gid)}).status_code == 403
    assert client.post(f"/admin/students/{sid}/archive").status_code == 403
    assert client.post(f"/admin/students/{sid}/restore").status_code == 403


def test_admin_students_page_lists_anomalies_and_filters(admin_env):
    client: FlaskClient = admin_env["client"]
    _login_admin(client)
    gid = admin_env["group"].id

    res = client.get(f"/admin/students?group_id={gid}")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    assert "Колотович Олег" in html
    assert "колотович олег" in html
    assert "Манько Еремей 1. manko.drawio" in html
    assert "Антонова Дарья" in html
    assert "Антонова Даря" in html
    assert "Гилюк Даниил и Бондаренко Владислав" in html
    assert "Иванов-Петров Анна-Мария" in html
    assert "Сидоров Петр" not in html

    # issues=1 excludes clean records like Ivanov-Petrov Anna-Maria
    issues_res = client.get(f"/admin/students?group_id={gid}&issues=1")
    assert issues_res.status_code == 200
    issues_html = issues_res.get_data(as_text=True)
    assert "колотович олег" in issues_html
    assert "Иванов-Петров Анна-Мария" not in issues_html


def test_admin_students_normalize_group_merges_and_preserves_submissions(admin_env):
    client: FlaskClient = admin_env["client"]
    _login_admin(client)
    gid = admin_env["group"].id
    db_path = admin_env["db_path"]

    res = client.post(
        "/admin/students/normalize-group",
        json={"group_id": str(gid), "apply_fuzzy": False, "split_paired": False},
        headers={"Accept": "application/json"},
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "ok"
    assert data["merged_groups"] >= 1
    assert data["renamed_students"] >= 1

    # Submissions count unchanged (all 4 intact)
    with sqlite3.connect(str(db_path)) as conn:
        sub_count = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        names = [
            r[0]
            for r in conn.execute(
                "SELECT display_name FROM students WHERE group_id = ?",
                (str(gid),),
            ).fetchall()
        ]
    assert sub_count == 4
    assert "колотович олег" not in names
    assert "Колотович Олег" in names
    assert "Манько Еремей" in names
    assert "Манько Еремей 1. manko.drawio" not in names


def test_admin_students_manual_merge_and_cross_group_validation(admin_env):
    client: FlaskClient = admin_env["client"]
    _login_admin(client)
    gid = admin_env["group"].id
    st_a = admin_env["students"]["fuzzy_a"]
    st_b = admin_env["students"]["fuzzy_b"]
    st_other = admin_env["students"]["other_group"]

    # Cross-group merge must fail
    bad_res = client.post(
        "/admin/students/merge",
        json={
            "group_id": str(gid),
            "target_student_id": str(st_a.id),
            "source_student_ids": [str(st_other.id)],
        },
        headers={"Accept": "application/json"},
    )
    assert bad_res.status_code == 400

    # Same-group fuzzy merge succeeds
    res = client.post(
        "/admin/students/merge",
        json={
            "group_id": str(gid),
            "target_student_id": str(st_a.id),
            "source_student_ids": [str(st_b.id)],
            "target_display_name": "Антонова Дарья",
        },
        headers={"Accept": "application/json"},
    )
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["target_student"]["display_name"] == "Антонова Дарья"
    assert admin_env["store"].get_student(st_b.id) is None


def test_admin_students_rename_and_hyphenated_double_names(admin_env):
    client: FlaskClient = admin_env["client"]
    _login_admin(client)
    st_tail = admin_env["students"]["tail"]

    # Reject renaming individual record to a paired string
    paired_res = client.post(
        f"/admin/students/{st_tail.id}/rename",
        json={"display_name": "Иванов Иван и Петров Петр"},
        headers={"Accept": "application/json"},
    )
    assert paired_res.status_code == 400

    res = client.post(
        f"/admin/students/{st_tail.id}/rename",
        json={"display_name": "манько-петров еремей-илья иванович"},
        headers={"Accept": "application/json"},
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["student"]["display_name"] == "Манько-Петров Еремей-Илья"
    assert data["warnings"]  # patronymic stripped -> warns format might need check

    # Clean hyphenated name -> no warning
    res_clean = client.post(
        f"/admin/students/{st_tail.id}/rename",
        json={"display_name": "Манько-Петров Еремей-Илья"},
        headers={"Accept": "application/json"},
    )
    assert res_clean.status_code == 200
    assert res_clean.get_json()["warnings"] == []


def test_admin_students_split_paired_preserves_submission_files(admin_env):
    client: FlaskClient = admin_env["client"]
    _login_admin(client)
    gid = admin_env["group"].id
    st_paired = admin_env["students"]["paired"]
    store: SubmissionStore = admin_env["store"]

    res = client.post(
        f"/admin/students/{st_paired.id}/split",
        json={
            "group_id": str(gid),
            "targets": [
                {"display_name": "Гилюк Даниил"},
                {"display_name": "Бондаренко Владислав"},
            ],
        },
        headers={"Accept": "application/json"},
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["moved_submissions"] == 1
    assert data["copied_submissions"] == 1
    assert len(data["target_students"]) == 2
    assert store.get_student(st_paired.id) is None

    # Both files exist on disk and match content
    all_subs = store.list_submissions(group_id=gid)
    paired_files = [
        store.get_file(sub.id)[0].read_bytes()
        for sub in all_subs
        if sub.student_display_name in ("Гилюк Даниил", "Бондаренко Владислав")
    ]
    assert paired_files == [b"pair work", b"pair work"]


def test_admin_students_archive_and_restore(admin_env):
    client: FlaskClient = admin_env["client"]
    _login_admin(client)
    st_double = admin_env["students"]["double"]

    arch = client.post(
        f"/admin/students/{st_double.id}/archive",
        headers={"Accept": "application/json"},
    )
    assert arch.status_code == 200
    assert arch.get_json()["archived_at"] is not None

    # Archived student is hidden from fuzzy/paired cards when include_archived=0, shown & tagged when include_archived=1
    st_fuzzy_b = admin_env["students"]["fuzzy_b"]
    client.post(f"/admin/students/{st_fuzzy_b.id}/archive", headers={"Accept": "application/json"})
    gid = admin_env["group"].id
    html_active = client.get(f"/admin/students?group_id={gid}").get_data(as_text=True)
    assert "Антонова Даря" not in html_active
    html_with_arch = client.get(f"/admin/students?group_id={gid}&include_archived=1").get_data(as_text=True)
    assert "Антонова Даря" in html_with_arch
    assert "архив" in html_with_arch
    # Radio inputs in same fuzzy card share identical name in SSR HTML
    assert f'name="fuzzy_target_{gid}_1"' in html_with_arch
    assert f'name="fuzzy_target_{gid}_2"' not in html_with_arch

    rest = client.post(
        f"/admin/students/{st_double.id}/restore",
        headers={"Accept": "application/json"},
    )
    assert rest.status_code == 200
    assert rest.get_json()["archived_at"] is None
