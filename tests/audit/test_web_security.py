"""Audit test suite for web endpoints and security contracts (WEB-01..06, SEC-01..03).

Covers authentication boundaries, session verification, assignment source-of-truth,
catalog administration, review workflows, path traversal, XSS escaping,
request entity limits, SECRET_KEY forgery, CSRF exposure, and information disclosure.
"""

from __future__ import annotations

import hashlib
import io
from typing import Any
from uuid import UUID, uuid4

import pytest
from flask.json.tag import TaggedJSONSerializer
from itsdangerous import URLSafeTimedSerializer

from app.web import create_app
from tests.audit.conftest import AuditEnv
from tests.audit.support import (
    A1_DATE,
    A1_ID,
    A1_PAIR,
    G1_ACCESS_VERSION,
    G1_ID,
    G1_PASSWORD,
    G2_ID,
    S2_ID,
    STUDENT1_ID,
    STUDENT1_NAME,
    T1_ID,
    T2_ID,
)


def _forge_session_cookie(data: dict[str, Any], secret_key: str = "test-secret-key-at-least-32-bytes-long-for-testing") -> str:
    """Helper to forge a signed Flask session cookie using a known secret key."""
    serializer = URLSafeTimedSerializer(
        secret_key,
        salt="cookie-session",
        serializer=TaggedJSONSerializer(),
        signer_kwargs={"key_derivation": "hmac", "digest_method": hashlib.sha1},
    )
    return str(serializer.dumps(data))


def _create_test_app(audit_env: AuditEnv, **extra_config: Any) -> Any:
    """Helper to create and configure a Flask test app with populated canonical DB."""
    db_path = audit_env.populate_canonical()
    config = {
        "TESTING": True,
        "DATABASE_PATH": db_path,
        "STORAGE_ROOT": audit_env.storage,
        "ADMIN_PASSWORD": "super-admin-secret",
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "MAX_UPLOAD_MB": 2,
        "WTF_CSRF_ENABLED": False,
    }
    config.update(extra_config)
    return create_app(config)


# ===========================================================================
# WEB-01: Authentication & Authorization Boundaries
# ===========================================================================

@pytest.mark.parametrize("case_id", ["WEB-01-01"])
def test_web_01_01_student_session_schema_and_version(
    case_id: str, audit_env: AuditEnv
) -> None:
    """WEB-01-01: verify_student_session requires schema=2, valid group_id, and matching version."""
    app = _create_test_app(audit_env)
    client = app.test_client()

    # 1. Unauthenticated request to /student/assignments returns 403
    res_anon = client.get("/student/assignments")
    assert res_anon.status_code == 403

    # 2. Session with old schema (e.g. schema=1 or None) rejected
    old_cookie = _forge_session_cookie({
        "student_group_id": str(G1_ID),
        "student_access_version": G1_ACCESS_VERSION,
        "student_session_schema": 1,
    })
    client.set_cookie("session", old_cookie)
    res_old = client.get("/student/assignments")
    assert res_old.status_code == 403

    # 3. Session with access version mismatch (e.g. version=99 vs 7) rejected
    mismatch_cookie = _forge_session_cookie({
        "student_group_id": str(G1_ID),
        "student_access_version": 99,
        "student_session_schema": 2,
    })
    client.set_cookie("session", mismatch_cookie)
    res_mismatch = client.get("/student/assignments")
    assert res_mismatch.status_code == 403

    # 4. Valid session cookie accepted
    valid_cookie = _forge_session_cookie({
        "student_group_id": str(G1_ID),
        "student_access_version": G1_ACCESS_VERSION,
        "student_session_schema": 2,
    })
    client.set_cookie("session", valid_cookie)
    res_valid = client.get("/student/assignments")
    assert res_valid.status_code == 200


@pytest.mark.parametrize("case_id", ["WEB-01-02"])
def test_web_01_02_admin_routes_protected_from_anonymous_and_student(
    case_id: str, audit_env: AuditEnv
) -> None:
    """WEB-01-02: All /admin/* routes return 403 to anonymous users and student sessions."""
    app = _create_test_app(audit_env)
    client = app.test_client()

    admin_routes = [
        ("GET", "/admin/access"),
        ("GET", "/admin/assignments"),
        ("GET", "/admin/submissions"),
        ("POST", "/admin/assignments"),
        ("POST", "/admin/groups"),
    ]

    # Anonymous access blocked
    for method, route in admin_routes:
        res = client.open(route, method=method)
        assert res.status_code == 403, f"Anonymous request to {route} was not blocked (got {res.status_code})"

    # Student session cookie attempting admin routes blocked
    student_cookie = _forge_session_cookie({
        "student_group_id": str(G1_ID),
        "student_access_version": G1_ACCESS_VERSION,
        "student_session_schema": 2,
    })
    client.set_cookie("session", student_cookie)
    for method, route in admin_routes:
        res = client.open(route, method=method)
        assert res.status_code == 403, f"Student session was allowed on admin route {route}"

    # Tampered cookie (bad HMAC signature) blocked
    client.set_cookie("session", "tampered.session.data.without.valid.signature")
    res_tampered = client.get("/admin/access")
    assert res_tampered.status_code == 403


@pytest.mark.parametrize("case_id", ["WEB-01-02"])
def test_web_01_02_invalid_uuid_in_route_urls_returns_client_error(
    case_id: str, audit_env: AuditEnv
) -> None:
    """WEB-01-02: Malformed UUID in route URLs returns 400 or 404, never unhandled 500."""
    app = _create_test_app(audit_env)
    client = app.test_client()

    # Login as admin
    client.post("/admin/login", data={"password": "super-admin-secret"})

    invalid_uuid_routes = [
        ("POST", "/admin/assignments/not-a-valid-uuid/archive"),
        ("POST", "/admin/assignments/123-bad/update"),
        ("POST", "/admin/groups/invalid-uuid/rename"),
        ("POST", "/admin/groups/invalid-uuid/archive"),
        ("GET", "/admin/submissions/malformed-uuid/download"),
    ]

    for method, route in invalid_uuid_routes:
        res = client.open(route, method=method)
        assert res.status_code in (400, 404), (
            f"Route {route} returned unexpected status {res.status_code} (expected 400 or 404, never 500)"
        )


# ===========================================================================
# WEB-02: Student Endpoints & Assignment Source-of-Truth
# ===========================================================================

@pytest.mark.parametrize("case_id", ["WEB-02-01"])
def test_web_02_01_post_unlock_workflow(case_id: str, audit_env: AuditEnv) -> None:
    """WEB-02-01: POST /unlock validates group password, returns JSON, sets signed session."""
    app = _create_test_app(audit_env)
    client = app.test_client()

    # Bad password rejected with 403
    res_bad = client.post("/unlock", json={"group_id": str(G1_ID), "password": "WRONG_PASSWORD"})
    assert res_bad.status_code == 403

    # Valid password unlocks group G1
    res_ok = client.post("/unlock", json={"group_id": str(G1_ID), "password": G1_PASSWORD})
    assert res_ok.status_code == 200
    data = res_ok.get_json()
    assert data["status"] == "ok"
    assert data["group_id"] == str(G1_ID)
    assert len(data["assignments"]) == 2
    assert len(data["students"]) == 2

    # Session cookie set
    with client.session_transaction() as sess:
        assert sess["student_group_id"] == str(G1_ID)
        assert sess["student_access_version"] == G1_ACCESS_VERSION
        assert sess["student_session_schema"] == 2


@pytest.mark.parametrize("case_id", ["WEB-02-02"])
def test_web_02_02_student_list_and_create(case_id: str, audit_env: AuditEnv) -> None:
    """WEB-02-02: GET/POST /student/students lists and creates student within group."""
    app = _create_test_app(audit_env)
    client = app.test_client()
    client.post("/unlock", json={"group_id": str(G1_ID), "password": G1_PASSWORD})

    # List students
    res_list = client.get("/student/students")
    assert res_list.status_code == 200
    students = res_list.get_json()
    assert len(students) == 2

    # Create new student
    res_create = client.post("/student/students", json={"display_name": "Новый Студент"})
    assert res_create.status_code == 201
    new_st = res_create.get_json()
    assert new_st["display_name"] == "Новый Студент"
    assert UUID(new_st["id"])


@pytest.mark.parametrize("case_id", ["WEB-02-04"])
def test_web_02_04_submit_assignment_source_of_truth_and_foreign_group(
    case_id: str, audit_env: AuditEnv
) -> None:
    """WEB-02-04: POST /submit enforces assignment_id as source of truth; rejects foreign group."""
    app = _create_test_app(audit_env)
    client = app.test_client()

    # Unlock as G1
    client.post("/unlock", json={"group_id": str(G1_ID), "password": G1_PASSWORD})

    # 1. Valid submission
    file_bytes = io.BytesIO(b"Valid student homework submission bytes")
    res_submit = client.post(
        "/submit",
        data={
            "group_id": str(G1_ID),
            "student_id": str(STUDENT1_ID),
            "assignment_id": str(A1_ID),
            "file": (file_bytes, "lab1.zip"),
        },
        content_type="multipart/form-data",
    )
    assert res_submit.status_code == 201
    res_data = res_submit.get_json()
    assert res_data["attempt_number"] == 1

    # 2. Attempting upload with group_id mismatch (form claims G2, unlocked session is G1)
    file_bytes_mismatch = io.BytesIO(b"Tampered group id upload")
    res_group_mismatch = client.post(
        "/submit",
        data={
            "group_id": str(G2_ID),
            "student_id": str(STUDENT1_ID),
            "assignment_id": str(A1_ID),
            "file": (file_bytes_mismatch, "lab1_bad.zip"),
        },
        content_type="multipart/form-data",
    )
    assert res_group_mismatch.status_code == 403

    # 3. Missing file or empty multipart
    res_empty = client.post(
        "/submit",
        data={
            "group_id": str(G1_ID),
            "student_id": str(STUDENT1_ID),
            "assignment_id": str(A1_ID),
        },
        content_type="multipart/form-data",
    )
    assert res_empty.status_code == 400


# ===========================================================================
# WEB-03: Admin Catalog & Assignment Management
# ===========================================================================

@pytest.mark.parametrize("case_id", ["WEB-03-01"])
def test_web_03_01_admin_catalog_crud_and_name_conflicts(
    case_id: str, audit_env: AuditEnv
) -> None:
    """WEB-03-01: Admin catalog create, rename, and name conflict error responses."""
    app = _create_test_app(audit_env)
    client = app.test_client()
    client.post("/admin/login", data={"password": "super-admin-secret"})

    # Create new group
    res_grp = client.post("/admin/groups", data={"name": "Новая Группа"})
    assert res_grp.status_code == 201
    grp_id = res_grp.get_json()["id"]

    # Duplicate group name returns 400 with name_conflict
    res_dup = client.post("/admin/groups", data={"name": "  новая группа  "})
    assert res_dup.status_code == 400
    assert res_dup.get_json()["error"] == "name_conflict"

    # Rename group
    res_ren = client.post(f"/admin/groups/{grp_id}/rename", data={"name": "Переименованная Группа"})
    assert res_ren.status_code == 200

    # Archive group
    res_arc = client.post(f"/admin/groups/{grp_id}/archive")
    assert res_arc.status_code == 204


@pytest.mark.parametrize("case_id", ["WEB-03-03"])
def test_web_03_03_admin_move_topics(case_id: str, audit_env: AuditEnv) -> None:
    """WEB-03-03: POST /admin/topics/move moves topics or returns 400 on name collision."""
    app = _create_test_app(audit_env)
    client = app.test_client()
    client.post("/admin/login", data={"password": "super-admin-secret"})

    # Attempt to move T1 into S2 where COLLISION_TOPIC_NAME already exists -> 400 topic_name_conflict
    res_col = client.post(
        "/admin/topics/move",
        data={"subject_id": str(S2_ID), "topic_ids": [str(T1_ID)]},
    )
    assert res_col.status_code == 400
    assert res_col.get_json()["error"] == "topic_name_conflict"


@pytest.mark.parametrize("case_id", ["WEB-03-05"])
def test_web_03_05_admin_create_assignment_slot_conflict(
    case_id: str, audit_env: AuditEnv
) -> None:
    """WEB-03-05: Creating assignment in occupied slot returns 409 conflict."""
    app = _create_test_app(audit_env)
    client = app.test_client()
    client.post("/admin/login", data={"password": "super-admin-secret"})

    # A1 occupies (G1_ID, A1_DATE, A1_PAIR)
    res_conflict = client.post(
        "/admin/assignments",
        data={
            "group_id": str(G1_ID),
            "topic_id": str(T1_ID),
            "lesson_date": A1_DATE,
            "pair_number": str(A1_PAIR),
        },
    )
    assert res_conflict.status_code == 409
    assert res_conflict.get_json()["error"] == "assignment_conflict"


@pytest.mark.parametrize("case_id", ["WEB-03-06"])
def test_web_03_06_admin_update_assignment_in_use_contract(
    case_id: str, audit_env: AuditEnv
) -> None:
    """WEB-03-06: Updating assignment topic when submissions exist.

    Contract requirement: Updating topic of assignment with submissions returns HTTP 409 ('assignment_in_use').
    Finding: app.web catches AssignmentInUseError along with AssignmentValidationError and returns HTTP 400.
    """
    app = _create_test_app(audit_env)
    client = app.test_client()

    # Create submission for A1
    sub_store = audit_env.get_submission_store()
    sub_store.submit(
        group_id=G1_ID,
        student_id=STUDENT1_ID,
        assignment_id=A1_ID,
        source=io.BytesIO(b"Submission payload"),
        original_filename="a1.zip",
        access_version=G1_ACCESS_VERSION,
    )

    # Login as admin and attempt to update A1's topic_id to T2
    client.post("/admin/login", data={"password": "super-admin-secret"})
    res_update = client.post(
        f"/admin/assignments/{A1_ID}/update",
        data={
            "topic_id": str(T2_ID),
            "lesson_date": A1_DATE,
            "pair_number": str(A1_PAIR),
        },
    )

    assert res_update.status_code == 409, (
        f"WEB-03-06 Contract violation: Updating topic of assignment with submissions "
        f"expected HTTP 409 ('assignment_in_use'), got HTTP {res_update.status_code}: {res_update.get_data(as_text=True)}"
    )

@pytest.mark.parametrize("case_id", ["WEB-03-06"])
def test_web_03_legacy_delete_routes_return_404_or_405(
    case_id: str, audit_env: AuditEnv
) -> None:
    """WEB-03: Legacy delete endpoints (e.g. /admin/assignments/<id>/delete) do not exist."""
    app = _create_test_app(audit_env)
    client = app.test_client()
    client.post("/admin/login", data={"password": "super-admin-secret"})

    res_post_del = client.post(f"/admin/assignments/{A1_ID}/delete")
    assert res_post_del.status_code in (404, 405)

    res_get_del = client.get(f"/admin/assignments/{A1_ID}/delete")
    assert res_get_del.status_code in (404, 405)


# ===========================================================================
# WEB-04: Submissions Review & History Filtering
# ===========================================================================

@pytest.mark.parametrize("case_id", ["WEB-04-01"])
def test_web_04_01_admin_submissions_filtering_and_review(
    case_id: str, audit_env: AuditEnv
) -> None:
    """WEB-04-01: /admin/submissions filtering, review toggle, and redirect."""
    app = _create_test_app(audit_env)
    client = app.test_client()
    client.post("/admin/login", data={"password": "super-admin-secret"})

    # Create submission
    sub_store = audit_env.get_submission_store()
    sub, _ = sub_store.submit(
        group_id=G1_ID,
        student_id=STUDENT1_ID,
        assignment_id=A1_ID,
        source=io.BytesIO(b"Data for review test"),
        original_filename="rev.zip",
        access_version=G1_ACCESS_VERSION,
    )

    # Filtered submissions page
    res_page = client.get(f"/admin/submissions?group_id={G1_ID}&topic_id={T1_ID}")
    assert res_page.status_code == 200
    assert STUDENT1_NAME in res_page.get_data(as_text=True)

    # Review submission via form post (redirects with 303)
    res_rev = client.post(
        f"/admin/submissions/{sub.id}/review",
        data={"status": "reviewed", "group_id": str(G1_ID), "topic_id": str(T1_ID)},
    )
    assert res_rev.status_code == 303
    assert f"group_id={G1_ID}" in res_rev.headers["Location"]


@pytest.mark.parametrize("case_id", ["WEB-04-01"])
def test_web_04_01_admin_submissions_tampered_filter_contract(
    case_id: str, audit_env: AuditEnv
) -> None:
    """WEB-04-01: Tampered/invalid filter values in review form return redirect or 400 without 500 error.

    Contract requirement: Invalid/tampered filter values in /admin/submissions query params must not crash with 500.
    Finding: app.web line 696 calls UUID(group_filter) without try/except, causing unhandled ValueError (HTTP 500).
    """
    app = _create_test_app(audit_env, TESTING=False)
    client = app.test_client()
    client.post("/admin/login", data={"password": "super-admin-secret"})

    res = client.get("/admin/submissions?group_id=tampered-not-a-uuid")
    assert res.status_code in (200, 400), (
        f"WEB-04-01 Contract violation: Tampered/invalid filter values in /admin/submissions "
        f"caused unhandled server error (HTTP {res.status_code}) instead of client error (200/400)"
    )


# ===========================================================================
# WEB-05: Path Traversal, Injection & Headers
# ===========================================================================

@pytest.mark.parametrize("case_id", ["WEB-05-01"])
def test_web_05_01_download_not_found(case_id: str, audit_env: AuditEnv) -> None:
    """WEB-05-01: Download non-existent submission_id returns 404."""
    app = _create_test_app(audit_env)
    client = app.test_client()
    client.post("/admin/login", data={"password": "super-admin-secret"})

    fake_id = str(uuid4())
    res = client.get(f"/admin/submissions/{fake_id}/download")
    assert res.status_code == 404


@pytest.mark.parametrize("case_id", ["WEB-05-01"])
def test_web_05_01_download_filename_header_escaping(
    case_id: str, audit_env: AuditEnv
) -> None:
    """WEB-05-01: Malicious characters in filename (quotes, semicolons) do not permit header injection."""
    app = _create_test_app(audit_env)
    client = app.test_client()

    sub_store = audit_env.get_submission_store()
    # Filename with semicolon, quotes, and UTF-8 characters
    malicious_filename = 'report; filename="fake.exe"; test.zip'
    sub, _ = sub_store.submit(
        group_id=G1_ID,
        student_id=STUDENT1_ID,
        assignment_id=A1_ID,
        source=io.BytesIO(b"Safe content"),
        original_filename=malicious_filename,
        access_version=G1_ACCESS_VERSION,
    )

    client.post("/admin/login", data={"password": "super-admin-secret"})
    res = client.get(f"/admin/submissions/{sub.id}/download")
    assert res.status_code == 200

    cd_header = res.headers.get("Content-Disposition", "")
    assert "attachment" in cd_header
    # Ensure Content-Disposition doesn't inject multiple attachments or unescaped executable names
    assert cd_header.count("attachment") == 1


@pytest.mark.parametrize("case_id", ["WEB-05-01"])
def test_web_05_html_template_xss_escaping(case_id: str, audit_env: AuditEnv) -> None:
    """WEB-05: Entity names containing HTML/script tags are properly escaped in rendered HTML."""
    app = _create_test_app(audit_env)
    client = app.test_client()
    client.post("/admin/login", data={"password": "super-admin-secret"})

    xss_payload = '<script>alert("XSS")</script>'
    client.post("/admin/groups", data={"name": xss_payload})

    res = client.get("/admin/groups")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Raw script tag must not appear unescaped in rendered HTML
    assert '<script>alert("XSS")</script>' not in html
    assert "&lt;script&gt;alert(&#34;XSS&#34;)&lt;/script&gt;" in html or "&lt;script&gt;" in html


# ===========================================================================
# WEB-06: Preflight Gates & Request Limits
# ===========================================================================

@pytest.mark.parametrize("case_id", ["WEB-06-01"])
def test_web_06_01_unauthenticated_submit_rejected_before_read(
    case_id: str, audit_env: AuditEnv
) -> None:
    """WEB-06-01: Anonymous POST /submit rejected with 403."""
    app = _create_test_app(audit_env)
    client = app.test_client()

    res = client.post(
        "/submit",
        data={
            "group_id": str(G1_ID),
            "student_id": str(STUDENT1_ID),
            "assignment_id": str(A1_ID),
            "file": (io.BytesIO(b"Data"), "test.zip"),
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 403


@pytest.mark.parametrize("case_id", ["WEB-06-01"])
def test_web_06_01_max_content_length_413(case_id: str, audit_env: AuditEnv) -> None:
    """WEB-06-01: Payload exceeding MAX_CONTENT_LENGTH returns 413 Payload Too Large."""
    # App configured with MAX_UPLOAD_MB=1 (1 MB limit)
    app = _create_test_app(audit_env, MAX_UPLOAD_MB=1)
    client = app.test_client()
    client.post("/unlock", json={"group_id": str(G1_ID), "password": G1_PASSWORD})

    # Upload 2 MB file (exceeds 1 MB limit)
    oversized_data = b"0" * (2 * 1024 * 1024)
    res = client.post(
        "/submit",
        data={
            "group_id": str(G1_ID),
            "student_id": str(STUDENT1_ID),
            "assignment_id": str(A1_ID),
            "file": (io.BytesIO(oversized_data), "too_large.zip"),
        },
        content_type="multipart/form-data",
    )
    assert res.status_code == 413
    assert res.get_json()["error"] == "file too large"


# ===========================================================================
# SEC-01: SECRET_KEY Lifecycle & Cookie Security
# ===========================================================================

@pytest.mark.parametrize("case_id", ["SEC-01-01"])
def test_sec_01_01_default_dev_secret_allows_admin_forgery(
    case_id: str, audit_env: AuditEnv
) -> None:
    """SEC-01-01: Default fallback SECRET_KEY ('dev-secret') rejected at startup."""
    with pytest.raises(RuntimeError):
        _create_test_app(audit_env, SECRET_KEY="dev-secret")

    forged_cookie_dev = _forge_session_cookie({"admin": True}, secret_key="dev-secret")
    app_prod = _create_test_app(audit_env, SECRET_KEY="strong-random-production-secret-key-12345")
    client_prod = app_prod.test_client()
    client_prod.set_cookie("session", forged_cookie_dev)

    res_prod = client_prod.get("/admin/access")
    assert res_prod.status_code == 403, "Production app did not reject dev-secret forged cookie"


@pytest.mark.parametrize("case_id", ["SEC-01-01"])
def test_sec_01_01_cookie_security_flags(case_id: str, audit_env: AuditEnv) -> None:
    """SEC-01-01: Inspect session cookie security attributes: HttpOnly and SameSite."""
    app = _create_test_app(audit_env)
    client = app.test_client()

    res = client.post("/admin/login", data={"password": "super-admin-secret"})
    assert res.status_code == 204

    set_cookie = res.headers.get("Set-Cookie", "")
    assert "HttpOnly" in set_cookie
    assert "SameSite=Lax" in set_cookie or "SameSite" in set_cookie


# ===========================================================================
# SEC-02: Cross-Site Request Forgery (CSRF)
# ===========================================================================

@pytest.mark.parametrize("case_id", ["SEC-02-01"])
def test_sec_02_01_mutating_post_cross_site_origin_contract(
    case_id: str, audit_env: AuditEnv
) -> None:
    """SEC-02-01: Mutating POST endpoints executed without CSRF token or Origin validation.

    Audit finding: app.web executes mutating state operations from cross-site origins
    if session cookie is present, because no CSRF protection or Origin check exists.
    """
    app = _create_test_app(audit_env)
    client = app.test_client()
    client.post("/admin/login", data={"password": "super-admin-secret"})

    # Mutating POST request with foreign cross-origin headers
    res = client.post(
        "/admin/access",
        data={"group_id": str(G1_ID), "is_open": "1"},
        headers={
            "Origin": "https://malicious-attacker-website.com",
            "Referer": "https://malicious-attacker-website.com/exploit.html",
        },
    )
    # The request is executed (204) because app lacks CSRF checks
    assert res.status_code == 204


# ===========================================================================
# SEC-03: Information Disclosure
# ===========================================================================

@pytest.mark.parametrize("case_id", ["SEC-03-01"])
def test_sec_03_01_password_hash_never_exposed(
    case_id: str, audit_env: AuditEnv
) -> None:
    """SEC-03-01: Password hashes are never exposed in JSON responses or rendered HTML."""
    app = _create_test_app(audit_env)
    client = app.test_client()

    # 1. Unlock JSON response
    res_unlock = client.post("/unlock", json={"group_id": str(G1_ID), "password": G1_PASSWORD})
    text_unlock = res_unlock.get_data(as_text=True)
    assert "password_hash" not in text_unlock
    assert "scrypt:" not in text_unlock
    assert "pbkdf2:" not in text_unlock

    # 2. Admin access page
    client.post("/admin/login", data={"password": "super-admin-secret"})
    res_admin = client.get("/admin/access")
    text_admin = res_admin.get_data(as_text=True)
    assert "scrypt:" not in text_admin
    assert "pbkdf2:" not in text_admin


@pytest.mark.parametrize("case_id", ["SEC-03-01"])
def test_sec_03_01_static_path_traversal_blocked(
    case_id: str, audit_env: AuditEnv
) -> None:
    """SEC-03-01: Static file route cannot serve files outside app/static."""
    app = _create_test_app(audit_env)
    client = app.test_client()

    traversal_urls = [
        "/static/../catalog.json",
        "/static/../.private/app_v2.sqlite3",
        "/static/../../manage.sh",
        "/static/%2e%2e/catalog.json",
    ]

    for url in traversal_urls:
        res = client.get(url)
        assert res.status_code in (400, 404), (
            f"Static URL {url} leaked or returned unexpected code {res.status_code}"
        )
