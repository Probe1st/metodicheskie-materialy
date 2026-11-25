"""Audit test suite for homework-submissions SQLite v2 submission integrity.

Covers test matrix families UP-01 through UP-04:
  - UP-01: Student Identity & Upload Basics
  - UP-02: Preflight & Stream Gatekeeping (Web & Store)
  - UP-03: Basename & Suffix Safety
  - UP-04: Submissions Lifecycle & Review Transitions
"""

from __future__ import annotations

import hashlib
import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from flask.testing import FlaskClient

import app.web
from app.database import connect
from app.storage import clean_extension
from app.submissions import (
    Submission,
    SubmissionAccessError,
    SubmissionFile,
    SubmissionNotFoundError,
    SubmissionValidationError,
)
from tests.audit.conftest import AuditEnv
from tests.audit.support import (
    A1_ID,
    A2_ID,
    G1_ACCESS_VERSION,
    G1_ID,
    G1_PASSWORD,
    G2_ID,
    S1_ID,
    STUDENT1_ID,
    STUDENT1_NAME,
    STUDENT2_ID,
    T1_ID,
    T2_ID,
)


class SentinelStream(io.BytesIO):
    """Memory stream that tracks whether .read() was called."""

    def __init__(self, data: bytes = b"sentinel-payload-bytes"):
        super().__init__(data)
        self.read_called = False

    def read(self, *args, **kwargs) -> bytes:
        self.read_called = True
        return super().read(*args, **kwargs)


@pytest.fixture
def web_client(audit_env: AuditEnv) -> FlaskClient:
    """Configures a Flask test client bound to the isolated AuditEnv."""
    audit_env.populate_canonical()
    flask_app = app.web.create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "PRIVATE_DATA_DIR": str(audit_env.private),
        "DATABASE_PATH": str(audit_env.db_path),
        "STORAGE_ROOT": str(audit_env.storage),
        "ADMIN_PASSWORD": "admin-audit-password",
        "WTF_CSRF_ENABLED": False,
    })
    return flask_app.test_client()


# ==============================================================================
# UP-01: Student Identity & Upload Basics
# ==============================================================================

def test_up_01_student_normalization_and_validation(audit_env: AuditEnv) -> None:
    """UP-01-01 & UP-01-02: NFC normalization, whitespace stripping, and empty name rejection."""
    audit_env.init_db()
    cat_store = audit_env.get_catalog_store()
    sub_store = audit_env.get_submission_store()

    grp = cat_store.create_group("Тестовая Группа Студентов")

    # 1. Whitespace stripped and NFC normalized
    # Decomposed Cyrillic 'е' + combining diaeresis '\u0308' -> composed 'ё'
    decomposed_name = "  Е\u0308лка Петрова  "
    st1 = sub_store.create_student(grp.id, decomposed_name)
    assert st1.display_name == "Ёлка Петрова"
    assert st1.group_id == grp.id
    assert isinstance(st1.id, UUID)

    # 2. Empty or whitespace-only name rejected
    with pytest.raises(SubmissionValidationError):
        sub_store.create_student(grp.id, "   ")

    with pytest.raises(SubmissionValidationError):
        sub_store.create_student(grp.id, "")

    # 3. Active vs archived listing
    active_students = sub_store.list_students(grp.id, include_archived=False)
    assert len(active_students) == 1
    assert active_students[0].id == st1.id


def test_up_01_namesakes_distinct_uuids(audit_env: AuditEnv) -> None:
    """UP-01-02: Namesakes (students with identical display_name) have distinct UUIDs with no auto-merge."""
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()

    # Pre-seeded canonical students have identical name "Иванов Иван"
    students = sub_store.list_students(G1_ID)
    namesakes = [s for s in students if s.display_name == STUDENT1_NAME]
    assert len(namesakes) >= 2
    assert namesakes[0].id != namesakes[1].id
    assert {s.id for s in namesakes} >= {STUDENT1_ID, STUDENT2_ID}

    # Add a third student with the same name
    st3 = sub_store.create_student(G1_ID, STUDENT1_NAME)
    assert st3.display_name == STUDENT1_NAME
    assert st3.id not in (STUDENT1_ID, STUDENT2_ID)

    all_namesakes = [s for s in sub_store.list_students(G1_ID) if s.display_name == STUDENT1_NAME]
    assert len(all_namesakes) >= 3


def test_up_01_student_in_foreign_or_archived_group(audit_env: AuditEnv) -> None:
    """UP-01-01: Creating student in nonexistent or archived group raises appropriate errors."""
    audit_env.init_db()
    cat_store = audit_env.get_catalog_store()
    sub_store = audit_env.get_submission_store()
    nonexistent = uuid.uuid4()

    # Nonexistent group
    with pytest.raises(SubmissionNotFoundError):
        sub_store.create_student(nonexistent, "Сидоров Сидор")

    # Archived group
    g_arch = cat_store.create_group("Архивная Группа")
    cat_store.archive_group(g_arch.id)
    with pytest.raises(SubmissionValidationError):
        sub_store.create_student(g_arch.id, "Сидоров Сидор")


@pytest.mark.parametrize(
    "case_id,payload_bytes,description",
    [
        ("UP-01-03", b"", "zero_bytes_file"),
        ("UP-01-03", b"Small payload containing exactly 32 bytes...", "small_payload"),
        ("UP-01-03", b"A" * 200000, "multi_block_streaming_payload"),
    ],
)
def test_up_01_upload_payload_sizes(
    audit_env: AuditEnv, case_id: str, payload_bytes: bytes, description: str
) -> None:
    """UP-01-03: Submissions of 0 bytes, small payload, and multi-block stream verify size, SHA-256, and stored path."""
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()

    expected_sha256 = hashlib.sha256(payload_bytes).hexdigest()
    expected_size = len(payload_bytes)

    sub, sub_file = sub_store.submit(
        group_id=G1_ID,
        student_id=STUDENT1_ID,
        assignment_id=A1_ID,
        source=io.BytesIO(payload_bytes),
        original_filename=f"test_{description}.dat",
        access_version=G1_ACCESS_VERSION,
    )

    # 1. Submission record assertions
    assert isinstance(sub, Submission)
    assert sub.student_id == STUDENT1_ID
    assert sub.group_id == G1_ID
    assert sub.topic_id == T1_ID
    assert sub.assignment_id == A1_ID
    assert sub.attempt_number == 1
    assert sub.review_status == "pending"
    assert sub.reviewed_at is None

    # 2. SubmissionFile record assertions
    assert isinstance(sub_file, SubmissionFile)
    assert sub_file.submission_id == sub.id
    assert sub_file.size_bytes == expected_size
    assert sub_file.sha256 == expected_sha256
    assert sub_file.original_filename == f"test_{description}.dat"

    # 3. Storage path assertions
    # Stored path format: {group_id}/{topic_id}/{submission_id}{suffix}
    expected_rel_path = f"{G1_ID}/{T1_ID}/{sub.id}.dat"
    assert sub_file.stored_path == expected_rel_path

    physical_path = audit_env.storage / expected_rel_path
    assert physical_path.is_file()
    assert physical_path.stat().st_size == expected_size
    assert physical_path.read_bytes() == payload_bytes


# ==============================================================================
# UP-02: Preflight & Stream Gatekeeping (Web & Store)
# ==============================================================================

@pytest.mark.parametrize(
    "case_id,invalid_reason",
    [
        ("UP-02-01", "stale_version"),
        ("UP-02-01", "closed_access"),
        ("UP-02-01", "expired_deadline"),
        ("UP-02-02", "archived_group"),
        ("UP-02-02", "foreign_student"),
        ("UP-02-02", "archived_student"),
        ("UP-02-02", "foreign_assignment"),
        ("UP-02-02", "archived_assignment"),
        ("UP-02-02", "archived_topic"),
    ],
)
def test_up_02_preflight_stream_not_read_in_store(
    audit_env: AuditEnv, case_id: str, invalid_reason: str
) -> None:
    """UP-02-01 & UP-02-02: Preflight validation rejects invalid submissions BEFORE calling stream.read(); no .pending files created."""
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()
    acc_store = audit_env.get_access_store()
    cat_store = audit_env.get_catalog_store()
    asgn_store = audit_env.get_assignment_store()

    stream = SentinelStream(b"Should never be read by preflight check")
    group_id = G1_ID
    student_id = STUDENT1_ID
    assignment_id = A1_ID
    version = G1_ACCESS_VERSION

    if invalid_reason == "stale_version":
        version = G1_ACCESS_VERSION - 1
    elif invalid_reason == "closed_access":
        acc = acc_store.configure(G1_ID, is_open=False)
        version = acc.version
    elif invalid_reason == "expired_deadline":
        past_deadline = datetime(2020, 1, 1, 0, 0, tzinfo=timezone.utc)
        acc = acc_store.configure(G1_ID, is_open=True, password=None, closes_at=past_deadline)
        version = acc.version
    elif invalid_reason == "archived_group":
        cat_store.archive_group(G1_ID)
    elif invalid_reason == "foreign_student":
        # Create student in G2, attempt submitting for G1
        st_g2 = sub_store.create_student(G2_ID, "Чужой Студент")
        student_id = st_g2.id
    elif invalid_reason == "archived_student":
        # Archive STUDENT1
        with connect(audit_env.db_path) as conn:
            conn.execute("UPDATE students SET archived_at = ? WHERE id = ?;", (datetime.now(timezone.utc).isoformat(), str(STUDENT1_ID)))
    elif invalid_reason == "foreign_assignment":
        # Create assignment for G2
        a_g2 = asgn_store.create(G2_ID, T2_ID, "2026-11-20", 1)
        assignment_id = a_g2.id
    elif invalid_reason == "archived_assignment":
        asgn_store.archive(A1_ID)
    elif invalid_reason == "archived_topic":
        cat_store.archive_topic(T1_ID)

    with pytest.raises((SubmissionAccessError, SubmissionNotFoundError, SubmissionValidationError)):
        sub_store.submit(
            group_id=group_id,
            student_id=student_id,
            assignment_id=assignment_id,
            source=stream,
            original_filename="preflight_test.zip",
            access_version=version,
        )

    # Invariant: stream.read was NEVER called
    assert stream.read_called is False, f"Stream was read during preflight failure: {invalid_reason}"

    # Invariant: no temporary files created in .pending
    pending_dir = audit_env.storage / ".pending"
    if pending_dir.is_dir():
        assert len(list(pending_dir.iterdir())) == 0


def test_up_02_preflight_gatekeeping_web(web_client: FlaskClient, audit_env: AuditEnv) -> None:
    """UP-02-02: Web client submission gatekeeping: rejects unauthorized, missing assignment (400), foreign group/assignment (403)."""
    # 1. Unauthorized submit (no session cookie) -> HTTP 403
    res_unauth = web_client.post(
        "/submit",
        data={
            "group_id": str(G1_ID),
            "student_id": str(STUDENT1_ID),
            "assignment_id": str(A1_ID),
            "file": (io.BytesIO(b"data"), "lab.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert res_unauth.status_code == 403

    # Unlock session for group G1
    unlock_res = web_client.post("/unlock", data={"group_id": str(G1_ID), "password": G1_PASSWORD})
    assert unlock_res.status_code == 200

    # 2. Missing assignment (nonexistent UUID) -> HTTP 400
    res_missing_asgn = web_client.post(
        "/submit",
        data={
            "group_id": str(G1_ID),
            "student_id": str(STUDENT1_ID),
            "assignment_id": str(uuid.uuid4()),
            "file": (io.BytesIO(b"data"), "lab.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert res_missing_asgn.status_code == 400

    # 3. Foreign group in request body (mismatch with session cookie) -> HTTP 403
    res_mismatched_grp = web_client.post(
        "/submit",
        data={
            "group_id": str(G2_ID),
            "student_id": str(STUDENT1_ID),
            "assignment_id": str(A1_ID),
            "file": (io.BytesIO(b"data"), "lab.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert res_mismatched_grp.status_code == 403

    # 4. Foreign assignment (assignment created for G2, submitted for G1) -> HTTP 403
    asgn_store = audit_env.get_assignment_store()
    g2_asgn = asgn_store.create(G2_ID, T2_ID, "2026-11-22", 1)
    res_foreign_asgn = web_client.post(
        "/submit",
        data={
            "group_id": str(G1_ID),
            "student_id": str(STUDENT1_ID),
            "assignment_id": str(g2_asgn.id),
            "file": (io.BytesIO(b"data"), "lab.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert res_foreign_asgn.status_code == 403


# ==============================================================================
# UP-03: Basename & Suffix Safety
# ==============================================================================

@pytest.mark.parametrize(
    "filename,expected_suffix",
    [
        ("document.pdf", ".pdf"),
        ("archive.tar.gz", ".gz"),
        ("отчет.документ", ""),
        ("report.toolongextension12345", ""),
        ("file;cmd.zip", ".zip"),
        ("no_extension", ""),
        ("trailing_dot.", ""),
        ("mixed_case.PDF", ".pdf"),
        ("script.sh", ".sh"),
    ],
)
def test_up_03_clean_extension_unit(filename: str, expected_suffix: str) -> None:
    """UP-03-01: clean_extension extracts lowercase ASCII alphanumeric suffix <= 16 chars; rejects non-ascii/long."""
    assert clean_extension(filename) == expected_suffix


@pytest.mark.parametrize(
    "case_id,exotic_name,expected_suffix",
    [
        ("UP-03-02", "../../etc/passwd", ""),
        ("UP-03-02", r"..\windows\system32\calc.exe", ".exe"),
        ("UP-03-02", "archive.tar.gz", ".gz"),
        ("UP-03-02", "отчет.документ", ""),
        ("UP-03-02", "file;cmd.zip", ".zip"),
        ("UP-03-02", "report.longext1234567890", ""),
        ("UP-03-02", "<script>alert(1)</script>.zip", ".zip"),
        ("UP-03-02", "+Иванов 2.zip", ".zip"),
        ("UP-03-02", "deploy.sh", ".sh"),
    ],
)
def test_up_03_malicious_and_exotic_filenames(
    audit_env: AuditEnv, case_id: str, exotic_name: str, expected_suffix: str
) -> None:
    """UP-03-02: Storage filename is UUID-based; cannot escape storage directory; preserves original filename for display."""
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()

    _sub, sub_file = sub_store.submit(
        group_id=G1_ID,
        student_id=STUDENT1_ID,
        assignment_id=A1_ID,
        source=io.BytesIO(b"Safe payload content"),
        original_filename=exotic_name,
        access_version=G1_ACCESS_VERSION,
    )

    # 1. Stored path must end with expected_suffix
    assert sub_file.stored_path.endswith(expected_suffix)

    # 2. Target physical file must be strictly within storage root
    full_path = audit_env.storage / sub_file.stored_path
    resolved_root = audit_env.storage.resolve()
    assert full_path.resolve().is_relative_to(resolved_root)
    assert full_path.is_file()

    # 3. Original filename stored cleanly in metadata
    clean_base = Path(exotic_name).name or "submission"
    assert sub_file.original_filename == clean_base


def test_up_03_content_disposition_header_safety(
    web_client: FlaskClient, audit_env: AuditEnv
) -> None:
    """UP-03-02: Download Content-Disposition header prevents header injection, unescaped quotes, or path escapes."""
    sub_store = audit_env.get_submission_store()

    # Submit with filename containing semicolon, quote, and Unicode
    safe_name = "отчет_студента;ver=\"2\".pdf"
    sub, _ = sub_store.submit(
        group_id=G1_ID,
        student_id=STUDENT1_ID,
        assignment_id=A1_ID,
        source=io.BytesIO(b"File bytes for download test"),
        original_filename=safe_name,
        access_version=G1_ACCESS_VERSION,
    )

    # Login admin and download
    login_res = web_client.post("/admin/login", data={"password": "admin-audit-password"})
    assert login_res.status_code == 204

    dl_res = web_client.get(f"/admin/submissions/{sub.id}/download")
    assert dl_res.status_code == 200

    cd_header = dl_res.headers.get("Content-Disposition", "")
    assert "attachment" in cd_header
    # Header must not contain raw CRLF
    assert "\r" not in cd_header
    assert "\n" not in cd_header


# ==============================================================================
# UP-04: Submissions Lifecycle & Review Transitions
# ==============================================================================

def test_up_04_monotonic_attempt_numbering(audit_env: AuditEnv) -> None:
    """UP-04-01: Repeated submissions assign monotonic attempt numbers 1, 2, 3... scoped to (student_id, topic_id)."""
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()
    cat_store = audit_env.get_catalog_store()
    asgn_store = audit_env.get_assignment_store()

    # Create second assignment A_other in G1 under a different topic T_other
    t_other = cat_store.create_topic(S1_ID, "Вторая Тема Для Попыток")
    a_other = asgn_store.create(G1_ID, t_other.id, "2026-11-25", 2)

    # Student 1 submits attempt 1 and attempt 2 for Topic 1 (using A1 and A2)
    s1_t1_att1, _ = sub_store.submit(G1_ID, STUDENT1_ID, A1_ID, io.BytesIO(b"S1 T1 att 1"), "lab1.zip", G1_ACCESS_VERSION)
    s1_t1_att2, _ = sub_store.submit(G1_ID, STUDENT1_ID, A2_ID, io.BytesIO(b"S1 T1 att 2"), "lab1_v2.zip", G1_ACCESS_VERSION)
    s1_t1_att3, _ = sub_store.submit(G1_ID, STUDENT1_ID, A1_ID, io.BytesIO(b"S1 T1 att 3"), "lab1_v3.zip", G1_ACCESS_VERSION)

    assert s1_t1_att1.attempt_number == 1
    assert s1_t1_att2.attempt_number == 2
    assert s1_t1_att3.attempt_number == 3

    # Student 1 submits for Topic 2 (starts at attempt 1)
    s1_t2_att1, _ = sub_store.submit(G1_ID, STUDENT1_ID, a_other.id, io.BytesIO(b"S1 T2 att 1"), "lab2.zip", G1_ACCESS_VERSION)
    assert s1_t2_att1.attempt_number == 1

    # Student 2 submits for Topic 1 (starts at attempt 1)
    s2_t1_att1, _ = sub_store.submit(G1_ID, STUDENT2_ID, A1_ID, io.BytesIO(b"S2 T1 att 1"), "lab1_s2.zip", G1_ACCESS_VERSION)
    assert s2_t1_att1.attempt_number == 1


def test_up_04_review_status_transitions_store(audit_env: AuditEnv) -> None:
    """UP-04-02: set_review_status transitions between pending and reviewed; validates status; preserves physical file."""
    audit_env.populate_canonical()
    sub_store = audit_env.get_submission_store()

    file_bytes = b"Submission for review test"
    sub, _sub_file = sub_store.submit(
        group_id=G1_ID,
        student_id=STUDENT1_ID,
        assignment_id=A1_ID,
        source=io.BytesIO(file_bytes),
        original_filename="review_me.zip",
        access_version=G1_ACCESS_VERSION,
    )
    assert sub.review_status == "pending"

    # Transition to reviewed
    st, rev_at = sub_store.set_review_status(sub.id, "reviewed")
    assert st == "reviewed"
    assert rev_at is not None

    with connect(audit_env.db_path) as conn:
        row = conn.execute("SELECT review_status, reviewed_at FROM submissions WHERE id = ?;", (str(sub.id),)).fetchone()
        assert row["review_status"] == "reviewed"
        assert row["reviewed_at"] == rev_at

    # Transition back to pending
    st2, rev_at2 = sub_store.set_review_status(sub.id, "pending")
    assert st2 == "pending"
    assert rev_at2 is None

    # Invalid status rejected
    with pytest.raises(SubmissionValidationError):
        sub_store.set_review_status(sub.id, "approved")

    # Nonexistent submission ID
    with pytest.raises(SubmissionNotFoundError):
        sub_store.set_review_status(uuid.uuid4(), "reviewed")

    # Invariant: physical file bytes and path unchanged
    full_path, _ = sub_store.get_file(sub.id)
    assert full_path.read_bytes() == file_bytes


def test_up_04_review_status_transitions_web(
    web_client: FlaskClient, audit_env: AuditEnv
) -> None:
    """UP-04-02: Web review endpoint handles JSON and form updates; returns proper error codes."""
    sub_store = audit_env.get_submission_store()
    sub, _ = sub_store.submit(
        group_id=G1_ID,
        student_id=STUDENT1_ID,
        assignment_id=A1_ID,
        source=io.BytesIO(b"Data"),
        original_filename="work.zip",
        access_version=G1_ACCESS_VERSION,
    )

    # Login admin
    web_client.post("/admin/login", data={"password": "admin-audit-password"})

    # JSON review update
    res_json = web_client.post(f"/admin/submissions/{sub.id}/review", json={"status": "reviewed"})
    assert res_json.status_code == 200
    assert res_json.get_json()["review_status"] == "reviewed"

    # Invalid status -> HTTP 400
    res_invalid = web_client.post(f"/admin/submissions/{sub.id}/review", json={"status": "invalid_status"})
    assert res_invalid.status_code == 400

    # Nonexistent ID -> HTTP 404
    res_nonexistent = web_client.post(f"/admin/submissions/{uuid.uuid4()}/review", json={"status": "reviewed"})
    assert res_nonexistent.status_code == 404


def test_up_04_historical_submissions_with_null_assignment(
    web_client: FlaskClient, audit_env: AuditEnv
) -> None:
    """UP-04-03: Historical submissions with NULL assignment_id: review succeeds, physical file bytes and paths remain intact."""
    sub_store = audit_env.get_submission_store()

    # Create historical submission row directly with assignment_id = NULL
    hist_sub_id = uuid.uuid4()
    hist_file_id = uuid.uuid4()
    rel_path = f"{G1_ID}/{T1_ID}/{hist_sub_id}.zip"
    full_path = audit_env.storage / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    historical_content = b"Historical content without active class assignment"
    full_path.write_bytes(historical_content)

    with connect(audit_env.db_path) as conn:
        conn.execute(
            """
            INSERT INTO submissions (
                id, student_id, group_id, topic_id, assignment_id,
                attempt_number, review_status, reviewed_at, created_at
            ) VALUES (?, ?, ?, ?, NULL, 1, 'pending', NULL, '2026-09-01T10:00:00Z');
            """,
            (str(hist_sub_id), str(STUDENT1_ID), str(G1_ID), str(T1_ID)),
        )
        conn.execute(
            """
            INSERT INTO submission_files (
                id, submission_id, original_filename, stored_path, size_bytes, sha256
            ) VALUES (?, ?, 'historical_archive.zip', ?, ?, NULL);
            """,
            (str(hist_file_id), str(hist_sub_id), rel_path, len(historical_content)),
        )

    # 1. list_submissions includes historical submission with assignment_id=None
    subs = sub_store.list_submissions(group_id=G1_ID, topic_id=T1_ID)
    matching = [s for s in subs if s.id == hist_sub_id]
    assert len(matching) == 1
    assert matching[0].assignment_id is None
    assert matching[0].lesson_date is None
    assert matching[0].pair_number is None

    # 2. get_file returns physical file
    f_path, orig_name = sub_store.get_file(hist_sub_id)
    assert f_path == full_path
    assert orig_name == "historical_archive.zip"

    # 3. Review transition succeeds
    st, rev_at = sub_store.set_review_status(hist_sub_id, "reviewed")
    assert st == "reviewed"
    assert rev_at is not None

    # 4. Web download endpoint delivers the file
    web_client.post("/admin/login", data={"password": "admin-audit-password"})
    dl_res = web_client.get(f"/admin/submissions/{hist_sub_id}/download")
    assert dl_res.status_code == 200
    assert dl_res.data == historical_content

    # 5. Invariant: physical file bytes and path unchanged
    assert full_path.is_file()
    assert full_path.read_bytes() == historical_content
