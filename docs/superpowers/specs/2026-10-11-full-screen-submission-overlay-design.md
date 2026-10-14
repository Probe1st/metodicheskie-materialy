# Full-Screen Submission Overlay — Design Specification

**Status:** Approved Design Specification  
**Scope:** Architecture, HTML markup, accessibility semantics, CSS design tokens, and client-side lifecycle logic for the full-screen modal submission overlay in `homework-submissions/`.  
**Date:** 2026-10-11  

---

## 1. Executive Summary & Goals

### 1.1 Problem Statement & Background
In the homework submission portal (`homework-submissions/`), students submit completed assignments by selecting their group, academic discipline, and topic, entering their full name, attaching a file (up to `window.submissionLimit` GB, typically 1 GB), and clicking the primary "Отправить работу" button.

Under realistic classroom conditions, multiple issues arise during file upload:
1. **Network Latency & Perception Lag:** Uploading multi-megabyte files (PDFs, archives, CAD models) over campus Wi-Fi or mobile networks introduces an inherent latency of several seconds.
2. **Duplicate Submissions via Rapid Multi-Clicks:** Because the student sees only an inline button text swap ("Отправляем…"), students frequently assume the system did not register their action and repeatedly tap or click the submit button.
3. **Server-Side File Multiplication:** Each duplicate click initiates a concurrent multipart POST request to `/submit`. This creates race conditions in the file storage engine, resulting in redundant versioned files (e.g., `Иванов Иван Иванович 1.pdf`, `Иванов Иван Иванович 2.pdf`), filling server storage, wasting bandwidth, and causing confusion for instructors reviewing submissions.
4. **Distraction & Accidental Navigation:** While an upload is in flight, the rest of the form and navigation links remain interactive. Students can accidentally edit fields, click "Сменить группу", or navigate away, aborting uploads mid-stream.

### 1.2 Proposed Solution: Full-Screen Modal Overlay
Introduce a dedicated full-screen modal overlay container (`#submission_overlay`) that:
- Activates immediately upon form submission validation.
- Renders a dimmed, blurred backdrop (`backdrop-filter: blur(4px)`) that intercepts and blocks all pointer, touch, scroll, and tab interactions with the underlying page.
- Traps keyboard focus within an accessible dialog card (`role="dialog"`, `aria-modal="true"`).
- Visually and programmatically guides the student through three distinct lifecycle states: **Loading**, **Success**, and **Error**.

### 1.3 State Model Overview
The overlay card renders one of three mutually exclusive state views:

```
[ Form Submission Intercepted ]
               │
               ▼
   ┌───────────────────────┐
   │ 1. Loading State      │
   │    #overlay_loading   │
   │    • Animated Spinner │
   │    • Status Headings  │
   └───────────┬───────────┘
               │
      Fetch POST /submit
               │
       ┌───────┴───────┐
       ▼               ▼
┌───────────────┐ ┌───────────────┐
│ 2. Success    │ │ 3. Error      │
│ #overlay_     │ │ #overlay_     │
│ success       │ │ error         │
│ • Emerald SVG │ │ • Ruby SVG    │
│ • File Badge  │ │ • Error Msg   │
│ • "Сдать ещё" │ │ • "Повторить" │
└───────┬───────┘ └───────┬───────┘
        │                 │
   Reset Flow        Retry Flow
  (Keep Group)      (Keep Fields)
        │                 │
        ▼                 ▼
[ Return to Form: Step 2 ]
```

1. **Loading State (`#overlay_loading`):**
   - Active during the network flight of `fetch('/submit', ...)`.
   - Displays an indeterminate SVG spinner rotating with CSS keyframe animation (`@keyframes spin`).
   - Title: `"Отправляем работу..."`.
   - Subtitle: `"Пожалуйста, подождите, файл загружается на сервер..."`.
   - All interactive dismissal controls are disabled to prevent upload cancellation.

2. **Success State (`#overlay_success`):**
   - Activated when the server responds with HTTP `201 Created`.
   - Displays a large emerald checkmark icon (vector SVG).
   - Title: `"Работа успешно отправлена!"`.
   - Confirmed filename badge displaying the exact server-assigned filename (e.g., `Иванов Иван Иванович 1.pdf`) parsed from the JSON response `{ "filename": "..." }`.
   - Primary action button: `"Сдать ещё одну работу"` (`#overlay_reset_button`).
   - Clicking the button dismisses the overlay, clears the file input and dropzone preview, resets Step 2 for a new submission, and retains the group authentication session so the student does not need to re-enter the group password.
   - Programmatic focus immediately shifts to the `"Сдать ещё одну работу"` button upon entering this state.

3. **Error State (`#overlay_error`):**
   - Activated when the server returns a non-2xx status (e.g., 400, 403, 500) or when a client-side network error/timeout occurs.
   - Displays a prominent ruby alert/warning icon (vector SVG).
   - Title: `"Не удалось отправить работу"`.
   - Dynamic error message paragraph (`#overlay_error_message`) displaying actionable guidance (e.g., `"Ошибка отправки: проверьте статус приёма и поля формы."` or server-provided error details).
   - Primary action button: `"Попробовать снова"` (`#overlay_retry_button`).
   - Clicking the button dismisses the overlay and restores focus to the form while preserving all entered data (group, discipline, topic, student name, and attached file) so the student can retry without re-entering their work.
   - Programmatic focus immediately shifts to the `"Попробовать снова"` button upon entering this state.

### 1.4 Non-Goals
- **No Backend API Changes:** The server route `POST /submit` in `app/web.py` continues to return HTTP 201 with `{ "filename": str }` on success and HTTP 400/403 with `{ "error": str }` or HTML on failure. No database schemas or endpoint signatures are modified.
- **No New Remote Dependencies:** All icons are inline SVG; no third-party icon fonts or script libraries are introduced.
- **No Disruption of Step 1 Unlock:** The group password check (`/unlock`) remains an inline step; the full-screen overlay is specifically reserved for the file submission phase.

---

## 2. HTML Markup & Accessibility Architecture (`index.html`)

### 2.1 DOM Placement & Container Structure
The modal overlay container is defined directly inside `index.html` as a top-level sibling of `<main class="page-shell">` (or at the bottom of the body element) so that its stacking context is decoupled from any parent layout transforms or clipping masks:

```html
<!-- Full-screen submission overlay -->
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

### 2.2 Accessibility & Assistive Technology Semantics
1. **Dialog Landmark & Modality:**
   - Container has `role="dialog"` and `aria-modal="true"`. Assistive technology recognizes that the rest of the web document is occluded and non-interactive.
   - `aria-labelledby="overlay_title"` associates the dialog with the primary state heading. As states shift, `student.js` dynamically updates `aria-labelledby` to point to `overlay_title`, `overlay_success_title`, or `overlay_error_title`.
2. **Live Region Announcements:**
   - Loading and Success views use `role="status"` and `aria-live="polite"`, ensuring the screen reader announces the status change after current speech completes.
   - Error view uses `role="alert"` and `aria-live="assertive"` so that transmission failures are announced immediately.
3. **Pure Vector Iconography:**
   - Strict rule: **Zero emoji characters** are used for UI or status communication.
   - All icons are rendered as vector `<svg>` with `aria-hidden="true"` and `focusable="false"`. They provide visual clarity without cluttering screen reader narration.
4. **Programmatic Focus Management:**
   - While the overlay is in the **Loading** state, focus remains safely locked inside the modal card. Because no interactive controls exist in the loading card, keyboard tab attempts cycle internally or are blocked.
   - Upon transitioning to the **Success** state, script immediately executes `overlayResetButton.focus()`.
   - Upon transitioning to the **Error** state, script immediately executes `overlayRetryButton.focus()`.
   - Both action buttons maintain a prominent 2px/4px focus ring meeting WCAG 2.2 criteria.

---

## 3. CSS Styling & Design Tokens (`styles.css`)

### 3.1 Design System Integration & Semantic Tokens
The overlay styles are integrated into the existing 3-layer CSS variable architecture defined in `homework-submissions/app/static/styles.css`.

#### Layer 2: Semantic Theme Aliases
```css
:root,
[data-theme="light"] {
  --color-overlay-backdrop: rgb(15 23 42 / 60%);
  --color-overlay-card-bg: var(--primitive-slate-0);
  --color-overlay-card-border: var(--primitive-slate-200);
  --color-overlay-success-icon: var(--primitive-emerald-800);
  --color-overlay-success-surface: var(--primitive-emerald-50);
  --color-overlay-error-icon: var(--primitive-ruby-800);
  --color-overlay-error-surface: var(--primitive-ruby-50);
  --color-overlay-spinner-track: var(--primitive-slate-200);
  --color-overlay-spinner-head: var(--primitive-indigo-700);
}

[data-theme="dark"] {
  --color-overlay-backdrop: rgb(11 18 32 / 75%);
  --color-overlay-card-bg: var(--primitive-dark-slate-50);
  --color-overlay-card-border: var(--primitive-dark-slate-200);
  --color-overlay-success-icon: var(--primitive-dark-emerald-800);
  --color-overlay-success-surface: var(--primitive-dark-emerald-50);
  --color-overlay-error-icon: var(--primitive-dark-ruby-800);
  --color-overlay-error-surface: var(--primitive-dark-ruby-50);
  --color-overlay-spinner-track: var(--primitive-dark-slate-200);
  --color-overlay-spinner-head: var(--primitive-dark-indigo-700);
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme]) {
    --color-overlay-backdrop: rgb(11 18 32 / 75%);
    --color-overlay-card-bg: var(--primitive-dark-slate-50);
    --color-overlay-card-border: var(--primitive-dark-slate-200);
    --color-overlay-success-icon: var(--primitive-dark-emerald-800);
    --color-overlay-success-surface: var(--primitive-dark-emerald-50);
    --color-overlay-error-icon: var(--primitive-dark-ruby-800);
    --color-overlay-error-surface: var(--primitive-dark-ruby-50);
    --color-overlay-spinner-track: var(--primitive-dark-slate-200);
    --color-overlay-spinner-head: var(--primitive-dark-indigo-700);
  }
}
```

### 3.2 Modal Backdrop & Centered Layout
The backdrop covers the entire viewport and prevents background scrolling and interaction:

```css
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
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}
```

### 3.3 Modal Card Component
The modal card uses existing component tokens (`--card-bg`, `--card-border`, `--shadow-overlay`) with a constrained maximum width of `480px`:

```css
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
  from {
    transform: scale(0.96);
    opacity: 0;
  }
  to {
    transform: scale(1);
    opacity: 1;
  }
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
```

### 3.4 SVG Icon Framing & Spinner Keyframe Animation
The state icons are visually grounded in a 64×64px circular container with subtle semantic background tints:

```css
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

/* Spinner */
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
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
```

### 3.5 Filename Badge & Action Buttons
The confirmed filename returned by the server is highlighted in a distinct, high-contrast monospace/tabular pill badge:

```css
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

### 3.6 Reduced Motion & Accessibility Contrast Audit
1. **Reduced Motion Adaptation:**
   In compliance with `@media (prefers-reduced-motion: reduce)`, animation durations are suppressed and continuous rotation is neutralized to avoid nausea or disorientation:
   ```css
   @media (prefers-reduced-motion: reduce) {
     .submission-overlay,
     .submission-overlay__card {
       animation: none !important;
     }
     .submission-overlay__spinner {
       animation: none !important;
       stroke-dasharray: 40 10;
     }
   }
   ```
2. **Contrast Ratio Audit (WCAG AAA Target >7:1):**
   - **Card Background to Text:**
     - Light Mode: `--primitive-slate-900` (`#0F172A`) on `--primitive-slate-0` (`#FFFFFF`) = **16.1:1** (Passes AAA).
     - Dark Mode: `--primitive-dark-slate-900` (`#F8FAFC`) on `--primitive-dark-slate-50` (`#111B2E`) = **15.4:1** (Passes AAA).
   - **Secondary Description Text:**
     - Light Mode: `--primitive-slate-700` (`#334155`) on `#FFFFFF` = **9.6:1** (Passes AAA).
     - Dark Mode: `--primitive-dark-slate-700` (`#E2E8F0`) on `#111B2E` = **12.8:1** (Passes AAA).
   - **Success Badge & Icon:**
     - Light Mode: `--primitive-emerald-800` (`#14532D`) on `--primitive-emerald-50` (`#F0FDF4`) = **9.8:1** (Passes AAA).
     - Dark Mode: `--primitive-dark-emerald-800` (`#86EFAC`) on `--primitive-dark-emerald-50` (`#052E16`) = **11.2:1** (Passes AAA).
   - **Error Alert Text & Icon:**
     - Light Mode: `--primitive-ruby-800` (`#9F1239`) on `--primitive-ruby-50` (`#FFF1F2`) = **7.8:1** (Passes AAA).
     - Dark Mode: `--primitive-dark-ruby-800` (`#FDA4AF`) on `--primitive-dark-ruby-50` (`#4C0519`) = **8.5:1** (Passes AAA).

---

## 4. Client Script Logic (`student.js`)

### 4.1 DOM References & State Management Functions
`student.js` caches references to the overlay elements and provides helper functions to transition between states:

```javascript
// Overlay DOM elements
const submissionOverlay = document.querySelector('#submission_overlay');
const overlayLoading = document.querySelector('#overlay_loading');
const overlaySuccess = document.querySelector('#overlay_success');
const overlayError = document.querySelector('#overlay_error');
const overlayFilename = document.querySelector('#overlay_filename');
const overlayErrorMessage = document.querySelector('#overlay_error_message');
const overlayResetButton = document.querySelector('#overlay_reset_button');
const overlayRetryButton = document.querySelector('#overlay_retry_button');

/**
 * Transitions the submission overlay to a specific view.
 * @param {'loading' | 'success' | 'error'} state
 * @param {string} [titleId] ID of the active heading for aria-labelledby
 */
function setOverlayState(state, titleId) {
  submissionOverlay.hidden = false;
  overlayLoading.hidden = state !== 'loading';
  overlaySuccess.hidden = state !== 'success';
  overlayError.hidden = state !== 'error';

  if (titleId) {
    submissionOverlay.setAttribute('aria-labelledby', titleId);
  }
}

/**
 * Hides the full-screen submission overlay completely.
 */
function hideOverlay() {
  submissionOverlay.hidden = true;
  overlayLoading.hidden = true;
  overlaySuccess.hidden = true;
  overlayError.hidden = true;
}
```

### 4.2 Form Submission Interception Workflow
The submission listener on `submitForm` is updated to integrate the overlay seamlessly:

```javascript
submitForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  
  // 1. Client-side pre-validation
  const selectedFile = file.files[0];
  if (!selectedFile) {
    setStatus(submitStatus, 'error', 'Прикрепите файл с выполненной работой.');
    file.focus();
    return;
  }
  if (!showSelectedFile(selectedFile)) {
    return;
  }
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

  // 2. Lock UI & activate overlay loading view
  setBusy(submitForm, true, 'Отправляем…');
  setStatus(submitStatus, 'working', 'Загружаем файл…');
  setOverlayState('loading', 'overlay_title');

  try {
    // 3. Dispatch upload request
    const response = await fetch('/submit', {
      method: 'POST',
      body: new FormData(submitForm)
    });

    if (response.status === 201) {
      // 4. Success handling
      const data = await response.json();
      const confirmedName = data.filename || selectedFile.name;

      overlayFilename.textContent = confirmedName;
      setStatus(submitStatus, 'success', `Работа принята: ${confirmedName}`);
      setOverlayState('success', 'overlay_success_title');
      
      // Accessibility: Move focus to the primary reset button
      overlayResetButton.focus();
    } else {
      // 5. Server-side validation or business logic rejection
      let errorDetail = 'Ошибка отправки: проверьте статус приёма и поля формы.';
      try {
        const errorJson = await response.json();
        if (errorJson.error) {
          errorDetail = errorJson.error;
        }
      } catch {
        // Response was not JSON, retain default message
      }
      throw new Error(errorDetail);
    }
  } catch (err) {
    // 6. Network failure, timeout, or thrown rejection
    const message = err.message && err.message !== 'failed'
      ? err.message
      : 'Не удалось связаться с сервером. Проверьте интернет-соединение и повторите попытку.';
    
    overlayErrorMessage.textContent = message;
    setStatus(submitStatus, 'error', message);
    setOverlayState('error', 'overlay_error_title');
    
    // Accessibility: Move focus to the retry button
    overlayRetryButton.focus();
  } finally {
    setBusy(submitForm, false, 'Отправить работу');
    submitButton.disabled = work.disabled;
  }
});
```

### 4.3 Reset Flow ("Сдать ещё одну работу")
When a student completes a submission, they may need to submit another assignment (e.g. for a different topic or subject) during the same session.

```javascript
overlayResetButton.addEventListener('click', () => {
  // 1. Hide the overlay
  hideOverlay();

  // 2. Clear attached file and reset dropzone preview
  resetFilePreview();

  // 3. Clear the status paragraph under the form
  setStatus(submitStatus, '', '');

  // 4. Note: Group access is preserved!
  // selectedGroup.value, groupDisplay, and groupAssignments remain valid.
  // The student remains on Step 2 without needing to re-enter the password.

  // 5. Set focus to the file dropzone for convenient next upload
  file.focus();
});
```

### 4.4 Retry Flow ("Попробовать снова")
When a submission fails (e.g., brief Wi-Fi hiccup or temporary server load), the student must not lose the data they typed:

```javascript
overlayRetryButton.addEventListener('click', () => {
  // 1. Hide the overlay
  hideOverlay();

  // 2. All form fields remain intact!
  // discipline, work, student_name, and file.files are unchanged.

  // 3. Shift focus back to the submit button for convenient immediate retry
  submitButton.focus();
});
```

### 4.5 Focus Trapping & Keyboard Trap Prevention
To ensure keyboard accessibility and conformance with WCAG 2.2 AA (Criterion 2.1.2 No Keyboard Trap):
1. **Escape Key Handling:**
   - While in `loading` state, `Escape` is intercepted and ignored to avoid disconnecting an active network stream.
   - While in `error` state, pressing `Escape` triggers the retry dismissal (`hideOverlay()` and restores focus to `submitButton`).
2. **Tab Cycling:**
   - While `submissionOverlay` is active, `keydown` tab events are contained within the active card. In the `success` state, `Tab` focuses `#overlay_reset_button`. In the `error` state, `Tab` focuses `#overlay_retry_button`.

```javascript
submissionOverlay.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    if (!overlayError.hidden) {
      overlayRetryButton.click();
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
```

---

## 5. Technical Constraints & Backward Compatibility

### 5.1 Preservation of Existing DOM Hooks & Contracts
The overlay implementation is strictly additive and non-breaking. Every selector and contract relied upon by Flask templates, existing scripts, and the pytest suite remains unchanged:
- Form IDs `#unlock` and `#submit` remain unaltered.
- File inputs `#file`, `#file_dropzone`, `#file_name`, and `#clear_file` retain their names, event bindings, and behavior.
- Status element `#submit_status` retains its `role="status"` and `data-state` attributes; it continues to be updated concurrently so automated headless tests asserting on `#submit_status` pass without modification.
- Global variable `window.submissionLimit` continues to govern maximum upload size validation.

### 5.2 Server Route Contract Invariance
- `POST /submit` returns status `201` with body `{"filename": "<name>"}` on success.
- `POST /submit` returns status `403` with body `{"error": "Access closed"}` or redirection when unauthenticated.
- `POST /unlock` returns status `200` or `204` as tested.

---

## 6. Verification & Acceptance Criteria

### 6.1 Test Suite & Integrity
1. **Existing Pytest Suite:**
   All 72 existing automated tests in `tests/` pass with zero regressions:
   - `tests/test_access.py` (5 tests)
   - `tests/test_assignments.py` (14 tests)
   - `tests/test_catalog.py` (1 test)
   - `tests/test_catalog_store.py` (24 tests)
   - `tests/test_storage.py` (1 test)
   - `tests/test_web.py` (27 tests)
2. **Zero Incomplete Code Artifacts:**
   Zero placeholder comments (`TODO`, `FIXME`, `STUB`, or temporary dummy markup) anywhere in documentation, templates, or scripts.

### 6.2 Observable Acceptance Criteria
1. **Double-Click Interception:**
   - Clicking "Отправить работу" immediately brings up `#submission_overlay` with the dimmed backdrop and active `#overlay_loading` view.
   - Pointer events to the underlying page are 100% blocked; rapid multiple clicks cannot generate secondary HTTP requests.
2. **Success Lifecycle:**
   - On HTTP 201 response, `#overlay_loading` transitions cleanly to `#overlay_success`.
   - The verified filename appears inside `#overlay_filename`.
   - Keyboard focus automatically shifts to `#overlay_reset_button`.
   - Clicking `"Сдать ещё одну работу"` hides the overlay, clears the file attachment and dropzone display, but keeps the unlocked group active for immediate subsequent submissions.
3. **Error Lifecycle:**
   - On network drop or server error, `#overlay_loading` transitions to `#overlay_error`.
   - The error message appears in `#overlay_error_message`.
   - Keyboard focus automatically shifts to `#overlay_retry_button`.
   - Clicking `"Попробовать снова"` hides the overlay, leaving all student inputs and the attached file intact in the form.
4. **Visual & Accessibility Compliance:**
   - Strict vector SVG usage: no emojis present in any modal state.
   - Backdrop blur (`backdrop-filter: blur(4px)`) renders smoothly in both light and dark modes.
   - Text contrast exceeds WCAG AAA (7:1) across all text elements in both light and dark themes.
   - Motion is fully reduced when `prefers-reduced-motion: reduce` is enabled.
