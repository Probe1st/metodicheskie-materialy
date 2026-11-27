"""Comprehensive tests for the AI agent REST API (/api/v1/*) and admin grading/token UI."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.access import AccessStore
from app.assignments import AssignmentStore
from app.database import initialize
from app.sqlite_catalog import SqliteCatalogStore
from app.submissions import SubmissionStore
from app.task_content import TaskContentStore
from app.tokens import TokenStore
from app.web import create_app


@pytest.fixture
def api_env(tmp_path: Path):
    private_dir = tmp_path / ".private"
    private_dir.mkdir(parents=True, exist_ok=True)
    db_path = private_dir / "app_v2.sqlite3"
    storage_root = tmp_path / "storage"
    storage_root.mkdir(parents=True, exist_ok=True)
    attachments_dir = private_dir / "task-attachments"

    initialize(db_path)
    cat = SqliteCatalogStore(db_path)
    g1 = cat.create_group("ПИ-21")
    g2 = cat.create_group("ИС-22")
    s1 = cat.create_subject("Программирование")
    t1 = cat.create_topic(s1.id, "Лабораторная 1")
    t2 = cat.create_topic(s1.id, "Лабораторная 2")

    acc_store = AccessStore(db_path)
    acc1 = acc_store.configure(g1.id, is_open=True, password="group-secret-password")

    asgn_store = AssignmentStore(db_path)
    a1 = asgn_store.create(g1.id, t1.id, "2026-11-10", 1)
    a2 = asgn_store.create(g1.id, t2.id, "2026-11-12", 2)

    tc_store = TaskContentStore(db_path, attachments_dir)
    topic_att = tc_store.save_attachment("topic_guide.pdf", b"%PDF-1.4 topic guide")
    tc_store.set_content("topic", t2.id, "# Методичка темы 2\n\nРешить задачи.", "md", topic_att.id)

    lesson_att = tc_store.save_attachment("lab1_starter.py", b"def solve():\n    pass\n")
    tc_store.set_content("lesson", a1.id, "Напишите функцию `solve()`.", "md", lesson_att.id)

    sub_store = SubmissionStore(db_path, storage_root)
    st1 = sub_store.create_student(g1.id, "Иванов Иван")
    st2 = sub_store.create_student(g1.id, "Петрова Анна")
    sub1, _ = sub_store.submit(
        g1.id, st1.id, a1.id, io.BytesIO(b"def solve():\n    return 42\n"), "solution.py", acc1.version
    )
    sub2, _ = sub_store.submit(
        g1.id, st2.id, a2.id, io.BytesIO(b"answer text"), "report.txt", acc1.version
    )

    tok_store = TokenStore(db_path)
    tok_obj, raw_token = tok_store.create_token("grader-agent")

    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "ADMIN_PASSWORD": "admin-secret-password",
        "PRIVATE_DATA_DIR": private_dir,
        "DATABASE_PATH": db_path,
        "STORAGE_ROOT": storage_root,
        "TASK_ATTACHMENTS_DIR": attachments_dir,
        "AGENT_API_TOKEN": "env-static-agent-token",
        "WTF_CSRF_ENABLED": True,
    })

    return {
        "app": app,
        "client": app.test_client(),
        "db_path": db_path,
        "token": raw_token,
        "token_id": tok_obj.id,
        "env_token": "env-static-agent-token",
        "g1": g1,
        "g2": g2,
        "s1": s1,
        "t1": t1,
        "t2": t2,
        "a1": a1,
        "a2": a2,
        "st1": st1,
        "st2": st2,
        "sub1": sub1,
        "sub2": sub2,
        "lesson_att": lesson_att,
        "topic_att": topic_att,
    }


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_api_authentication_and_csrf_isolation(api_env) -> None:
    client = api_env["client"]
    sub1 = api_env["sub1"]

    # 1. Missing token -> 401
    r_no_auth = client.get("/api/v1/me")
    assert r_no_auth.status_code == 401
    assert r_no_auth.get_json()["error"] == "unauthorized"

    # 2. Invalid token -> 401
    r_bad = client.get("/api/v1/me", headers=_auth_headers("invalid-token"))
    assert r_bad.status_code == 401

    # 3. Admin browser session cookie does NOT authenticate /api/v1
    with client.session_transaction() as sess:
        sess["admin"] = True
    r_cookie = client.get("/api/v1/catalog")
    assert r_cookie.status_code == 401

    # 4. Valid DB token works and mutating POST succeeds WITHOUT CSRF token
    r_me = client.get("/api/v1/me", headers=_auth_headers(api_env["token"]))
    assert r_me.status_code == 200
    me_data = r_me.get_json()
    assert me_data["actor"]["name"] == "grader-agent"
    assert me_data["actor"]["label"] == "agent:grader-agent"
    assert me_data["summary"]["submissions_total"] == 2

    r_grade = client.post(
        f"/api/v1/submissions/{sub1.id}/grade",
        json={"grade": 5},
        headers=_auth_headers(api_env["token"]),
    )
    assert r_grade.status_code == 200

    # 5. X-API-Key with env static token works
    r_env = client.get("/api/v1/me", headers={"X-API-Key": api_env["env_token"]})
    assert r_env.status_code == 200
    assert r_env.get_json()["actor"]["source"] == "env"

    # 6. Revoked token rejected
    TokenStore(api_env["db_path"]).revoke_token(api_env["token_id"])
    r_revoked = client.get("/api/v1/me", headers=_auth_headers(api_env["token"]))
    assert r_revoked.status_code == 401


def test_api_read_everything_endpoints(api_env) -> None:
    client = api_env["client"]
    headers = _auth_headers(api_env["token"])
    g1, s1, t1, t2, a1, st1 = (
        api_env["g1"],
        api_env["s1"],
        api_env["t1"],
        api_env["t2"],
        api_env["a1"],
        api_env["st1"],
    )

    # Catalog
    cat_res = client.get("/api/v1/catalog", headers=headers)
    assert cat_res.status_code == 200
    cat_json = cat_res.get_json()
    assert "password_hash" not in cat_res.get_data(as_text=True)
    assert len(cat_json["groups"]) == 2
    assert len(cat_json["topics"]) == 2

    # Groups & group detail
    grp_res = client.get(f"/api/v1/groups/{g1.id}", headers=headers)
    assert grp_res.status_code == 200
    grp_data = grp_res.get_json()
    assert grp_data["access"]["is_open"] is True
    assert "password_hash" not in grp_res.get_data(as_text=True)
    assert len(grp_data["students"]) == 2
    assert len(grp_data["assignments"]) == 2

    # Group students
    gs_res = client.get(f"/api/v1/groups/{g1.id}/students", headers=headers)
    assert gs_res.status_code == 200
    assert len(gs_res.get_json()["items"]) == 2

    # Students search & detail
    st_list = client.get("/api/v1/students?q=Иванов", headers=headers)
    assert st_list.status_code == 200
    assert st_list.get_json()["total"] == 1

    st_det = client.get(f"/api/v1/students/{st1.id}", headers=headers)
    assert st_det.status_code == 200
    assert len(st_det.get_json()["submissions"]) == 1

    # Subjects & topics
    subj_res = client.get(f"/api/v1/subjects/{s1.id}", headers=headers)
    assert subj_res.status_code == 200
    assert len(subj_res.get_json()["topics"]) == 2

    top_res = client.get(f"/api/v1/topics/{t2.id}", headers=headers)
    assert top_res.status_code == 200
    assert top_res.get_json()["task"]["has_task"] is True
    assert "Методичка темы 2" in top_res.get_json()["task"]["description"]

    # Assignments list & detail
    asgn_list = client.get(f"/api/v1/assignments?group_id={g1.id}&lesson_date=2026-11-10", headers=headers)
    assert asgn_list.status_code == 200
    assert asgn_list.get_json()["total"] == 1
    assert asgn_list.get_json()["items"][0]["has_description"] is True
    assert asgn_list.get_json()["items"][0]["has_attachment"] is True

    asgn_det = client.get(f"/api/v1/assignments/{a1.id}", headers=headers)
    assert asgn_det.status_code == 200
    assert asgn_det.get_json()["task"]["attachment"]["original_name"] == "lab1_starter.py"
    assert len(asgn_det.get_json()["submissions"]) == 1


def test_api_downloads_submissions_and_assignments(api_env) -> None:
    client = api_env["client"]
    headers = _auth_headers(api_env["token"])
    a1, a2, t2, sub1 = api_env["a1"], api_env["a2"], api_env["t2"], api_env["sub1"]

    # 1. Download student submission file
    dl_sub = client.get(f"/api/v1/submissions/{sub1.id}/download", headers=headers)
    assert dl_sub.status_code == 200
    assert dl_sub.data == b"def solve():\n    return 42\n"
    assert dl_sub.headers.get("X-Content-Type-Options") == "nosniff"
    assert "solution.py" in dl_sub.headers.get("Content-Disposition", "")

    # 2. Download assignment task text (raw) and attachment
    dl_task_raw = client.get(f"/api/v1/assignments/{a1.id}/task?format=raw", headers=headers)
    assert dl_task_raw.status_code == 200
    assert "solve()" in dl_task_raw.get_data(as_text=True)
    assert ".md" in dl_task_raw.headers.get("Content-Disposition", "")

    dl_att = client.get(f"/api/v1/assignments/{a1.id}/attachment", headers=headers)
    assert dl_att.status_code == 200
    assert dl_att.data == b"def solve():\n    pass\n"
    assert "lab1_starter.py" in dl_att.headers.get("Content-Disposition", "")

    # 3. Assignment a2 has no own task, fallback_to_topic=1 downloads topic t2 task/attachment
    dl_a2_att_no_fb = client.get(f"/api/v1/assignments/{a2.id}/attachment", headers=headers)
    assert dl_a2_att_no_fb.status_code == 404
    dl_a2_att_fb = client.get(f"/api/v1/assignments/{a2.id}/attachment?fallback_to_topic=1", headers=headers)
    assert dl_a2_att_fb.status_code == 200
    assert dl_a2_att_fb.data == b"%PDF-1.4 topic guide"

    # 4. Topic attachment direct & attachment_id direct
    dl_t2_att = client.get(f"/api/v1/topics/{t2.id}/attachment", headers=headers)
    assert dl_t2_att.status_code == 200
    assert dl_t2_att.data == b"%PDF-1.4 topic guide"

    dl_by_id = client.get(f"/api/v1/attachments/{api_env['lesson_att'].id}/download", headers=headers)
    assert dl_by_id.status_code == 200
    assert dl_by_id.data == b"def solve():\n    pass\n"

    # 5. Assignment ZIP bundle
    dl_bundle = client.get(f"/api/v1/assignments/{a1.id}/bundle", headers=headers)
    assert dl_bundle.status_code == 200
    with zipfile.ZipFile(io.BytesIO(dl_bundle.data), "r") as zf:
        names = set(zf.namelist())
        assert "metadata.json" in names
        assert "task.md" in names
        assert "attachments/lab1_starter.py" in names
        assert zf.read("attachments/lab1_starter.py") == b"def solve():\n    pass\n"


def test_api_grading_and_filtering_submissions(api_env) -> None:
    client = api_env["client"]
    headers = _auth_headers(api_env["token"])
    sub1, sub2 = api_env["sub1"], api_env["sub2"]

    # Grade sub1 with numeric grade and feedback -> auto review_status='reviewed'
    res_grade = client.post(
        f"/api/v1/submissions/{sub1.id}/grade",
        json={"grade": 5, "feedback": "Отличное решение!"},
        headers=headers,
    )
    assert res_grade.status_code == 200
    g_data = res_grade.get_json()
    assert g_data["grade"] == "5"
    assert g_data["feedback"] == "Отличное решение!"
    assert g_data["review_status"] == "reviewed"
    assert g_data["reviewed_at"] is not None
    assert g_data["graded_at"] is not None
    assert g_data["graded_by"] == "agent:grader-agent"

    # Filter submissions: graded=true, graded=false, review_status=pending, grade=5
    res_graded = client.get("/api/v1/submissions?graded=true", headers=headers)
    assert res_graded.status_code == 200
    assert res_graded.get_json()["total"] == 1
    assert res_graded.get_json()["items"][0]["id"] == str(sub1.id)

    res_ungraded = client.get("/api/v1/submissions?graded=false&review_status=pending", headers=headers)
    assert res_ungraded.status_code == 200
    assert res_ungraded.get_json()["total"] == 1
    assert res_ungraded.get_json()["items"][0]["id"] == str(sub2.id)

    # Single submission detail includes task + grade
    res_det = client.get(f"/api/v1/submissions/{sub1.id}", headers=headers)
    assert res_det.status_code == 200
    assert res_det.get_json()["grade"] == "5"
    assert res_det.get_json()["task"]["source"] == "lesson"

    # Empty grade rejected with 400
    res_empty = client.post(
        f"/api/v1/submissions/{sub2.id}/grade",
        json={"grade": "   "},
        headers=headers,
    )
    assert res_empty.status_code == 400

    # Clear grade with reset_status=true -> pending & grade=None
    res_del = client.delete(
        f"/api/v1/submissions/{sub1.id}/grade?reset_status=1",
        headers=headers,
    )
    assert res_del.status_code == 200
    assert res_del.get_json()["grade"] is None
    assert res_del.get_json()["review_status"] == "pending"


def test_api_allowed_grades_validation(api_env) -> None:
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "ADMIN_PASSWORD": "admin-secret-password",
        "DATABASE_PATH": api_env["db_path"],
        "STORAGE_ROOT": api_env["app"].config["STORAGE_ROOT"],
        "TASK_ATTACHMENTS_DIR": api_env["app"].config["TASK_ATTACHMENTS_DIR"],
        "ALLOWED_GRADES": "2,3,4,5,зачёт",
        "WTF_CSRF_ENABLED": True,
    })
    client = app.test_client()
    headers = _auth_headers(api_env["token"])
    sub1 = api_env["sub1"]

    bad = client.patch(f"/api/v1/submissions/{sub1.id}/grade", json={"grade": "100"}, headers=headers)
    assert bad.status_code == 400
    assert bad.get_json()["error"] == "validation_error"

    ok = client.patch(f"/api/v1/submissions/{sub1.id}/grade", json={"grade": "ЗАЧЁТ"}, headers=headers)
    assert ok.status_code == 200
    assert ok.get_json()["grade"] == "зачёт"


def test_admin_tokens_and_submissions_grading_ui(api_env) -> None:
    # Disable CSRF for simple form testing, or test with CSRF token
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "ADMIN_PASSWORD": "admin-secret-password",
        "DATABASE_PATH": api_env["db_path"],
        "STORAGE_ROOT": api_env["app"].config["STORAGE_ROOT"],
        "TASK_ATTACHMENTS_DIR": api_env["app"].config["TASK_ATTACHMENTS_DIR"],
        "WTF_CSRF_ENABLED": False,
    })
    client = app.test_client()
    sub1 = api_env["sub1"]

    # Unauthenticated blocked
    assert client.get("/admin/tokens").status_code == 403

    client.post("/admin/login", data={"password": "admin-secret-password"})

    # Create token via JSON
    res_tok = client.post(
        "/admin/tokens",
        json={"name": "ui-agent"},
        headers={"Accept": "application/json"},
    )
    assert res_tok.status_code == 201
    tok_data = res_tok.get_json()
    assert tok_data["token"].startswith("ag_")

    # Tokens page renders token name & prefix (never plaintext)
    page = client.get("/admin/tokens")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "ui-agent" in html
    assert tok_data["token"] not in html

    # Grade submission via admin review form
    rev = client.post(
        f"/admin/submissions/{sub1.id}/review",
        data={"grade": "4", "feedback": "Хорошо, но есть замечания"},
    )
    assert rev.status_code == 303

    subs_page = client.get("/admin/submissions")
    assert subs_page.status_code == 200
    subs_html = subs_page.get_data(as_text=True)
    assert "Хорошо, но есть замечания" in subs_html
    assert "admin" in subs_html

    # Revoke token
    rev_tok = client.post(f"/admin/tokens/{tok_data['id']}/revoke")
    assert rev_tok.status_code == 303
    assert UUID(tok_data["id"])
    assert client.get("/api/v1/me", headers=_auth_headers(tok_data["token"])).status_code == 401


def test_api_token_scopes_and_strict_json_booleans(api_env) -> None:
    client = api_env["client"]
    sub1 = api_env["sub1"]
    tok_store = TokenStore(api_env["db_path"])
    _, read_token = tok_store.create_token("read-only-agent", scopes="read")

    read_headers = _auth_headers(read_token)
    assert client.get("/api/v1/me", headers=read_headers).status_code == 200
    assert client.get("/api/v1/submissions", headers=read_headers).status_code == 200
    assert client.get(f"/api/v1/submissions/{sub1.id}/download", headers=read_headers).status_code == 200

    # Read-only token cannot mutate grades or review status
    assert client.post(
        f"/api/v1/submissions/{sub1.id}/grade",
        json={"grade": 5},
        headers=read_headers,
    ).status_code == 403
    assert client.delete(
        f"/api/v1/submissions/{sub1.id}/grade",
        headers=read_headers,
    ).status_code == 403
    assert client.post(
        f"/api/v1/submissions/{sub1.id}/review",
        json={"status": "reviewed"},
        headers=read_headers,
    ).status_code == 403

    # Strict boolean parsing rejects string "false"
    agent_headers = _auth_headers(api_env["token"])
    bad_clear = client.post(
        f"/api/v1/submissions/{sub1.id}/grade",
        json={"grade": 5, "clear_feedback": "false"},
        headers=agent_headers,
    )
    assert bad_clear.status_code == 400
    assert bad_clear.get_json()["error"] == "validation_error"

    bad_reset = client.delete(
        f"/api/v1/submissions/{sub1.id}/grade",
        json={"reset_status": "false"},
        headers=agent_headers,
    )
    assert bad_reset.status_code == 400
    assert bad_reset.get_json()["error"] == "validation_error"


def test_review_feedback_preservation_and_admin_html_validation_errors(api_env) -> None:
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "ADMIN_PASSWORD": "admin-secret-password",
        "DATABASE_PATH": api_env["db_path"],
        "STORAGE_ROOT": api_env["app"].config["STORAGE_ROOT"],
        "TASK_ATTACHMENTS_DIR": api_env["app"].config["TASK_ATTACHMENTS_DIR"],
        "ALLOWED_GRADES": "2,3,4,5",
        "WTF_CSRF_ENABLED": False,
    })
    client = app.test_client()
    sub1 = api_env["sub1"]
    headers = _auth_headers(api_env["token"])

    # 1. Pending review via API with feedback preserves feedback
    r_pend = client.post(
        f"/api/v1/submissions/{sub1.id}/review",
        json={"status": "pending", "feedback": "Нужно доработать раздел 2"},
        headers=headers,
    )
    assert r_pend.status_code == 200
    assert r_pend.get_json()["review_status"] == "pending"
    assert r_pend.get_json()["feedback"] == "Нужно доработать раздел 2"

    # 2. Admin login and verify HTML form save on reviewed submission keeps reviewed status & updates feedback
    client.post("/admin/login", data={"password": "admin-secret-password"})
    page_html = client.get("/admin/submissions").get_data(as_text=True)
    # Save form hidden input must always be value="reviewed"
    assert '<input type="hidden" name="status" value="reviewed">' in page_html

    r_save = client.post(
        f"/admin/submissions/{sub1.id}/review",
        data={"status": "reviewed", "grade": "", "feedback": "Проверено без оценки"},
    )
    assert r_save.status_code == 303

    sub_after = client.get(f"/api/v1/submissions/{sub1.id}", headers=headers).get_json()
    assert sub_after["review_status"] == "reviewed"
    assert sub_after["grade"] is None
    assert sub_after["feedback"] == "Проверено без оценки"

    # 3. Admin HTML form invalid grade returns 400 HTML (not raw JSON)
    r_bad_grade = client.post(
        f"/admin/submissions/{sub1.id}/review",
        data={"status": "reviewed", "grade": "999", "feedback": "test"},
        headers={"Accept": "text/html"},
    )
    assert r_bad_grade.status_code == 400
    assert "text/html" in r_bad_grade.content_type
    assert "Invalid grade" in r_bad_grade.get_data(as_text=True)

    # 4. Admin HTML form duplicate token name returns 409 HTML (not raw JSON)
    r_dup_tok = client.post(
        "/admin/tokens",
        data={"name": "grader-agent", "scopes": "agent"},
        headers={"Accept": "text/html"},
    )
    assert r_dup_tok.status_code == 409
    assert "text/html" in r_dup_tok.content_type
    assert "already exists" in r_dup_tok.get_data(as_text=True)

