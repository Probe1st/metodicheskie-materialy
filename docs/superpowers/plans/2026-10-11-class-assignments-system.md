# Class-Based Assignments System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a class-based assignment scheduling system where teachers assign topics to specific groups on specific dates and pairs (1-6), groups see only their scheduled assignments with date/pair context, and teachers can manage and edit assignments per group and subject.

**Architecture:** SQLite-backed `AssignmentStore` (`class_assignments` table in `.private/access.sqlite3` or `.private/assignments.sqlite3`) with unique constraint on `(group_name, lesson_date, pair_number)`. Flask backend routes for `/admin/assignments` (list, create, update, delete) and updated `/unlock` response providing scheduled assignments. Updated student template & JS to filter topics by assigned pairs while preserving clean `<option value>` topic names for backward compatibility. New admin page and navigation with design system styles.

**Tech Stack:** Python 3.14, Flask, SQLite3, Jinja2, Vanilla JavaScript (ES6+), CSS Custom Properties, Pytest.

## Global Constraints
- In student `<select id="work" name="work">`, `<option value>` must strictly be the clean topic name (e.g. `value="Арифметика и коды чисел"`), while the text label displays `ДД.ММ, пара N — <Название темы>`.
- Preserves all 49 existing pytest tests; all existing submission storage paths (`storage/<Group>-<Discipline>-<Topic>/...`) remain identical.
- Pair number must strictly be validated as an integer from 1 to 6.
- Unique constraint on `(group_name, lesson_date, pair_number)` prevents double-booking a pair.
- All interactive controls have visible focus rings and WCAG AAA contrast for body text.
- Every task must pass pytest verification: `homework-submissions/.venv/bin/pytest homework-submissions/tests`.

---

## Task 1: `AssignmentStore` Data Layer & SQLite Engine

**Files:**
- Create: `homework-submissions/app/assignments.py`
- Create: `homework-submissions/tests/test_assignments.py`

**Interfaces:**
- Produces:
  - `Assignment(id: UUID, group_name: str, subject_id: UUID, topic_id: UUID, lesson_date: str, pair_number: int, created_at: datetime, updated_at: datetime)`
  - `AssignmentConflictError(ValueError)`
  - `AssignmentNotFoundError(ValueError)`
  - `AssignmentValidationError(ValueError)`
  - `AssignmentStore(path: Path)`:
    - `create(group_name: str, subject_id: UUID, topic_id: UUID, lesson_date: str, pair_number: int) -> Assignment`
    - `update(assignment_id: UUID, lesson_date: str, pair_number: int, topic_id: UUID) -> Assignment`
    - `delete(assignment_id: UUID) -> None`
    - `get(assignment_id: UUID) -> Assignment | None`
    - `list_for_group(group_name: str) -> tuple[Assignment, ...]`
    - `list_for_group_and_subject(group_name: str, subject_id: UUID) -> tuple[Assignment, ...]`
    - `rename_group(old_name: str, new_name: str) -> None`
    - `delete_group(name: str) -> None`

- [ ] **Step 1: Write the failing unit tests for `AssignmentStore`**

Write `homework-submissions/tests/test_assignments.py`:
```python
from pathlib import Path
from uuid import uuid4
import pytest

from app.assignments import (
    AssignmentStore,
    AssignmentConflictError,
    AssignmentNotFoundError,
    AssignmentValidationError,
)


@pytest.fixture
def store(tmp_path: Path) -> AssignmentStore:
    return AssignmentStore(tmp_path / "assignments.sqlite3")


def test_create_and_get_assignment(store: AssignmentStore) -> None:
    subject_id = uuid4()
    topic_id = uuid4()
    item = store.create(
        group_name="ИС 25/9-1П",
        subject_id=subject_id,
        topic_id=topic_id,
        lesson_date="2026-10-14",
        pair_number=2,
    )
    assert item.group_name == "ИС 25/9-1П"
    assert item.subject_id == subject_id
    assert item.topic_id == topic_id
    assert item.lesson_date == "2026-10-14"
    assert item.pair_number == 2

    fetched = store.get(item.id)
    assert fetched == item


def test_unique_group_date_pair_constraint(store: AssignmentStore) -> None:
    subject_id = uuid4()
    topic_id_1 = uuid4()
    topic_id_2 = uuid4()

    store.create("ИС 25/9-1П", subject_id, topic_id_1, "2026-10-14", 2)
    with pytest.raises(AssignmentConflictError):
        store.create("ИС 25/9-1П", subject_id, topic_id_2, "2026-10-14", 2)


def test_pair_number_boundary_validation(store: AssignmentStore) -> None:
    subject_id = uuid4()
    topic_id = uuid4()
    with pytest.raises(AssignmentValidationError):
        store.create("ИС 25/9-1П", subject_id, topic_id, "2026-10-14", 0)

    with pytest.raises(AssignmentValidationError):
        store.create("ИС 25/9-1П", subject_id, topic_id, "2026-10-14", 7)


def test_lesson_date_format_validation(store: AssignmentStore) -> None:
    subject_id = uuid4()
    topic_id = uuid4()
    with pytest.raises(AssignmentValidationError):
        store.create("ИС 25/9-1П", subject_id, topic_id, "14-10-2026", 1)


def test_update_assignment_success_and_conflict(store: AssignmentStore) -> None:
    subject_id = uuid4()
    topic_id_1 = uuid4()
    topic_id_2 = uuid4()

    first = store.create("ИС 25/9-1П", subject_id, topic_id_1, "2026-10-14", 1)
    second = store.create("ИС 25/9-1П", subject_id, topic_id_2, "2026-10-14", 2)

    updated = store.update(first.id, "2026-10-15", 3, topic_id_1)
    assert updated.lesson_date == "2026-10-15"
    assert updated.pair_number == 3

    with pytest.raises(AssignmentConflictError):
        store.update(first.id, "2026-10-14", 2, topic_id_1)

    with pytest.raises(AssignmentNotFoundError):
        store.update(uuid4(), "2026-10-14", 4, topic_id_1)


def test_delete_assignment(store: AssignmentStore) -> None:
    subject_id = uuid4()
    topic_id = uuid4()
    item = store.create("ИС 25/9-1П", subject_id, topic_id, "2026-10-14", 1)
    store.delete(item.id)
    assert store.get(item.id) is None


def test_list_for_group_and_subject_ordering(store: AssignmentStore) -> None:
    subject_1 = uuid4()
    subject_2 = uuid4()
    t1, t2, t3 = uuid4(), uuid4(), uuid4()

    store.create("ИС 25/9-1П", subject_1, t1, "2026-10-10", 2)
    store.create("ИС 25/9-1П", subject_1, t2, "2026-10-14", 1)
    store.create("ИС 25/9-1П", subject_2, t3, "2026-10-12", 3)
    store.create("ИС 24/9-3П", subject_1, t1, "2026-10-14", 1)

    group_items = store.list_for_group("ИС 25/9-1П")
    assert len(group_items) == 3
    assert [x.lesson_date for x in group_items] == ["2026-10-14", "2026-10-12", "2026-10-10"]

    subject_items = store.list_for_group_and_subject("ИС 25/9-1П", subject_1)
    assert len(subject_items) == 2
    assert [x.lesson_date for x in subject_items] == ["2026-10-14", "2026-10-10"]


def test_rename_and_delete_group_cascades(store: AssignmentStore) -> None:
    subject_id = uuid4()
    t1 = uuid4()
    store.create("ИС 25/9-1П", subject_id, t1, "2026-10-14", 1)

    store.rename_group("ИС 25/9-1П", "ИС 25-Новая")
    assert len(store.list_for_group("ИС 25/9-1П")) == 0
    assert len(store.list_for_group("ИС 25-Новая")) == 1

    store.delete_group("ИС 25-Новая")
    assert len(store.list_for_group("ИС 25-Новая")) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
./homework-submissions/.venv/bin/pytest homework-submissions/tests/test_assignments.py
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.assignments'`

- [ ] **Step 3: Implement `AssignmentStore`**

Write `homework-submissions/app/assignments.py`:
```python
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4


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


class AssignmentConflictError(ValueError):
    """Raised when an assignment already exists for (group_name, lesson_date, pair_number)."""
    pass


class AssignmentNotFoundError(ValueError):
    """Raised when an assignment ID is not found."""
    pass


class AssignmentValidationError(ValueError):
    """Raised when input parameters fail domain validation."""
    pass


_DATE_REGEX = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$")


def _validate_lesson_date(lesson_date: str) -> str:
    cleaned = lesson_date.strip()
    if not _DATE_REGEX.match(cleaned):
        raise AssignmentValidationError(f"Invalid lesson_date format: {lesson_date!r}. Expected YYYY-MM-DD.")
    try:
        datetime.strptime(cleaned, "%Y-%m-%d")
    except ValueError as err:
        raise AssignmentValidationError(f"Invalid calendar date: {lesson_date!r}") from err
    return cleaned


def _validate_pair_number(pair_number: int) -> int:
    if not isinstance(pair_number, int) or pair_number < 1 or pair_number > 6:
        raise AssignmentValidationError(f"Invalid pair_number: {pair_number!r}. Must be an integer between 1 and 6.")
    return pair_number


class AssignmentStore:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)

        with self._connect() as conn:
            conn.execute(
                """
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
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_assignments_group_subject
                ON class_assignments(group_name, subject_id, lesson_date)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_assignments_group_date
                ON class_assignments(group_name, lesson_date, pair_number)
                """
            )
            conn.commit()

        self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _row_to_assignment(self, row: sqlite3.Row) -> Assignment:
        return Assignment(
            id=UUID(row["id"]),
            group_name=row["group_name"],
            subject_id=UUID(row["subject_id"]),
            topic_id=UUID(row["topic_id"]),
            lesson_date=row["lesson_date"],
            pair_number=int(row["pair_number"]),
            created_at=datetime.fromisoformat(row["created_at"]).astimezone(UTC),
            updated_at=datetime.fromisoformat(row["updated_at"]).astimezone(UTC),
        )

    def create(
        self,
        group_name: str,
        subject_id: UUID,
        topic_id: UUID,
        lesson_date: str,
        pair_number: int,
    ) -> Assignment:
        cleaned_group = group_name.strip()
        if not cleaned_group:
            raise AssignmentValidationError("group_name cannot be empty.")
        valid_date = _validate_lesson_date(lesson_date)
        valid_pair = _validate_pair_number(pair_number)

        assignment_id = uuid4()
        now_iso = datetime.now(UTC).isoformat()

        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO class_assignments (
                        id, group_name, subject_id, topic_id, lesson_date, pair_number, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(assignment_id),
                        cleaned_group,
                        str(subject_id),
                        str(topic_id),
                        valid_date,
                        valid_pair,
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as err:
                raise AssignmentConflictError(
                    f"Пара {valid_pair} на дату {valid_date} для группы {cleaned_group} уже занята."
                ) from err

        item = self.get(assignment_id)
        assert item is not None
        return item

    def update(
        self,
        assignment_id: UUID,
        lesson_date: str,
        pair_number: int,
        topic_id: UUID,
    ) -> Assignment:
        valid_date = _validate_lesson_date(lesson_date)
        valid_pair = _validate_pair_number(pair_number)
        now_iso = datetime.now(UTC).isoformat()

        with self._connect() as conn:
            row = conn.execute(
                "SELECT group_name FROM class_assignments WHERE id = ?",
                (str(assignment_id),),
            ).fetchone()
            if row is None:
                raise AssignmentNotFoundError(f"Assignment {assignment_id} not found.")

            try:
                conn.execute(
                    """
                    UPDATE class_assignments
                    SET lesson_date = ?, pair_number = ?, topic_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        valid_date,
                        valid_pair,
                        str(topic_id),
                        now_iso,
                        str(assignment_id),
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as err:
                raise AssignmentConflictError(
                    f"Пара {valid_pair} на дату {valid_date} уже занята."
                ) from err

        item = self.get(assignment_id)
        assert item is not None
        return item

    def delete(self, assignment_id: UUID) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM class_assignments WHERE id = ?",
                (str(assignment_id),),
            )
            conn.commit()

    def get(self, assignment_id: UUID) -> Assignment | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM class_assignments WHERE id = ?",
                (str(assignment_id),),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_assignment(row)

    def list_for_group(self, group_name: str) -> tuple[Assignment, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM class_assignments
                WHERE group_name = ?
                ORDER BY lesson_date DESC, pair_number ASC
                """,
                (group_name.strip(),),
            ).fetchall()
            return tuple(self._row_to_assignment(r) for r in rows)

    def list_for_group_and_subject(
        self, group_name: str, subject_id: UUID
    ) -> tuple[Assignment, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM class_assignments
                WHERE group_name = ? AND subject_id = ?
                ORDER BY lesson_date DESC, pair_number ASC
                """,
                (group_name.strip(), str(subject_id)),
            ).fetchall()
            return tuple(self._row_to_assignment(r) for r in rows)

    def rename_group(self, old_name: str, new_name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE class_assignments SET group_name = ? WHERE group_name = ?",
                (new_name.strip(), old_name.strip()),
            )
            conn.commit()

    def delete_group(self, name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM class_assignments WHERE group_name = ?",
                (name.strip(),),
            )
            conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
./homework-submissions/.venv/bin/pytest homework-submissions/tests/test_assignments.py
```
Expected: PASS (all tests green)

- [ ] **Step 5: Commit changes**

```bash
git add homework-submissions/app/assignments.py homework-submissions/tests/test_assignments.py
git commit -m "feat(assignments): implement AssignmentStore sqlite data layer"
```

---

## Task 2: Backend Route Integration & Web API

**Files:**
- Modify: `homework-submissions/app/web.py`
- Modify: `homework-submissions/tests/test_web.py`

**Interfaces:**
- Consumes:
  - `AssignmentStore` from `app.assignments`
- Produces:
  - Updated `/unlock` route returning JSON assignments on `Accept: application/json` and `204` on standard requests.
  - `GET /student/assignments` returning assignments for the authenticated session group.
  - `GET /admin/assignments` rendering `admin_assignments.html`.
  - `POST /admin/assignments` creating an assignment (returns 201 JSON or 409 JSON).
  - `POST /admin/assignments/<id>/update` updating an assignment (returns 200 JSON or 409 JSON).
  - `POST /admin/assignments/<id>/delete` deleting an assignment (returns 204).
  - Cascades group rename and delete in `admin_catalog_mutate` to `assignment_store`.

- [ ] **Step 1: Write failing web route tests**

Add to `homework-submissions/tests/test_web.py`:
```python
def test_student_unlock_with_json_accept_returns_assignments(client_env):
    client, private_dir = client_env
    configure_group(private_dir)
    from app.assignments import AssignmentStore
    from app.catalog_store import create_catalog_store
    catalog = create_catalog_store(client.application.config).snapshot()
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    store = AssignmentStore(private_dir / "access.sqlite3")
    store.create("ИС 25/9-1П", subject.id, topic.id, "2026-10-14", 2)

    # Standard form post without Accept: application/json gives 204
    resp_204 = client.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
    assert resp_204.status_code == 204

    # Post with Accept: application/json gives 200 with assignments payload
    resp_json = client.post(
        "/unlock",
        data={"group": "ИС 25/9-1П", "password": "1234"},
        headers={"Accept": "application/json"},
    )
    assert resp_json.status_code == 200
    data = resp_json.json
    assert data["group"] == "ИС 25/9-1П"
    assert len(data["assignments"]) == 1
    assignment = data["assignments"][0]
    assert assignment["topic_name"] == topic.name
    assert assignment["pair_number"] == 2
    assert assignment["lesson_date"] == "2026-10-14"
    assert "14.10, пара 2 — " in assignment["label"]


def test_admin_assignments_crud_and_conflict(client_env):
    client, private_dir = client_env
    client.post("/admin/login", data={"password": "admin-secret"})

    from app.catalog_store import create_catalog_store
    catalog = create_catalog_store(client.application.config).snapshot()
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    # Create assignment
    create_resp = client.post(
        "/admin/assignments",
        data={
            "group_name": "ИС 25/9-1П",
            "subject_id": str(subject.id),
            "topic_id": str(topic.id),
            "lesson_date": "2026-10-14",
            "pair_number": "2",
        },
    )
    assert create_resp.status_code == 201
    created_id = create_resp.json["id"]

    # Conflict on same pair
    conflict_resp = client.post(
        "/admin/assignments",
        data={
            "group_name": "ИС 25/9-1П",
            "subject_id": str(subject.id),
            "topic_id": str(topic.id),
            "lesson_date": "2026-10-14",
            "pair_number": "2",
        },
    )
    assert conflict_resp.status_code == 409
    assert conflict_resp.json["error"] == "assignment_conflict"

    # Update assignment
    update_resp = client.post(
        f"/admin/assignments/{created_id}/update",
        data={
            "lesson_date": "2026-10-15",
            "pair_number": "3",
            "topic_id": str(topic.id),
        },
    )
    assert update_resp.status_code == 200
    assert update_resp.json["pair_number"] == 3

    # Delete assignment
    del_resp = client.post(f"/admin/assignments/{created_id}/delete")
    assert del_resp.status_code == 204
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
./homework-submissions/.venv/bin/pytest homework-submissions/tests/test_web.py -k "test_admin_assignments_crud_and_conflict"
```
Expected: FAIL with 404 Not Found on `/admin/assignments`.

- [ ] **Step 3: Implement web route integration in `app/web.py`**

In `homework-submissions/app/web.py`:
1. Import `AssignmentStore`, `AssignmentConflictError`, `AssignmentNotFoundError`, `AssignmentValidationError` from `.assignments`.
2. Add helper function `_format_date_short(iso_date: str) -> str`:
   ```python
   def _format_date_short(iso_date: str) -> str:
       parts = iso_date.split("-")
       if len(parts) == 3:
           return f"{parts[2]}.{parts[1]}"
       return iso_date
   ```
3. Inside `create_app()`:
   ```python
   def assignment_store() -> AssignmentStore:
       db_path = Path(app.config["PRIVATE_DATA_DIR"]) / "access.sqlite3"
       return AssignmentStore(db_path)
   ```
4. Update `@app.post("/unlock")`:
   ```python
   @app.post("/unlock")
   def unlock():
       group = request.form.get("group", "").strip()
       password = request.form.get("password", "")
       if not group or not password:
           abort(403)

       with group_state_lock():
           store = access_store()
           version = store.authorize(group, password)
           if version is None:
               abort(403)

           item = next((item for item in catalog().groups if item.name == group), None)
           if item is None:
               abort(403)
           session["student_group"] = group
           session["student_group_id"] = str(item.id)
           session["student_access_version"] = version

           # Retrieve assignments for group
           assigned = assignment_store().list_for_group(group)
           snapshot = catalog()
           subject_map = {s.id: s.name for s in snapshot.subjects}
           topic_map = {t.id: t.name for t in snapshot.topics}
           assignments_data = [
               {
                   "id": str(a.id),
                   "subject_id": str(a.subject_id),
                   "subject_name": subject_map.get(a.subject_id, ""),
                   "topic_id": str(a.topic_id),
                   "topic_name": topic_map.get(a.topic_id, ""),
                   "lesson_date": a.lesson_date,
                   "pair_number": a.pair_number,
                   "label": f"{_format_date_short(a.lesson_date)}, пара {a.pair_number} — {topic_map.get(a.topic_id, '')}",
               }
               for a in assigned
               if a.subject_id in subject_map and a.topic_id in topic_map
           ]

       if request.headers.get("Accept") == "application/json" or (
           request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html
       ):
           return jsonify(group=group, assignments=assignments_data), 200
       return ("", 204)
   ```
5. Add `GET /student/assignments`:
   ```python
   @app.get("/student/assignments")
   def student_assignments():
       session_group = session.get("student_group")
       session_version = session.get("student_access_version")
       if not session_group or session_version is None:
           abort(403)
       if not access_store().permits(session_group, int(session_version)):
           abort(403)

       assigned = assignment_store().list_for_group(session_group)
       snapshot = catalog()
       subject_map = {s.id: s.name for s in snapshot.subjects}
       topic_map = {t.id: t.name for t in snapshot.topics}
       assignments_data = [
           {
               "id": str(a.id),
               "subject_id": str(a.subject_id),
               "subject_name": subject_map.get(a.subject_id, ""),
               "topic_id": str(a.topic_id),
               "topic_name": topic_map.get(a.topic_id, ""),
               "lesson_date": a.lesson_date,
               "pair_number": a.pair_number,
               "label": f"{_format_date_short(a.lesson_date)}, пара {a.pair_number} — {topic_map.get(a.topic_id, '')}",
           }
           for a in assigned
           if a.subject_id in subject_map and a.topic_id in topic_map
       ]
       return jsonify(group=session_group, assignments=assignments_data), 200
   ```
6. Add admin assignment endpoints:
   ```python
   @app.get("/admin/assignments")
   def admin_assignments_page():
       if not is_admin():
           abort(403)
       snapshot = catalog()
       selected_group = request.args.get("group", "").strip()
       if not selected_group and snapshot.groups:
           selected_group = snapshot.groups[0].name

       selected_subject_id = None
       raw_subj = request.args.get("subject_id", "").strip()
       if raw_subj:
           try:
               selected_subject_id = UUID(raw_subj)
           except ValueError:
               pass

       store = assignment_store()
       if selected_subject_id:
           assignments = store.list_for_group_and_subject(selected_group, selected_subject_id)
       else:
           assignments = store.list_for_group(selected_group)

       subject_map = {s.id: s.name for s in snapshot.subjects}
       topic_map = {t.id: t.name for t in snapshot.topics}

       items = [
           {
               "id": str(a.id),
               "lesson_date": a.lesson_date,
               "lesson_date_formatted": f"{a.lesson_date.split('-')[2]}.{a.lesson_date.split('-')[1]}.{a.lesson_date.split('-')[0]}",
               "pair_number": a.pair_number,
               "subject_id": str(a.subject_id),
               "subject_name": subject_map.get(a.subject_id, ""),
               "topic_id": str(a.topic_id),
               "topic_name": topic_map.get(a.topic_id, ""),
           }
           for a in assignments
       ]

       unique_subjects_count = len({a.subject_id for a in assignments})

       return render_template(
           "admin_assignments.html",
           section="assignments",
           groups=snapshot.groups,
           subjects=snapshot.subjects,
           topics=snapshot.topics,
           selected_group=selected_group,
           selected_subject_id=str(selected_subject_id) if selected_subject_id else "",
           assignments=items,
           unique_subjects_count=unique_subjects_count,
       )

   @app.post("/admin/assignments")
   def admin_create_assignment():
       if not is_admin():
           abort(403)
       group_name = request.form.get("group_name", "").strip()
       raw_subject = request.form.get("subject_id", "").strip()
       raw_topic = request.form.get("topic_id", "").strip()
       lesson_date = request.form.get("lesson_date", "").strip()
       raw_pair = request.form.get("pair_number", "").strip()

       try:
           subject_id = UUID(raw_subject)
           topic_id = UUID(raw_topic)
           pair_number = int(raw_pair)
       except (ValueError, TypeError):
           abort(400)

       with group_state_lock():
           store = assignment_store()
           try:
               item = store.create(group_name, subject_id, topic_id, lesson_date, pair_number)
           except AssignmentConflictError as err:
               return jsonify(error="assignment_conflict", message=str(err)), 409
           except AssignmentValidationError as err:
               return jsonify(error="validation_error", message=str(err)), 400

       return jsonify(
           id=str(item.id),
           group_name=item.group_name,
           subject_id=str(item.subject_id),
           topic_id=str(item.topic_id),
           lesson_date=item.lesson_date,
           pair_number=item.pair_number,
       ), 201

   @app.post("/admin/assignments/<assignment_id>/update")
   def admin_update_assignment(assignment_id):
       if not is_admin():
           abort(403)
       try:
           uid = UUID(assignment_id)
           topic_id = UUID(request.form.get("topic_id", ""))
           pair_number = int(request.form.get("pair_number", ""))
       except (ValueError, TypeError):
           abort(400)

       lesson_date = request.form.get("lesson_date", "").strip()

       with group_state_lock():
           store = assignment_store()
           try:
               item = store.update(uid, lesson_date, pair_number, topic_id)
           except AssignmentConflictError as err:
               return jsonify(error="assignment_conflict", message=str(err)), 409
           except AssignmentNotFoundError:
               abort(404)
           except AssignmentValidationError as err:
               return jsonify(error="validation_error", message=str(err)), 400

       return jsonify(
           id=str(item.id),
           lesson_date=item.lesson_date,
           pair_number=item.pair_number,
           topic_id=str(item.topic_id),
       ), 200

   @app.post("/admin/assignments/<assignment_id>/delete")
   def admin_delete_assignment(assignment_id):
       if not is_admin():
           abort(403)
       try:
           uid = UUID(assignment_id)
       except ValueError:
           abort(400)

       with group_state_lock():
           assignment_store().delete(uid)
       return ("", 204)
   ```
7. In `admin_catalog_mutate()`:
   Hook `assignment_store().rename_group(old.name, item.name)` on rename, and `assignment_store().delete_group(old.name)` on group delete.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
./homework-submissions/.venv/bin/pytest homework-submissions/tests/test_web.py -k "test_student_unlock_with_json_accept_returns_assignments or test_admin_assignments_crud_and_conflict"
```
Expected: PASS

- [ ] **Step 5: Run full test suite to guarantee 49 existing tests are preserved**

Run:
```bash
./homework-submissions/.venv/bin/pytest homework-submissions/tests
```
Expected: PASS (all 49 existing + new tests passing)

- [ ] **Step 6: Commit changes**

```bash
git add homework-submissions/app/web.py homework-submissions/tests/test_web.py
git commit -m "feat(assignments): integrate assignment routes and student unlock payload"
```

---

## Task 3: Teacher Admin Assignments UI

**Files:**
- Modify: `homework-submissions/app/templates/_admin_nav.html`
- Create: `homework-submissions/app/templates/admin_assignments.html`
- Create: `homework-submissions/app/static/admin-assignments.js`
- Modify: `homework-submissions/app/static/styles.css`

**Interfaces:**
- Consumes:
  - Admin endpoints: `GET /admin/assignments`, `POST /admin/assignments`, `POST /admin/assignments/<id>/update`, `POST /admin/assignments/<id>/delete`
  - Design tokens from `styles.css`
- Produces:
  - "Занятия и пары" timetable item in admin navigation.
  - Complete journal interface for scheduling and managing pairs.
  - Reactive JS filtering topics by selected subject in create and edit forms.

- [ ] **Step 1: Add "Занятия и пары" to navigation `_admin_nav.html`**

In `homework-submissions/app/templates/_admin_nav.html`:
Insert after "Доступы групп" link:
```html
  <a class="admin-nav__link{% if section == 'assignments' %} is-active{% endif %}" href="{{ url_for('admin_assignments_page') }}"{% if section == 'assignments' %} aria-current="page"{% endif %}>
    <svg class="admin-nav__icon" viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true" focusable="false">
      <rect x="3" y="4" width="18" height="17" rx="2" stroke="currentColor" stroke-width="1.8"/>
      <path d="M3 9h18M8 2v4M16 2v4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
      <path d="M7 13h2M11 13h2M15 13h2M7 17h2M11 17h2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
    </svg>
    <span>Занятия и пары</span>
  </a>
```

- [ ] **Step 2: Create `admin_assignments.html` template**

Write `homework-submissions/app/templates/admin_assignments.html`:
```html
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Занятия и пары — панель преподавателя</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='styles.css') }}">
</head>
<body>
  <header class="site-header">
    <div class="site-header__inner">
      <a class="brand" href="{{ url_for('index') }}">Учебный портал</a>
      <div class="header-actions">
        <span class="header-label">Панель преподавателя</span>
        <a class="button button--quiet" href="{{ url_for('index') }}">К сдаче заданий</a>
        <form method="post" action="{{ url_for('admin_logout') }}">
          <button class="button button--quiet" type="submit">Выйти</button>
        </form>
      </div>
    </div>
  </header>
  <main class="page-shell">
    <section class="admin-hero" aria-labelledby="admin_title">
      <div>
        <p class="eyebrow">Панель преподавателя</p>
        <h1 id="admin_title">Занятия и пары</h1>
      </div>
      <p class="lead">Планируйте расписание тем по датам и учебным парам для каждой группы.</p>
    </section>

    <div class="admin-layout">
      {% set section = "assignments" %}
      {% include "_admin_nav.html" %}

      <section class="admin-content" aria-label="Расписание занятий">
        <section class="panel filter-toolbar" aria-label="Фильтры расписания">
          <form id="filter_form" class="filter-grid" method="get" action="{{ url_for('admin_assignments_page') }}">
            <label class="field">Учебная группа
              <select id="filter_group" name="group" onchange="this.form.submit()">
                {% for g in groups %}
                  <option value="{{ g.name }}" {% if g.name == selected_group %}selected{% endif %}>{{ g.name }}</option>
                {% endfor %}
              </select>
            </label>
            <label class="field">Дисциплина
              <select id="filter_subject" name="subject_id" onchange="this.form.submit()">
                <option value="">Все дисциплины</option>
                {% for s in subjects %}
                  <option value="{{ s.id }}" {% if s.id|string == selected_subject_id %}selected{% endif %}>{{ s.name }}</option>
                {% endfor %}
              </select>
            </label>
          </form>
        </section>

        <section class="summary-list" aria-label="Сводка расписания">
          <article class="summary-card">
            <strong id="assigned_count">{{ assignments|length }}</strong>
            <span>занятий запланировано</span>
          </article>
          <article class="summary-card">
            <strong>{{ unique_subjects_count }}</strong>
            <span>активных дисциплин</span>
          </article>
        </section>

        <div class="catalog-workspace">
          <form id="assignment_create_form" class="panel catalog-create form-stack" method="post" action="{{ url_for('admin_create_assignment') }}">
            <h2>Запланировать пару</h2>
            <input type="hidden" name="group_name" value="{{ selected_group }}">

            <label class="field" for="assignment_subject">Дисциплина
              <select id="assignment_subject" name="subject_id" required>
                {% for s in subjects %}
                  <option value="{{ s.id }}" {% if s.id|string == selected_subject_id %}selected{% endif %}>{{ s.name }}</option>
                {% endfor %}
              </select>
            </label>

            <label class="field" for="assignment_topic">Тема занятия
              <select id="assignment_topic" name="topic_id" required>
                {% for t in topics %}
                  <option value="{{ t.id }}" data-subject-id="{{ t.subject_id }}">{{ t.name }}</option>
                {% endfor %}
              </select>
            </label>

            <label class="field" for="assignment_date">Дата занятия
              <input id="assignment_date" name="lesson_date" type="date" required>
            </label>

            <div class="field">
              <label>Номер пары (1..6)</label>
              <div class="pair-selector" role="radiogroup" aria-label="Выбор номера пары">
                {% for p in range(1, 7) %}
                  <label class="pair-button">
                    <input type="radio" name="pair_number" value="{{ p }}" {% if p == 1 %}checked{% endif %}>
                    <span>{{ p }} пара</span>
                  </label>
                {% endfor %}
              </div>
            </div>

            <button class="button button--primary" type="submit">Запланировать пару</button>
            <p id="assignment_create_status" class="status" aria-live="polite"></p>
          </form>

          <section class="assignment-journal" aria-label="Список назначенных занятий">
            {% if assignments|length == 0 %}
              <p class="empty-state">Для выбранной группы пока не назначено ни одной пары. Заполните форму слева, чтобы запланировать занятие.</p>
            {% else %}
              <div class="catalog-list" data-assignment-list>
                {% for item in assignments %}
                  <article class="panel catalog-row assignment-card" data-assignment-id="{{ item.id }}">
                    <div class="catalog-row__heading">
                      <div class="assignment-meta">
                        <span class="chip chip--open">{{ item.lesson_date_formatted }}</span>
                        <span class="chip chip--pair">{{ item.pair_number }} пара</span>
                      </div>
                      <div class="assignment-details">
                        <p class="eyebrow">{{ item.subject_name }}</p>
                        <h3>{{ item.topic_name }}</h3>
                      </div>
                    </div>
                    <div class="catalog-actions">
                      <button class="button button--outline" type="button" data-action="edit">Редактировать</button>
                      <button class="button button--danger" type="button" data-action="delete">Удалить</button>
                    </div>

                    <form class="assignment-edit-form form-stack" method="post" action="/admin/assignments/{{ item.id }}/update" hidden>
                      <div class="edit-grid">
                        <label class="field">Дата
                          <input type="date" name="lesson_date" value="{{ item.lesson_date }}" required>
                        </label>
                        <label class="field">Пара
                          <select name="pair_number" required>
                            {% for p in range(1, 7) %}
                              <option value="{{ p }}" {% if p == item.pair_number %}selected{% endif %}>{{ p }} пара</option>
                            {% endfor %}
                          </select>
                        </label>
                        <label class="field">Тема
                          <select name="topic_id" required>
                            {% for t in topics %}
                              {% if t.subject_id|string == item.subject_id %}
                                <option value="{{ t.id }}" {% if t.id|string == item.topic_id %}selected{% endif %}>{{ t.name }}</option>
                              {% endif %}
                            {% endfor %}
                          </select>
                        </label>
                      </div>
                      <div class="catalog-actions">
                        <button class="button button--primary" type="submit">Сохранить</button>
                        <button class="button button--outline" type="button" data-action="cancel">Отмена</button>
                      </div>
                      <p class="status edit-status" aria-live="polite"></p>
                    </form>
                  </article>
                {% endfor %}
              </div>
            {% endif %}
          </section>
        </div>
      </section>
    </div>
  </main>
  <script src="{{ url_for('static', filename='admin-assignments.js') }}"></script>
</body>
</html>
```

- [ ] **Step 3: Create `admin-assignments.js` client logic**

Write `homework-submissions/app/static/admin-assignments.js`:
```javascript
document.addEventListener('DOMContentLoaded', () => {
  const subjectSelect = document.querySelector('#assignment_subject');
  const topicSelect = document.querySelector('#assignment_topic');
  const dateInput = document.querySelector('#assignment_date');
  const createForm = document.querySelector('#assignment_create_form');
  const createStatus = document.querySelector('#assignment_create_status');

  if (dateInput && !dateInput.value) {
    const today = new Date();
    const year = today.getFullYear();
    const month = String(today.getMonth() + 1).padStart(2, '0');
    const day = String(today.getDate()).padStart(2, '0');
    dateInput.value = `${year}-${month}-${day}`;
  }

  function filterTopics() {
    if (!subjectSelect || !topicSelect) return;
    const selectedSubjectId = subjectSelect.value;
    let firstVisible = null;
    Array.from(topicSelect.options).forEach((opt) => {
      const match = opt.dataset.subjectId === selectedSubjectId;
      opt.hidden = !match;
      opt.disabled = !match;
      if (match && !firstVisible) firstVisible = opt;
    });
    if (firstVisible && (topicSelect.selectedOptions[0]?.hidden || !topicSelect.value)) {
      topicSelect.value = firstVisible.value;
    }
  }

  if (subjectSelect) {
    subjectSelect.addEventListener('change', filterTopics);
    filterTopics();
  }

  if (createForm) {
    createForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const submitBtn = createForm.querySelector('button[type="submit"]');
      submitBtn.disabled = true;
      createStatus.dataset.state = 'working';
      createStatus.textContent = 'Сохраняем...';

      try {
        const res = await fetch(createForm.action, {
          method: 'POST',
          body: new FormData(createForm),
        });
        if (res.status === 409) {
          const body = await res.json();
          createStatus.dataset.state = 'error';
          createStatus.textContent = body.message || 'Данная пара уже занята.';
          return;
        }
        if (!res.ok) throw new Error('Create failed');

        createStatus.dataset.state = 'success';
        createStatus.textContent = 'Пара успешно запланирована!';
        setTimeout(() => window.location.reload(), 400);
      } catch (err) {
        createStatus.dataset.state = 'error';
        createStatus.textContent = 'Ошибка сохранения: проверьте введённые данные.';
      } finally {
        submitBtn.disabled = false;
      }
    });
  }

  document.querySelectorAll('.assignment-card').forEach((card) => {
    const editBtn = card.querySelector('[data-action="edit"]');
    const deleteBtn = card.querySelector('[data-action="delete"]');
    const cancelBtn = card.querySelector('[data-action="cancel"]');
    const editForm = card.querySelector('.assignment-edit-form');
    const editStatus = card.querySelector('.edit-status');
    const cardId = card.dataset.assignmentId;

    if (editBtn && editForm) {
      editBtn.addEventListener('click', () => {
        editForm.hidden = false;
        editBtn.hidden = true;
      });
    }

    if (cancelBtn && editForm) {
      cancelBtn.addEventListener('click', () => {
        editForm.hidden = true;
        editBtn.hidden = false;
      });
    }

    if (editForm) {
      editForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const submitBtn = editForm.querySelector('button[type="submit"]');
        submitBtn.disabled = true;
        editStatus.dataset.state = 'working';
        editStatus.textContent = 'Обновляем...';

        try {
          const res = await fetch(editForm.action, {
            method: 'POST',
            body: new FormData(editForm),
          });
          if (res.status === 409) {
            const body = await res.json();
            editStatus.dataset.state = 'error';
            editStatus.textContent = body.message || 'Пара уже занята.';
            return;
          }
          if (!res.ok) throw new Error('Update failed');
          editStatus.dataset.state = 'success';
          editStatus.textContent = 'Обновлено!';
          setTimeout(() => window.location.reload(), 400);
        } catch {
          editStatus.dataset.state = 'error';
          editStatus.textContent = 'Ошибка при обновлении.';
        } finally {
          submitBtn.disabled = false;
        }
      });
    }

    if (deleteBtn) {
      deleteBtn.addEventListener('click', async () => {
        const topicTitle = card.querySelector('h3')?.textContent || '';
        if (!confirm(`Удалить занятие «${topicTitle}»?`)) return;

        try {
          const res = await fetch(`/admin/assignments/${cardId}/delete`, {
            method: 'POST',
          });
          if (!res.ok) throw new Error('Delete failed');
          card.remove();
        } catch {
          alert('Не удалось удалить занятие.');
        }
      });
    }
  });
});
```

- [ ] **Step 4: Add CSS styles for pair selector and assignment card**

In `homework-submissions/app/static/styles.css`:
Append:
```css
.pair-selector {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.pair-button {
  display: inline-flex;
  cursor: pointer;
}
.pair-button input {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}
.pair-button span {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0.5rem 0.85rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
  background: var(--color-surface);
  color: var(--color-text);
  font-size: var(--type-sm);
  font-weight: 600;
  transition: background-color var(--motion-fast), border-color var(--motion-fast);
}
.pair-button input:checked + span {
  background: var(--button-primary-bg);
  color: var(--button-primary-fg);
  border-color: var(--button-primary-bg);
}
.pair-button input:focus-visible + span {
  outline: 2px solid var(--color-focus);
  outline-offset: 2px;
}
.chip--pair {
  background: var(--color-surface-muted);
  color: var(--color-text);
  border: 1px solid var(--color-border);
}
.assignment-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}
.filter-toolbar {
  margin-bottom: var(--space-6);
  padding: var(--space-4);
  border-radius: var(--radius-card);
}
.filter-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
  gap: var(--space-4);
}
.edit-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
  gap: var(--space-3);
  margin-top: var(--space-3);
}
```

- [ ] **Step 5: Validate JavaScript syntax**

Run:
```bash
node --check homework-submissions/app/static/admin-assignments.js
```
Expected: PASS with 0 syntax errors.

- [ ] **Step 6: Commit changes**

```bash
git add homework-submissions/app/templates/_admin_nav.html homework-submissions/app/templates/admin_assignments.html homework-submissions/app/static/admin-assignments.js homework-submissions/app/static/styles.css
git commit -m "feat(assignments): add teacher schedule admin interface and styles"
```

---

## Task 4: Student Interface & Contextual Topic Selection

**Files:**
- Modify: `homework-submissions/app/static/student.js`
- Modify: `homework-submissions/app/templates/index.html`
- Modify: `homework-submissions/tests/test_web.py`

**Interfaces:**
- Consumes:
  - JSON payload from `/unlock` or `/student/assignments`
- Produces:
  - Filtered topics in `#work` with formatted label: `ДД.ММ, пара N — <Тема>`.
  - Strictly preserves `<option value>` as the clean topic name.
  - Shows `#topic_hint` empty state if a discipline has no scheduled topics for the unlocked group.

- [ ] **Step 1: Write integration test for student topic select preservation**

Add to `homework-submissions/tests/test_web.py`:
```python
def test_student_topic_select_preserves_clean_topic_name_and_backward_compatibility(client_env, tmp_path):
    client, private_dir = client_env
    configure_group(private_dir)
    from app.assignments import AssignmentStore
    from app.catalog_store import create_catalog_store
    catalog = create_catalog_store(client.application.config).snapshot()
    subject = catalog.subjects[0]
    topic = next(t for t in catalog.topics if t.subject_id == subject.id)

    store = AssignmentStore(private_dir / "access.sqlite3")
    store.create("ИС 25/9-1П", subject.id, topic.id, "2026-10-14", 2)

    # Unlock via JSON
    unlock_res = client.post(
        "/unlock",
        data={"group": "ИС 25/9-1П", "password": "1234"},
        headers={"Accept": "application/json"},
    )
    assert unlock_res.status_code == 200
    assignment = unlock_res.json["assignments"][0]

    # Verify that clean topic name is submitted and accepted
    sub_data = {
        "group": "ИС 25/9-1П",
        "discipline": subject.name,
        "work": assignment["topic_name"],  # Clean topic name
        "student_name": "Иванов Иван Иванович",
        "file": (io.BytesIO(b"%PDF-1.4 test content"), "homework.pdf"),
    }
    submit_res = client.post("/submit", data=sub_data)
    assert submit_res.status_code == 201
```

- [ ] **Step 2: Run test to verify it passes backend check**

Run:
```bash
./homework-submissions/.venv/bin/pytest homework-submissions/tests/test_web.py -k "test_student_topic_select_preserves_clean_topic_name_and_backward_compatibility"
```
Expected: PASS

- [ ] **Step 3: Update `student.js` to parse unlock assignments and format topics**

In `homework-submissions/app/static/student.js`:
Update `unlockForm.addEventListener('submit', ...)`:
```javascript
let groupAssignments = [];

unlockForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const formData = new FormData(unlockForm);
  setBusy(unlockForm, true, 'Проверяем доступ…');
  setStatus(unlockStatus, 'working', 'Проверяем доступ…');

  try {
    const response = await fetch('/unlock', {
      method: 'POST',
      body: formData,
      headers: { 'Accept': 'application/json' },
    });
    if (!response.ok) throw new Error('denied');

    if (response.status === 200) {
      const data = await response.json();
      groupAssignments = data.assignments || [];
    }

    selectedGroup.value = formData.get('group');
    groupDisplay.textContent = selectedGroup.value;
    unlockForm.hidden = true;
    accessSummary.hidden = false;
    submitForm.hidden = false;
    showActiveStep(1);
    setStatus(unlockStatus, '', '');
    updateTopics();
    discipline.focus();
  } catch {
    setStatus(unlockStatus, 'error', 'Доступ к приёму работ закрыт или пароль неверен.');
  } finally {
    setBusy(unlockForm, false, 'Подтвердить доступ');
  }
});
```

Update `updateTopics()`:
```javascript
function updateTopics() {
  const selectedSubjectId = discipline.selectedOptions[0]?.dataset.subjectId;
  let availableTopics = [];

  if (groupAssignments.length > 0) {
    const assignedForSubject = groupAssignments.filter(
      (a) => a.subject_id === selectedSubjectId
    );
    availableTopics = assignedForSubject.map((a) => {
      const opt = document.createElement('option');
      opt.value = a.topic_name; // CRITICAL: strictly clean topic name for backward compatibility
      opt.dataset.subjectId = a.subject_id;
      opt.dataset.assignmentId = a.id;
      opt.textContent = a.label; // e.g. "14.10, пара 2 — Арифметика и коды чисел"
      return opt;
    });
  } else {
    availableTopics = topicOptions
      .filter((option) => option.dataset.subjectId === selectedSubjectId)
      .map((option) => option.cloneNode(true));
  }

  work.replaceChildren(topicPlaceholder.cloneNode(true), ...availableTopics);

  const hasTopics = availableTopics.length > 0;
  work.disabled = !hasTopics;
  topicHint.hidden = hasTopics;
  topicHint.textContent = hasTopics
    ? ''
    : 'Для вашей группы по данному предмету ещё нет назначенных занятий.';
  submitButton.disabled = !hasTopics;
}
```

- [ ] **Step 4: Verify syntax with `node --check`**

Run:
```bash
node --check homework-submissions/app/static/student.js
```
Expected: PASS with 0 syntax errors.

- [ ] **Step 5: Commit changes**

```bash
git add homework-submissions/app/static/student.js homework-submissions/tests/test_web.py
git commit -m "feat(assignments): dynamic pair-context topic select in student wizard"
```

---

## Task 5: End-to-End Regression Testing & Audit

**Files:**
- Test: `homework-submissions/tests/`

- [ ] **Step 1: Run full pytest suite**

Run:
```bash
./homework-submissions/.venv/bin/pytest homework-submissions/tests -v
```
Expected: All tests PASS (50+ tests passing, 0 failures, 0 regressions).

- [ ] **Step 2: Verify git status and clean working tree**

Run:
```bash
git status
```
Expected: No untracked cache artifacts or stray lock files.

- [ ] **Step 3: Commit any final test suite adjustments**

```bash
git commit --allow-empty -m "test(assignments): e2e verification and regression test suite audit"
```
