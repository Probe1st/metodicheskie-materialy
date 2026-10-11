# Homework Submission System Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete visual and interaction redesign from scratch of the homework submission system (student flow and teacher administration) to a Modern Tech / Clean SaaS Dashboard style with full Light and Dark mode support, preserving all existing Flask routes, DOM hooks, and test contracts.

**Architecture:** Three-layer CSS custom properties architecture (Primitive -> Semantic -> Component) in `styles.css` with responsive grid layouts and `@media (prefers-color-scheme: dark)`. Updated Jinja2 templates (`index.html`, `admin.html`, `admin_access.html`, `admin_catalog.html`, `_admin_nav.html`) maintaining all form actions, IDs, names, and `data-*` attributes. Client JS enhancements (`student.js`, `admin.js`, `catalog-admin.js`) for modern drag-and-drop, inline editing, and smooth feedback.

**Tech Stack:** Python 3.14, Flask, Jinja2, Vanilla JavaScript (ES6+), Modern CSS3 (Grid, Flexbox, Custom Properties, SVGs), Pytest.

## Global Constraints
- Preserve all existing Flask/Jinja routes, form names, input names, IDs, data-* hooks, and response shapes listed in Section 5 of the design spec.
- No new server dependencies, external font downloads, or build tooling.
- Normal body and control text must achieve at least 7:1 (WCAG AAA) contrast in both light and dark themes; large text and UI boundaries must meet at least WCAG 2.2 AA (4.5:1 / 3:1).
- Every keyboard-operable control must have a visible 2px focus ring with 2px offset.
- Responsive across 375px, 768px, 1024px, and 1440px breakpoints without horizontal scroll.
- All 49 existing pytest tests in `homework-submissions/tests/` must pass after every task.

---

## File Structure

| File | Responsibility |
|---|---|
| `homework-submissions/app/static/styles.css` | Primitive, semantic, and component tokens; light/dark assignments; base typography and controls; shared layout, state, responsive, and reduced-motion styles. |
| `homework-submissions/app/templates/index.html` | Student page structure, two-step unlock/submit forms, accessible dropzone, preserved Jinja data and DOM hooks. |
| `homework-submissions/app/static/student.js` | Existing unlock, topic filtering, and submit interactions plus dropzone, size validation, file preview/removal, and feedback. |
| `homework-submissions/app/templates/_admin_nav.html` | Shared active admin navigation links and active-page semantics. |
| `homework-submissions/app/templates/admin.html` | Login and authenticated overview dashboard. |
| `homework-submissions/app/templates/admin_access.html` | Group access summaries, cards, forms, and local statuses. |
| `homework-submissions/app/templates/admin_catalog.html` | Create/edit/list pages and topic filters/bulk reassignment controls. |
| `homework-submissions/app/static/admin.js` | Admin login/password visibility and existing per-group asynchronous save feedback. |
| `homework-submissions/app/static/catalog-admin.js` | Existing create/edit/delete/topic filtering/bulk behavior and any UI state enhancements. |
| `homework-submissions/tests/` | Existing route, access, catalog, storage, and upload regression suite; keep product contracts unchanged. |

## Task 1: CSS Design System Tokens, Reset, Typography & Base Components

**Files:**
- Modify: `homework-submissions/app/static/styles.css`
- Test: `homework-submissions/tests/` (existing suite only; no source-text assertions)

**Interfaces:**
- Consumes: existing selectors across `index.html`, `admin.html`, `admin_access.html`, `_admin_nav.html`, and `admin_catalog.html`.
- Produces: shared CSS custom properties consumed by all later template tasks. Use `[data-theme="light"]` and `[data-theme="dark"]` for explicit themes and `@media (prefers-color-scheme: dark)` for system-selected dark mode when no explicit theme is set.

- [ ] **Step 1: Run the baseline backend regression suite**

Run: `homework-submissions/.venv/bin/pytest homework-submissions/tests`

Expected: all 49 existing tests pass before visual changes. If the specified virtualenv command is unavailable, stop and record that environment prerequisite; do not substitute a different test runner silently.

- [ ] **Step 2: Replace the old flat palette with the three token layers**

In `styles.css`, define primitive tokens from Section 2 of `docs/superpowers/specs/2026-10-10-homework-submission-redesign-design.md`. Define semantic aliases for page/surface/text/border/primary/link/success/danger/warning/focus and component aliases for buttons, fields, cards, statuses, switches, badges, and dropzone. Keep every visible component declaration downstream of semantic/component tokens rather than embedding independent color literals.

Use this exact organization and palette mapping from Section 2.2 of `docs/superpowers/specs/2026-10-10-homework-submission-redesign-design.md`. Keep component rules dependent on component/semantic values, never raw color literals.

```css
/* Layer 1: raw values (include dark palette values here too). */
:root {
  --primitive-slate-0: #ffffff;
  --primitive-slate-50: #f8fafc;
  --primitive-slate-100: #f1f5f9;
  --primitive-slate-200: #e2e8f0;
  --primitive-slate-400: #94a3b8;
  --primitive-slate-600: #475569;
  --primitive-slate-700: #334155;
  --primitive-slate-900: #0f172a;
  --primitive-dark-slate-0: #0b1220;
  --primitive-dark-slate-50: #111b2e;
  --primitive-dark-slate-100: #1e293b;
  --primitive-dark-slate-200: #334155;
  --primitive-dark-slate-600: #cbd5e1;
  --primitive-dark-slate-700: #e2e8f0;
  --primitive-dark-slate-900: #f8fafc;
  --primitive-indigo-700: #4338ca;
  --primitive-indigo-800: #3730a3;
  --primitive-blue-700: #1d4ed8;
  --primitive-emerald-800: #166534;
  --primitive-emerald-50: #f0fdf4;
  --primitive-ruby-800: #9f1239;
  --primitive-ruby-50: #fff1f2;
  --primitive-amber-800: #92400e;
  --primitive-dark-indigo-700: #a5b4fc;
  --primitive-dark-indigo-800: #c7d2fe;
  --primitive-dark-blue-700: #93c5fd;
  --primitive-dark-emerald-800: #86efac;
  --primitive-dark-emerald-50: #052e16;
  --primitive-dark-ruby-800: #fda4af;
  --primitive-dark-ruby-50: #4c0519;
  --primitive-dark-amber-800: #fcd34d;
  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --space-6: 1.5rem;
  --space-8: 2rem;
  --space-10: 2.5rem;
  --space-12: 3rem;
  --space-16: 4rem;
  --radius-control: 0.625rem;
  --radius-card: 0.875rem;
  --radius-pill: 999px;
  --shadow-card: 0 1px 3px rgb(15 23 42 / 8%);
  --shadow-overlay: 0 12px 32px rgb(15 23 42 / 16%);
  --motion-fast: 120ms;
  --motion-normal: 180ms;
  --font-sans: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --type-xs: 0.75rem;
  --type-sm: 0.875rem;
  --type-base: 1rem;
  --type-lg: 1.125rem;
  --type-xl: 1.25rem;
  --type-2xl: 1.5rem;
  --type-3xl: 1.875rem;
}

/* Layer 2: theme-specific semantic aliases. */
:root, [data-theme="light"] {
  color-scheme: light;
  --color-page: var(--primitive-slate-50);
  --color-surface: var(--primitive-slate-0);
  --color-surface-muted: var(--primitive-slate-100);
  --color-text: var(--primitive-slate-900);
  --color-text-secondary: var(--primitive-slate-700);
  --color-border: var(--primitive-slate-200);
  --color-primary: var(--primitive-indigo-700);
  --color-primary-hover: var(--primitive-indigo-800);
  --color-link: var(--primitive-blue-700);
  --color-success: var(--primitive-emerald-800);
  --color-success-surface: var(--primitive-emerald-50);
  --color-danger: var(--primitive-ruby-800);
  --color-danger-surface: var(--primitive-ruby-50);
  --color-warning: var(--primitive-amber-800);
  --color-focus: var(--primitive-indigo-700);
}

[data-theme="dark"] {
  color-scheme: dark;
  --color-page: var(--primitive-dark-slate-0);
  --color-surface: var(--primitive-dark-slate-50);
  --color-surface-muted: var(--primitive-dark-slate-100);
  --color-text: var(--primitive-dark-slate-900);
  --color-text-secondary: var(--primitive-dark-slate-700);
  --color-border: var(--primitive-dark-slate-200);
  --color-primary: var(--primitive-dark-indigo-700);
  --color-primary-hover: var(--primitive-dark-indigo-800);
  --color-link: var(--primitive-dark-blue-700);
  --color-success: var(--primitive-dark-emerald-800);
  --color-success-surface: var(--primitive-dark-emerald-50);
  --color-danger: var(--primitive-dark-ruby-800);
  --color-danger-surface: var(--primitive-dark-ruby-50);
  --color-warning: var(--primitive-dark-amber-800);
  --color-focus: var(--primitive-dark-indigo-700);
}

/* Layer 3: component tokens. */
:root {
  --card-bg: var(--color-surface);
  --card-border: var(--color-border);
  --card-shadow: var(--shadow-card);
  --button-primary-bg: var(--color-primary);
  --button-primary-fg: var(--color-surface);
  --button-primary-hover-bg: var(--color-primary-hover);
  --input-bg: var(--color-surface);
  --input-fg: var(--color-text);
  --input-border: var(--color-border);
  --status-success-fg: var(--color-success);
  --status-success-bg: var(--color-success-surface);
  --status-danger-fg: var(--color-danger);
  --status-danger-bg: var(--color-danger-surface);
  --status-working-fg: var(--color-text-secondary);
  --status-working-bg: var(--color-surface-muted);
  --badge-bg: var(--color-surface-muted);
  --switch-accent: var(--color-primary);
  --dropzone-bg: var(--color-surface-muted);
  --dropzone-border: var(--color-border);
  --dropzone-active-border: var(--color-primary);
  --focus-ring: 0 0 0 2px var(--color-page), 0 0 0 4px var(--color-focus);
}
```

Add this explicit system-dark rule after `[data-theme="dark"]`, with no ellipsis:

```css
@media (prefers-color-scheme: dark) {
  :root:not([data-theme]) {
    color-scheme: dark;
    --color-page: var(--primitive-dark-slate-0);
    --color-surface: var(--primitive-dark-slate-50);
    --color-surface-muted: var(--primitive-dark-slate-100);
    --color-text: var(--primitive-dark-slate-900);
    --color-text-secondary: var(--primitive-dark-slate-700);
    --color-border: var(--primitive-dark-slate-200);
    --color-primary: var(--primitive-dark-indigo-700);
    --color-primary-hover: var(--primitive-dark-indigo-800);
    --color-link: var(--primitive-dark-blue-700);
    --color-success: var(--primitive-dark-emerald-800);
    --color-success-surface: var(--primitive-dark-emerald-50);
    --color-danger: var(--primitive-dark-ruby-800);
    --color-danger-surface: var(--primitive-dark-ruby-50);
    --color-warning: var(--primitive-dark-amber-800);
    --color-focus: var(--primitive-dark-indigo-700);
  }
}
```

For text on the primary action, define a separate `--color-on-primary` semantic: light theme `#ffffff` on `#4338ca`; dark theme `#0b1220` on `#a5b4fc`. For status copy, use the emerald/ruby/amber foreground tokens on their corresponding pale/dark surfaces from the palette. Measure these, secondary text, links, controls, and focus indicators at implementation time; when any pairing is below the specified contrast, use a darker same-hue foreground (light mode) or lighter same-hue foreground (dark mode) and record the measured contrast before accepting the styles.

The token table above defines the exact names consumed by subsequent implementation tasks; add further component aliases only when needed and keep the same semantic-to-component direction.

- [ ] **Step 3: Add base rules, focus, component states, and media rules**

Add box sizing, `[hidden] { display: none !important; }`, body/type defaults, button/input inheritance, common button/field/card/badge/status styles, and consistent hover/disabled/working states. Use the 4px spacing rhythm, 6/10/14/999px radii, restrained shadows, and `:focus-visible` 2px outline with 2px offset. Include reduced-motion rules. Define layout breakpoints at 375px, 768px, 1024px, and 1440px; keep component styles theme-neutral by using component tokens.

- [ ] **Step 4: Verify contrast pairings and run the existing regression suite**

Calculate the actual foreground/background contrast for normal text in both modes; adjust semantic shades until each normal text pairing reaches 7:1 and control boundaries meet at least 3:1. Include primary buttons, muted text on surfaces, status banners, selected/hover states, and focus rings. Then run: `homework-submissions/.venv/bin/pytest homework-submissions/tests`

Expected: all 49 tests pass; stylesheet token review records compliant rendered pairs. Browser-level contrast and responsive proof is performed in Task 6.

- [ ] **Step 5: Commit the design foundation**

```bash
git add homework-submissions/app/static/styles.css
git commit -m "feat: add homework redesign design tokens"
```

## Task 2: Student Submission Flow UI & Modern File Dropzone

**Files:**
- Modify: `homework-submissions/app/templates/index.html`
- Modify: `homework-submissions/app/static/student.js`
- Test: `homework-submissions/tests/` (existing route and upload contracts)

**Interfaces:**
- Consumes: Task 1 CSS component tokens; existing Jinja `catalog` and `limit`; current endpoints `/unlock` and `/submit`.
- Produces: existing form selectors and names unchanged; two-step visual hierarchy, accessible dropzone states, client-side size validation using `window.submissionLimit`, and user-visible file/submit statuses.

- [ ] **Step 1: Inspect current student selectors and route payloads**

Read `homework-submissions/app/templates/index.html`, `homework-submissions/app/static/student.js`, and the index/unlock/submit route handlers in `homework-submissions/app/web.py`. Record the existing fields (`group`, `password`, `discipline`, `work`, `student_name`, `file`), IDs, option `data-subject-id`, multipart encoding, status codes, and `window.submissionLimit`; do not introduce a catalog schema or route not present in the app.

- [ ] **Step 2: Restructure the student template without changing hooks**

Keep the `#unlock` POST form/action, `#submit` multipart POST form/action, all IDs and field names, and all existing option data attributes. Build the topbar, left instructions column, right form card, confirmed group badge, field sections, file-picker/dropzone, file preview/size/remove affordance, and separate error/success feedback using those existing hooks. The topic select remains `#work` / `name="work"`, subject select remains `#discipline` / `name="discipline"`, and the topic empty hint remains `#topic_hint`. Include an accessible theme control only if implemented in Task 1’s theme mechanism; the root theme selector must work consistently on this page.

- [ ] **Step 3: Add file drag/drop, preview, remove, and size enforcement**

In `student.js`, extend the existing `#file` and `#file_name` handlers. Add dragenter/dragover/dragleave/drop handling to the dropzone, with a CSS state class and `preventDefault()` for valid drag targets. On file selection or drop, compare `file.size` to `window.submissionLimit * 1024 * 1024 * 1024`; if oversized, clear the input, set the existing submit status to an inline error, and do not submit. Otherwise render text-only filename and formatted byte size, reveal the remove control, and ensure removal clears the native input and preview. Keep the input label keyboard accessible and never hide it with `display:none`.

- [ ] **Step 4: Complete wizard and result states while preserving existing request behavior**

Retain the existing unlock and submit fetch endpoints and `FormData` fields. Keep existing subject-to-topic reactive filtering and no-topics disabling. Make successful unlock collapse Step 1 into `#access_summary`, show group name in `#group_display`, and reveal `#submit`; changing group restores the unlock form and existing reset behavior. Prevent duplicate requests while busy. On submit success, show the server-confirmed status/result without discarding it prematurely; on failure, keep entered values and show a clear inline status. Avoid asserting response fields not returned by `/submit`.

- [ ] **Step 5: Run the regression suite and commit the student flow**

Run: `homework-submissions/.venv/bin/pytest homework-submissions/tests`

Expected: all 49 existing tests pass, including unlock authorization, topic/subject relationship, upload limit, and accepted upload behavior.

```bash
git add homework-submissions/app/templates/index.html homework-submissions/app/static/student.js
git commit -m "feat: redesign student homework submission flow"
```

## Task 3: Teacher Admin Navigation & Overview Dashboard

**Files:**
- Modify: `homework-submissions/app/templates/_admin_nav.html`
- Modify: `homework-submissions/app/templates/admin.html`
- Modify: `homework-submissions/app/static/admin.js`
- Test: `homework-submissions/tests/` (existing admin authentication/route tests)

**Interfaces:**
- Consumes: Task 1 tokens; existing `/admin`, `/admin/login`, `/admin/logout` and current nav endpoint names; shared admin shell styles.
- Produces: responsive sticky/scrollable nav with accurate active state; accessible centered login with visibility toggle; overview quick links and only metrics derivable from current server data; existing `#admin_login`, `#admin_login_status`, password field/action preserved.

- [ ] **Step 1: Preserve and map actual admin routes**

Inspect the admin route functions in `homework-submissions/app/web.py` and `_admin_nav.html`. Keep overview at `/admin`, access at `/admin/access`, catalogs at `/admin/<kind>` for existing kinds. The design spec’s `/admin/catalog/<kind>` is descriptive only; do not add that route or emit links to it. Keep logout POST to `/admin/logout`.

- [ ] **Step 2: Build shared navigation and overview markup**

Update `_admin_nav.html` to keep all current `url_for` destinations, active `is-active` class and `aria-current="page"`; add visual count badges only where the template has accurate data. Restructure the authenticated section of `admin.html` into page heading, shared nav, metric cards sourced from available data, and quick-action links to access and all three catalogs. Keep the unauthenticated sign-in card, form ID, action, password name, and `#admin_login_status`. Keep brand link to the student page.

- [ ] **Step 3: Add login visibility behavior without changing request flow**

Enhance `admin.js` so the password visibility button toggles only the existing login field’s `type` between `password` and `text`, updates an accessible name and `aria-pressed`, and can be used by keyboard. Keep the current async POST to `/admin/login`, busy state, status messaging, and successful `window.location.reload()`. Do not add a login route or expose the stored password.

- [ ] **Step 4: Run the regression suite and commit overview/navigation**

Run: `homework-submissions/.venv/bin/pytest homework-submissions/tests`

Expected: all 49 tests pass, including login/session and admin route tests.

```bash
git add homework-submissions/app/templates/_admin_nav.html homework-submissions/app/templates/admin.html homework-submissions/app/static/admin.js
git commit -m "feat: redesign teacher dashboard navigation"
```

## Task 4: Teacher Admin Group Access UI

**Files:**
- Modify: `homework-submissions/app/templates/admin_access.html`
- Modify: `homework-submissions/app/static/admin.js`
- Test: `homework-submissions/tests/` (existing group-access tests)

**Interfaces:**
- Consumes: Task 1 shared tokens and Task 3 admin nav conventions; existing `groups` data from `/admin/access`, `.group-form`, `/admin/access` save contract.
- Produces: responsive count cards and group cards while retaining `.group-card`, `.chip`, `#open_count`, `#closed_count`, `.group-status` and fields `group`, `is_open`, `closes_at`, `password`.

- [ ] **Step 1: Inspect the existing access form and submit serialization**

Read `admin_access.html`, `admin.js`, and the access route in `web.py`. Preserve checkbox serialization (`is_open=1` only when checked), group name hidden field, local `datetime-local` value, blank password meaning unchanged password, and the existing async save endpoint.

- [ ] **Step 2: Apply the shared admin shell and responsive cards**

Update the access template with section heading, shared navigation, open/closed count cards, responsive group grid, textual status chip, switch with explicit accessible label, deadline helper, new-password helper, save button, and per-card live status. Keep current IDs/classes/names/actions exactly. Include a useful no-groups empty state linking to the existing groups catalog URL (`url_for('admin_catalog_page', kind='groups')`).

- [ ] **Step 3: Ensure local asynchronous status and count updates remain correct**

Retain `admin.js`’s per-form submit handler. Ensure the switch state updates the corresponding `.chip` and count values only after a successful save, not on a failed request. Keep button busy text and status local to the group card, and restore button interactivity in `finally` even on an error. Do not move the switch outside its `.group-form` or card outside `.group-card`.

- [ ] **Step 4: Run the regression suite and commit group access**

Run: `homework-submissions/.venv/bin/pytest homework-submissions/tests`

Expected: all 49 tests pass, including deadline closure, group access, and concurrent group state behavior.

```bash
git add homework-submissions/app/templates/admin_access.html homework-submissions/app/static/admin.js
git commit -m "feat: redesign teacher group access controls"
```

## Task 5: Teacher Admin Catalog & Topics Toolbar UI

**Files:**
- Modify: `homework-submissions/app/templates/admin_catalog.html`
- Modify: `homework-submissions/app/static/catalog-admin.js`
- Test: `homework-submissions/tests/` (existing catalog route/store tests)

**Interfaces:**
- Consumes: Task 1 tokens and Task 3 shared nav; current catalog template context (`kind`, `items`, `subjects`, `topics`, `unassigned_subject_id`); existing create/rename/update/delete and `/admin/topics/move` response contracts.
- Produces: responsive create/list page; inline row edit/delete; topic unassigned, subject, and text filters; visible topic bulk selection/reassignment; all existing `data-*`, form and field hooks retained.

- [ ] **Step 1: Map catalog request/response contracts and special rows**

Inspect `admin_catalog.html`, `catalog-admin.js`, and the `admin_catalog_*`/topic move routes in `web.py`. Record form action generation, kind-specific names, response payloads, conflict handling, special unassigned subject row, current hidden-row selection clearing, and empty/no-match states. Preserve the route `/admin/<kind>`, mutation `/admin/<kind>/<item_id>/<action>`, and `/admin/topics/move`.

- [ ] **Step 2: Restructure catalog templates while preserving selectors**

Use the shared shell with the topic toolbar before create/list content. Place the create panel inline with the catalog list at wide desktop and before it on mobile through CSS grid ordering. Preserve `[data-catalog-create]`, `data-kind`, `#catalog_new_name`, `name="name"`, topic `subject_id`, `[data-catalog-list]`, `[data-catalog-item]`, row `data-kind`, `data-id`, `data-name`, topic `data-subject-id`, `[data-item-name]`, `[data-topic-subject]`, `[data-catalog-rename]`, `[data-delete]`, `[data-create-status]`, `[data-topic-status]`, `[data-topic-filter-empty]`, `[data-catalog-empty]`, `.topic-toolbar`, `[data-topic-unassigned-filter]`, `[data-topic-unassigned-count]`, `[data-topic-filter]`, `[data-topic-search]`, `#topic_bulk`, and `[data-topic-bulk]`. Preserve relationship selectors consumed by JS and topic checkbox field `topic_ids`.

- [ ] **Step 3: Complete accessible filter, edit, delete, and bulk feedback states**

Keep the existing client-side filter behavior and update only where needed for accessible selected state, visible counts, and selected-topic feedback. Changing filter/search must continue clearing selections for hidden rows. Ensure visible selection submission uses only visible checked `topic_ids`. Use an explicit confirmation naming the row before destructive delete; keep row status and errors local and preserve current conflict/network messages. Bulk success must update subject label, `data-subject-id`, filter counts, and selection. Do not fabricate counts or change request field names.

- [ ] **Step 4: Run the regression suite and commit catalog UI**

Run: `homework-submissions/.venv/bin/pytest homework-submissions/tests`

Expected: all 49 tests pass, including catalog CRUD, topic ownership, bulk moves, conflicts, and delete invariants.

```bash
git add homework-submissions/app/templates/admin_catalog.html homework-submissions/app/static/catalog-admin.js
git commit -m "feat: redesign teacher catalog management"
```

## Task 6: Full Regression Testing & Accessibility / Responsive Audit

**Files:**
- Modify if needed: `homework-submissions/app/static/styles.css`
- Modify if needed: `homework-submissions/app/static/student.js`
- Modify if needed: `homework-submissions/app/static/admin.js`
- Modify if needed: `homework-submissions/app/static/catalog-admin.js`
- Modify if needed: `homework-submissions/app/templates/index.html`
- Modify if needed: `homework-submissions/app/templates/admin.html`
- Modify if needed: `homework-submissions/app/templates/admin_access.html`
- Modify if needed: `homework-submissions/app/templates/admin_catalog.html`
- Modify if needed: `homework-submissions/app/templates/_admin_nav.html`
- Test: `homework-submissions/tests/` (run complete existing suite)

**Interfaces:**
- Consumes: all completed UI contracts from Tasks 1–5 and the acceptance criteria in Section 6 of the design spec.
- Produces: verified end-to-end redesign with all existing backend tests green, accessible interaction states, two themes, responsive page layouts, and no broken DOM contracts.

- [ ] **Step 1: Run the required complete pytest suite**

Run exactly: `homework-submissions/.venv/bin/pytest homework-submissions/tests`

Expected: all 49 tests pass. Fix only real regressions without weakening existing route, storage, authorization, upload, and catalog tests; rerun this exact command after fixes.

- [ ] **Step 2: Smoke the actual student UI in a browser**

Start the app using its existing documented/configured local run method and inspect `/` at 375px, 768px, 1024px, and 1440px. Exercise unlock success and error, change group, topic filtering/no topics, keyboard file selection and drag/drop, preview/remove, oversized-file rejection, busy prevention, and submit success/error. Confirm there is no horizontal scroll, current hooks remain selected by `student.js`, and status text is announced without claiming unconfirmed outcomes.

- [ ] **Step 3: Smoke admin login, access, and catalog interactions**

In the same browser session, exercise login failure/success and password visibility; verify `/admin` quick links use existing routes; save one group and induce a failed save to confirm feedback remains local; verify open/closed counts; create/rename/delete a catalog item; test topic unassigned/subject/search filters and hidden-selection clearing; bulk-reassign visible selected topics and inspect updated row subject and counts. Do not add or expect `/admin/catalog/<kind>` routes.

- [ ] **Step 4: Audit accessibility, themes, and responsive behavior**

Use keyboard only through navigation, fields, switch, password toggle, dropzone, remove-file, topic filters, selection, bulk action, and delete confirmation. Inspect both light and dark root theme selectors; compute actual normal text contrast to at least 7:1 and control boundaries to at least 3:1; verify 2px focus outline with 2px offset. Check readable labels, required state, `aria-current`, status live regions, icon SVG semantics, and `prefers-reduced-motion`. Verify no remote font/icon request and no horizontal overflow at all four target widths.

- [ ] **Step 5: Run the complete suite once more and commit any final audit fixes**

Run: `homework-submissions/.venv/bin/pytest homework-submissions/tests`

Expected: all 49 tests pass after audit fixes. Commit only implementation files changed by this task:

```bash
git add homework-submissions/app/static/styles.css homework-submissions/app/static/student.js homework-submissions/app/static/admin.js homework-submissions/app/static/catalog-admin.js homework-submissions/app/templates/index.html homework-submissions/app/templates/admin.html homework-submissions/app/templates/admin_access.html homework-submissions/app/templates/admin_catalog.html homework-submissions/app/templates/_admin_nav.html
git commit -m "fix: complete homework redesign accessibility audit"
```

## Self-Review Checklist

- **Spec coverage:** Task 1 covers the three token layers, explicit palette, light/dark semantic mappings, typography, spacing, radius/elevation, focus, SVG policy, and reduced motion. Task 2 covers header, responsive student layout, two-step wizard, reactive topics/empty hint, accessible dropzone, size checks, and submission states. Tasks 3–4 cover admin navigation, login, overview, access cards, async status, and accurate counts. Task 5 covers all catalogs, topic filters, bulk reassignment, empty states, mutation feedback, and preservation of routes/hooks. Task 6 covers all 49 pytest tests, browser flows, four responsive widths, WCAG contrast, keyboard, reduced motion, and hook regression.
- **Placeholder scan:** The plan contains no TODOs, TBDs, stubs, or deferred implementation steps. Every task specifies concrete behavior and uses the exact required pytest command.
- **Interface consistency:** All task interfaces retain `#discipline`/`name="discipline"`, `#work`/`name="work"`, `window.submissionLimit`, the real `/admin/<kind>` catalog routes, and existing catalog form fields. Explicit `data-theme` attributes override system preference; the CSS media query handles dark system preference when no attribute is set.
