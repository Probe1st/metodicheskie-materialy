import asyncio
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def run_smoke_test():
    repo_dir = Path(__file__).resolve().parent
    evidence_dir = repo_dir / "browser_evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    port = find_free_port()
    secret_key = secrets.token_hex(32)
    admin_password = "test-only"

    # 1. Setup isolated server environment in TemporaryDirectory "root with spaces"
    temp_parent = Path(tempfile.mkdtemp(prefix="smoke_test_"))
    root_with_spaces = temp_parent / "root with spaces"
    root_with_spaces.mkdir()

    print(f"Isolated server dir: {root_with_spaces}")

    # Copy necessary application files
    shutil.copy2(repo_dir / "manage.sh", root_with_spaces / "manage.sh")
    (root_with_spaces / "manage.sh").chmod(0o755)
    shutil.copy2(repo_dir / "wsgi.py", root_with_spaces / "wsgi.py")
    shutil.copy2(repo_dir / "catalog.json", root_with_spaces / "catalog.json")
    shutil.copytree(repo_dir / "app", root_with_spaces / "app")
    (root_with_spaces / ".venv").symlink_to(repo_dir / ".venv")

    # Clean env: remove APP_DIR, PYTHONPATH, GUNICORN_CMD_ARGS
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("APP_DIR", "PYTHONPATH", "GUNICORN_CMD_ARGS")
    }
    env.update({
        "HOST": "127.0.0.1",
        "PORT": str(port),
        "SECRET_KEY": secret_key,
        "ADMIN_PASSWORD": admin_password,
    })

    server_started = False
    try:
        # Start server via manage.sh start
        print("Starting isolated test server via manage.sh start...")
        res_start = subprocess.run(
            [str(root_with_spaces / "manage.sh"), "start"],
            cwd=str(root_with_spaces),
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        print("Server start output:\n", res_start.stdout)
        server_started = True

        # Verify HTTP health
        base_url = f"http://127.0.0.1:{port}"
        health_ok = False
        for _ in range(20):
            try:
                with urllib.request.urlopen(f"{base_url}/", timeout=2) as resp:
                    if resp.status == 200:
                        health_ok = True
                        break
            except Exception:
                await asyncio.sleep(0.5)
        assert health_ok, f"Test server did not become healthy at {base_url}"

        # Browser Automation with Playwright (Real Chromium)
        async with async_playwright() as p:
            chrome_bin = "/usr/bin/google-chrome-stable" if Path("/usr/bin/google-chrome-stable").exists() else None
            browser = await p.chromium.launch(
                headless=True,
                executable_path=chrome_bin,
            )
            print(f"Browser launched: {browser.version} (executable: {chrome_bin or 'playwright bundled'})")

            admin_context = await browser.new_context()
            student_context = await browser.new_context()

            admin_page = await admin_context.new_page()
            student_page = await student_context.new_page()

            # Console and page error tracking
            admin_console_errors: list[str] = []
            student_console_errors: list[str] = []
            admin_page_errors: list[str] = []
            student_page_errors: list[str] = []

            def handle_admin_console(msg):
                if msg.type == "error":
                    admin_console_errors.append(msg.text)

            def handle_student_console(msg):
                if msg.type == "error":
                    student_console_errors.append(msg.text)

            admin_page.on("console", handle_admin_console)
            student_page.on("console", handle_student_console)
            admin_page.on("pageerror", lambda err: admin_page_errors.append(str(err)))
            student_page.on("pageerror", lambda err: student_page_errors.append(str(err)))

            # Auto-accept confirm dialogs (for delete actions)
            admin_page.on("dialog", lambda dialog: dialog.accept())
            student_page.on("dialog", lambda dialog: dialog.accept())

            # ==========================================================
            # ADMIN SCENARIO
            # ==========================================================
            print("\n--- Admin Scenario Starting ---")

            # 1. Visit /admin, see login form. Enter password "test-only" -> submit. Login successful.
            await admin_page.goto(f"{base_url}/admin")
            await admin_page.wait_for_selector("#admin_login")
            assert await admin_page.is_visible("#admin_password")
            await admin_page.fill("#admin_password", admin_password)
            await admin_page.click('#admin_login button[type="submit"]')

            # Wait for reload and authenticated dashboard
            await admin_page.wait_for_selector(".admin-nav", timeout=5000)
            assert await admin_page.is_visible("text=Рабочее пространство преподавателя")
            print("Admin Step 1: Login successful")

            # 2. Create a test group
            await admin_page.goto(f"{base_url}/admin/groups")
            await admin_page.wait_for_selector("form[data-catalog-create]")
            test_group_name = "ТЕСТ-СМОУК-ГРУППА"
            await admin_page.fill("#catalog_new_name", test_group_name)
            await admin_page.click('form[data-catalog-create] button[type="submit"]')

            created_row = admin_page.locator(f'[data-catalog-item][data-name="{test_group_name}"]')
            await created_row.wait_for(timeout=5000)
            print(f"Admin Step 2: Created group '{test_group_name}'")

            # 3. Rename it
            renamed_group_name = "ТЕСТ-СМОУК-ПЕРЕИМЕНОВАНО"
            await created_row.locator('.catalog-row__heading [data-toggle-edit]').click()
            rename_form = created_row.locator("[data-catalog-rename]")
            await rename_form.wait_for(state="visible")
            await rename_form.locator('input[name="name"]').fill(renamed_group_name)
            await rename_form.locator('button[type="submit"]').click()

            renamed_row = admin_page.locator(f'[data-catalog-item][data-name="{renamed_group_name}"]')
            await renamed_row.wait_for(timeout=5000)
            print(f"Admin Step 3: Renamed group to '{renamed_group_name}'")

            # 4. Delete it (bodyless delete)
            await renamed_row.locator("[data-delete]").click()
            await renamed_row.wait_for(state="detached", timeout=5000)
            print(f"Admin Step 4: Deleted group '{renamed_group_name}' (bodyless delete verified)")

            # 5. Bulk move topics
            await admin_page.goto(f"{base_url}/admin/topics")
            await admin_page.wait_for_selector("#topic_bulk")
            # Select target subject
            subject_select = admin_page.locator('#topic_bulk select[name="subject_id"]')
            subject_options = await subject_select.locator("option").all()
            assert len(subject_options) >= 2
            target_subject_id = await subject_options[1].get_attribute("value")
            assert target_subject_id is not None
            await subject_select.select_option(value=target_subject_id)

            # Check first 2 topics
            topic_checkboxes = admin_page.locator('input[name="topic_ids"]')
            assert await topic_checkboxes.count() >= 2
            await topic_checkboxes.nth(0).check()
            await topic_checkboxes.nth(1).check()

            # Submit bulk move
            await admin_page.click('#topic_bulk button[type="submit"]')
            bulk_status = admin_page.locator('[data-topic-status][data-state="success"]')
            await bulk_status.wait_for(timeout=5000)
            status_text = await bulk_status.text_content()
            print(f"Admin Step 5: Bulk move topics completed ({status_text})")

            # 6. Go to /admin/assignments: create an assignment for a group. Update it. Delete it.
            await admin_page.goto(f"{base_url}/admin/assignments")
            await admin_page.wait_for_selector("#assignment_create_form")

            # Create an assignment
            await admin_page.locator('.pair-button:has-text("2 пара")').click()
            await admin_page.click('#assignment_create_form button[type="submit"]')

            # Wait for reload
            assignment_card = admin_page.locator(".assignment-card").first
            await assignment_card.wait_for(timeout=5000)
            print("Admin Step 6a: Created assignment")

            # Update it
            await assignment_card.locator('[data-action="edit"]').click()
            edit_form = assignment_card.locator(".assignment-edit-form")
            await edit_form.wait_for(state="visible")
            await edit_form.locator('select[name="pair_number"]').select_option("3")
            await edit_form.locator('button[type="submit"]').click()

            # Wait for reload and verify update
            updated_card = admin_page.locator(".assignment-card").first
            await updated_card.wait_for(timeout=5000)
            await admin_page.wait_for_selector(".pair-chip:has-text('3 пара')", timeout=5000)
            print("Admin Step 6b: Updated assignment to pair 3")

            # Capture visual evidence of admin assignments before deleting
            admin_assignments_shot = evidence_dir / "admin_assignments.png"
            await admin_page.screenshot(path=str(admin_assignments_shot), full_page=True)
            print(f"Captured screenshot: {admin_assignments_shot}")

            # Delete it
            await updated_card.locator('[data-action="delete"]').click()
            await updated_card.wait_for(state="detached", timeout=5000)
            print("Admin Step 6c: Deleted assignment")

            # Re-create an assignment for group "ИС 25/9-1П" so student can see it when unlocking
            await admin_page.fill("#assignment_date", "2026-11-25")
            await admin_page.locator('.pair-button:has-text("1 пара")').click()
            await admin_page.click('#assignment_create_form button[type="submit"]')
            print("Admin Step 6d: Created scheduled assignment for student group")

            # 7. Go to /admin/access: configure group with password "1234" and open access.
            await admin_page.goto(f"{base_url}/admin/access")
            await admin_page.wait_for_selector('.group-card[data-group-name="ИС 25/9-1П"]')
            group_card = admin_page.locator('.group-card[data-group-name="ИС 25/9-1П"]')

            is_open_checkbox = group_card.locator('input[name="is_open"]')
            if not await is_open_checkbox.is_checked():
                await is_open_checkbox.check()

            await group_card.locator('input[name="password"]').fill("1234")
            await group_card.locator('button[type="submit"]').click()

            # Wait for status success
            save_status = group_card.locator('.group-status[data-state="success"]')
            await save_status.wait_for(timeout=5000)
            assert await group_card.locator(".chip--open").is_visible()
            print("Admin Step 7: Group 'ИС 25/9-1П' configured with password '1234' and open access")

            # Capture admin dashboard screenshot
            await admin_page.goto(f"{base_url}/admin")
            await admin_page.wait_for_selector(".admin-layout")
            admin_dash_shot = evidence_dir / "admin_dashboard.png"
            await admin_page.screenshot(path=str(admin_dash_shot), full_page=True)
            print(f"Captured screenshot: {admin_dash_shot}")

            # ==========================================================
            # STUDENT SCENARIO
            # ==========================================================
            print("\n--- Student Scenario Starting ---")

            # 1. Visit /, select group, enter password "1234", click unlock.
            await student_page.goto(f"{base_url}/")
            await student_page.wait_for_selector("#unlock")
            await student_page.select_option("#group", "ИС 25/9-1П")
            await student_page.fill("#group_password", "1234")
            await student_page.click('#unlock button[type="submit"]')

            # 2. Verify assignments loaded and step 2 unlocked.
            await student_page.wait_for_selector("#submit:not([hidden])", timeout=5000)
            assert await student_page.is_hidden("#unlock")
            assert await student_page.is_visible("#access_summary")
            display_text = await student_page.locator("#group_display").text_content() or ""
            assert "ИС 25/9-1П" in display_text

            # Check that assignments / topics loaded
            work_options = await student_page.locator("#work option").all()
            assert len(work_options) > 1
            print("Student Steps 1 & 2: Group unlocked, step 2 active, assignments loaded")

            # 3. Submit valid .pdf file -> observe success overlay/status, verify HTTP 201 and file saved on disk.
            valid_pdf_path = temp_parent / "valid_solution.pdf"
            valid_pdf_content = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
            valid_pdf_path.write_bytes(valid_pdf_content)

            await student_page.fill("#student_name", "Смирнов Алексей Петрович")
            await student_page.select_option("#work", index=1)
            await student_page.set_input_files("#file", str(valid_pdf_path))

            async with student_page.expect_response(lambda r: "/submit" in r.url and r.request.method == "POST") as resp_info:
                await student_page.click('#submit button[type="submit"]')
            submit_resp = await resp_info.value
            assert submit_resp.status == 201, f"Expected 201, got {submit_resp.status}"

            # Observe success overlay
            await student_page.wait_for_selector("#submission_overlay:not([hidden])")
            await student_page.wait_for_selector("#overlay_success:not([hidden])")
            success_title = await student_page.locator("#overlay_success_title").text_content() or ""
            assert "Работа успешно отправлена!" in success_title

            # Capture visual evidence of success overlay
            student_success_shot = evidence_dir / "student_success.png"
            await student_page.screenshot(path=str(student_success_shot), full_page=True)
            print(f"Captured screenshot: {student_success_shot}")

            # Verify file saved on disk
            storage_dir = root_with_spaces / "storage"
            saved_pdfs = list(storage_dir.rglob("*.pdf"))
            assert len(saved_pdfs) > 0, "PDF file was not saved on disk in storage root!"
            print(f"Student Step 3: Valid PDF submitted, HTTP 201 verified, file saved on disk: {saved_pdfs[0]}")

            # Reset overlay for next submission
            await student_page.click("#overlay_reset_button")
            await student_page.wait_for_selector("#submission_overlay", state="hidden")

            # 4. Submit invalid .exe file -> observe error message "Недопустимый формат файла.", verify HTTP 400 and file NOT saved on disk.
            invalid_exe_path = temp_parent / "malicious_script.exe"
            invalid_exe_path.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00")

            await student_page.fill("#student_name", "Смирнов Алексей Петрович")
            await student_page.select_option("#work", index=1)
            await student_page.set_input_files("#file", str(invalid_exe_path))

            async with student_page.expect_response(lambda r: "/submit" in r.url and r.request.method == "POST") as resp_info:
                await student_page.click('#submit button[type="submit"]')
            submit_resp_bad = await resp_info.value
            assert submit_resp_bad.status == 400, f"Expected 400, got {submit_resp_bad.status}"

            # Observe error overlay
            await student_page.wait_for_selector("#submission_overlay:not([hidden])")
            await student_page.wait_for_selector("#overlay_error:not([hidden])")
            error_message = await student_page.locator("#overlay_error_message").text_content() or ""
            assert "Недопустимый формат файла." in error_message, f"Unexpected error message: {error_message}"

            # Capture visual evidence of file_type_not_allowed error
            student_bad_shot = evidence_dir / "student_file_type_not_allowed.png"
            await student_page.screenshot(path=str(student_bad_shot), full_page=True)
            print(f"Captured screenshot: {student_bad_shot}")

            # Verify .exe file NOT saved on disk
            saved_exes = list(storage_dir.rglob("*.exe"))
            assert len(saved_exes) == 0, f"Forbidden exe file was saved on disk: {saved_exes}"
            print("Student Step 4: Invalid .exe rejected with 400, error message observed, NOT saved on disk")

            # 5. Teacher revokes access (access closed in admin).
            print("\nTeacher revoking group access in admin...")
            await admin_page.goto(f"{base_url}/admin/access")
            await admin_page.wait_for_selector('.group-card[data-group-name="ИС 25/9-1П"]')
            group_card_admin = admin_page.locator('.group-card[data-group-name="ИС 25/9-1П"]')
            await group_card_admin.locator('input[name="is_open"]').uncheck()
            await group_card_admin.locator('button[type="submit"]').click()
            await group_card_admin.locator('.group-status[data-state="success"]').wait_for(timeout=5000)
            await admin_page.wait_for_selector('.group-card[data-group-name="ИС 25/9-1П"] .chip--closed')
            print("Admin Step 5: Teacher revoked access (group closed)")

            # 6. Student re-submits -> gets 403.
            print("\nStudent re-submitting after access revocation...")
            await student_page.click("#overlay_retry_button")
            await student_page.wait_for_selector("#submission_overlay", state="hidden")

            # Re-select valid PDF file
            await student_page.set_input_files("#file", str(valid_pdf_path))
            async with student_page.expect_response(lambda r: "/submit" in r.url and r.request.method == "POST") as resp_info:
                await student_page.click('#submit button[type="submit"]')
            submit_resp_403 = await resp_info.value
            assert submit_resp_403.status == 403, f"Expected 403, got {submit_resp_403.status}"

            await student_page.wait_for_selector("#submission_overlay:not([hidden])")
            await student_page.wait_for_selector("#overlay_error:not([hidden])")
            err_revoked = await student_page.locator("#overlay_error_message").text_content() or ""
            assert "Приём работ для данной группы закрыт" in err_revoked
            print(f"Student Step 6: HTTP 403 received, error overlay observed: '{err_revoked}'")

            # 7. Admin logout via HTML form.
            print("\nAdmin logging out via HTML form...")
            await admin_page.goto(f"{base_url}/admin")
            logout_button = admin_page.locator('form[action*="/admin/logout"] button[type="submit"]')
            await logout_button.click()
            await admin_page.wait_for_selector("#admin_login", timeout=5000)
            print("Admin Step 7: Logout successful, login form visible")

            # Assert NO unexpected JS errors in console or pageerror
            print("\nVerifying console errors and page errors...")
            # Uncaught exceptions in page:
            assert len(admin_page_errors) == 0, f"Unexpected admin page errors: {admin_page_errors}"
            assert len(student_page_errors) == 0, f"Unexpected student page errors: {student_page_errors}"

            # Filter console error logs: in Chromium, failed network requests (400, 403) log a network warning
            unexpected_admin_logs = [e for e in admin_console_errors if "status of 4" not in e and "status of 5" not in e]
            unexpected_student_logs = [e for e in student_console_errors if "status of 4" not in e and "status of 5" not in e]
            assert len(unexpected_admin_logs) == 0, f"Unexpected admin console errors: {unexpected_admin_logs}"
            assert len(unexpected_student_logs) == 0, f"Unexpected student console errors: {unexpected_student_logs}"
            print("NO unexpected JS/console errors observed on either page!")

            await browser.close()
            print("Browser closed successfully.")

    finally:
        if server_started:
            print("\nStopping test server via manage.sh stop...")
            res_stop = subprocess.run(
                [str(root_with_spaces / "manage.sh"), "stop"],
                cwd=str(root_with_spaces),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            print("Server stop output:\n", res_stop.stdout)

        # Cleanup temp directory
        shutil.rmtree(temp_parent, ignore_errors=True)

    print("\n✅ Real Chromium Browser Smoke Test PASSED completely!")


if __name__ == "__main__":
    asyncio.run(run_smoke_test())
