# Homework Submission UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an accessible, responsive student submission form and group-access administration panel in the approved calm premium visual style without changing backend routes or access behavior.

**Architecture:** Flask keeps rendering Jinja templates and the existing HTTP contract stays intact. A shared stylesheet supplies visual tokens and responsive primitives; two small page-owned scripts manage the existing asynchronous student and administrator flows. Server code remains unchanged.

**Tech Stack:** Flask 3, Jinja templates, browser-native CSS and JavaScript, pytest.

## Global Constraints

- Keep all existing routes, form field names, status codes, and JSON responses unchanged.
- Add no frontend dependency, build pipeline, external font, image, icon, or network request.
- Use system serif headings and system sans-serif controls.
- Use `#173F37` as the primary colour, `#FBFAF6` as the base background, and `#D5EF67` only for primary actions.
- Preserve visible labels, keyboard focus, `aria-live` status announcements, and `prefers-reduced-motion` support.
- Keep error copy non-specific where the current access model deliberately does so.

## Execution Amendment

Do not add document-source assertions for template IDs, classes, or static-asset paths: they test implementation details rather than a user-visible contract. The existing Flask endpoint suite remains unchanged; real-browser scenarios verify the visual UI, form states, responsive layout and accessibility semantics.

---

## File Structure

| File | Change |
| --- | --- |
| `homework-submissions/app/static/styles.css` | Create shared tokens, page frame, form/card controls, status blocks, group cards, responsive layouts and motion rule. |
| `homework-submissions/app/static/student.js` | Create student access, work-list, selected-file and upload-result behavior. |
| `homework-submissions/app/static/admin.js` | Create admin-login and per-group save behavior. |
| `homework-submissions/app/templates/index.html` | Replace bare forms with semantic student page structure and external assets. |
| `homework-submissions/app/templates/admin.html` | Replace table UI with login card and group cards, then load external assets. |
| `homework-submissions/tests/test_web.py` | Keep the existing endpoint and access-control tests unchanged. |

## Task 1: Shared visual foundation

**Files:**
- Create: `homework-submissions/app/static/styles.css`
- Test: `homework-submissions/tests/test_web.py`

**Interfaces:**
- Consumes: semantic class names introduced by Tasks 2 and 3.
- Produces: `static/styles.css`, loaded by both Jinja templates with `url_for('static', filename='styles.css')`.

- [ ] **Step 1: Write the failing document-contract tests**

Add these assertions after `test_admin_page_shows_all_catalog_groups`:

```python
def test_student_page_exposes_accessible_submission_regions(client_env):
    client, _ = client_env

    page = client.get('/').get_data(as_text=True)

    assert 'href="/static/styles.css"' in page
    assert 'id="unlock_status"' in page
    assert 'aria-live="polite"' in page
    assert 'id="submit_status"' in page


def test_admin_page_exposes_accessible_status_region(client_env):
    client, _ = client_env

    page = client.get('/admin').get_data(as_text=True)

    assert 'href="/static/styles.css"' in page
    assert 'id="admin_login_status"' in page
    assert 'aria-live="polite"' in page
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `cd homework-submissions && pytest tests/test_web.py -k 'accessible' -v`

Expected: FAIL because the rendered templates do not yet contain the stylesheet or the new status-region IDs.

- [ ] **Step 3: Create the shared stylesheet**

Create `homework-submissions/app/static/styles.css` with the following complete foundation. Do not include page-specific request logic in CSS.

```css
:root {
  color-scheme: light;
  --pine: #173f37;
  --pine-dark: #102b25;
  --paper: #fbfaf6;
  --surface: #ffffff;
  --mist: #edf2f1;
  --line: #cbd7d2;
  --ink: #172824;
  --muted: #5f716b;
  --accent: #d5ef67;
  --danger: #8d3025;
  --danger-surface: #fff0ed;
  --success: #255b42;
  --success-surface: #e8f5ea;
  --shadow: 0 18px 45px rgb(23 63 55 / 11%);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: var(--ink);
  background: var(--paper);
}

* { box-sizing: border-box; }
body { margin: 0; min-width: 320px; background: var(--paper); }
button, input, select { font: inherit; }
button { cursor: pointer; }
button:disabled { cursor: wait; opacity: .62; }
:focus-visible { outline: 3px solid var(--accent); outline-offset: 3px; }
[hidden] { display: none !important; }

.site-header { background: var(--pine); color: var(--paper); }
.site-header__inner, .page-shell { width: min(100% - 32px, 1120px); margin-inline: auto; }
.site-header__inner { display: flex; align-items: center; justify-content: space-between; min-height: 72px; gap: 20px; }
.brand { color: inherit; text-decoration: none; font-size: .78rem; font-weight: 750; letter-spacing: .12em; text-transform: uppercase; }
.page-shell { padding-block: clamp(36px, 8vw, 88px); }
.eyebrow { margin: 0 0 10px; color: var(--muted); font-size: .75rem; font-weight: 750; letter-spacing: .12em; text-transform: uppercase; }
h1, h2, h3 { font-family: Georgia, "Times New Roman", serif; color: var(--pine-dark); }
h1 { max-width: 700px; margin: 0; font-size: clamp(2.4rem, 7vw, 4.8rem); line-height: .98; letter-spacing: -.045em; }
h2 { margin: 0; font-size: clamp(1.65rem, 4vw, 2.35rem); line-height: 1.05; }
h3 { margin: 0; font-size: 1.35rem; }
.lead { max-width: 620px; margin: 20px 0 0; color: var(--muted); font-size: 1.05rem; line-height: 1.6; }

.panel { background: var(--surface); border: 1px solid var(--line); box-shadow: var(--shadow); }
.student-layout { display: grid; grid-template-columns: minmax(0, .9fr) minmax(320px, .7fr); gap: clamp(28px, 7vw, 84px); align-items: center; }
.form-card { padding: clamp(24px, 5vw, 44px); border-radius: 22px; }
.form-stack { display: grid; gap: 20px; }
.field { display: grid; gap: 8px; color: var(--pine-dark); font-size: .9rem; font-weight: 700; }
.field__hint { color: var(--muted); font-size: .8rem; font-weight: 500; }
input, select { width: 100%; min-height: 48px; border: 1px solid var(--line); border-radius: 10px; padding: 11px 13px; color: var(--ink); background: var(--surface); }
input:hover, select:hover { border-color: #91a69f; }
.button { display: inline-flex; min-height: 48px; align-items: center; justify-content: center; border: 1px solid transparent; border-radius: 10px; padding: 11px 16px; font-weight: 800; text-decoration: none; }
.button--primary { color: var(--pine-dark); background: var(--accent); }
.button--primary:hover { background: #c8e958; }
.button--quiet { color: var(--paper); border-color: rgb(255 255 255 / 35%); background: transparent; }
.button--outline { color: var(--pine); border-color: var(--pine); background: transparent; }
.status { min-height: 24px; margin: 0; border-radius: 10px; font-size: .9rem; line-height: 1.45; }
.status:not(:empty) { padding: 12px 14px; }
.status[data-state="error"] { color: var(--danger); background: var(--danger-surface); }
.status[data-state="success"] { color: var(--success); background: var(--success-surface); }
.status[data-state="working"] { color: var(--pine); background: var(--mist); }

.access-summary { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 16px; border: 1px solid var(--line); border-radius: 12px; background: var(--mist); }
.access-summary strong { display: block; margin-top: 2px; }
.form-section { display: grid; gap: 18px; padding-top: 22px; border-top: 1px solid var(--line); }
.file-picker { position: relative; display: grid; gap: 8px; min-height: 122px; place-items: center; border: 1.5px dashed #86a198; border-radius: 12px; padding: 18px; text-align: center; background: var(--mist); }
.file-picker input { position: absolute; inset: 0; min-height: 100%; opacity: 0; cursor: pointer; }
.file-picker__title { font-weight: 800; }
.file-picker__detail { color: var(--muted); font-size: .85rem; }
.file-picker:focus-within { outline: 3px solid var(--accent); outline-offset: 3px; }

.admin-hero, .admin-topline { display: flex; align-items: end; justify-content: space-between; gap: 24px; }
.summary-list { display: flex; flex-wrap: wrap; gap: 12px; margin: 30px 0; }
.summary-card { min-width: 150px; padding: 16px; border: 1px solid var(--line); border-radius: 12px; background: var(--mist); }
.summary-card strong { display: block; font: 2rem/1 Georgia, "Times New Roman", serif; color: var(--pine); }
.group-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 18px; }
.group-card { display: grid; gap: 20px; padding: 24px; border-radius: 16px; }
.group-card__heading { display: flex; align-items: start; justify-content: space-between; gap: 12px; }
.chip { display: inline-flex; border-radius: 999px; padding: 5px 9px; font-size: .74rem; font-weight: 800; white-space: nowrap; }
.chip--open { color: var(--success); background: var(--success-surface); }
.chip--closed { color: var(--danger); background: var(--danger-surface); }
.switch { display: flex; align-items: center; gap: 10px; font-weight: 750; }
.switch input { width: 20px; min-height: 20px; accent-color: var(--pine); }
.group-actions { display: flex; align-items: center; gap: 12px; }

@media (max-width: 760px) {
  .site-header__inner, .admin-hero, .admin-topline, .access-summary { align-items: start; flex-direction: column; }
  .student-layout { grid-template-columns: 1fr; }
  .student-layout__intro { order: -1; }
  .page-shell { width: min(100% - 24px, 1120px); }
  .form-card, .group-card { padding: 22px; }
  .button { width: 100%; }
}
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; } }
```

- [ ] **Step 4: Run the focused test to verify it still fails**

Run: `cd homework-submissions && pytest tests/test_web.py -k 'accessible' -v`

Expected: FAIL. The stylesheet exists but the page templates have not loaded it or exposed the status IDs yet.

- [ ] **Step 5: Commit the foundation**

```bash
git add homework-submissions/app/static/styles.css homework-submissions/tests/test_web.py
git commit -m "feat: add homework submission UI foundation"
```

## Task 2: Student submission interface

**Files:**
- Create: `homework-submissions/app/static/student.js`
- Modify: `homework-submissions/app/templates/index.html`
- Modify: `homework-submissions/tests/test_web.py`

**Interfaces:**
- Consumes: `catalog` and `limit` injected by the existing `index()` route; existing `/unlock` and `/submit` contracts.
- Produces: student controls with IDs `unlock`, `submit`, `unlock_status`, `submit_status`, `selected_group`, `group_display`, `discipline`, `work`, `works`, `file`, `file_name`.

- [ ] **Step 1: Extend the document-contract test before changing templates**

Extend `test_student_page_exposes_accessible_submission_regions`:

```python
    assert 'id="unlock"' in page
    assert 'id="submit"' in page
    assert 'id="file_name"' in page
    assert 'src="/static/student.js"' in page
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `cd homework-submissions && pytest tests/test_web.py::test_student_page_exposes_accessible_submission_regions -v`

Expected: FAIL because the current page has no file-name region or external JavaScript asset.

- [ ] **Step 3: Replace `app/templates/index.html` with semantic student markup**

Use the existing Jinja loops unchanged for groups and disciplines. Load the shared stylesheet in `<head>`, assign `window.submissionCatalog = {{ catalog.disciplines|tojson }}`, then load `/static/student.js` with `defer`. The body must expose the exact IDs in the interface block and use this hierarchy:

```html
<header class="site-header">
  <div class="site-header__inner">
    <a class="brand" href="/">Учебный портал</a>
    <span>Приём заданий</span>
  </div>
</header>
<main class="page-shell student-layout">
  <section class="student-layout__intro" aria-labelledby="page_title">
    <p class="eyebrow">Загрузка домашней работы</p>
    <h1 id="page_title">Сдать работу.<br>Без лишнего.</h1>
    <p class="lead">Подтвердите доступ своей группы, заполните данные и загрузите готовый файл.</p>
  </section>
  <section class="panel form-card" aria-label="Форма сдачи работы">
    <!-- access form, summary, submission form and result regions -->
  </section>
</main>
```

The access form contains labelled `group` and `password` fields, the primary button text `Открыть форму`, and `<p class="status" id="unlock_status" aria-live="polite"></p>`. The post-access summary contains `#group_display`, a `Сменить группу` button of `type="button"`, and hidden `#selected_group`. The submission form contains its existing form fields, with a labelled `.file-picker` wrapping `#file`, an `#file_name` helper element, a `#submit_status` status element and button text `Отправить работу`.

- [ ] **Step 4: Create `app/static/student.js`**

Implement all existing behaviors without inline handlers:

```js
const catalog = window.submissionCatalog;
const unlockForm = document.querySelector('#unlock');
const submitForm = document.querySelector('#submit');
const unlockStatus = document.querySelector('#unlock_status');
const submitStatus = document.querySelector('#submit_status');
const discipline = document.querySelector('#discipline');
const work = document.querySelector('#work');
const workList = document.querySelector('#works');
const selectedGroup = document.querySelector('#selected_group');
const groupDisplay = document.querySelector('#group_display');
const accessSummary = document.querySelector('#access_summary');
const changeGroup = document.querySelector('#change_group');
const file = document.querySelector('#file');
const fileName = document.querySelector('#file_name');

function setStatus(element, state, message) {
  element.dataset.state = state;
  element.textContent = message;
}

function updateWorks() {
  workList.replaceChildren(...(catalog[discipline.value] || []).map((name) => {
    const option = document.createElement('option');
    option.value = name;
    return option;
  }));
  work.value = '';
}

function setBusy(form, busy, label) {
  const button = form.querySelector('button[type="submit"]');
  button.disabled = busy;
  button.textContent = label;
}

function resetAccess() {
  selectedGroup.value = '';
  groupDisplay.textContent = '';
  accessSummary.hidden = true;
  submitForm.hidden = true;
  unlockForm.hidden = false;
  unlockForm.querySelector('[name="password"]').value = '';
  unlockForm.querySelector('[name="password"]').focus();
}

discipline.addEventListener('change', updateWorks);
file.addEventListener('change', () => {
  fileName.textContent = file.files[0] ? file.files[0].name : `Файл до ${window.submissionLimit} ГБ`;
});
changeGroup.addEventListener('click', resetAccess);
updateWorks();

unlockForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const formData = new FormData(unlockForm);
  setBusy(unlockForm, true, 'Проверяем доступ…');
  setStatus(unlockStatus, 'working', 'Проверяем доступ…');
  try {
    const response = await fetch('/unlock', { method: 'POST', body: formData });
    if (!response.ok) throw new Error('denied');
    selectedGroup.value = formData.get('group');
    groupDisplay.textContent = selectedGroup.value;
    unlockForm.hidden = true;
    accessSummary.hidden = false;
    submitForm.hidden = false;
    setStatus(unlockStatus, '', '');
    discipline.focus();
  } catch {
    setStatus(unlockStatus, 'error', 'Доступ к приёму работ закрыт или пароль неверен.');
  } finally {
    setBusy(unlockForm, false, 'Открыть форму');
  }
});

submitForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  setBusy(submitForm, true, 'Отправляем…');
  setStatus(submitStatus, 'working', 'Загружаем файл…');
  try {
    const response = await fetch('/submit', { method: 'POST', body: new FormData(submitForm) });
    if (!response.ok) throw new Error('failed');
    const { filename } = await response.json();
    setStatus(submitStatus, 'success', `Работа принята: ${filename}`);
  } catch {
    setStatus(submitStatus, 'error', 'Ошибка отправки: проверьте статус приёма и поля формы.');
  } finally {
    setBusy(submitForm, false, 'Отправить работу');
  }
});
```

Before the script tag, assign `window.submissionLimit = {{ limit|tojson }}`. Do not use `innerHTML` to insert catalogue data or server-returned filename.

- [ ] **Step 5: Run the focused template test**

Run: `cd homework-submissions && pytest tests/test_web.py::test_student_page_exposes_accessible_submission_regions -v`

Expected: PASS.

- [ ] **Step 6: Smoke-test the student flow in a browser**

Run the Flask development server with a temporary local environment, configure a group through the existing admin endpoint, then use Chromium to: open `/`; unlock the configured group; change discipline; select a small PDF fixture; submit it; confirm the success block names the returned file. Repeat with a wrong password and confirm only the access card reports the safe rejection message. Resize to 375px wide and confirm no horizontal overflow.

- [ ] **Step 7: Commit the student experience**

```bash
git add homework-submissions/app/templates/index.html homework-submissions/app/static/student.js homework-submissions/tests/test_web.py
git commit -m "feat: redesign student submission form"
```

## Task 3: Administration interface

**Files:**
- Create: `homework-submissions/app/static/admin.js`
- Modify: `homework-submissions/app/templates/admin.html`
- Modify: `homework-submissions/tests/test_web.py`

**Interfaces:**
- Consumes: existing `authenticated` and `groups` Jinja data; existing `/admin/login` and `/admin/groups` contracts.
- Produces: login status `#admin_login_status`, individual `.group-status` elements, `.group-form` requests, card counters and `/static/admin.js`.

- [ ] **Step 1: Extend the admin document-contract test**

Update `test_admin_page_exposes_accessible_status_region`:

```python
    assert 'src="/static/admin.js"' in page

    client.post('/admin/login', data={'password': 'admin-secret'})
    dashboard = client.get('/admin').get_data(as_text=True)
    assert 'class="group-grid"' in dashboard
    assert 'class="group-status status"' in dashboard
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd homework-submissions && pytest tests/test_web.py::test_admin_page_exposes_accessible_status_region -v`

Expected: FAIL because the current dashboard uses a table and no external script.

- [ ] **Step 3: Replace `app/templates/admin.html` with card-based semantic markup**

Load the shared stylesheet and `admin.js` on both authenticated and login states. Use the shared header. Preserve every existing form field name and Jinja value exactly.

For unauthenticated users, render a centred `.panel.form-card` login form with `id="admin_login"`, its password field and `<p class="status" id="admin_login_status" aria-live="polite"></p>`.

For authenticated users, calculate counters in Jinja with:

```jinja2
{% set open_count = groups | selectattr('is_open') | list | length %}
{% set closed_count = groups | length - open_count %}
```

Render two `.summary-card` counters. For each group, render one `<article class="panel group-card">` containing a heading, a `chip--open` or `chip--closed` span, a `.group-form` with hidden `group`, existing checkbox, datetime-local and password fields, a submit button, and a local `<p class="group-status status" aria-live="polite"></p>`. Keep the logout as a conventional POST form.

- [ ] **Step 4: Create `app/static/admin.js`**

Implement the login and independent group-card persistence:

```js
function setStatus(element, state, message) {
  element.dataset.state = state;
  element.textContent = message;
}

function setBusy(form, busy, label) {
  const button = form.querySelector('button[type="submit"]');
  button.disabled = busy;
  button.textContent = label;
}

const loginForm = document.querySelector('#admin_login');
if (loginForm) {
  const status = document.querySelector('#admin_login_status');
  loginForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    setBusy(loginForm, true, 'Входим…');
    setStatus(status, 'working', 'Проверяем пароль…');
    try {
      const response = await fetch('/admin/login', { method: 'POST', body: new FormData(loginForm) });
      if (!response.ok) throw new Error('denied');
      window.location.reload();
    } catch {
      setStatus(status, 'error', 'Неверный мастер-пароль.');
      setBusy(loginForm, false, 'Войти в панель');
    }
  });
}

document.querySelectorAll('.group-form').forEach((form) => {
  const status = form.closest('.group-card').querySelector('.group-status');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    setBusy(form, true, 'Сохраняем…');
    setStatus(status, 'working', 'Сохранение…');
    try {
      const response = await fetch('/admin/groups', { method: 'POST', body: new FormData(form) });
      if (!response.ok) throw new Error('failed');
      setStatus(status, 'success', 'Настройки группы сохранены.');
    } catch {
      setStatus(status, 'error', 'Ошибка сохранения настроек.');
    } finally {
      setBusy(form, false, 'Сохранить');
    }
  });
});
```

- [ ] **Step 5: Run the focused test to verify it passes**

Run: `cd homework-submissions && pytest tests/test_web.py::test_admin_page_exposes_accessible_status_region -v`

Expected: PASS.

- [ ] **Step 6: Smoke-test administration in a browser**

Open `/admin` in Chromium. Confirm an incorrect password shows the local error, then authenticate. Confirm summary counters match cards. Change one group to open with a password and a future deadline; submit it; confirm only that card reports success. On a 375px viewport, confirm cards fit without a horizontal scrollbar.

- [ ] **Step 7: Run the full application test suite**

Run: `cd homework-submissions && pytest -q`

Expected: PASS with every access-control and storage test passing.

- [ ] **Step 8: Commit the administration experience**

```bash
git add homework-submissions/app/templates/admin.html homework-submissions/app/static/admin.js homework-submissions/tests/test_web.py
git commit -m "feat: redesign submission administration"
```

## Task 4: Final review and cleanup

**Files:**
- Modify only if browser verification identifies a concrete defect in the files above.

**Interfaces:**
- Consumes: complete UI from Tasks 1–3.
- Produces: verified responsive pages; no scaffold, local browser session, or temporary fixture files committed.

- [ ] **Step 1: Verify static asset delivery**

Run: `cd homework-submissions && python -c "from app.web import create_app; app = create_app({'TESTING': True}); client = app.test_client(); print(client.get('/static/styles.css').status_code, client.get('/static/student.js').status_code, client.get('/static/admin.js').status_code)"`

Expected: `200 200 200`.

- [ ] **Step 2: Inspect the two actual browser surfaces**

Use Chromium at desktop and 375px widths. Verify student initial, unlocked, successful-upload and rejected-access states; verify administrator login, dashboard, card-save success and card-save error states. Check labels, focus rings, no horizontal overflow and the absence of unstyled-content flashes.

- [ ] **Step 3: Remove throwaway test uploads and stop the local server**

Delete only files created by the smoke test under its temporary directory. Stop the local process through the supervised process manager.

- [ ] **Step 4: Commit only concrete corrective edits**

If a browser check produced an actual defect and it was fixed:

```bash
git add homework-submissions/app/static homework-submissions/app/templates homework-submissions/tests
git commit -m "fix: polish homework submission interface"
```

Otherwise, do not create an empty commit.
