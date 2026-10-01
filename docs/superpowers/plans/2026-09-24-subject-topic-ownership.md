# Subject-Owned Topics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every topic belongs to one subject, with a practical admin workflow for assigning legacy topics individually or in bulk and student choices constrained to the selected subject.

**Architecture:** Add `Topic(subject_id)` and a distinguished `unassigned_subject_id` to the catalog port. JSON v1 and v2 migrate atomically into v3; all topic and subject mutations validate ownership under the provider lock. Flask stays provider-neutral, while the existing topic admin page gains filters, counts, single-item editing, and atomic bulk movement; the student page updates its topic select on subject change.

**Tech Stack:** Python 3.14, Flask 3, stdlib JSON/UUID/flock, pytest, Jinja, vanilla JavaScript/CSS.

## Global Constraints

- Keep `CATALOG_PROVIDER=json`; unsupported providers still fail at startup. Preserve the `CatalogStore` boundary.
- Topics have one existing subject and case-insensitive name uniqueness only within that subject. Groups and access stay unchanged.
- System subject `Без предмета` has a stable UUID, cannot be renamed/deleted, and is the fallback for v2 global topics and subject deletion.
- Topic movement and subject deletion are all-or-nothing under the JSON lock; name conflicts reject the operation and retain every entry.
- Never delete or relocate previously uploaded files. Retain `discipline` and `work` form field names and UUID-based admin mutation routes.
- Work in the existing linked worktree, never in `master`; use `homework-submissions/.venv/bin/python -m pytest` and no frontend dependencies.

---

## Task 1: Domain, v3 migration, and atomic ownership operations

**Files:** `homework-submissions/app/catalog_store.py`, `homework-submissions/app/json_catalog.py`, `homework-submissions/tests/test_catalog_store.py`, `homework-submissions/tests/test_catalog.py`.

**Interfaces:** `Topic(id: UUID, name: str, subject_id: UUID)`; `Catalog(..., topics: tuple[Topic,...], unassigned_subject_id: UUID)`; `CatalogStore.create_topic(name, subject_id)`, `update_topic(id, name, subject_id)`, `delete_topic(id)`, `move_topics(ids, subject_id)`; existing subject deletion moves children atomically.

- [ ] Add tests to `test_catalog_store.py`: v1 maps topics per discipline (same name across disciplines yields distinct IDs); v2 preserves topic UUID while assigning every topic to the system subject; v3 round-trip preserves IDs and subjects; `contains` rejects mismatched subject/topic; same title in different subjects succeeds; duplicate within one fails; bulk move succeeds for multiple UUIDs and fails atomically when destination or selected names collide; deleting a subject moves all children or fails unchanged on collision; system subject cannot be renamed/deleted; malformed subject references reject startup. Update the former `version: 3` malformed fixture to use a different unsupported version.
- [ ] Run `./.venv/bin/python -m pytest -q tests/test_catalog_store.py tests/test_catalog.py` from `homework-submissions`; confirm failures identify missing ownership/version 3 behavior.
- [ ] Add immutable `Topic`, `Catalog.unassigned_subject_id`, and subject-aware `contains`. Replace old `rename_topic` port operation with `update_topic`; no alias remains.
- [ ] Upgrade JSON decode: validate v3 subject references, system subject identity/name, and per-subject casefold uniqueness; map v2 topics to an existing or newly created `Без предмета`, preserving UUID; map legacy disciplines directly to owned topics, retaining duplicate titles across subjects; persist migrations only inside flock and fsync atomic replacement. Distinguish no-op movement (no write) from invalid subject or missing topic.
- [ ] Implement `create_topic`, `update_topic`, `move_topics`, and subject deletion against one locked document. Before modifying the document, collect the entire destination name set and all selected topic IDs; reject any duplicate or missing item, then mutate and save once. `CatalogNameConflict` should carry conflicting topic names for safe HTTP errors.
- [ ] Run focused provider/catalog tests green, then the complete suite to identify broken old topic contract tests. Do not re-pin the old global-topic behavior.

## Task 2: Server contract and student selection

**Files:** `homework-submissions/app/web.py`, `homework-submissions/app/templates/index.html`, `homework-submissions/app/static/student.js`, `homework-submissions/tests/test_web.py`.

**Interfaces:** `POST /admin/topics` requires `name` and `subject_id`; `POST /admin/topics/<uuid>/update` requires both fields; `POST /admin/topics/move` accepts repeated `topic_ids` and `subject_id`; `POST /admin/topics/<uuid>/delete` remains. All require admin authentication and return `400/404` for invalid IDs, invalid subjects or name conflicts, with JSON `{error: 'topic_name_conflict', names: [...]}` for topic conflicts. Subject delete returns the same structured conflict when fallback names clash.

- [ ] Replace the old parametrized topic CRUD case in `tests/test_web.py` with topic-specific tests: unauthorized and valid create/update/move/delete; invalid/missing subject; casefold duplicate within subject; two subjects sharing a title; atomic bulk conflict; protected system subject; subject deletion moves children or fails unchanged. Change the old independent-topic submission test into a rejection test and verify an owned topic succeeds while files from earlier submissions persist after topic movement/deletion.
- [ ] Run `./.venv/bin/python -m pytest -q tests/test_web.py` to observe expected failures before route changes.
- [ ] In `web.py`, dispatch topic create and update to new provider methods, add bulk move route, and handle topic conflicts as structured safe responses. Render subject choices, system subject ID and counts on admin pages. Keep the group access lock and use one catalog snapshot per submission check.
- [ ] Render subject IDs and topics in `index.html`; populate and reset `select[name=work]` in `student.js` on subject change. No topics means disabled/empty topic selector with an explanatory hint; the server still rejects a mismatched or stale pair.
- [ ] Run web and full suites green; run `node --check` on changed JavaScript.

## Task 3: Convenient admin mapping UI and browser proof

**Files:** `homework-submissions/app/templates/admin_catalog.html`, `homework-submissions/app/static/catalog-admin.js`, `homework-submissions/app/static/styles.css`, `homework-submissions/tests/test_web.py`, `docs/superpowers/specs/2026-09-24-catalog-crud-design.md` (mark the earlier independent-topic constraint superseded by the new specification).

**Interfaces:** Topic rows expose UUID and subject UUID, an accessible edit form with name/subject, a selectable checkbox, and per-row delete. The page exposes filter/search, counts including `Без предмета`, a destination select and `Перенести выбранные`. Existing group/subject CRUD remains; the protected fallback subject has no edit/delete actions.

- [ ] Render subject selectors for topic creation/editing and a bulk move form on the topics page, using existing card styles and `data-` attributes; leave group/subject templates' interactions intact. Distinguish system subject and expose count/empty-state copy.
- [ ] Extend `catalog-admin.js` to handle topic creation/update/move, filter/search and counts without unsafe HTML insertion. On successful move update each row's subject display and counts and clear selection; on `topic_name_conflict` show the conflicting names and preserve selection. Reset only the topic-specific controls after success.
- [ ] Update CSS for compact filters, selection actions and narrow screens without horizontal scroll. Explain migration and how to distribute topics in the existing design documentation; no new runtime dependencies.
- [ ] Run `./.venv/bin/python -m pytest -q` and `node --check app/static/catalog-admin.js && node --check app/static/student.js` from `homework-submissions`.
- [ ] Start an isolated Flask smoke server with a temporary copy of `catalog.json`. In a real browser, log in; create a topic under a chosen subject; filter the unassigned set; move one and several selected topics; reproduce a conflict and confirm the entire batch remains unchanged; delete a subject and verify its topics move to fallback; unlock the student form, switch subjects, observe only the corresponding topics, upload with one valid topic, and verify 375px has no horizontal overflow. Close the tab, stop the server and remove its temporary files.

## Final verification

- [ ] Verify `git diff --check`, final test and JS command outputs, updated documentation, and no generated JSON/SQLite/uploads in the worktree. Preserve the worktree for the existing open PR unless asked to publish or clean up.
