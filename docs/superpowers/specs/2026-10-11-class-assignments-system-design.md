# Class-Based Assignments System — Design Specification

**Status:** Approved Design Specification  
**Scope:** Architecture and UI/UX design for class-based assignment scheduling, group isolation, teacher administration (`/admin/assignments`), student submission flow with pair context, and backward-compatible storage in `homework-submissions/`.  
**Date:** 2026-10-11

---

## 1. Executive Summary & Goals

### 1.1 Problem Statement & Background
The existing homework submission system operates on a global catalog model (`catalog.json`). Once a student unlocks a group with its group password, they are presented with all catalog disciplines and all catalog topics, regardless of:
1. Whether that specific group is actually taking that course.
2. What classes or academic pairs have actually been conducted for that group.
3. When the class took place in the academic calendar.

This creates several operational problems:
- **No group isolation:** Students from one group (e.g., "ИС 25/9-1П") can view and submit assignments intended for an entirely different group (e.g., "ИС 24/9-3П").
- **Missing pair context:** Students lack temporal context when submitting work (e.g., distinguishing between introductory and advanced practice on similar topics).
- **No scheduling workflow for teachers:** Instructors cannot plan out topics pair by pair, restrict student submissions to currently active or completed pairs, or track when each topic was delivered.

### 1.2 Core Product Goals
- **Class-Based Scheduling by Pair:** Teachers schedule assignments across five concrete dimensions:
  1. **Group:** Target student group (`group_name`).
  2. **Subject:** Academic discipline (`subject_id`).
  3. **Topic:** Curriculum topic belonging to that discipline (`topic_id`).
  4. **Lesson Date:** Calendar date of the class (`lesson_date`, ISO `YYYY-MM-DD`).
  5. **Pair Number:** Academic time slot (`pair_number`, integer `1` to `6`).
- **Strict Group Isolation:** Each student group sees only the assignments specifically scheduled for it. A student in Group A cannot see or select topics scheduled for Group B.
- **Contextual Student Submission Flow:**
  - Upon unlocking a group at `/unlock`, the server associates the group session and returns the scheduled assignments for that group.
  - Step 2 in the submission wizard dynamically filters available disciplines to those assigned to the group.
  - The topic selector (`<select id="work" name="work">`) displays rich contextual labels formatted as `"ДД.ММ, пара N — Тема"` (e.g., `"14.10, пара 2 — Арифметика и коды чисел"`).
  - **Backward-Compatible Storage Contract:** The `<option value>` attribute strictly retains the clean topic name (e.g., `value="Арифметика и коды чисел"`). As a result, backend checks (`catalog().contains(...)`) and filesystem storage paths (`storage/<Group>-<Discipline>-<Topic>/...`) remain 100% backward-compatible without any migration or path mutation.
  - Descriptive empty states are shown when a discipline has no scheduled topics for the unlocked group.
- **Teacher Administration UI (`/admin/assignments`):**
  - Dedicated admin section added to `_admin_nav.html` as "Занятия и пары" with a timetable SVG icon and active state `section == 'assignments'`.
  - Group and Subject filters via dropdowns, reflected in URL query parameters (`?group=...&subject_id=...`).
  - Fast assignment creation form with date picker (`type="date"`, defaults to today), pair number buttons (1 to 6), reactive topic dropdown, and duplicate conflict error handling.
  - Interactive schedule journal listing cards sorted by date DESC and pair ASC, featuring date badges, pair chips, topic titles, inline edit controls (allowing change of date, pair 1..6, and topic), and deletion with modal confirmation.
- **Zero Regression Guarantee:** Retains all 49 existing pytest tests without alteration, introducing new test suites for the storage engine and routes.

---

## 2. Data Model & Storage Architecture

### 2.1 Storage Location & SQLite Schema
To maintain atomic access control and leverage the application's existing private directory security (directory permissions `0700`, database file `0600`), the assignments store uses a dedicated SQLite table `class_assignments` stored in `.private/access.sqlite3` (or configurable `.private/assignments.sqlite3` in the private directory).

```sql
CREATE TABLE IF NOT EXISTS class_assignments (
    id TEXT PRIMARY KEY,
    group_name TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    lesson_date TEXT NOT NULL,
    pair_number INTEGER NOT NULL CHECK (pair_number BETWEEN 1 AND 6),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(group_name, lesson_date, pair_number)
);

CREATE INDEX IF NOT EXISTS idx_assignments_group_subject
    ON class_assignments(group_name, subject_id, lesson_date);

CREATE INDEX IF NOT EXISTS idx_assignments_group_date
    ON class_assignments(group_name, lesson_date, pair_number);
```

#### Field Specifications:
- `id` (`TEXT PRIMARY KEY`): Canonical UUID v4 string (36 characters).
- `group_name` (`TEXT NOT NULL`): Group name matching `CatalogItem.name` in the groups catalog.
- `subject_id` (`TEXT NOT NULL`): Subject UUID string matching `CatalogItem.id` in the subjects catalog.
- `topic_id` (`TEXT NOT NULL`): Topic UUID string matching `Topic.id` in the topics catalog.
- `lesson_date` (`TEXT NOT NULL`): ISO date formatted string `YYYY-MM-DD` (e.g. `2026-10-14`).
- `pair_number` (`INTEGER NOT NULL`): Academic pair index constrained between `1` and `6` inclusive.
- `created_at` (`TEXT NOT NULL`): ISO 8601 UTC timestamp string (`YYYY-MM-DDTHH:MM:SSZ`).
- `updated_at` (`TEXT NOT NULL`): ISO 8601 UTC timestamp string (`YYYY-MM-DDTHH:MM:SSZ`).

#### Constraints & Indexes:
1. `UNIQUE(group_name, lesson_date, pair_number)`: Enforces the core business rule that a single group cannot have more than one scheduled assignment in the same academic pair slot on the same day. Any attempt to insert or move an assignment into an occupied slot triggers a conflict exception.
2. `CHECK (pair_number BETWEEN 1 AND 6)`: Guarantees that only valid academic pairs are stored.
3. `idx_assignments_group_subject`: Optimizes filtering by group and discipline for both the teacher admin journal and student topic discovery.
4. `idx_assignments_group_date`: Optimizes chronological sorting of schedule cards for teachers and students.

### 2.2 Domain Model: `Assignment`
```python
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

@dataclass(frozen=True, slots=True)
class Assignment:
    id: UUID
    group_name: str
    subject_id: UUID
    topic_id: UUID
    lesson_date: str  # YYYY-MM-DD
    pair_number: int  # 1..6
    created_at: datetime
    updated_at: datetime
```

### 2.3 Store Engine: `AssignmentStore`
The `AssignmentStore` class encapsulates all database operations, concurrency handling, and catalog lifecycle events.

```python
class AssignmentConflictError(ValueError):
    """Raised when an assignment already exists for (group_name, lesson_date, pair_number)."""
    pass

class AssignmentNotFoundError(ValueError):
    """Raised when an assignment ID does not exist."""
    pass

class AssignmentStore:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        self._init_db()
        self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def create(
        self,
        group_name: str,
        subject_id: UUID,
        topic_id: UUID,
        lesson_date: str,
        pair_number: int,
    ) -> Assignment:
        """Creates a new assignment. Raises AssignmentConflictError if the pair is taken."""

    def update(
        self,
        assignment_id: UUID,
        lesson_date: str,
        pair_number: int,
        topic_id: UUID,
    ) -> Assignment:
        """Updates date, pair number, or topic for an existing assignment.
        Validates uniqueness against other records."""

    def delete(self, assignment_id: UUID) -> None:
        """Deletes an assignment by ID. Idempotent if not found."""

    def get(self, assignment_id: UUID) -> Assignment | None:
        """Retrieves a single assignment by ID."""

    def list_for_group(self, group_name: str) -> tuple[Assignment, ...]:
        """Returns all assignments for a group ordered by lesson_date DESC, pair_number ASC."""

    def list_for_group_and_subject(
        self, group_name: str, subject_id: UUID
    ) -> tuple[Assignment, ...]:
        """Returns all assignments for a group in a specific subject ordered by lesson_date DESC, pair_number ASC."""

    def rename_group(self, old_name: str, new_name: str) -> None:
        """Cascades group rename across all assignment records when a group is renamed in the catalog."""

    def delete_group(self, name: str) -> None:
        """Removes all assignments for a group when the group is deleted from the catalog."""
```

### 2.4 Catalog Lifecycle Coordination & Integrity
1. **Group Rename:** When a teacher renames a group via `/admin/groups/<id>/rename`, `admin_catalog_mutate` calls `assignment_store.rename_group(old_name, new_name)` alongside `access_store.rename_group(old_name, new_name)`.
2. **Group Deletion:** When a teacher deletes a group via `/admin/groups/<id>/delete`, `assignment_store.delete_group(old_name)` is invoked to purge scheduled pairs for that group.
3. **Subject / Topic Deletion:** When a subject or topic is removed from the catalog, the application verifies whether active assignments reference it. If references exist, the deletion either cascades cleanly or returns an actionable conflict error (`"Нельзя удалить тему, пока она назначена на занятия групп"`), preventing dangling references.
4. **Locking & Concurrency:** All write operations coordinate under `group_state_lock()` to ensure race-free execution during concurrent updates.

---

## 3. Teacher Admin Panel Specifications (`/admin/assignments`)

### 3.1 Navigation & Shell Integration
In `homework-submissions/app/templates/_admin_nav.html`, a new navigation item is added between "Доступы групп" and the "Каталог" heading:

```html
<a class="admin-nav__link{% if section == 'assignments' %} is-active{% endif %}"
   href="{{ url_for('admin_assignments_page') }}"
   {% if section == 'assignments' %} aria-current="page"{% endif %}>
  <svg class="admin-nav__icon" viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true" focusable="false">
    <rect x="3" y="4" width="18" height="17" rx="2" stroke="currentColor" stroke-width="1.8"/>
    <path d="M3 9h18M8 2v4M16 2v4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
    <path d="M7 13h2M11 13h2M15 13h2M7 17h2M11 17h2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
  </svg>
  <span>Занятия и пары</span>
</a>
```

### 3.2 Filter Toolbar & URL Synchronization
- **Location:** At the top of `/admin/assignments`.
- **Controls:**
  1. **Group filter:** `<select id="filter_group" name="group">` populated with all active groups from the catalog. Defaults to the query parameter `?group=...` or the first group alphabetically.
  2. **Subject filter:** `<select id="filter_subject" name="subject_id">` with `<option value="">Все предметы</option>` followed by catalog subjects. Reflects `?subject_id=...`.
- **Behavior:**
  - Selecting a different group or discipline reloads or dynamically refreshes the view and synchronizes the browser address bar via `window.history.replaceState`.
  - Summary metrics display total pairs scheduled for the selected group and subject:
    ```html
    <div class="summary-list" aria-label="Сводка по занятиям">
      <article class="summary-card">
        <strong id="assigned_count">{{ assignments|length }}</strong>
        <span>занятий запланировано</span>
      </article>
      <article class="summary-card">
        <strong>{{ unique_subjects_count }}</strong>
        <span>дисциплин задействовано</span>
      </article>
    </div>
    ```

### 3.3 Assignment Creation Form (`.assignment-create`)
- **Container:** Accessible `<form id="assignment_create_form" class="panel assignment-create form-stack" method="post" action="/admin/assignments">`.
- **Form Fields:**
  1. **Group (Hidden or Read-only):** Linked to the currently selected filter group.
  2. **Subject Selector:** `<select id="assignment_subject" name="subject_id" required>` containing catalog subjects.
  3. **Topic Selector:** `<select id="assignment_topic" name="topic_id" required>` dynamically filtered by chosen subject. When the subject changes, non-matching topics are hidden and the first valid topic is selected.
  4. **Lesson Date Picker:** `<input id="assignment_date" name="lesson_date" type="date" required>` defaulting to current local date (`YYYY-MM-DD`).
  5. **Pair Number Selector (1..6):** Segmented buttons or radio group corresponding to academic pairs:
     - **Пара 1** (08:30 – 10:00)
     - **Пара 2** (10:10 – 11:40)
     - **Пара 3** (12:10 – 13:40)
     - **Пара 4** (13:50 – 15:20)
     - **Пара 5** (15:30 – 17:00)
     - **Пара 6** (17:10 – 18:40)
  6. **Submit Button:** `<button class="button button--primary" type="submit">Запланировать пару</button>`.
- **Conflict Handling & Feedback:**
  - If a pair is already scheduled for that group on that date, the server returns HTTP `409 Conflict` with JSON:
    `{"error": "conflict", "message": "Пара 2 на дату 14.10.2026 для группы ИС 25/9-1П уже занята темой 'Арифметика и коды чисел'."}`.
  - The form intercepts the 409 response, sets the status badge `[data-state="error"]`, announces it via `aria-live="polite"`, and retains entered field values for quick correction.

### 3.4 Assignment Journal / Schedule View
- **List Container:** `<div class="assignment-list" data-assignment-list>` rendering assignments sorted by `lesson_date DESC, pair_number ASC`.
- **Assignment Card Structure (`.assignment-card`):**
  ```html
  <article class="panel assignment-card" data-assignment-id="{{ item.id }}" data-date="{{ item.lesson_date }}" data-pair="{{ item.pair_number }}">
    <div class="assignment-card__header">
      <div class="assignment-card__meta">
        <span class="chip chip--date">{{ item.lesson_date_formatted }}</span>
        <span class="chip chip--pair">Пара {{ item.pair_number }} ({{ item.pair_time }})</span>
      </div>
      <div class="assignment-card__actions">
        <button class="button button--outline button--sm" type="button" data-action="edit">Редактировать</button>
        <button class="button button--danger button--sm" type="button" data-action="delete">Удалить</button>
      </div>
    </div>
    <div class="assignment-card__body">
      <p class="eyebrow">{{ item.subject_name }}</p>
      <h3 class="assignment-card__title">{{ item.topic_name }}</h3>
    </div>
    <!-- Inline Edit Panel (Hidden by default) -->
    <form class="assignment-card__edit-form form-stack" hidden data-action="update">
      <div class="edit-grid">
        <label class="field">Дата
          <input type="date" name="lesson_date" value="{{ item.lesson_date }}" required>
        </label>
        <label class="field">Пара
          <select name="pair_number" required>
            <option value="1" {% if item.pair_number == 1 %}selected{% endif %}>1 пара (08:30–10:00)</option>
            <option value="2" {% if item.pair_number == 2 %}selected{% endif %}>2 пара (10:10–11:40)</option>
            <option value="3" {% if item.pair_number == 3 %}selected{% endif %}>3 пара (12:10–13:40)</option>
            <option value="4" {% if item.pair_number == 4 %}selected{% endif %}>4 пара (13:50–15:20)</option>
            <option value="5" {% if item.pair_number == 5 %}selected{% endif %}>5 пара (15:30–17:00)</option>
            <option value="6" {% if item.pair_number == 6 %}selected{% endif %}>6 пара (17:10–18:40)</option>
          </select>
        </label>
        <label class="field">Тема
          <select name="topic_id" required>
            {% for topic in subject_topics %}
              <option value="{{ topic.id }}" {% if topic.id == item.topic_id %}selected{% endif %}>{{ topic.name }}</option>
            {% endfor %}
          </select>
        </label>
      </div>
      <div class="assignment-card__edit-actions">
        <button class="button button--primary" type="submit">Сохранить изменения</button>
        <button class="button button--outline" type="button" data-action="cancel">Отмена</button>
      </div>
      <p class="status" aria-live="polite"></p>
    </form>
  </article>
  ```
- **Inline Editing Behavior:**
  - Clicking "Редактировать" toggles the inline edit form, auto-focuses the date input, and populates existing values.
  - Submitting sends `POST /admin/assignments/<id>/update`. On conflict, the error is displayed inline. On success, the card header and title update without full page reload.
- **Deletion with Confirmation:**
  - Clicking "Удалить" presents an accessible confirmation modal or inline confirmation: `"Удалить занятие: Пара 2 (14.10.2026) — Арифметика и коды чисел?"`.
  - Confirming sends `POST /admin/assignments/<id>/delete`, removes the card with a smooth transition, updates summary counts, and shows empty state if the list becomes empty.
- **Empty State (`.empty-state`):**
  - Rendered when no assignments match the filter:
    `"Для выбранной группы и предмета ещё не назначено ни одной пары. Запланируйте первое занятие через форму выше."`

---

## 4. Student Interface Specifications (`/`)

### 4.1 Unlock Flow & Session Association
1. **Student submits Step 1 (`POST /unlock`):**
   - Form fields: `group` (e.g. `"ИС 25/9-1П"`), `password`.
   - Server authenticates against `AccessStore.authorize(group, password)`.
   - On success, the server binds session variables:
     ```python
     session["student_group"] = group
     session["student_group_id"] = str(item.id)
     session["student_access_version"] = version
     ```
2. **Content Negotiation & Response Contract:**
   - **HTML / Form Submissions & Existing Tests:** Returns HTTP `204 No Content`. This is vital for 100% backward compatibility with existing tests asserting `assert unlock_response.status_code == 204`.
   - **AJAX / Fetch Client Submissions (`Accept: application/json`):** Returns HTTP `200 OK` with JSON payload containing all active assignments for that unlocked group:
     ```json
     {
       "group": "ИС 25/9-1П",
       "assignments": [
         {
           "id": "e8d648b2-579c-4f73-90d1-678bb3f06489",
           "subject_id": "a24f0c92-3c1e-4cb8-b9a3-559d8329681b",
           "subject_name": "Архитектура аппаратных средств",
           "topic_id": "7f13b192-8051-4e76-a947-fbc3e9b10901",
           "topic_name": "Арифметика и коды чисел",
           "lesson_date": "2026-10-14",
           "pair_number": 2,
           "label": "14.10, пара 2 — Арифметика и коды чисел"
         }
       ]
     }
     ```
   - **Supplementary Query Endpoint:** Unlocked students can also query `GET /student/assignments` at any time to re-fetch their group's active schedule.

### 4.2 Dynamic Discipline & Topic Selection (Step 2)

#### Discipline Selector (`<select id="discipline" name="discipline">`)
- Filtered to display **only those disciplines** that have at least one scheduled assignment for the student's group.
- If a group has assignments across two disciplines, only those two disciplines appear in the dropdown.
- If no assignments exist at all for the group, a clear empty notice is presented: `"Для вашей группы пока нет открытых заданий"`.

#### Topic Selector (`<select id="work" name="work">`)
- **Formatting Rule:**
  - Topic label (displayed text): Formatted as `"ДД.ММ, пара N — Тема"`.
    - Date format: Day and month with leading zero (e.g. `14.10`).
    - Pair format: `пара ` followed by pair number 1..6.
    - Separator: Em dash `—` surrounded by spaces.
    - Example: `14.10, пара 2 — Арифметика и коды чисел`.
  - If multiple pairs cover the same topic across different dates, each appears as a distinct option with its own date and pair context, sorted by date DESC, pair ASC.
- **CRITICAL BACKWARD-COMPATIBILITY INVARIANT:**
  ```html
  <option value="Арифметика и коды чисел"
          data-subject-id="a24f0c92-3c1e-4cb8-b9a3-559d8329681b"
          data-assignment-id="e8d648b2-579c-4f73-90d1-678bb3f06489"
          data-lesson-date="2026-10-14"
          data-pair-number="2">
    14.10, пара 2 — Арифметика и коды чисел
  </option>
  ```
  - **The `<option value>` attribute MUST strictly contain the clean topic name verbatim** (e.g., `value="Арифметика и коды чисел"`).
  - **Rationale & Protection:**
    1. `catalog().contains(group, discipline, work)` checks that `work.casefold() == topic.name.casefold()`.
    2. `destination_directory(storage_root, group, discipline, work)` produces the filesystem directory:
       `storage/ИС 25／9-1П-Архитектура аппаратных средств-Арифметика и коды чисел/`.
    3. Keeping `value` clean guarantees that neither catalog validation nor filesystem directory naming undergoes breaking changes. All automated grading scripts, existing directory parsers, and unit tests continue functioning without any migration.

#### Empty State Handling
- If a discipline is selected that has no assigned topics for this group:
  1. The topic selector is disabled: `work.disabled = true`.
  2. The topic hint (`#topic_hint`) becomes visible with explanatory text: `"Для вашей группы по данному предмету ещё нет назначенных занятий."`.
  3. The submission button is disabled: `submitButton.disabled = true`.

### 4.3 Upload & Submission Flow (`POST /submit`)
- **Submitted Fields:** `group`, `discipline`, `work` (clean topic name), `student_name`, `file`.
- **Server Validation Pipeline:**
  1. **Group Session Authentication:** Confirms `group == session["student_group"]`.
  2. **Access Version Validity:** Confirms group is open and version matches `AccessStore.permits(...)`.
  3. **Catalog Verification:** Confirms `catalog().contains(group, discipline, work)`.
  4. **Class Assignment Group Isolation:** Confirms an active record exists in `class_assignments` matching `(group_name, subject_id, topic_id)`. If a student attempts to tamper with form data to submit a topic not assigned to their group, the server responds with HTTP `400 Bad Request` or `403 Forbidden`.
- **Storage Output:**
  - Written via `write_upload(...)` to `destination_directory(...)`.
  - Format: `storage/<Clean Group>-<Clean Discipline>-<Clean Topic>/<Student Name> <Attempt>.<ext>`.
  - Response: HTTP `201 Created` with `{"filename": "..."}`.

---

## 5. Technical Constraints & API Contracts

### 5.1 Route Inventory

| Method | Endpoint | Auth | Purpose | Response |
|---|---|---|---|---|
| `GET` | `/admin/assignments` | Teacher | Renders assignments admin dashboard | `200 OK` (HTML) |
| `POST` | `/admin/assignments` | Teacher | Creates a new class assignment | `201 Created` (JSON) / `409 Conflict` |
| `POST` | `/admin/assignments/<id>/update` | Teacher | Updates date, pair number, or topic | `200 OK` (JSON) / `409 Conflict` |
| `POST` | `/admin/assignments/<id>/delete` | Teacher | Deletes a class assignment | `204 No Content` |
| `GET` | `/student/assignments` | Student | Returns active assignments for student session | `200 OK` (JSON) / `403 Forbidden` |
| `POST` | `/unlock` | Public | Unlocks group, establishes session | `204 No Content` / `200 OK` (JSON) |
| `POST` | `/submit` | Student | Submits file for assigned topic | `201 Created` (JSON) / `400` / `403` |

### 5.2 JSON Schemas & Payloads

#### `POST /admin/assignments`
- **Request Form Data:**
  - `group_name`: `"ИС 25/9-1П"`
  - `subject_id`: `"a24f0c92-3c1e-4cb8-b9a3-559d8329681b"`
  - `topic_id`: `"7f13b192-8051-4e76-a947-fbc3e9b10901"`
  - `lesson_date`: `"2026-10-14"`
  - `pair_number`: `"2"`
- **Success Response (201):**
  ```json
  {
    "id": "e8d648b2-579c-4f73-90d1-678bb3f06489",
    "group_name": "ИС 25/9-1П",
    "subject_id": "a24f0c92-3c1e-4cb8-b9a3-559d8329681b",
    "topic_id": "7f13b192-8051-4e76-a947-fbc3e9b10901",
    "lesson_date": "2026-10-14",
    "pair_number": 2,
    "created_at": "2026-10-11T12:00:00Z",
    "updated_at": "2026-10-11T12:00:00Z"
  }
  ```
- **Conflict Response (409):**
  ```json
  {
    "error": "assignment_conflict",
    "message": "Пара 2 на дату 2026-10-14 для группы ИС 25/9-1П уже занята."
  }
  ```

#### `POST /unlock` (With `Accept: application/json`)
- **Success Response (200):**
  ```json
  {
    "group": "ИС 25/9-1П",
    "assignments": [
      {
        "id": "e8d648b2-579c-4f73-90d1-678bb3f06489",
        "subject_id": "a24f0c92-3c1e-4cb8-b9a3-559d8329681b",
        "subject_name": "Архитектура аппаратных средств",
        "topic_id": "7f13b192-8051-4e76-a947-fbc3e9b10901",
        "topic_name": "Арифметика и коды чисел",
        "lesson_date": "2026-10-14",
        "pair_number": 2,
        "label": "14.10, пара 2 — Арифметика и коды чисел"
      }
    ]
  }
  ```

### 5.3 DOM Contract & Selector Invariants
The implementation preserves all selectors and input names required by existing tests:
- Forms: `#unlock`, `#submit`, `#admin_login`.
- Student inputs: `#group`, `#group_password`, `#discipline`, `#work`, `#student_name`, `#file`.
- Student status containers: `#unlock_status`, `#submit_status`, `#topic_hint`, `#access_summary`, `#group_display`, `#change_group`.
- New Admin IDs & attributes:
  - Form: `#assignment_create_form`
  - Filters: `#filter_group`, `#filter_subject`
  - Inputs: `#assignment_subject`, `#assignment_topic`, `#assignment_date`
  - Pair buttons: `[data-pair-number]`
  - List container: `[data-assignment-list]`
  - Item container: `[data-assignment-id]`
  - Edit trigger: `[data-action="edit"]`
  - Delete trigger: `[data-action="delete"]`

### 5.4 Design System Tokens & Styling (`styles.css`)
All UI extensions strictly utilize existing design tokens:
- **Card styling:** `.assignment-card` uses `var(--card-bg)`, `var(--card-border)`, `var(--radius-card)`, and `var(--card-shadow)`.
- **Badges and chips:** `.chip--pair` uses `var(--color-surface-muted)`, `var(--color-text)`, and `var(--radius-pill)`. `.chip--date` uses `var(--color-primary)` text accent.
- **Pair segmented buttons:** `.pair-selector` uses `var(--radius-control)`, `var(--color-surface-muted)` inactive, and `var(--button-primary-bg)` active.
- **Responsive layouts:** Adapts across the 4 canonical breakpoints:
  - `375px`: Mobile single-column, pair buttons wrap 3x2, full-width cards.
  - `768px`: Tablet single-column with side-by-side date and pair pickers.
  - `1024px`: Compact desktop two-column layout (`.catalog-workspace` pattern with creation form on left, schedule list on right).
  - `1440px`: Wide desktop comfortable layout with sticky filter and creation sidebar.

---

## 6. Technical Constraints & Test Plan

### 6.1 Baseline Integrity Requirement
All existing **49 pytest tests** in `homework-submissions/tests/` MUST pass without modifications:
- `test_access.py` (5 tests): Group access, passwords, deadlines, permissions.
- `test_catalog.py` (1 test): Catalog snapshot validation.
- `test_catalog_store.py` (24 tests): CRUD, rename, move, delete operations for groups, subjects, topics.
- `test_storage.py` (1 test): Safe path cleaning, incremental file uploads.
- `test_web.py` (18 tests): Web routing, student unlock, student submit, admin auth, access configuration.

### 6.2 New Test Suite 1: `test_assignment_store.py`
Unit and integration tests for `AssignmentStore`:
1. `test_assignment_store_initializes_table_and_permissions`: Verifies table creation, index creation, and file permissions `0600`/`0700`.
2. `test_create_and_get_assignment`: Validates inserting a record and retrieving it by UUID.
3. `test_unique_constraint_prevents_duplicate_pair`: Asserts `AssignmentConflictError` when creating two assignments for the same group, date, and pair number.
4. `test_pair_number_boundary_validation`: Confirms pairs < 1 or > 6 raise validation errors.
5. `test_lesson_date_format_validation`: Confirms non-ISO date formats are rejected.
6. `test_update_assignment_success_and_conflict`: Tests updating date, pair, or topic, verifying that moving to an occupied slot raises `AssignmentConflictError`.
7. `test_delete_assignment_removes_record`: Tests deletion by ID.
8. `test_list_for_group_ordered_by_date_desc_pair_asc`: Asserts sorting order across multiple dates and pairs.
9. `test_list_for_group_and_subject_filters_correctly`: Asserts discipline-specific filtering.
10. `test_rename_group_cascades_to_assignments`: Verifies that renaming a group updates all its scheduled assignments.
11. `test_delete_group_purges_assignments`: Verifies that deleting a group removes its scheduled assignments.

### 6.3 New Test Suite 2: `test_assignments_web.py`
Route and controller integration tests:
1. `test_admin_assignments_requires_authentication`: Unauthenticated `GET /admin/assignments` or `POST /admin/assignments` returns 403 or redirects to login.
2. `test_admin_can_schedule_assignment_successfully`: Admin posts valid assignment data; receives 201 JSON; assignment appears in journal.
3. `test_admin_schedule_conflict_returns_409`: Submitting duplicate assignment returns 409 JSON with descriptive message.
4. `test_admin_can_update_assignment`: Validates `POST /admin/assignments/<id>/update`.
5. `test_admin_can_delete_assignment`: Validates `POST /admin/assignments/<id>/delete` returns 204.
6. `test_student_unlock_with_json_accept_returns_assigned_topics`: Asserts `POST /unlock` with `Accept: application/json` returns 200 with formatted assignment labels.
7. `test_student_unlock_without_json_accept_returns_204`: Confirms standard unlock returns 204 for backward compatibility.
8. `test_group_isolation_student_only_sees_own_group_assignments`: Verifies that a student unlocked under Group A receives only Group A's assignments, never Group B's.
9. `test_student_topic_select_value_is_clean_topic_name`: Verifies that the client receives the clean topic name as the value while the label contains `"ДД.ММ, пара N — Тема"`.
10. `test_student_submit_creates_expected_backward_compatible_directory`: Confirms that uploading a work with formatted display label still saves files into `storage/<Group>-<Discipline>-<Clean Topic>/`.
11. `test_student_cannot_submit_unassigned_topic_for_group`: Submitting a topic that belongs to the subject in the catalog but was not scheduled for that group returns HTTP 400 or 403.
12. `test_catalog_group_rename_and_delete_maintain_assignments_integrity`: Tests admin renaming and deleting groups through web routes and verifies assignment synchronization.

---

## 7. Implementation Roadmap & Checklist

- [ ] **Phase 1: Storage Layer (`app/assignment_store.py`)**
  - Implement `Assignment` dataclass and `AssignmentStore` class.
  - Implement SQLite initialization, table creation, and indexes.
  - Implement `create`, `update`, `delete`, `get`, `list_for_group`, `list_for_group_and_subject`, `rename_group`, `delete_group`.
  - Write and pass `tests/test_assignment_store.py`.
- [ ] **Phase 2: Admin Web Endpoints & Templates**
  - Add `_admin_nav.html` timetable entry.
  - Create `admin_assignments.html` template with filter toolbar, creation panel, and schedule journal.
  - Implement routes `GET /admin/assignments`, `POST /admin/assignments`, `POST /admin/assignments/<id>/update`, `POST /admin/assignments/<id>/delete`.
  - Wire catalog group rename and delete actions in `web.py` to `assignment_store`.
  - Implement `assignments-admin.js` for reactive dropdowns, conflict alerts, and inline editing.
- [ ] **Phase 3: Student Flow & Backward-Compatible Submission**
  - Update `POST /unlock` in `web.py` to support content negotiation (`Accept: application/json`).
  - Add `GET /student/assignments` endpoint.
  - Update `student.js` to render assigned topics with `"ДД.ММ, пара N — Тема"` label while keeping `<option value>` as the clean topic name.
  - Implement empty state hints when a selected discipline has no scheduled topics for the group.
  - Enforce server-side assignment check in `POST /submit`.
- [ ] **Phase 4: CSS Tokens & Responsive Verification**
  - Add component styles for `.assignment-card`, `.pair-chip`, `.pair-selector` to `styles.css`.
  - Verify layout across 375px, 768px, 1024px, and 1440px viewport widths.
  - Confirm WCAG 2.2 AA contrast compliance and visible focus rings.
- [ ] **Phase 5: Verification & Quality Assurance**
  - Run full test suite: all 49 existing tests + new assignment test suites.
  - Audit codebase for zero placeholders or TODOs.
