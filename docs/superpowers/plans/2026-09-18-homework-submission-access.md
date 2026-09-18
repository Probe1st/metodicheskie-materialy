# Homework Submission Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a master-password-protected admin interface that controls one password, open state, and automatic close time for each student group.

**Architecture:** `app.access` owns persistent SQLite state, password hashing, and access decisions. Flask routes use it to issue group-scoped student sessions and protect administrator operations; every upload revalidates the current group state. State lives below `.private/`, never in source control.

**Tech Stack:** Python 3.14, Flask 3.1, SQLite standard library, Werkzeug password hashing, pytest.

## Global Constraints

- Store mutable access state only in `homework-submissions/.private/access.sqlite3`.
- Exclude `.private/` from Git; create its directory as `0700` and database as `0600`.
- Use `ADMIN_PASSWORD` from the ignored environment file for administrator authentication; never store it in SQLite.
- Store student-group passwords only using Werkzeug password hashes.
- A group password applies to every discipline and work in that group.
- Upload authorization must use the group and access version in the Flask session, not the submitted `group` field.
- A closed group, expired deadline, or changed access version rejects existing student sessions with HTTP 403.
- Deadline input is server-local time; persist it in UTC.

---

### Task 1: Persistent group-access service

**Files:**
- Create: `homework-submissions/app/access.py`
- Create: `homework-submissions/tests/test_access.py`
- Modify: `homework-submissions/.gitignore`
- Modify: `homework-submissions/.env.example`

**Interfaces:**
- Produces `AccessStore(path: Path, groups: Iterable[str])`.
- Produces `AccessStore.configure(group: str, password: str | None, is_open: bool, closes_at: datetime | None) -> None`.
- Produces `AccessStore.authorize(group: str, password: str, now: datetime | None = None) -> int | None` returning the current version only when access is usable.
- Produces `AccessStore.permits(group: str, version: int, now: datetime | None = None) -> bool`.
- Produces `AccessStore.groups() -> tuple[GroupAccess, ...]`, where `GroupAccess` includes `group`, `is_open`, `closes_at`, and `version`, but never a password hash.

- [ ] **Step 1: Write failing persistence and expiry tests**

```python
def test_private_state_uses_owner_only_permissions(tmp_path):
    database = tmp_path / ".private" / "access.sqlite3"
    AccessStore(database, ["Группа 1"])

    assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.stat().st_mode) == 0o600


def test_group_access_requires_open_state_password_and_future_deadline(tmp_path):
    store = AccessStore(tmp_path / ".private" / "access.sqlite3", ["Группа 1"])
    deadline = datetime(2026, 9, 18, 15, tzinfo=UTC)
    store.configure("Группа 1", "1234", True, deadline)

    assert store.authorize("Группа 1", "wrong", datetime(2026, 9, 18, 14, tzinfo=UTC)) is None
    version = store.authorize("Группа 1", "1234", datetime(2026, 9, 18, 14, tzinfo=UTC))
    assert version is not None
    assert store.permits("Группа 1", version, datetime(2026, 9, 18, 14, tzinfo=UTC))
    assert not store.permits("Группа 1", version, deadline)


def test_reconfiguring_group_invalidates_previous_student_session(tmp_path):
    store = AccessStore(tmp_path / ".private" / "access.sqlite3", ["Группа 1"])
    store.configure("Группа 1", "1234", True, None)
    version = store.authorize("Группа 1", "1234")
    store.configure("Группа 1", "5678", True, None)

    assert version is not None
    assert not store.permits("Группа 1", version)
    assert store.authorize("Группа 1", "1234") is None
    assert store.authorize("Группа 1", "5678") is not None
```

- [ ] **Step 2: Run the new tests and verify they fail because `app.access` is missing**

Run: `.venv/bin/pytest tests/test_access.py -q`

Expected: collection error stating `ModuleNotFoundError: No module named 'app.access'`.

- [ ] **Step 3: Implement the minimal SQLite-backed access service**

Implement `GroupAccess` as a frozen slots dataclass. In `AccessStore.__init__`, create the parent directory with mode `0o700`, create the database file with mode `0o600`, and create a table keyed by `group_name` with `password_hash`, `is_open`, `closes_at_utc`, and integer `version`. Insert missing catalog groups as closed with no password. Use `generate_password_hash` to replace a supplied group password and `check_password_hash` to authenticate. Convert all comparison times to aware UTC datetimes. Treat an absent password, closed state, and a deadline at or before `now` as denied. Increment `version` on every `configure` call.

Update `.gitignore` with `.private/`. Replace `SUBMISSION_PASSWORD` in `.env.example` with:

```dotenv
MAX_UPLOAD_GB=1
ADMIN_PASSWORD=replace-with-a-long-admin-password
SECRET_KEY=replace-with-a-long-random-secret
PRIVATE_DATA_DIR=.private
```

- [ ] **Step 4: Run the focused access tests and verify they pass**

Run: `.venv/bin/pytest tests/test_access.py -q`

Expected: `3 passed`.

- [ ] **Step 5: Commit the isolated access service**

```bash
git add homework-submissions/app/access.py homework-submissions/tests/test_access.py homework-submissions/.gitignore homework-submissions/.env.example
git commit -m "feat: persist group submission access"
```

### Task 2: Enforce group-scoped student authorization

**Files:**
- Modify: `homework-submissions/app/web.py`
- Modify: `homework-submissions/app/templates/index.html`
- Modify: `homework-submissions/tests/test_web.py`

**Interfaces:**
- Consumes `AccessStore.authorize()` and `AccessStore.permits()` from Task 1.
- `POST /unlock` consumes `group` and `password`; it sets `session["student_group"]` and `session["student_access_version"]` only on success.
- `POST /submit` accepts an upload only when submitted group equals `session["student_group"]` and `AccessStore.permits()` returns true.

- [ ] **Step 1: Replace the old password tests with failing observable group-access tests**

```python
@pytest.fixture
def client(tmp_path: Path):
    private_dir = tmp_path / ".private"
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "secret",
        "ADMIN_PASSWORD": "admin-secret",
        "PRIVATE_DATA_DIR": private_dir,
        "STORAGE_ROOT": tmp_path / "uploads",
        "CATALOG_PATH": Path(__file__).resolve().parents[1] / "catalog.json",
        "MAX_UPLOAD_GB": 1,
    })
    return app.test_client(), private_dir


def configure_group(private_dir, group="ИС 25/9-1П", password="1234", is_open=True):
    store = AccessStore(private_dir / "access.sqlite3", ["ИС 25/9-1П", "ИС 25/9-2П"])
    store.configure(group, password, is_open, None)


def test_student_can_upload_only_after_unlocking_the_selected_open_group(client, tmp_path):
    http, private_dir = client
    configure_group(private_dir)
    assert http.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"}).status_code == 204
    response = http.post("/submit", data=valid_submission())
    assert response.status_code == 201
    assert list(tmp_path.rglob("Иванов Иван Иванович 1.pdf"))


def test_upload_is_denied_when_the_student_session_group_differs(client):
    http, private_dir = client
    configure_group(private_dir)
    http.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
    payload = valid_submission()
    payload["group"] = "ИС 25/9-2П"
    assert http.post("/submit", data=payload).status_code == 403


def test_reconfiguring_group_invalidates_unlocked_student(client):
    http, private_dir = client
    configure_group(private_dir)
    http.post("/unlock", data={"group": "ИС 25/9-1П", "password": "1234"})
    configure_group(private_dir, password="5678", is_open=False)
    assert http.post("/submit", data=valid_submission()).status_code == 403
```

- [ ] **Step 2: Run the focused web tests and verify they fail because group access is not yet wired**

Run: `.venv/bin/pytest tests/test_web.py -q`

Expected: failures because `/unlock` still accepts no `group` and `/submit` still expects the obsolete `unlocked` session field.

- [ ] **Step 3: Wire student routes to `AccessStore`**

Configure `PRIVATE_DATA_DIR` from `os.getenv("PRIVATE_DATA_DIR", ".private")`; create `AccessStore(private_dir / "access.sqlite3", catalog().groups)` after application configuration. Replace `SUBMISSION_PASSWORD` and `session["unlocked"]`. `/unlock` must reject unknown groups and must set only `student_group` plus `student_access_version` after a successful `authorize`. `/submit` must reject missing session values, a submitted group unequal to the session group, and a failed `permits` call before accepting the upload.

Replace the unlock form with a group `<select name="group">` and label it `Пароль группы`. In browser JavaScript submit its group and password to `/unlock`; after a 403, display `Доступ к приёму работ закрыт или пароль неверен.`. Keep the current upload fields and catalog-driven work list unchanged.

- [ ] **Step 4: Run the focused web tests and verify they pass**

Run: `.venv/bin/pytest tests/test_web.py -q`

Expected: all web tests pass.

- [ ] **Step 5: Commit group-scoped student enforcement**

```bash
git add homework-submissions/app/web.py homework-submissions/app/templates/index.html homework-submissions/tests/test_web.py
git commit -m "feat: enforce group submission sessions"
```

### Task 3: Master-password-protected administrative interface

**Files:**
- Modify: `homework-submissions/app/web.py`
- Create: `homework-submissions/app/templates/admin.html`
- Modify: `homework-submissions/tests/test_web.py`

**Interfaces:**
- `GET /admin` shows login when `session["admin"]` is absent; otherwise shows one group row per current catalog group.
- `POST /admin/login` compares `ADMIN_PASSWORD` using `hmac.compare_digest` and sets `session["admin"] = True` on success.
- `POST /admin/groups` requires admin session, accepts `group`, optional `password`, `is_open`, and optional server-local `closes_at`, and updates `AccessStore`.
- `POST /admin/logout` removes admin state and returns to the login page.

- [ ] **Step 1: Write failing administrator-flow tests**

```python
def test_admin_login_is_required_to_change_group_access(client):
    assert client.post("/admin/groups", data={"group": "ИС 25/9-1П", "is_open": "1"}).status_code == 403
    assert client.post("/admin/login", data={"password": "wrong"}).status_code == 403
    assert client.post("/admin/login", data={"password": "admin-secret"}).status_code == 204


def test_admin_can_open_group_set_deadline_and_student_access_expires(client):
    assert client.post("/admin/login", data={"password": "admin-secret"}).status_code == 204
    response = client.post("/admin/groups", data={
        "group": "ИС 25/9-1П",
        "password": "1234",
        "is_open": "1",
        "closes_at": "2026-09-18T15:00",
    })
    assert response.status_code == 204
    page = client.get("/admin").get_data(as_text=True)
    assert "2026-09-18T15:00" in page


def test_admin_page_lists_every_catalog_group(client):
    client.post("/admin/login", data={"password": "admin-secret"})
    page = client.get("/admin").get_data(as_text=True)
    assert "ИС 25/9-1П" in page
    assert "ИС 25/9-2П" in page
```

Set test configuration `ADMIN_PASSWORD="admin-secret"` and a tmp-path `PRIVATE_DATA_DIR` so tests never create real state.

- [ ] **Step 2: Run the focused administrator tests and verify they fail because routes are absent**

Run: `.venv/bin/pytest tests/test_web.py -q`

Expected: failures with HTTP 404 for `/admin/login` and `/admin/groups`.

- [ ] **Step 3: Implement administrative routes and form**

`/admin/login` uses `hmac.compare_digest` against non-empty configured `ADMIN_PASSWORD`; on success set `session["admin"]`. A small `require_admin()` helper aborts 403 otherwise. `/admin/groups` rejects unknown catalog groups and a request that tries to open a group without either a pre-existing password or a supplied nonempty password. Parse `closes_at` by `datetime.fromisoformat`; attach the server local timezone when naive; convert to UTC. An empty deadline clears it. `admin.html` must have a master-password login form and an authenticated table where every group has its own update form: password input, Open checkbox, `datetime-local` deadline input, and save button. Never render password hashes or plaintext passwords.

- [ ] **Step 4: Run focused administrator and full test suites**

Run: `.venv/bin/pytest tests/test_web.py -q && .venv/bin/pytest -q`

Expected: all tests pass.

- [ ] **Step 5: Perform an HTTP smoke test**

Run a throwaway Python invocation that creates the Flask app with a temporary private directory, logs in with `ADMIN_PASSWORD`, opens `ИС 25/9-1П` with password `1234`, unlocks the same group, posts a valid multipart upload, and asserts `201`. It must remove the temporary directory after the process exits.

- [ ] **Step 6: Commit the administrative interface**

```bash
git add homework-submissions/app/web.py homework-submissions/app/templates/admin.html homework-submissions/tests/test_web.py
git commit -m "feat: add homework access administration"
```

