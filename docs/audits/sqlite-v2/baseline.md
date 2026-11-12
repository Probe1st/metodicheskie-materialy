# SQLite v2 Transition Audit: Baseline Environment & Repository Status

**Date:** 2026-11-03  
**Auditor ID:** `worker-env-matrix`  
**Repository Working Directory:** `/home/probe/education/homework-submissions`  
**Parent Repository:** `/home/probe/education`  

---

## 1. Repository & Git Status

### 1.1 Git Topology & Commit
- **Parent Repository Path:** `/home/probe/education` (symlink pointing to worktree `/home/probe/orca/workspaces/Разработка методического материала/Работа-со-связями-сущностей`)
- **Git Common Dir:** `/home/probe/reps/Разработка методического материала/.git`
- **Active Branch:** `Probe1st/Работа-со-связями-сущностей`
- **Latest Base Commit:** `69c4dd0fd56697bb0ded71fc0489c0d7435e2d81`
  - **Author:** `probe1st <svintsitskiy.leha@mail.ru>`
  - **Commit Date:** `Mon Oct 26 16:55:00 2026 +0500`
  - **Subject:** `feat(catalog): untrack catalog.json, add catalog.example.json and fallback support`

### 1.2 Uncommitted Working Tree Status
The working tree contains existing uncommitted changes representing the ongoing transition to SQLite v2.

#### Tracked Modified Files:
- `homework-submissions/.env.example`: Updated environment template for SQLite v2 paths.
- `homework-submissions/app/access.py`: Migrated from legacy SQLite access database to unified v2 database with `group_access` table.
- `homework-submissions/app/assignments.py`: Migrated assignments store to `class_assignments` in SQLite v2.
- `homework-submissions/app/catalog_store.py`: Updated interface for SQLite-backed catalog store.
- `homework-submissions/app/static/admin-assignments.js`: Admin assignment UI updates for v2 API.
- `homework-submissions/app/static/catalog-admin.js`: Catalog admin UI updates for v2 API.
- `homework-submissions/app/static/student.js`: Student submission UI updates for v2 API.
- `homework-submissions/app/static/styles.css`: Styling updates.
- `homework-submissions/app/storage.py`: Storage helpers, path safety validation, and pending uploads.
- `homework-submissions/app/templates/_admin_nav.html`: Admin navigation template.
- `homework-submissions/app/templates/admin_access.html`: Admin access control template.
- `homework-submissions/app/templates/admin_assignments.html`: Admin assignment management template.
- `homework-submissions/app/templates/admin_catalog.html`: Catalog management template.
- `homework-submissions/app/templates/index.html`: Student landing page.
- `homework-submissions/app/web.py`: Web endpoints adapted to unified SQLite v2 architecture.
- `homework-submissions/tests/conftest.py`: Test configuration and shared fixtures.
- `homework-submissions/tests/test_access.py`: Access store tests.
- `homework-submissions/tests/test_assignments.py`: Assignment store tests.
- `homework-submissions/tests/test_catalog.py`: Catalog tests.
- `homework-submissions/tests/test_catalog_store.py`: Catalog store tests.
- `homework-submissions/tests/test_manage.py`: Management script tests.
- `homework-submissions/tests/test_storage.py`: Storage tests.
- `homework-submissions/tests/test_web.py`: Flask web layer tests.
- `manage.sh`: Root management script updated with `migrate-v2` command.

#### Tracked Deleted Files (Legacy v1 cutover):
- `homework-submissions/app/json_catalog.py`: Old JSON catalog store (replaced by `sqlite_catalog.py`).
- `homework-submissions/tests/test_migrate_db.py`: Obsolete legacy migration test.
- `homework-submissions/tests/test_migrate_storage.py`: Obsolete legacy migration test.
- `homework-submissions/tools/migrate_db.py`: Legacy migration tool (replaced by `tools/migrate_v2.py`).
- `homework-submissions/tools/migrate_storage.py`: Legacy migration tool (replaced by `tools/migrate_v2.py`).

#### Untracked New Files (v2 implementation & tooling):
- `homework-submissions/app/database.py`: Core SQLite v2 connection manager, schema initialization, and validation.
- `homework-submissions/app/schema_v2.sql`: DDL for unified SQLite v2 schema.
- `homework-submissions/app/sqlite_catalog.py`: SQLite-backed implementation of `CatalogStore`.
- `homework-submissions/app/submissions.py`: Submissions store with atomic submission publishing.
- `homework-submissions/app/templates/admin_submissions.html`: Submissions review UI.
- `homework-submissions/tests/test_database.py`: Database engine tests.
- `homework-submissions/tests/test_migrate_v2.py`: v2 migration tool tests.
- `homework-submissions/tests/test_submissions.py`: Submission store tests.
- `homework-submissions/tests/test_submissions_web.py`: Submission web endpoints tests.
- `homework-submissions/tools/legacy_catalog.py`: Legacy catalog JSON parser (v1, v2, v3 formats).
- `homework-submissions/tools/migrate_v2.py`: Comprehensive offline migration tool (init, analyze, apply, verify, restore).
- `homework-submissions/tools/reconcile_storage.py`: Tool for auditing SQLite submission metadata against filesystem storage.

---

## 2. Host Operating System & Hardware

- **Kernel:** `Linux probe-frihxx 6.18.49-1-MANJARO #1 SMP PREEMPT_DYNAMIC Thu, 03 Sep 2026 23:21:05 +0000 x86_64 GNU/Linux`
- **Distribution:** `Manjaro Linux` (rolling release, `ID=manjaro`, `ID_LIKE=arch`)
- **C Library:** `glibc 2.44`
- **Compiler:** `GCC 16.1.1 20260728`
- **Architecture:** `x86_64` (64-bit)

---

## 3. Runtimes & Dependency Versions

| Component | Version | Details |
|---|---|---|
| **Python** | `3.14.7` | `main, Aug 10 2026, 07:46:56 [GCC 16.1.1 20260728]` |
| **sqlite3.sqlite_version** | `3.53.4` | Python runtime SQLite library binding |
| **libsqlite3** (system) | `3.53.4` | System library build `2026-07-24 19:02:57` (64-bit) |
| **Flask** | `3.1.3` | WSGI web framework |
| **Werkzeug** | `3.1.9` | WSGI utility library / password hashing |
| **pytest** | `9.2.0` | Test runner framework |
| **Playwright** | `1.64.1` | Browser automation framework |
| **Selenium** | `4.52.0` | Browser automation framework |

---

## 4. Browser Tools & Automation Capabilities

| Browser Engine | Availability | Path / Version | Status / Notes |
|---|---|---|---|
| **Playwright Chromium** | **Available** | `/home/probe/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome` (`156.0.8078.4`) | Verified working in headless execution mode |
| **Playwright Firefox** | Not installed in cache | `/home/probe/.cache/ms-playwright/firefox-1555/firefox/firefox` | Executable missing in cache |
| **Playwright WebKit** | Not installed in cache | `/home/probe/.cache/ms-playwright/webkit-2370/pw_run.sh` | Executable missing in cache |
| **System Firefox** | **Available** | `/usr/bin/firefox` (`Mozilla Firefox 158.0.1`) | Installed system-wide |
| **System Chromium/Chrome**| Not in system PATH | N/A | Available via Playwright Chromium cache |

---

## 5. Operational Constraints & Execution Safeguards

### 5.1 Listening Ports & Conflicting Processes
- **Port `5099` Collision:** Port `5099` is currently in use by a background test runner (`PID 995053`, command `/tmp/tasks_server.py`). Any live Flask smoke tests or HTTP server fixtures **MUST NOT** bind to `5099`. All test harnesses must allocate free ephemeral loopback ports (e.g. via `socket.bind(('', 0))`).
- **Gunicorn Instances:** Active gunicorn workers are running on ports `35277`, `40693`, `49957`, `50543` under user `probe`.
- **Active PID Files:** Production PID files and live server processes must never be signaled or killed during audit runs.

### 5.2 Filesystem & Mount Layout
- **Root & User Filesystem:** `btrfs` on `/dev/nvme0n1p2` (total 477 GB, 121 GB used, 355 GB available, 26% utilization).
- **In-Memory Mounts (`tmpfs`):**
  - `/tmp`: `7.5 GB` (`noatime, inode64`, 137 MB used). Provides high-performance, isolated I/O for synthetic databases and storage roots.
  - `/dev/shm`: `7.5 GB` (`usrquota`).
  - `/run`: `7.5 GB`.
  - `/run/user/1000`: `1.5 GB`.

### 5.3 User Privileges & Permissions
- **User:** `probe` (UID `1000`, GID `1000`).
- **Default umask:** `0022` (default permissions: directories `0755`, files `0644`).
- **Security Invariant:** Real `.private`, `catalog.json`, `storage`, and `.env` files are strictly read-only or excluded. All audit actions and synthetic data generation must occur within isolated temporary directories under `/tmp`.
