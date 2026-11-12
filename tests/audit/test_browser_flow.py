"""End-to-end browser audit flow using Playwright and headless Chromium.

Executes complete student submission and admin review flows against a live Flask
server bound to an ephemeral loopback port. Captures screenshots into
docs/audits/sqlite-v2/screenshots/ and verifies database state via independent SQLite connections.
"""

from __future__ import annotations

import os
import socket
import sqlite3
import threading
import zipfile
from pathlib import Path
from typing import Any

import pytest
playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_api.sync_playwright
from werkzeug.serving import make_server

from app.web import create_app
from tests.audit.conftest import AuditEnv
from tests.audit.support import (
    A1_ID,
    A2_ID,
    G1_ID,
    G1_NAME,
    G1_PASSWORD,
    STUDENT1_ID,
    STUDENT1_NAME,
    STUDENT2_ID,
    T1_ID,
)


def _get_free_port() -> int:
    """Allocate an available ephemeral port on loopback."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _save_screenshot(page: Any, filename: str) -> None:
    """Save screenshot to both local and repository screenshots directories."""
    dirs = [
        Path("docs/audits/sqlite-v2/screenshots"),
        Path("../docs/audits/sqlite-v2/screenshots"),
    ]
    for d in dirs:
        try:
            d.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(d / filename), full_page=True)
        except OSError:
            pass


@pytest.mark.parametrize("case_id", ["BROWSER-FLOW-01"])
def test_end_to_end_browser_flow(case_id: str, audit_env: AuditEnv) -> None:
    """End-to-end browser flow verifying student unlock, distinction, submit, review, and archival reactivity."""
    # 1. Initialize canonical database
    db_path = audit_env.populate_canonical()
    storage_root = audit_env.storage

    # Create temporary zip payload for upload
    sample_zip = audit_env.root / "sample_solution.zip"
    with zipfile.ZipFile(sample_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("solution.py", b"# Audit sample solution\ndef solve(): return 42\n")

    admin_password = "smoke-admin"
    secret_key = "smoke-audit-secret-key-at-least-32-bytes"

    # 2. Launch real Flask server on an ephemeral port
    port = _get_free_port()
    flask_app = create_app({
        "TESTING": True,
        "DATABASE_PATH": db_path,
        "STORAGE_ROOT": storage_root,
        "ADMIN_PASSWORD": admin_password,
        "SECRET_KEY": secret_key,
        "MAX_UPLOAD_MB": 10,
    })

    server = make_server("127.0.0.1", port, flask_app)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{port}"
    chrome_path = "/home/probe/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome"
    exec_path = chrome_path if os.path.exists(chrome_path) else None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=exec_path)

            # Context 1: Student browser context
            student_context = browser.new_context()
            page = student_context.new_page()

            # Step 1: Navigate to / and screenshot landing page
            page.goto(f"{base_url}/")
            page.wait_for_selector("#group")
            _save_screenshot(page, "01_landing.png")

            # Step 2: Select group G1, enter password, submit #unlock
            page.select_option("#group", str(G1_ID))
            page.fill("#group_password", G1_PASSWORD)
            page.click("#unlock button[type='submit']")

            # Wait for Step 2 form to become visible
            page.wait_for_selector("#submit:not([hidden])")
            _save_screenshot(page, "02_unlocked.png")

            # Step 3: Check student identity distinction by ID
            student_options = page.eval_on_selector_all(
                "#student_id option",
                "opts => opts.map(o => ({ value: o.value, text: o.text }))",
            )
            # Filter out empty default placeholder
            populated_students = [opt for opt in student_options if opt["value"]]
            assert len(populated_students) == 2, f"Expected 2 students, got {populated_students}"

            student_ids = {opt["value"] for opt in populated_students}
            assert str(STUDENT1_ID) in student_ids
            assert str(STUDENT2_ID) in student_ids
            # Verify distinct option values for identical display names
            assert len(student_ids) == 2
            assert all(opt["text"] == STUDENT1_NAME for opt in populated_students)

            # Step 4: Check assignment distinction (two distinct options for two pairs of same topic)
            assignment_options = page.eval_on_selector_all(
                "#assignment_id option",
                "opts => opts.map(o => ({ value: o.value, text: o.text }))",
            )
            populated_assignments = [opt for opt in assignment_options if opt["value"]]
            assert len(populated_assignments) == 2
            asgn_ids = {opt["value"] for opt in populated_assignments}
            assert str(A1_ID) in asgn_ids
            assert str(A2_ID) in asgn_ids

            # Step 5: Student creation flow
            page.click("#toggle_new_student_btn")
            page.wait_for_selector("#new_student_panel:not([hidden])")
            page.fill("#new_student_input", "Петров Петр")
            page.click("#save_student_btn")

            # Wait for panel to close and verify newly created student is selected
            page.wait_for_selector("#new_student_panel", state="hidden")
            selected_student_name = page.eval_on_selector(
                "#student_id", "el => el.options[el.selectedIndex].text"
            )
            assert "Петров Петр" in selected_student_name
            _save_screenshot(page, "03_new_student.png")

            # Existing students were not overwritten
            all_st_now = page.eval_on_selector_all(
                "#student_id option", "opts => opts.map(o => o.text)"
            )
            assert all_st_now.count(STUDENT1_NAME) == 2

            # Step 6: Submission flow (Student 2 on Pair A2)
            page.select_option("#student_id", str(STUDENT2_ID))
            page.select_option("#assignment_id", str(A2_ID))
            page.set_input_files("#file", str(sample_zip))

            page.click("#submit button[type='submit']")
            page.wait_for_selector("#overlay_success:not([hidden])")
            _save_screenshot(page, "04_submission_success.png")

            # Direct SQLite database check for attempt 1
            with sqlite3.connect(str(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                sub_row = conn.execute(
                    """
                    SELECT s.id, s.assignment_id, s.attempt_number, s.review_status,
                           f.original_filename, f.stored_path, f.size_bytes, f.sha256
                    FROM submissions s
                    JOIN submission_files f ON f.submission_id = s.id
                    WHERE s.student_id = ? AND s.assignment_id = ?;
                    """,
                    (str(STUDENT2_ID), str(A2_ID)),
                ).fetchone()

                assert sub_row is not None
                assert sub_row["assignment_id"] == str(A2_ID)
                assert sub_row["attempt_number"] == 1
                assert sub_row["review_status"] == "pending"
                stored_file = storage_root / sub_row["stored_path"]
                assert stored_file.is_file()
                assert sub_row["size_bytes"] == sample_zip.stat().st_size

            # Step 7: Attempt increment (Submit again for same student and assignment)
            page.click("#overlay_reset_button")
            page.wait_for_selector("#submission_overlay", state="hidden")

            page.select_option("#student_id", str(STUDENT2_ID))
            page.select_option("#assignment_id", str(A2_ID))
            page.set_input_files("#file", str(sample_zip))
            page.click("#submit button[type='submit']")
            page.wait_for_selector("#overlay_success:not([hidden])")

            # Verify attempt 2 in database
            with sqlite3.connect(str(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                attempts = [
                    r["attempt_number"]
                    for r in conn.execute(
                        "SELECT attempt_number FROM submissions WHERE student_id = ? AND assignment_id = ? ORDER BY attempt_number;",
                        (str(STUDENT2_ID), str(A2_ID)),
                    ).fetchall()
                ]
                assert attempts == [1, 2]

            # Step 8: Admin session in separate browser context
            admin_context = browser.new_context()
            admin_page = admin_context.new_page()

            # Admin login
            admin_page.goto(f"{base_url}/admin")
            admin_page.fill("#admin_password", admin_password)
            admin_page.click("#admin_login button[type='submit']")
            admin_page.wait_for_selector(".admin-layout")
            _save_screenshot(admin_page, "05_admin_dashboard.png")

            # Admin submissions page
            admin_page.goto(f"{base_url}/admin/submissions")
            admin_page.wait_for_selector("table tbody tr")
            _save_screenshot(admin_page, "06_admin_submissions.png")

            # Review submission: click 'Проверено'
            admin_page.click("table tbody tr button:has-text('Проверено')")
            admin_page.wait_for_selector(".chip--reviewed")
            _save_screenshot(admin_page, "07_admin_reviewed.png")

            # Verify review_status in SQLite database
            with sqlite3.connect(str(db_path)) as conn:
                reviewed_rows = conn.execute(
                    "SELECT count(*) FROM submissions WHERE review_status = 'reviewed';"
                ).fetchone()[0]
                assert reviewed_rows >= 1

            # Step 9: Revocation & Archival reactivity
            # 9a. Rename group G1 via admin -> increments access version
            cat_store = audit_env.get_catalog_store()
            cat_store.rename_group(G1_ID, f"{G1_NAME}-Переименованная")

            # Switch back to student page: reload /student/assignments or submit
            resp_stale = page.goto(f"{base_url}/student/assignments")
            assert resp_stale is not None
            assert resp_stale.status == 403
            # Reload landing page -> session requires re-unlock
            page.goto(f"{base_url}/")
            page.wait_for_selector("#unlock:not([hidden])")
            assert page.is_hidden("#submit")

            # 9b. Archive topic T1
            cat_store.archive_topic(T1_ID)

            # In student portal, re-unlock G1 with new group name
            page.select_option("#group", str(G1_ID))
            page.fill("#group_password", G1_PASSWORD)
            page.click("#unlock button[type='submit']")
            page.wait_for_selector("#submit:not([hidden])")

            # Archived topic T1 assignments must be hidden from active assignment dropdown
            asgn_opts_after_archive = page.eval_on_selector_all(
                "#assignment_id option", "opts => opts.map(o => o.value)"
            )
            assert str(A1_ID) not in asgn_opts_after_archive
            assert str(A2_ID) not in asgn_opts_after_archive

            # In admin portal, existing submissions for T1 remain downloadable
            with sqlite3.connect(str(db_path)) as conn:
                sub_id_to_download = conn.execute(
                    "SELECT id FROM submissions LIMIT 1;"
                ).fetchone()[0]

            with admin_page.expect_download() as download_info:
                admin_page.evaluate(
                    "url => { window.location.href = url; }",
                    f"{base_url}/admin/submissions/{sub_id_to_download}/download",
                )
            download = download_info.value
            assert download is not None
            assert download.suggested_filename.endswith(".zip")

            browser.close()

    finally:
        # Step 10: Graceful server shutdown
        server.shutdown()
        server_thread.join(timeout=5.0)
