# Homework Submission System Redesign — Design Specification

**Status:** Approved design direction: Modern Tech / Clean SaaS Dashboard  
**Scope:** Complete visual and interaction redesign of the student submission interface and teacher administration panel in `homework-submissions/`.  
**Date:** 2026-10-10

## 1. Executive Summary & Goals

Redesign the complete homework-submission experience from the ground up while retaining the existing application behavior and integration contracts. The scope includes the public student flow, teacher sign-in, administration overview, group access controls, and group/subject/topic catalog management. The product should feel like a clear, dependable, contemporary SaaS dashboard: information-dense enough for teachers to manage data, and focused enough for students to submit a file without uncertainty.

### Product goals

- Let a student unlock their group, supply assignment details, attach a valid file, submit, and understand the result in a clear two-step flow.
- Give teachers a coherent authenticated shell for monitoring and editing group access and catalogs.
- Make validation, loading, success, error, empty, and disabled states visible and understandable; never rely on color alone.
- Support light and dark themes through CSS custom properties, with usable contrast and visible keyboard focus in both.
- Preserve the current Flask/Jinja routes, form names, IDs, `data-*` hooks, response behavior, and client-script interactions; this is a presentation redesign, not an API redesign.

### Visual direction

Use **Modern Tech / Clean SaaS Dashboard**: neutral Slate surfaces and text, restrained indigo/blue brand actions, emerald success, and ruby/red danger. Use geometric sans-serif typography, compact but breathable spacing, crisp borders, subtle elevation, and purposeful SVG iconography. Avoid decorative clutter, gradients as decoration, emoji icons, and visual effects that compete with form tasks.

## 2. Design System Architecture & Tokens

### 2.1 Three-layer token architecture

Implement tokens in the existing `homework-submissions/app/static/styles.css`, grouped and documented as three layers. Components must consume component or semantic tokens, not independently hard-code palette values.

1. **Primitive tokens** are raw palette, type, spacing, radius, shadow, and motion values (for example `--primitive-slate-900`, `--primitive-indigo-600`, `--space-4`).
2. **Semantic tokens** assign purpose and change by theme (for example `--color-page`, `--color-surface`, `--color-text`, `--color-primary`, `--color-success`, `--color-danger`, `--color-border`, `--color-focus`).
3. **Component tokens** map semantic values to specific controls (for example `--button-primary-bg`, `--input-border`, `--card-bg`, `--status-success-fg`, `--dropzone-border`).

Theme switching overrides semantic tokens under `[data-theme="light"]` and `[data-theme="dark"]` (or an equivalently centralized root selector); component declarations remain theme-agnostic. The selected theme must have a deterministic initial value and persist consistently using the application’s available client-side approach. Declare `color-scheme` to match native form controls. Do not add a UI framework or change the server contract to implement themes.

### 2.2 Color tokens and contrast

The following are starting palette values; semantic assignments and rendered foreground/background pairs are normative. Verify final rendered pairs with a contrast calculator during implementation, adjusting shades when a pairing misses its target.

| Primitive | Light value | Dark value / use |
|---|---:|---:|
| Slate 0 | `#FFFFFF` | `#0B1220` page base |
| Slate 50 | `#F8FAFC` | `#111B2E` raised page/surface |
| Slate 100 | `#F1F5F9` | `#1E293B` muted surface |
| Slate 200 | `#E2E8F0` | `#334155` borders |
| Slate 400 | `#94A3B8` | `#94A3B8` subdued non-text UI |
| Slate 600 | `#475569` | `#CBD5E1` secondary text |
| Slate 700 | `#334155` | `#E2E8F0` body text |
| Slate 900 | `#0F172A` | `#F8FAFC` primary text |
| Indigo 700 | `#4338CA` | `#A5B4FC` primary action/text accent |
| Indigo 800 | `#3730A3` | `#C7D2FE` pressed/deep accent |
| Blue 700 | `#1D4ED8` | `#93C5FD` links/secondary accent |
| Emerald 800 | `#166534` | `#86EFAC` success foreground |
| Emerald 50 | `#F0FDF4` | `#052E16` success surface |
| Ruby 800 | `#9F1239` | `#FDA4AF` danger foreground |
| Ruby 50 | `#FFF1F2` | `#4C0519` danger surface |
| Amber 800 | `#92400E` | `#FCD34D` warning foreground |

Map these to semantic tokens for page background, primary and elevated surfaces, primary/secondary text, muted text, borders, brand action/hover, link, success, danger, warning, and focus. Choose light/dark foregrounds so each **normal text** pairing is at least **7:1 (WCAG AAA)**. Large text and meaningful graphical controls/boundaries must be at least **4.5:1 and 3:1 respectively (WCAG 2.2 AA)**; target 7:1 for text wherever practical. Validate actual composited states (including hover, disabled, badges, borders, and input focus), not just the palette swatches. Never put white text on a light accent or low-contrast muted text on a card.

### 2.3 Typography

Use Inter when already locally available or bundled without a new external request, followed by a robust system fallback: `Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`. Use a geometric sans character: consistent weights, clean numerals for metrics, and restrained emphasis. Avoid introducing a remote font dependency.

| Token | Size / line height | Use |
|---|---|---|
| `--type-xs` | 0.75rem / 1rem | compact metadata, never essential body copy |
| `--type-sm` | 0.875rem / 1.25rem | labels, secondary descriptions |
| `--type-base` | 1rem / 1.5rem | body and control text |
| `--type-lg` | 1.125rem / 1.75rem | card leads |
| `--type-xl` | 1.25rem / 1.75rem | section headings |
| `--type-2xl` | 1.5rem / 2rem | page headings on mobile |
| `--type-3xl` | 1.875rem / 2.25rem | page headings on wide screens |

Body text and interactive text must remain comfortably readable; avoid using the xs size for instructions or status messages.

### 2.4 Spacing, shape, elevation, focus, and icons

- Use a 4px base and 8px rhythm: spacing primitives 4, 8, 12, 16, 24, 32, 40, 48, 64px. Use 16–24px internal card padding, scaling up on wide screens.
- Radius scale: 6px compact control, 10px input/button, 14px card, 999px pill/badge. Keep overall geometry crisp rather than overly rounded.
- Use restrained elevation: subtle shadow for menus/floating elements and cards only when separation needs it. Borders establish primary structure; avoid heavy shadows, especially in dark mode.
- Every keyboard-operable control has a clearly visible **2px focus ring with a 2px offset**, using a theme-appropriate high-contrast focus token. Never suppress the native outline without replacing it.
- SVG icons only (Phosphor or Heroicons, consistent outline/fill style and 16/20/24px sizing). Inline SVG or the project’s existing local icon method is preferred; icon-only controls require an accessible name and a comfortable hit area. No emojis as structural, status, or navigation icons. Icons supplement, not replace, visible labels.

## 3. Student Interface Specifications (`/`)

### 3.1 Page shell and responsive layout

- **Topbar:** compact brand mark/name at left; a textual intake-status badge; a clearly labeled link to the teacher admin panel at right. Keep adequate spacing and touch targets on narrow widths.
- **Desktop:** two-column page: explanatory/instructions column on the left, submission card on the right. Use a restrained max-width and a stable, readable gap; align the card near the top of the explanatory block. The left side explains the two steps, accepted file/size expectations available to the app, and what happens after sending.
- **Mobile:** one column in logical reading order, heading/instructions then form card. No horizontal scrolling; controls fill available width. Keep the submission action and feedback easy to locate.
- Include explicit page title, section headings, visible field labels, and semantic form structure. The browser and screen-reader order must match the visual task order.

### 3.2 Two-step wizard and unlock state

**Step 1 — Confirm group access.** Present the group `<select>` and group password field together, followed by a prominent action. Preserve inline loading/busy state (disable the submit action while pending, announce progress), and show a concise inline error for rejected credentials/access or network failure. Error feedback must not erase the selected group. Keep the password masked by default.

**Successful transition.** After successful unlock, collapse the unlock form and display a compact confirmed-access badge/card containing the selected group name and a **Change group** action. Reveal Step 2. The change action returns focus to the password field, restores Step 1, clears the prior password/selected session group as the current script does, and hides Step 2. Do not imply an unlock before the server has confirmed it.

**Step 2 — Assignment details.** Organize controls in this order:

1. Subject selection.
2. Topic selection, filtered immediately to the selected subject. If the selected subject has no available topics, disable the topic control and submission action, show an explanatory inline “no topics” hint, and ensure the empty state is announced accessibly.
3. Student full name.
4. Required file attachment.

Keep labels visible, required state programmatically available, and values intact after submission errors. Subject changes must not leave an unavailable/stale topic selected.

### 3.3 File dropzone and submission feedback

Create a keyboard-accessible modern drag-and-drop zone around the existing file input. Use a dashed border at rest, a clear indigo/blue border/surface change on drag-over and hover, and a descriptive browse-file action. The native input remains available to keyboard/screen-reader users (visually hidden only with a proven accessible pattern; never `display:none` when the label cannot activate it). Announce drag/drop status where useful.

When chosen, show a file-preview row/badge with filename, human-readable size, and an explicit remove/replace button with an accessible name. Removing resets the underlying input and returns the zone to its empty state. Validate size client-side against the existing `window.submissionLimit` (expressed by the app in GB) before upload; clearly state the limit, reject oversized files with an inline error, and leave server-side enforcement authoritative. Do not invent new accepted file types or a new size limit.

Submission states:

- **Ready:** clear primary “submit work” action.
- **Busy:** prevent duplicate submissions, show progress text, retain entered values/file.
- **Success:** distinct emerald feedback banner containing the server-confirmed result/filename and a clear “submit another” action where supported by current behavior; do not claim success until the server returns its successful response.
- **Error:** distinct ruby feedback banner for validation, access, too-large, server, or network failures; explain the next action and retain entered data when possible.

Use `aria-live` on status areas with appropriate polite/assertive behavior; focus the result summary only when appropriate, without stealing focus during ordinary typing.

## 4. Teacher Admin Panel Specifications

### 4.1 Shared authenticated shell and navigation

Admin pages share a consistent header with brand, **active-section badge/title**, link back to the student view, and logout form/button. Preserve logout as the existing POST action. Below the header, show a sticky admin sidebar on desktop (or a sticky, horizontally scrollable tab/navigation bar on constrained layouts) with active indicator and links for Overview, Group access, Groups, Subjects, and Topics. Use visible text labels; show counts only when those counts can be supplied accurately without new server work. Mark active navigation with `aria-current="page"` and do not depend on color alone.

Routes already implemented are `/admin`, `/admin/access`, and `/admin/<kind>` where `kind` is `groups`, `subjects`, or `topics`. UI documentation/design may refer to catalog pages as `/admin/catalog/<kind>` for information architecture, but this is a proposed visual label only: **do not change or advertise new URL routes** unless a separate backend change is approved. Link using the existing endpoints.

### 4.2 Admin login

Unauthenticated `/admin` shows a centered, compact sign-in card within the same visual system. Include a password field with a visibility toggle that has an accessible name and pressed state, a primary sign-in button, busy state, and inline error for a rejected password/network failure. Preserve the existing form ID, field name, form action, and asynchronous login behavior. Never expose password text unless the user explicitly activates the toggle; return it to concealed mode on navigation if feasible.

### 4.3 Overview page

The authenticated `/admin` overview starts with a clear heading and short operational summary. Show metric cards for open groups, closed groups, subjects, total topics, and unassigned topics, with labels and numbers (and optional SVG visual cue). Values must come from actual catalog/access state and must not be fabricated; if the current server does not provide a metric, show only metrics it can reliably render or derive. Provide quick-action cards/links to access settings and each catalog area, with concise descriptions and clear destinations.

### 4.4 Group access (`/admin/access`)

- Start with summary cards showing accurate open and closed group counts.
- Render responsive group cards in a grid, each visually scannable by group name and explicit open/closed status.
- Provide an accessible modern switch for intake open/closed. Pair its accessible label with an adjacent textual state badge; keep counts/status synchronized after a successful save using existing behavior.
- Include a `datetime-local` deadline field, helper text clarifying the local deadline and empty-means-no-automatic-close behavior, a new-password input (never display the stored password; blank keeps the existing one), a save action, and per-card asynchronous progress/success/error feedback.
- Saving one group affects only that card. Disable/relabel only that form’s submit control while pending; announce inline status. On errors, retain field values and offer an understandable retry. Provide a meaningful empty state with a link to create a group when there are no cards.

### 4.5 Catalog management (groups, subjects, topics)

All catalog pages use a consistent page header, create panel, list of catalog row/cards, inline editing controls, per-row status, deletion action, and useful empty state. Creation panel is inline beside the list on wide desktop where space allows and placed before the list on narrow layouts. Creation and editing use visible labels, clear busy/error/success states, and preserve existing asynchronous behavior. Destructive delete actions must have clear button text and a confirmation step that names the target item; errors such as conflicts must be communicated inline and must not discard edits.

Catalog rows show item name prominently with edit/rename controls and delete action. For topics, show current subject, provide subject reassignment select, and support bulk selection/reassignment. Preserve the system/unassigned subject’s special non-editable/delete-restricted behavior where applicable; explain it rather than presenting a misleading delete action.

**Topic filter toolbar:**

- An **Unassigned** filter chip/button showing the live unassigned topic count and pressed/selected state.
- A subject filter select with per-subject counts and an all-subjects option.
- An instant search input filtering by topic name.
- Filters combine predictably; show a dedicated no-results state when active filters match no rows, distinct from a genuinely empty catalog.
- Preserve the existing rule that changing filters/search clears selection for hidden topics so bulk reassignment cannot accidentally act on unseen rows.

**Bulk reassignment toolbar:** provide a destination subject select, selected/visible count where derivable, a clear bulk action, progress state, and inline success/error feedback. If nothing is selected, explain what the teacher must select. Preserve the current request payload’s selected visible `topic_ids` and destination `subject_id`; successful reassignment updates the topic subject label and filter counts without a full page reload.

## 5. DOM & Technical Constraints

This is a complete visual redesign, not permission to break integration. Preserve all existing DOM hooks, form action URLs, input `name` attributes, IDs, and `data-*` attributes used by `student.js`, `admin.js`, `catalog-admin.js`, Jinja, and `homework-submissions/tests/`. Do not rename/remove hooks during markup restructuring. Any wrapper/layout changes must keep selector relationships relied on by scripts (for example `.group-form` inside `.group-card`, `.group-status`, `[data-catalog-item]`, and the catalog list/form selectors).

### Existing student hooks and contract

- Forms: `#unlock` (`POST /unlock`), `#submit` (`POST /submit`, multipart); fields `group`, `password`, `discipline`, `work`, `student_name`, `file`.
- IDs: `unlock_status`, `submit_status`, `discipline`, `work`, `topic_hint`, `selected_group`, `group_display`, `access_summary`, `change_group`, `file`, `file_name`.
- Topic options retain `data-subject-id`; submission size continues to come from `window.submissionLimit`.

### Existing admin hooks and contract

- `#admin_login`, `#admin_login_status`; login form action `/admin/login`, password field named `password`.
- Group forms `.group-form` and fields `group`, `is_open`, `closes_at`, `password`; `.group-card`, `.chip`, `#open_count`, `#closed_count`, `.group-status`; save endpoint `/admin/access`.
- Logout remains a POST to `/admin/logout`.

### Existing catalog hooks and contract

- Create form `[data-catalog-create]`, `data-kind`, `#catalog_new_name`, `name="name"`, and topic `name="subject_id"`; keep its action `/admin/<kind>`.
- Topic toolbar `.topic-toolbar`, `data-unassigned-id`, `[data-topic-unassigned-filter]`, `[data-topic-unassigned-count]`, `[data-topic-filter]`, `[data-topic-search]`, `#topic_bulk`, `[data-topic-bulk]`, and `name="topic_ids"` checkboxes; bulk endpoint `/admin/topics/move` with `subject_id`.
- List `[data-catalog-list]`, `[data-catalog-item]`, each row’s `data-kind`, `data-id`, `data-name`, and topic `data-subject-id`; preserve `[data-item-name]`, `[data-topic-subject]`, `[data-catalog-rename]`, `[data-delete]`, `[data-create-status]`, `[data-topic-status]`, `[data-topic-filter-empty]`, and `[data-catalog-empty]` hooks and their relevant nesting.
- Keep the existing rename/update/delete endpoint shapes `/admin/<kind>/<item_id>/<action>` and field names.

Continue using the existing Flask/Jinja templates, static CSS, and client scripts; no new UI framework, build system, routes, server-side behavior, or network requests are required by the visual redesign. Use semantic HTML and progressive enhancement. CSS should be organized into token layers, reset/base, shared layout, component/state styles, and responsive rules, with no scattered theme-specific component overrides. Use breakpoints at **375px, 768px, 1024px, and 1440px** to describe and verify narrow mobile, tablet, compact desktop, and wide desktop behavior. Implement `prefers-reduced-motion: reduce` to remove nonessential transitions and movement while preserving state feedback.

## 6. Verification & Acceptance Criteria

The redesign is accepted when all of the following are true:

1. The complete existing `homework-submissions` pytest suite passes: **all 49 tests**. Tests and server/route behavior are not weakened or rewritten to fit the redesign.
2. Student unlock, success/collapse transition, change-group action, dependent topic filtering, no-topic hint, full-name entry, file choose/drop/remove/oversize feedback, busy prevention, and success/error banners work using the existing endpoints and hooks.
3. Admin login and visibility control work; overview quick links navigate correctly; group toggle/deadline/password saves show local asynchronous feedback and keep summary counts accurate.
4. Catalog create, rename, topic subject reassignment, delete, search, subject filter, unassigned filter/count, bulk reassignment, conflict feedback, and filtered-empty states work through existing DOM hooks and routes.
5. At 375px, 768px, 1024px, and 1440px, layout adapts as specified; no horizontal overflow or clipped controls; focus order remains logical. Student interface is two-column on desktop and single-column on mobile; catalog create/list and admin navigation adapt without losing access to controls.
6. Keyboard-only operation covers all controls, including the dropzone, toggles, navigation, filters, file removal, password visibility, and destructive actions. Screen-reader labels, required states, active navigation, and live status announcements are present. Focus rings are visible in both themes.
7. Light and dark themes render all specified surfaces, text, statuses, controls, focus states, and native inputs legibly. Verify normal text at **7:1 AAA target** and all relevant component/text pairs at least **WCAG 2.2 AA**, with non-text control boundaries at least **3:1**.
8. Reduced-motion preference removes nonessential animation. Structural/status/navigation icons are SVG, visually consistent, and contain no emoji substitutes.
9. Review markup against the contract lists in Section 5: every existing selector, ID, name, action URL, and required `data-*` attribute remains present and usable by its existing script/tests. No placeholder copy or unimplemented affordances are presented as functional controls.
