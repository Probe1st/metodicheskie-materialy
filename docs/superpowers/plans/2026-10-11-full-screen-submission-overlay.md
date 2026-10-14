# Full-Screen Submission Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a full-screen modal overlay with dimmed, blurred backdrop that activates immediately upon student submission to block duplicate clicks and guide the user through Loading, Success (with confirmed filename and "Сдать ещё одну работу" reset), and Error states.

**Architecture:** Client-side modal dialog component in `index.html` (`#submission_overlay`), styled via CSS custom properties in `styles.css` with keyframe spinner and WCAG AAA contrast, managed by event-driven transitions in `student.js`. No backend API changes needed.

**Tech Stack:** Jinja2, Vanilla JavaScript (ES6+), CSS Custom Properties, SVG, Pytest.

## Global Constraints
- All 72 existing pytest tests must pass without regressions.
- The overlay must block all user interactions (pointer, touch, scroll) while in flight.
- Focus must be trapped inside the modal while open (`aria-modal="true"`, `role="dialog"`), moving focus to action buttons on state transitions.
- Strictly no emojis as icons — use inline vector SVGs.
- WCAG AAA text contrast (>7:1) for normal text and AA (>=3:1) for UI boundaries across light and dark themes.
- Support `prefers-reduced-motion: reduce`.

---

## File Structure

| File | Responsibility |
|---|---|
| `homework-submissions/app/static/styles.css` | Define semantic theme tokens for the overlay (backdrop, surfaces, borders, icon fills, spinner); full-screen backdrop geometry; centered modal card; SVG framing; keyframe spinner animation; reduced-motion overrides; WCAG AAA contrast compliance. |
| `homework-submissions/app/templates/index.html` | Define `#submission_overlay` container with `role="dialog"`, `aria-modal="true"`, `aria-labelledby="overlay_title"`, and `hidden` attribute. Encapsulate 3 state views: `#overlay_loading`, `#overlay_success`, `#overlay_error` with inline SVGs, text elements, filename badge, and action buttons. |
| `homework-submissions/app/static/student.js` | Manage overlay lifecycle on form submission: validate fields/file, display `#submission_overlay`, activate `#overlay_loading`, disable submit action; handle HTTP 201 success (render filename badge, focus `#overlay_reset_button`); handle reset flow (clear dropzone, hide overlay, retain group authentication); handle fetch error (display message, focus `#overlay_retry_button`); handle retry flow (hide overlay, keep form fields intact); implement keyboard trapping. |
| `homework-submissions/tests/test_web.py` | Automated unit/integration test verifying that `GET /` renders the `#submission_overlay` dialog with `role="dialog"`, `aria-modal="true"`, all 3 state views, SVGs, and action buttons. |

---

## Task 1: CSS Overlay Tokens, Backdrop, Spinner, and Modal Card Styles

**Files:**
- Modify: `homework-submissions/app/static/styles.css:86-137` (Layer 2 semantic tokens), `styles.css:355-395` (component styles and reduced-motion rules)
- Test: `homework-submissions/tests/test_web.py`

**Interfaces:**
- Consumes: Primitive palette tokens `--primitive-slate-*`, `--primitive-dark-slate-*`, `--primitive-emerald-*`, `--primitive-ruby-*`, `--primitive-indigo-*`, `--shadow-overlay`, `--radius-card`, `--radius-pill`, `--space-*`, `--type-*`.
- Produces:
  - Semantic tokens: `--color-overlay-backdrop`, `--color-overlay-card-bg`, `--color-overlay-card-border`, `--color-overlay-success-icon`, `--color-overlay-success-surface`, `--color-overlay-error-icon`, `--color-overlay-error-surface`, `--color-overlay-spinner-track`, `--color-overlay-spinner-head`.
  - Class selectors: `.submission-overlay`, `.submission-overlay[hidden]`, `.submission-overlay__card`, `.submission-overlay__view`, `.submission-overlay__view[hidden]`, `.submission-overlay__title`, `.submission-overlay__lead`, `.submission-overlay__lead--error`, `.submission-overlay__icon-wrap`, `.submission-overlay__icon-wrap--success`, `.submission-overlay__icon-wrap--error`, `.submission-overlay__icon`, `.submission-overlay__icon--success`, `.submission-overlay__icon--error`, `.submission-overlay__spinner`, `.submission-overlay__spinner-track`, `.submission-overlay__spinner-head`, `.submission-overlay__badge`, `.submission-overlay__actions`, `.submission-overlay__button`.
  - Animations: `@keyframes spin`, `@keyframes overlayFadeIn`, `@keyframes overlayCardPop`.
  - Reduced-motion overrides in `@media (prefers-reduced-motion: reduce)`.

- [x] **Step 1: Verify baseline pytest tests pass**

Run:
```bash
homework-submissions/.venv/bin/pytest homework-submissions/tests
```
Expected output:
```
============================== 72 passed in ... ==============================
```

- [x] **Step 2: Add semantic overlay tokens to light and dark themes in `styles.css`**

In `homework-submissions/app/static/styles.css`:
1. In `:root, [data-theme="light"]` (around line 85), append:
```css
  --color-overlay-backdrop: rgb(15 23 42 / 60%);
  --color-overlay-card-bg: var(--primitive-slate-0);
  --color-overlay-card-border: var(--primitive-slate-200);
  --color-overlay-success-icon: var(--primitive-emerald-800);
  --color-overlay-success-surface: var(--primitive-emerald-50);
  --color-overlay-error-icon: var(--primitive-ruby-800);
  --color-overlay-error-surface: var(--primitive-ruby-50);
  --color-overlay-spinner-track: var(--primitive-slate-200);
  --color-overlay-spinner-head: var(--primitive-indigo-700);
```
2. In `[data-theme="dark"]` (around line 109), append:
```css
  --color-overlay-backdrop: rgb(11 18 32 / 75%);
  --color-overlay-card-bg: var(--primitive-dark-slate-50);
  --color-overlay-card-border: var(--primitive-dark-slate-200);
  --color-overlay-success-icon: var(--primitive-dark-emerald-800);
  --color-overlay-success-surface: var(--primitive-dark-emerald-50);
  --color-overlay-error-icon: var(--primitive-dark-ruby-800);
  --color-overlay-error-surface: var(--primitive-dark-ruby-50);
  --color-overlay-spinner-track: var(--primitive-dark-slate-200);
  --color-overlay-spinner-head: var(--primitive-dark-indigo-700);
```
3. In `@media (prefers-color-scheme: dark) { :root:not([data-theme]) { ... } }` (around line 135), append the same dark semantic tokens:
```css
    --color-overlay-backdrop: rgb(11 18 32 / 75%);
    --color-overlay-card-bg: var(--primitive-dark-slate-50);
    --color-overlay-card-border: var(--primitive-dark-slate-200);
    --color-overlay-success-icon: var(--primitive-dark-emerald-800);
    --color-overlay-success-surface: var(--primitive-dark-emerald-50);
    --color-overlay-error-icon: var(--primitive-dark-ruby-800);
    --color-overlay-error-surface: var(--primitive-dark-ruby-50);
    --color-overlay-spinner-track: var(--primitive-dark-slate-200);
    --color-overlay-spinner-head: var(--primitive-dark-indigo-700);
```

- [x] **Step 3: Add overlay layout, card, spinner, and badge styles to `styles.css`**

Add the following rules immediately before the responsive `@media (min-width: 375px)` section (around line 355):

```css
/* Full-screen submission overlay */
.submission-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-4);
  background-color: var(--color-overlay-backdrop);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  animation: overlayFadeIn var(--motion-normal) ease-out;
}

.submission-overlay[hidden] {
  display: none !important;
}

@keyframes overlayFadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

.submission-overlay__card {
  width: min(100%, 480px);
  background-color: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: var(--radius-card);
  box-shadow: var(--shadow-overlay);
  padding: var(--space-8);
  text-align: center;
  animation: overlayCardPop var(--motion-normal) cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes overlayCardPop {
  from { transform: scale(0.96); opacity: 0; }
  to { transform: scale(1); opacity: 1; }
}

.submission-overlay__view {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.submission-overlay__view[hidden] {
  display: none !important;
}

.submission-overlay__title {
  margin: 0 0 var(--space-2);
  font-size: var(--type-xl);
  font-weight: 700;
  color: var(--color-text);
  line-height: 1.3;
}

.submission-overlay__lead {
  margin: 0 0 var(--space-6);
  font-size: var(--type-base);
  color: var(--color-text-secondary);
  line-height: 1.5;
  max-width: 40ch;
}

.submission-overlay__lead--error {
  color: var(--status-danger-fg);
}

.submission-overlay__icon-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 4rem;
  height: 4rem;
  border-radius: var(--radius-pill);
  margin-bottom: var(--space-4);
  background-color: var(--color-surface-muted);
  color: var(--color-primary);
}

.submission-overlay__icon-wrap--success {
  background-color: var(--color-overlay-success-surface);
  color: var(--color-overlay-success-icon);
}

.submission-overlay__icon-wrap--error {
  background-color: var(--color-overlay-error-surface);
  color: var(--color-overlay-error-icon);
}

.submission-overlay__icon {
  width: 2.5rem;
  height: 2.5rem;
}

/* Indeterminate spinner */
.submission-overlay__spinner {
  width: 2.75rem;
  height: 2.75rem;
  animation: spin 1s linear infinite;
}

.submission-overlay__spinner-track {
  stroke: var(--color-overlay-spinner-track);
  opacity: 0.5;
}

.submission-overlay__spinner-head {
  stroke: var(--color-overlay-spinner-head);
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* Filename badge */
.submission-overlay__badge {
  display: inline-block;
  max-width: 100%;
  padding: var(--space-2) var(--space-4);
  margin-bottom: var(--space-6);
  font-size: var(--type-sm);
  font-weight: 600;
  color: var(--status-success-fg);
  background-color: var(--status-success-bg);
  border: 1px solid var(--color-overlay-success-icon);
  border-radius: var(--radius-pill);
  overflow-wrap: anywhere;
  word-break: break-word;
  line-height: 1.4;
}

.submission-overlay__actions {
  width: 100%;
}

.submission-overlay__button {
  width: 100%;
  padding-block: var(--space-3);
  font-size: var(--type-base);
  font-weight: 600;
}
```

- [x] **Step 4: Update reduced-motion media query in `styles.css`**

In `@media (prefers-reduced-motion: reduce)` at the end of `styles.css`, append:
```css
  .submission-overlay,
  .submission-overlay__card {
    animation: none !important;
  }
  .submission-overlay__spinner {
    animation: none !important;
    stroke-dasharray: 40 10;
  }
```

- [x] **Step 5: Run tests and verify CSS file integrity**

Run:
```bash
homework-submissions/.venv/bin/pytest homework-submissions/tests
```
Expected output:
```
============================== 72 passed in ... ==============================
```

- [x] **Step 6: Commit CSS styles**

Run:
```bash
git add homework-submissions/app/static/styles.css
git commit -m "feat(css): add styles and tokens for full-screen submission overlay"
```

---

## Task 2: HTML Overlay Dialog Markup in Student Template

**Files:**
- Modify: `homework-submissions/tests/test_web.py`
- Modify: `homework-submissions/app/templates/index.html:128-136`

**Interfaces:**
- Consumes: CSS classes from Task 1 (`.submission-overlay`, `.submission-overlay__card`, `.submission-overlay__view`, etc.).
- Produces:
  - Container `#submission_overlay` with `role="dialog"`, `aria-modal="true"`, `aria-labelledby="overlay_title"`, `hidden`.
  - Loading view `#overlay_loading` with spinner SVG and `#overlay_title`.
  - Success view `#overlay_success` with checkmark SVG, `#overlay_success_title`, `#overlay_filename`, and button `#overlay_reset_button`.
  - Error view `#overlay_error` with alert SVG, `#overlay_error_title`, `#overlay_error_message`, and button `#overlay_retry_button`.

- [x] **Step 1: Write a failing test in `tests/test_web.py`**

Append to `homework-submissions/tests/test_web.py`:
```python
def test_student_index_renders_submission_overlay_dialog(client_env):
    client, _ = client_env
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    # Dialog container and accessibility attributes
    assert 'id="submission_overlay"' in html
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'aria-labelledby="overlay_title"' in html

    # State views
    assert 'id="overlay_loading"' in html
    assert 'id="overlay_title"' in html
    assert "Отправляем работу..." in html
    assert "Пожалуйста, подождите, файл загружается на сервер..." in html

    assert 'id="overlay_success"' in html
    assert 'id="overlay_success_title"' in html
    assert 'id="overlay_filename"' in html
    assert 'id="overlay_reset_button"' in html
    assert "Сдать ещё одну работу" in html

    assert 'id="overlay_error"' in html
    assert 'id="overlay_error_title"' in html
    assert 'id="overlay_error_message"' in html
    assert 'id="overlay_retry_button"' in html
    assert "Попробовать снова" in html
```

- [x] **Step 2: Run test to verify it fails**

Run:
```bash
homework-submissions/.venv/bin/pytest homework-submissions/tests/test_web.py -k test_student_index_renders_submission_overlay_dialog -v
```
Expected output:
```
FAILED tests/test_web.py::test_student_index_renders_submission_overlay_dialog - assert 'id="submission_overlay"' in html
```

- [x] **Step 3: Add `#submission_overlay` markup to `homework-submissions/app/templates/index.html`**

In `homework-submissions/app/templates/index.html`, insert the overlay markup right after `</main>` and before `<script>` (around line 131):

```html
  <div id="submission_overlay"
       class="submission-overlay"
       role="dialog"
       aria-modal="true"
       aria-labelledby="overlay_title"
       hidden>
    <div class="submission-overlay__card">
      <!-- State 1: Loading -->
      <div id="overlay_loading" class="submission-overlay__view" role="status" aria-live="polite">
        <div class="submission-overlay__icon-wrap">
          <svg class="submission-overlay__spinner" viewBox="0 0 48 48" fill="none" aria-hidden="true" focusable="false">
            <circle class="submission-overlay__spinner-track" cx="24" cy="24" r="20" stroke="currentColor" stroke-width="4" />
            <path class="submission-overlay__spinner-head" d="M24 4a20 20 0 0 1 20 20" stroke="currentColor" stroke-width="4" stroke-linecap="round" />
          </svg>
        </div>
        <h2 id="overlay_title" class="submission-overlay__title">Отправляем работу...</h2>
        <p class="submission-overlay__lead">Пожалуйста, подождите, файл загружается на сервер...</p>
      </div>

      <!-- State 2: Success -->
      <div id="overlay_success" class="submission-overlay__view" role="status" aria-live="polite" hidden>
        <div class="submission-overlay__icon-wrap submission-overlay__icon-wrap--success">
          <svg class="submission-overlay__icon submission-overlay__icon--success" viewBox="0 0 48 48" fill="none" aria-hidden="true" focusable="false">
            <circle cx="24" cy="24" r="21" stroke="currentColor" stroke-width="3" />
            <path d="M14 24.5l6.5 6.5 13.5-13.5" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </div>
        <h2 id="overlay_success_title" class="submission-overlay__title">Работа успешно отправлена!</h2>
        <p class="submission-overlay__lead">Сервер принял и зарегистрировал ваш файл:</p>
        <div class="submission-overlay__badge" id="overlay_filename" aria-label="Имя сохранённого файла"></div>
        <div class="submission-overlay__actions">
          <button type="button" class="button button--primary submission-overlay__button" id="overlay_reset_button">
            Сдать ещё одну работу
          </button>
        </div>
      </div>

      <!-- State 3: Error -->
      <div id="overlay_error" class="submission-overlay__view" role="alert" aria-live="assertive" hidden>
        <div class="submission-overlay__icon-wrap submission-overlay__icon-wrap--error">
          <svg class="submission-overlay__icon submission-overlay__icon--error" viewBox="0 0 48 48" fill="none" aria-hidden="true" focusable="false">
            <circle cx="24" cy="24" r="21" stroke="currentColor" stroke-width="3" />
            <path d="M24 15v12m0 5v2" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" />
          </svg>
        </div>
        <h2 id="overlay_error_title" class="submission-overlay__title">Не удалось отправить работу</h2>
        <p class="submission-overlay__lead submission-overlay__lead--error" id="overlay_error_message">
          Произошла ошибка при передаче файла на сервер. Попробуйте повторить отправку.
        </p>
        <div class="submission-overlay__actions">
          <button type="button" class="button button--primary submission-overlay__button" id="overlay_retry_button">
            Попробовать снова
          </button>
        </div>
      </div>
    </div>
  </div>
```

- [x] **Step 4: Run test to verify it passes**

Run:
```bash
homework-submissions/.venv/bin/pytest homework-submissions/tests/test_web.py -k test_student_index_renders_submission_overlay_dialog -v
```
Expected output:
```
PASSED tests/test_web.py::test_student_index_renders_submission_overlay_dialog
```

- [x] **Step 5: Run full test suite**

Run:
```bash
homework-submissions/.venv/bin/pytest homework-submissions/tests
```
Expected output:
```
============================== 73 passed in ... ==============================
```

- [x] **Step 6: Commit template markup and test**

Run:
```bash
git add homework-submissions/app/templates/index.html homework-submissions/tests/test_web.py
git commit -m "feat(html): add submission overlay modal dialog markup to student template"
```

---

## Task 3: Client Script State Lifecycle & Interaction Logic

**Files:**
- Modify: `homework-submissions/app/static/student.js:20-40`, `student.js:200-222`
- Test: `homework-submissions/tests/test_web.py`

**Interfaces:**
- Consumes:
  - Overlay DOM elements: `#submission_overlay`, `#overlay_loading`, `#overlay_success`, `#overlay_error`, `#overlay_title`, `#overlay_filename`, `#overlay_error_message`, `#overlay_reset_button`, `#overlay_retry_button`.
  - Existing elements: `#submit`, `#submit_status`, `#file`, `#file_dropzone`, `#clear_file`, `#selected_group`, `#work`, `#student_name`.
- Produces:
  - Overlay lifecycle handlers: `setOverlayState(state, titleId)`, `hideOverlay()`.
  - Intercepted `submitForm` listener triggering `#overlay_loading`, HTTP 201 handling triggering `#overlay_success` with filename badge and focus on `#overlay_reset_button`, and catch handling triggering `#overlay_error` with message and focus on `#overlay_retry_button`.
  - Reset listener on `#overlay_reset_button` clearing dropzone preview and file value, hiding overlay, and keeping group authentication active.
  - Retry listener on `#overlay_retry_button` hiding overlay and keeping all form inputs and file intact.
  - Keyboard trap and `Escape` key containment within `submissionOverlay`.

- [x] **Step 1: Declare overlay elements and state transition helpers in `student.js`**

In `homework-submissions/app/static/student.js`, add around line 33 (after `clearFile` declaration):

```javascript
const studentName = document.querySelector('#student_name');
const submissionOverlay = document.querySelector('#submission_overlay');
const overlayLoading = document.querySelector('#overlay_loading');
const overlaySuccess = document.querySelector('#overlay_success');
const overlayError = document.querySelector('#overlay_error');
const overlayFilename = document.querySelector('#overlay_filename');
const overlayErrorMessage = document.querySelector('#overlay_error_message');
const overlayResetButton = document.querySelector('#overlay_reset_button');
const overlayRetryButton = document.querySelector('#overlay_retry_button');

function setOverlayState(state, titleId) {
  if (!submissionOverlay) return;
  submissionOverlay.hidden = false;
  if (overlayLoading) overlayLoading.hidden = state !== 'loading';
  if (overlaySuccess) overlaySuccess.hidden = state !== 'success';
  if (overlayError) overlayError.hidden = state !== 'error';

  if (titleId) {
    submissionOverlay.setAttribute('aria-labelledby', titleId);
  }
}

function hideOverlay() {
  if (!submissionOverlay) return;
  submissionOverlay.hidden = true;
  if (overlayLoading) overlayLoading.hidden = true;
  if (overlaySuccess) overlaySuccess.hidden = true;
  if (overlayError) overlayError.hidden = true;
}
```

- [x] **Step 2: Replace `submitForm` submit event listener with overlay lifecycle logic**

In `homework-submissions/app/static/student.js`, replace the existing `submitForm.addEventListener('submit', ...)` block (around line 201) with:

```javascript
submitForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const selectedFile = file.files[0];
  if (!selectedFile) {
    setStatus(submitStatus, 'error', 'Прикрепите файл с выполненной работой.');
    file.focus();
    return;
  }
  if (!showSelectedFile(selectedFile)) return;
  if (work.disabled || !work.value) {
    setStatus(submitStatus, 'error', 'Выберите тему работы.');
    work.focus();
    return;
  }
  if (!studentName.value.trim()) {
    setStatus(submitStatus, 'error', 'Укажите ФИО студента.');
    studentName.focus();
    return;
  }

  setBusy(submitForm, true, 'Отправляем…');
  setStatus(submitStatus, 'working', 'Загружаем файл…');
  setOverlayState('loading', 'overlay_title');

  try {
    const response = await fetch('/submit', {
      method: 'POST',
      body: new FormData(submitForm),
    });

    if (response.status === 201) {
      const data = await response.json();
      const confirmedName = data.filename || selectedFile.name;

      if (overlayFilename) overlayFilename.textContent = confirmedName;
      setStatus(submitStatus, 'success', `Работа принята: ${confirmedName}`);
      setOverlayState('success', 'overlay_success_title');
      if (overlayResetButton) overlayResetButton.focus();
    } else {
      let errorDetail = 'Ошибка отправки: проверьте статус приёма и поля формы.';
      try {
        const errorJson = await response.json();
        if (errorJson.error) {
          errorDetail = errorJson.error;
        }
      } catch {
        // Fallback to default message
      }
      throw new Error(errorDetail);
    }
  } catch (err) {
    const message = err.message && err.message !== 'failed'
      ? err.message
      : 'Не удалось связаться с сервером. Проверьте интернет-соединение и повторите попытку.';

    if (overlayErrorMessage) overlayErrorMessage.textContent = message;
    setStatus(submitStatus, 'error', message);
    setOverlayState('error', 'overlay_error_title');
    if (overlayRetryButton) overlayRetryButton.focus();
  } finally {
    setBusy(submitForm, false, 'Отправить работу');
    submitButton.disabled = work.disabled;
  }
});
```

- [x] **Step 3: Add event listeners for reset, retry, and keyboard focus trapping**

Immediately following the `submitForm` listener, append:

```javascript
if (overlayResetButton) {
  overlayResetButton.addEventListener('click', () => {
    hideOverlay();
    resetFilePreview();
    setStatus(submitStatus, '', '');
    file.focus();
  });
}

if (overlayRetryButton) {
  overlayRetryButton.addEventListener('click', () => {
    hideOverlay();
    submitButton.focus();
  });
}

if (submissionOverlay) {
  submissionOverlay.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      if (overlayError && !overlayError.hidden) {
        if (overlayRetryButton) overlayRetryButton.click();
      }
      return;
    }

    if (event.key === 'Tab') {
      const focusable = submissionOverlay.querySelectorAll(
        'button:not([disabled]), [tabindex]:not([tabindex="-1"])'
      );
      const visibleFocusable = Array.from(focusable).filter(
        (el) => !el.closest('[hidden]')
      );

      if (visibleFocusable.length === 0) {
        event.preventDefault();
        return;
      }

      const first = visibleFocusable[0];
      const last = visibleFocusable[visibleFocusable.length - 1];

      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });
}
```

- [x] **Step 4: Verify script syntax and run pytest test suite**

Run:
```bash
node -c homework-submissions/app/static/student.js
homework-submissions/.venv/bin/pytest homework-submissions/tests
```
Expected output:
```
============================== 73 passed in ... ==============================
```

- [x] **Step 5: Commit client-side script changes**

Run:
```bash
git add homework-submissions/app/static/student.js
git commit -m "feat(js): implement overlay lifecycle, double-click prevention, and focus management"
```

---

## Task 4: End-to-End Regression Verification & Testing

**Files:**
- Test: `homework-submissions/tests/`

**Interfaces:**
- Consumes: Completed implementations from Tasks 1, 2, and 3.
- Produces: Verified full regression pass, push to branch `feat/subject-topic-ownership` updating PR #5.

- [x] **Step 1: Run complete pytest test suite**

Run:
```bash
homework-submissions/.venv/bin/pytest homework-submissions/tests -v
```
Expected output:
```
============================== 73 passed in ... ==============================
```

- [x] **Step 2: Audit codebase for placeholders or TODOs**

Run:
```bash
git grep -i -E "TODO|FIXME|TBD|placeholder" -- "homework-submissions/app/" "docs/superpowers/plans/2026-10-11-full-screen-submission-overlay.md"
```
Expected output:
Only genuine UI placeholders (e.g. `placeholder="Например, Иванов Иван"`, `topicPlaceholder`) and no developer TODO comments.

- [x] **Step 3: Push changes to remote to update PR #5**

Run:
```bash
git push origin feat/subject-topic-ownership
```
Expected output:
```
To github.com:Probe1st/metodicheskie-materialy.git
   ...  feat/subject-topic-ownership -> feat/subject-topic-ownership
```

---

## Self-Review

### 1. Spec Coverage
| Spec Requirement | Plan Task |
|---|---|
| Full-screen modal overlay container `#submission_overlay` with `role="dialog"`, `aria-modal="true"`, `aria-labelledby="overlay_title"`, `hidden` | Task 2 (Step 3) |
| Dimmed, blurred backdrop (`backdrop-filter: blur(4px)`) blocking all page interactions | Task 1 (Step 3) |
| Centered modal card using `--card-bg`, `--card-border`, `--shadow-overlay`, `max-width: 480px` | Task 1 (Step 3) |
| Three distinct state views: `#overlay_loading`, `#overlay_success`, `#overlay_error` | Task 2 (Step 3) |
| Indeterminate SVG spinner with `@keyframes spin` respecting `prefers-reduced-motion` | Task 1 (Steps 3 & 4) |
| Large emerald checkmark SVG + confirmed filename badge + "Сдать ещё одну работу" button | Task 2 (Step 3) |
| Ruby alert icon SVG + error message + "Попробовать снова" button | Task 2 (Step 3) |
| WCAG AAA text contrast (>7:1) across all text elements in light and dark themes | Task 1 (Step 2) |
| Submit interception: client validation, activate `#overlay_loading`, disable submit button | Task 3 (Step 2) |
| HTTP 201 response: activate `#overlay_success`, show confirmed filename, focus reset button | Task 3 (Step 2) |
| Reset button click: clear file and dropzone preview, hide overlay, retain group authentication | Task 3 (Step 3) |
| Error / network failure: activate `#overlay_error` with message, focus retry button | Task 3 (Step 2) |
| Retry button click: hide overlay and preserve all form inputs and file attachment | Task 3 (Step 3) |
| Keyboard focus trap & Escape handling | Task 3 (Step 3) |
| Zero emojis, vector SVG only | Task 2 (Step 3) |
| All existing 72 tests pass without regressions | Tasks 1, 2, 3, 4 |

### 2. Placeholder Scan
Plan scanned: no `TODO`, `FIXME`, `TBD`, or "fill in later" comments. All code blocks contain complete, copy-paste ready implementations.

### 3. Type & Interface Consistency
- Container ID: `#submission_overlay`
- State IDs: `#overlay_loading`, `#overlay_success`, `#overlay_error`
- Heading IDs: `#overlay_title`, `#overlay_success_title`, `#overlay_error_title`
- Badge ID: `#overlay_filename`
- Error message ID: `#overlay_error_message`
- Buttons: `#overlay_reset_button`, `#overlay_retry_button`
- CSS Classes: `.submission-overlay`, `.submission-overlay__card`, `.submission-overlay__view`, `.submission-overlay__spinner`, `.submission-overlay__icon-wrap`, `.submission-overlay__badge`
All naming strictly matches between `styles.css`, `index.html`, and `student.js`.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-10-11-full-screen-submission-overlay.md`. Two execution options:

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - Execute tasks in this session using `executing-plans`, batch execution with checkpoints.

Which approach?
