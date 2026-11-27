"""Browser E2E tests for student name warnings, duplicate prevention, assignment page, and admin students UI."""

from __future__ import annotations

import os
import socket
import sqlite3
import threading
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_api.sync_playwright
from werkzeug.serving import make_server

from app.access import AccessStore
from app.assignments import AssignmentStore
from app.database import connect, initialize
from app.sqlite_catalog import SqliteCatalogStore
from app.submissions import SubmissionStore
from app.web import create_app


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_browser_student_normalization_warnings_and_admin_merge_ui(tmp_path: Path) -> None:
    db_path = tmp_path / "app_v2.sqlite3"
    storage_root = tmp_path / "storage"
    initialize(db_path)
    storage_root.mkdir(parents=True, exist_ok=True)

    catalog = SqliteCatalogStore(db_path)
    group = catalog.create_group("ИС 25/9-1П")
    subject = catalog.create_subject("Информатика")
    topic = catalog.create_topic(subject.id, "Порты компьютера")

    AccessStore(db_path).configure(group.id, is_open=True, password="pw123")
    assignment = AssignmentStore(db_path).create(group.id, topic.id, "2026-11-10", 1)

    # Seed legacy unnormalized rows directly into SQLite
    kol_upper = uuid4()
    kol_lower = uuid4()
    paired_id = uuid4()
    with connect(db_path) as conn:
        for sid, name in [
            (kol_upper, "Колотович Олег"),
            (kol_lower, "колотович олег"),
            (paired_id, "Гилюк Даниил и Бондаренко Владислав"),
        ]:
            conn.execute(
                "INSERT INTO students (id, group_id, display_name, archived_at) VALUES (?, ?, ?, NULL)",
                (str(sid), str(group.id), name),
            )
        conn.commit()

    sample_zip = tmp_path / "work.zip"
    with zipfile.ZipFile(sample_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("report.txt", b"student work payload")

    store = SubmissionStore(db_path, storage_root)
    acc = AccessStore(db_path).get(group.id)
    assert acc is not None
    with sample_zip.open("rb") as fh:
        store.submit(group.id, paired_id, assignment.id, fh, "pair.zip", acc.version)

    port = _get_free_port()
    flask_app = create_app({
        "TESTING": True,
        "DATABASE_PATH": db_path,
        "STORAGE_ROOT": storage_root,
        "ADMIN_PASSWORD": "admin-secret-password",
        "SECRET_KEY": "browser-ui-test-secret-key-at-least-32-bytes",
        "MAX_UPLOAD_MB": 10,
    })

    server = make_server("127.0.0.1", port, flask_app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    chrome_path = "/home/probe/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome"
    exec_path = chrome_path if os.path.exists(chrome_path) else None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=exec_path)

            # 1. Student portal context
            student_ctx = browser.new_context()
            page = student_ctx.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda err: page_errors.append(str(err)))

            page.goto(f"{base_url}/")
            page.select_option("#group", str(group.id))
            page.fill("#group_password", "pw123")
            page.click("#unlock button[type='submit']")
            page.wait_for_selector("#submit:not([hidden])")

            # Live preview + warning on dirty/tail input -> modal confirmation before saving
            page.click("#toggle_new_student_btn")
            page.wait_for_selector("#new_student_panel:not([hidden])")
            page.fill("#new_student_input", "  манько  еремей  1. manko.drawio ")
            page.wait_for_selector("#new_student_preview:not([hidden])")
            assert "Манько Еремей" in page.inner_text("#new_student_preview")
            page.wait_for_selector("#new_student_warning:not([hidden])")
            assert "может быть неверным" in page.inner_text("#new_student_warning").lower()

            # Saving dirty name opens confirmation modal, confirming saves "Манько Еремей"
            page.click("#save_student_btn")
            page.wait_for_selector("#student_confirm_dialog[open]")
            assert "может быть неверным" in page.inner_text("#student_dialog_warnings").lower()
            page.click("#student_dialog_confirm_btn")
            page.wait_for_selector("#student_confirm_dialog", state="hidden")
            page.wait_for_selector("#new_student_panel", state="hidden")
            selected_text = page.eval_on_selector(
                "#student_id", "el => el.options[el.selectedIndex].text"
            )
            assert selected_text == "Манько Еремей"

            # Exact match (case / swapped order) auto-selects existing student immediately without popup
            page.click("#toggle_new_student_btn")
            page.fill("#new_student_input", "еремей манько")
            page.click("#save_student_btn")
            page.wait_for_selector("#new_student_panel", state="hidden")
            page.wait_for_selector("#student_selection_notice:not([hidden])")
            selected_text = page.eval_on_selector(
                "#student_id", "el => el.options[el.selectedIndex].text"
            )
            assert selected_text == "Манько Еремей"

            # Fuzzy match (typo) opens modal popup with choices: select existing, pick from list, or create new
            page.click("#toggle_new_student_btn")
            page.fill("#new_student_input", "Манько Еремеи")
            page.click("#save_student_btn")
            page.wait_for_selector("#student_confirm_dialog[open]")
            dialog_text = page.inner_text("#student_confirm_dialog")
            assert "Манько Еремей" in dialog_text
            assert not page.is_hidden("#student_dialog_pick_list_btn")
            assert not page.is_hidden("#student_dialog_confirm_btn")
            # Choose the existing similar student from the popup
            page.click("#student_dialog_matches .modal-match-btn")
            page.wait_for_selector("#student_confirm_dialog", state="hidden")
            page.wait_for_selector("#new_student_panel", state="hidden")
            selected_text = page.eval_on_selector(
                "#student_id", "el => el.options[el.selectedIndex].text"
            )
            assert selected_text == "Манько Еремей"

            # Fuzzy match where user explicitly chooses "Всё равно создать нового"
            page.click("#toggle_new_student_btn")
            page.fill("#new_student_input", "Сорокин Олег")
            page.click("#save_student_btn")
            page.wait_for_selector("#new_student_panel", state="hidden")
            page.click("#toggle_new_student_btn")
            page.fill("#new_student_input", "Сорокин Олге")
            page.click("#save_student_btn")
            page.wait_for_selector("#student_confirm_dialog[open]")
            page.click("#student_dialog_confirm_btn")
            page.wait_for_selector("#student_confirm_dialog", state="hidden")
            page.wait_for_selector("#new_student_panel", state="hidden")
            selected_text = page.eval_on_selector(
                "#student_id", "el => el.options[el.selectedIndex].text"
            )
            assert selected_text == "Сорокин Олге"

            # Hyphenated double name saves cleanly without popup
            page.click("#toggle_new_student_btn")
            page.fill("#new_student_input", "иванов-петров анна-мария")
            page.wait_for_timeout(300)
            page.click("#save_student_btn")
            page.wait_for_selector("#new_student_panel", state="hidden")
            selected_text = page.eval_on_selector(
                "#student_id", "el => el.options[el.selectedIndex].text"
            )
            assert selected_text == "Иванов-Петров Анна-Мария"

            # 2. Student assignment detail page: fuzzy typo shows popup, choosing match uses existing name
            page.goto(f"{base_url}/student/assignments/{assignment.id}")
            page.wait_for_selector("#student_name")
            page.fill("#student_name", "Колотович Олге")
            page.set_input_files("#file", str(sample_zip))
            page.click("#submit button[type='submit']")
            page.wait_for_selector("#student_confirm_dialog[open]")
            page.click("#student_dialog_matches .modal-match-btn")
            page.wait_for_selector("#overlay_success:not([hidden])")
            assert not page_errors, f"Unexpected JS errors: {page_errors}"

            # 3. Admin students UI
            admin_ctx = browser.new_context()
            apage = admin_ctx.new_page()
            apage_errors: list[str] = []
            apage.on("pageerror", lambda err: apage_errors.append(str(err)))

            apage.goto(f"{base_url}/admin")
            apage.fill("#admin_password", "admin-secret-password")
            apage.click("#admin_login button[type='submit']")
            apage.wait_for_selector("a[href='/admin/students']")
            apage.click("a[href='/admin/students']")
            apage.wait_for_selector("#normalize_group_btn")

            # Batch normalize safe duplicates ("Колотович Олег" + "колотович олег")
            apage.click("#normalize_group_btn")
            apage.wait_for_selector("#app_dialog[open]")
            apage.click("#app_dialog_accept")
            apage.wait_for_function(
                "() => (document.querySelector('#admin_students_status')?.textContent || '').includes('объединено')"
            )

            # Split paired record via UI dialog
            apage.click("[data-paired-card] [data-split-btn]")
            apage.wait_for_selector("#app_dialog[open]")
            apage.click("#app_dialog_accept")
            apage.wait_for_function(
                "() => (document.querySelector('#admin_students_status')?.textContent || '').includes('разделена')"
            )
            assert not apage_errors, f"Unexpected admin JS errors: {apage_errors}"

            browser.close()
    finally:
        server.shutdown()

    # Verify DB invariants independently
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT display_name FROM students").fetchall()
        names = [r[0] for r in rows]
        subs_count = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        ic = conn.execute("PRAGMA integrity_check;").fetchone()[0]

    assert ic == "ok"
    assert names.count("Колотович Олег") == 1
    assert "колотович олег" not in names
    assert "Иванов-Петров Анна-Мария" in names
    assert "Гилюк Даниил" in names
    assert "Бондаренко Владислав" in names
    assert "Гилюк Даниил и Бондаренко Владислав" not in names
    # 1 submission from student assignment page + 2 after splitting paired submission (1 moved + 1 cloned)
    assert subs_count == 3
