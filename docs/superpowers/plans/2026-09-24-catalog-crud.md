# Catalog CRUD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add protected admin CRUD pages for groups, subjects and independent topics, plus a separate group-access page and provider-neutral catalog persistence.

**Architecture:** A `CatalogStore` interface isolates catalog reads and CRUD from Flask. `JsonCatalogStore` implements it with a versioned JSON document and process-wide file locks; Flask gets one provider from `create_catalog_store`. Existing group access remains SQLite and follows group rename/delete; browser pages share a card/list UI.

**Tech Stack:** Python 3.11+, Flask 3, stdlib JSON, UUID, sqlite3, fcntl, Jinja, vanilla JS/CSS, pytest.

## Global Constraints

- `CATALOG_PROVIDER=json` is the only implemented provider; selecting `sqlite` or `postgresql` fails clearly at startup. No fake implementations.
- No group↔subject or subject↔topic relationship. Student form displays all topics regardless of subject.
- Keep old HTTP upload/authentication contracts; preserve `storage/` contents on any catalog deletion.
- All admin mutations require session authentication. Cross-process JSON writes must serialize via `flock`, atomic replacement and file fsync.
- UUID identity survives rename; removing and recreating a group with the same name must not revive its old sessions.
- Keep existing styling and mobile layout. No new frontend dependencies.

---

## Task 1: Catalog domain and provider

**Files:** Create `homework-submissions/app/catalog_store.py`, `homework-submissions/app/json_catalog.py`; remove obsolete `homework-submissions/app/catalog.py`; modify `homework-submissions/tests/test_catalog.py`; leave tracked `homework-submissions/catalog.json` in its original format until the deployed application's first migration.

**Interfaces:** `CatalogItem(id: UUID, name: str)`; `Catalog(groups: tuple[CatalogItem,...], subjects: ..., topics: ...)` with `contains(group, subject, topic)`. `CatalogStore` Protocol offers `snapshot`, `list_groups`, `create_group`, `rename_group`, `delete_group` and analogous subject/topic methods. `CatalogNameConflict` and `CatalogItemNotFound` are port errors. `create_catalog_store(config)` selects `JsonCatalogStore(Path(config['CATALOG_PATH']))` for `json` and raises `ValueError` for unsupported provider names.

- [ ] Write failing `test_catalog.py` cases using tmp files: legacy `groups`/`disciplines` migration preserves names and deduplicates casefolded topics; for every kind create/rename/delete updates `snapshot`, UUID stays fixed on rename; duplicate name fails case-insensitively; missing UUID fails; repeated read is stable; concurrent mutations from separate processes never drop an item; simulated replacement failure preserves the old file. Test accepts an unlinked topic with any subject via `contains`.
- [ ] Run `/tmp/catalog-venv/bin/pytest tests/test_catalog.py -q`; expect migration/CRUD tests fail before implementation.
- [ ] Implement `catalog_store.py`: dataclasses, Protocol, error classes and factory. `Catalog.contains` checks independent membership by `name.casefold()`.
- [ ] Implement `json_catalog.py`: decode legacy JSON or v2; validate non-empty casefold-unique names, preserve insertion order; under `flock` on a separate `.lock` file reread current data, apply one mutation, write temp in same directory and fsync, `os.replace`, fsync directory. Always release lock and clean temporary file on errors. Reads may load the replaced JSON without an exclusive lock. Migration also uses this lock and rechecks version inside it.
- [ ] Run focused tests green, then application suite. Commit provider, tests and migration code.

## Task 2: Access consistency and student flow

**Files:** Modify `homework-submissions/app/access.py`, `homework-submissions/app/web.py`, `homework-submissions/app/templates/index.html`, `homework-submissions/app/static/student.js`, `homework-submissions/tests/test_access.py`, `homework-submissions/tests/test_web.py`.

**Interfaces:** `AccessStore.rename_group(old_name, new_name)` raises on missing/duplicate, retains password and deadline and increments version; `AccessStore.delete_group(name)` deletes its row. `web.py` captures group UUID and name on unlock, checks current `CatalogItem.id` on submit, and calls `CatalogStore.snapshot()` rather than `load_catalog()`. The student template renders one independent `<select>` for each entity; template retains `group`, `discipline`, `work` form names.

- [ ] Write failing regression tests: existing group password works after rename but old name/session is rejected; deletion and recreation of same name does not revive session; submission accepts any existing independent topic and rejects removed topic/subject; upload storage remains present after catalog deletion. Existing endpoint tests must still work using legacy tmp JSON migration.
- [ ] Run focused access/web tests red; implement `rename_group` and `delete_group` in SQLite store, UUID session check, provider wiring and flat topic selection. Keep the access-write sequence safe: catalog rename/delete and access update happen in request; if access update fails, return an error instead of a false success, with enough log context for repair.
- [ ] Run focused tests and suite green; commit access cutover and tests.

## Task 3: Protected admin endpoints and separated pages

**Files:** Modify `homework-submissions/app/web.py`, `homework-submissions/app/templates/admin.html`, create `homework-submissions/app/templates/admin_access.html`, `homework-submissions/app/templates/admin_catalog.html`, `homework-submissions/app/static/catalog-admin.js`, modify `homework-submissions/app/static/styles.css`, `homework-submissions/tests/test_web.py`.

**Interfaces:** `/admin` overview, `/admin/access` access settings, `/admin/groups`, `/admin/subjects`, `/admin/topics` list and create. Item operations `POST /admin/{kind}/{uuid}/rename` and `POST /admin/{kind}/{uuid}/delete`, with kind in `groups|subjects|topics`. Existing access writes `POST /admin/groups` conflict with CRUD create: move access updates to `POST /admin/access` and migrate all old callers/tests; all admin routes require session (except `/admin` login page). New CRUD create returns a successful response readable by JS; conflict -> 400, unknown -> 404, malformed UUID -> 400.

- [ ] Write failing HTTP tests for admin-required status on every page/mutation, create/list/rename/delete for three types, duplicate names and unknown UUID, group access preservation on rename and removal on delete, including rejected stale sessions. Test rendered pages via actual endpoints only where behavior is observed by browser; no brittle HTML string tests.
- [ ] Run focused tests red. Implement routes using a single shared kind-dispatch table of bound provider methods. Handle model exceptions with 400/404. Keep `/admin/login` and `/admin/logout` unchanged. Implement shared sidebar navigation, active section styling, card rows, accessible forms and `catalog-admin.js` for row-local success/errors and named delete confirmation; avoid exposing server tracebacks.
- [ ] Run focused tests green. In a real browser, log in and navigate `/admin`→`/admin/access`→three CRUD pages; create, rename and delete one entity of each kind, exercise 400 and confirmation cancellation, ensure desktop and 375px widths have no horizontal overflow.
- [ ] Run whole suite once, remove smoke data, update `homework-submissions/.env.example` with `CATALOG_PROVIDER=json` and document data migration/startup in the existing design doc. Commit routes/templates/assets/tests/docs.

## Final verification

- [ ] Run `pytest -q` inside `homework-submissions`; inspect output for failures.
- [ ] Verify browser student unlock and upload with a topic created in admin, group rename invalidation, and all four separated admin sections; close browser tabs and stop test server.
- [ ] Check that only intended files changed and no local `.private`, uploads, secrets or smoke fixtures are committed. Do not push after the reported SSH disconnect without diagnosing transport separately.
