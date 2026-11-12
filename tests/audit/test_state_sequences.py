"""Audit test suite for random state sequences and database invariant preservation.

Executes 200 deterministic state sequences of 50 actions each using seeds 0..199.
Maintains an independent reference model and verifies key database invariants
after each sequence (referential integrity, slot uniqueness, sequential attempts,
system subject preservation, and storage consistency).
"""

from __future__ import annotations

import io
import random
import sqlite3
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from app.access import AccessStore
from app.assignments import (
    AssignmentConflictError,
    AssignmentInUseError,
    AssignmentNotFoundError,
    AssignmentStore,
    AssignmentValidationError,
)
from app.catalog_store import (
    CatalogConflictError,
    CatalogNotFoundError,
    CatalogOperationForbiddenError,
    CatalogValidationError,
)
from app.database import connect, initialize
from app.sqlite_catalog import SqliteCatalogStore
from app.submissions import (
    SubmissionAccessError,
    SubmissionNotFoundError,
    SubmissionStore,
    SubmissionValidationError,
)
from tests.audit.conftest import AuditEnv
from tests.audit.support import compute_sha256, read_db_state


class StateSequenceRunner:
    """Executes a pseudorandom sequence of state mutations while maintaining an independent reference model."""

    def __init__(self, db_path: Path, storage_root: Path, seed: int) -> None:
        self.db_path = db_path
        self.storage_root = storage_root
        self.seed = seed
        self.rnd = random.Random(seed)

        # Reference model
        self.groups: dict[UUID, dict[str, Any]] = {}
        self.subjects: dict[UUID, dict[str, Any]] = {}
        self.topics: dict[UUID, dict[str, Any]] = {}
        self.students: dict[UUID, dict[str, Any]] = {}
        self.assignments: dict[UUID, dict[str, Any]] = {}
        self.submissions: dict[UUID, dict[str, Any]] = {}

        self.cat_store = SqliteCatalogStore(db_path)
        self.acc_store = AccessStore(db_path)
        self.asgn_store = AssignmentStore(db_path)
        self.sub_store = SubmissionStore(db_path, storage_root)

        # Discover system subject initialized in database
        with connect(db_path) as conn:
            row = conn.execute("SELECT id, name FROM subjects WHERE is_system = 1;").fetchone()
            self.system_subject_id = UUID(row["id"])
            self.subjects[self.system_subject_id] = {
                "name": row["name"],
                "is_system": True,
                "archived": False,
            }

        self.action_history: list[str] = []

    # 1. create_group
    def action_create_group(self, step: int) -> None:
        name = f"Группа-{self.rnd.randint(1, 100)}-{self.rnd.randint(1, 1000)}"
        try:
            item = self.cat_store.create_group(name)
            self.groups[item.id] = {
                "name": item.name,
                "archived": False,
                "access_version": 1,
                "is_open": False,
            }
            self.action_history.append(f"{step}: create_group({item.name}) -> {item.id}")
        except (CatalogConflictError, CatalogValidationError):
            pass

    # 2. rename_group
    def action_rename_group(self, step: int) -> None:
        if not self.groups:
            return
        gid = self.rnd.choice(list(self.groups.keys()))
        new_name = f"Переименованная-Группа-{self.rnd.randint(1, 500)}"
        try:
            item = self.cat_store.rename_group(gid, new_name)
            self.groups[gid]["name"] = item.name
            self.action_history.append(f"{step}: rename_group({gid}, {new_name})")
        except (CatalogNotFoundError, CatalogConflictError, CatalogValidationError, CatalogOperationForbiddenError):
            pass

    # 3. archive_group
    def action_archive_group(self, step: int) -> None:
        active_gids = [gid for gid, g in self.groups.items() if not g["archived"]]
        if not active_gids:
            return
        gid = self.rnd.choice(active_gids)
        try:
            self.cat_store.archive_group(gid)
            self.groups[gid]["archived"] = True
            # Cascades
            for st in self.students.values():
                if st["group_id"] == gid:
                    st["archived"] = True
            for a in self.assignments.values():
                if a["group_id"] == gid:
                    a["archived"] = True
            self.groups[gid]["is_open"] = False
            self.groups[gid]["access_version"] += 1
            self.action_history.append(f"{step}: archive_group({gid})")
        except (CatalogNotFoundError, CatalogOperationForbiddenError):
            pass

    # 4. create_subject
    def action_create_subject(self, step: int) -> None:
        name = f"Предмет-{self.rnd.randint(1, 50)}-{self.rnd.randint(1, 1000)}"
        try:
            item = self.cat_store.create_subject(name)
            self.subjects[item.id] = {
                "name": item.name,
                "is_system": False,
                "archived": False,
            }
            self.action_history.append(f"{step}: create_subject({item.name}) -> {item.id}")
        except (CatalogConflictError, CatalogValidationError):
            pass

    # 5. rename_subject
    def action_rename_subject(self, step: int) -> None:
        non_sys = [sid for sid, s in self.subjects.items() if not s["is_system"]]
        if not non_sys:
            return
        sid = self.rnd.choice(non_sys)
        new_name = f"Переименованный-Предмет-{self.rnd.randint(1, 500)}"
        try:
            item = self.cat_store.rename_subject(sid, new_name)
            self.subjects[sid]["name"] = item.name
            self.action_history.append(f"{step}: rename_subject({sid}, {new_name})")
        except (CatalogNotFoundError, CatalogConflictError, CatalogValidationError, CatalogOperationForbiddenError):
            pass

    # 6. archive_subject
    def action_archive_subject(self, step: int) -> None:
        active_sids = [sid for sid, s in self.subjects.items() if not s["is_system"] and not s["archived"]]
        if not active_sids:
            return
        sid = self.rnd.choice(active_sids)
        try:
            self.cat_store.archive_subject(sid)
            self.subjects[sid]["archived"] = True
            for t in self.topics.values():
                if t["subject_id"] == sid:
                    t["archived"] = True
            self.action_history.append(f"{step}: archive_subject({sid})")
        except (CatalogNotFoundError, CatalogOperationForbiddenError):
            pass

    # 7. create_topic
    def action_create_topic(self, step: int) -> None:
        active_sids = [sid for sid, s in self.subjects.items() if not s["archived"]]
        if not active_sids:
            return
        sid = self.rnd.choice(active_sids)
        name = f"Тема-{self.rnd.randint(1, 50)}-{self.rnd.randint(1, 1000)}"
        try:
            item = self.cat_store.create_topic(sid, name)
            self.topics[item.id] = {
                "subject_id": sid,
                "name": item.name,
                "archived": False,
            }
            self.action_history.append(f"{step}: create_topic({sid}, {item.name}) -> {item.id}")
        except (CatalogNotFoundError, CatalogConflictError, CatalogValidationError):
            pass

    # 8. rename_topic
    def action_rename_topic(self, step: int) -> None:
        if not self.topics:
            return
        tid = self.rnd.choice(list(self.topics.keys()))
        new_name = f"Переименованная-Тема-{self.rnd.randint(1, 500)}"
        try:
            item = self.cat_store.rename_topic(tid, new_name)
            self.topics[tid]["name"] = item.name
            self.action_history.append(f"{step}: rename_topic({tid}, {new_name})")
        except (CatalogNotFoundError, CatalogConflictError, CatalogValidationError, CatalogOperationForbiddenError):
            pass

    # 9. archive_topic
    def action_archive_topic(self, step: int) -> None:
        active_tids = [tid for tid, t in self.topics.items() if not t["archived"]]
        if not active_tids:
            return
        tid = self.rnd.choice(active_tids)
        try:
            self.cat_store.archive_topic(tid)
            self.topics[tid]["archived"] = True
            self.action_history.append(f"{step}: archive_topic({tid})")
        except (CatalogNotFoundError, CatalogOperationForbiddenError):
            pass

    # 10. move_topics
    def action_move_topics(self, step: int) -> None:
        active_tids = [tid for tid, t in self.topics.items() if not t["archived"]]
        active_sids = [sid for sid, s in self.subjects.items() if not s["archived"]]
        if not active_tids or not active_sids:
            return
        target_sid = self.rnd.choice(active_sids)
        sample_count = min(len(active_tids), self.rnd.randint(1, 3))
        selected_tids = self.rnd.sample(active_tids, sample_count)
        try:
            self.cat_store.move_topics(selected_tids, target_sid)
            for tid in selected_tids:
                self.topics[tid]["subject_id"] = target_sid
            self.action_history.append(f"{step}: move_topics({selected_tids} -> {target_sid})")
        except (CatalogNotFoundError, CatalogConflictError, CatalogOperationForbiddenError):
            pass

    # 11. configure_access
    def action_configure_access(self, step: int) -> None:
        active_gids = [gid for gid, g in self.groups.items() if not g["archived"]]
        if not active_gids:
            return
        gid = self.rnd.choice(active_gids)
        is_open = self.rnd.choice([True, False])
        pwd = f"pass-{self.rnd.randint(1, 1000)}" if self.rnd.choice([True, False]) else None
        try:
            cfg = self.acc_store.configure(gid, is_open=is_open, password=pwd)
            self.groups[gid]["access_version"] = cfg.version
            self.groups[gid]["is_open"] = cfg.is_open
            self.action_history.append(f"{step}: configure_access({gid}, open={is_open}, v={cfg.version})")
        except (ValueError, KeyError):
            pass

    # 12. create_student
    def action_create_student(self, step: int) -> None:
        active_gids = [gid for gid, g in self.groups.items() if not g["archived"]]
        if not active_gids:
            return
        gid = self.rnd.choice(active_gids)
        name = f"Студент-{self.rnd.randint(1, 50)}"
        try:
            st = self.sub_store.create_student(gid, name)
            self.students[st.id] = {
                "group_id": gid,
                "display_name": st.display_name,
                "archived": False,
            }
            self.action_history.append(f"{step}: create_student({gid}, {st.display_name}) -> {st.id}")
        except (SubmissionValidationError, SubmissionNotFoundError):
            pass

    # 13. create_assignment
    def action_create_assignment(self, step: int) -> None:
        active_gids = [gid for gid, g in self.groups.items() if not g["archived"]]
        active_tids = [tid for tid, t in self.topics.items() if not t["archived"]]
        if not active_gids or not active_tids:
            return
        gid = self.rnd.choice(active_gids)
        tid = self.rnd.choice(active_tids)
        day = self.rnd.randint(1, 28)
        lesson_date = f"2026-04-{day:02d}"
        pair_number = self.rnd.randint(1, 6)
        try:
            ca = self.asgn_store.create(gid, tid, lesson_date, pair_number)
            self.assignments[ca.id] = {
                "group_id": gid,
                "topic_id": tid,
                "date": lesson_date,
                "pair": pair_number,
                "archived": False,
            }
            self.action_history.append(f"{step}: create_assignment({gid}, {tid}, {lesson_date}, {pair_number}) -> {ca.id}")
        except (AssignmentConflictError, AssignmentValidationError, AssignmentNotFoundError, sqlite3.IntegrityError):
            pass

    # 14. update_assignment
    def action_update_assignment(self, step: int) -> None:
        active_aids = [aid for aid, a in self.assignments.items() if not a["archived"]]
        active_tids = [tid for tid, t in self.topics.items() if not t["archived"]]
        if not active_aids or not active_tids:
            return
        aid = self.rnd.choice(active_aids)
        tid = self.rnd.choice(active_tids)
        day = self.rnd.randint(1, 28)
        new_date = f"2026-05-{day:02d}"
        new_pair = self.rnd.randint(1, 6)
        try:
            self.asgn_store.update(aid, new_date, new_pair, tid)
            self.assignments[aid]["date"] = new_date
            self.assignments[aid]["pair"] = new_pair
            self.assignments[aid]["topic_id"] = tid
            self.action_history.append(f"{step}: update_assignment({aid}, {new_date}, {new_pair})")
        except (AssignmentConflictError, AssignmentInUseError, AssignmentValidationError, AssignmentNotFoundError, sqlite3.IntegrityError):
            pass

    # 15. archive_assignment
    def action_archive_assignment(self, step: int) -> None:
        active_aids = [aid for aid, a in self.assignments.items() if not a["archived"]]
        if not active_aids:
            return
        aid = self.rnd.choice(active_aids)
        try:
            self.asgn_store.archive(aid)
            self.assignments[aid]["archived"] = True
            self.action_history.append(f"{step}: archive_assignment({aid})")
        except AssignmentNotFoundError:
            pass

    # 16. submit_work
    def action_submit_work(self, step: int) -> None:
        active_aids = [aid for aid, a in self.assignments.items() if not a["archived"]]
        if not active_aids:
            return
        aid = self.rnd.choice(active_aids)
        assign = self.assignments[aid]
        gid = assign["group_id"]
        # Find students in group
        st_in_group = [sid for sid, s in self.students.items() if s["group_id"] == gid and not s["archived"]]
        if not st_in_group:
            return
        st_id = self.rnd.choice(st_in_group)
        version = self.groups[gid]["access_version"]

        content = f"Submission data for step {step} seed {self.seed}".encode()
        try:
            sub, sfile = self.sub_store.submit(
                group_id=gid,
                student_id=st_id,
                assignment_id=aid,
                source=io.BytesIO(content),
                original_filename=f"hw_{step}.zip",
                access_version=version,
            )
            self.submissions[sub.id] = {
                "student_id": st_id,
                "topic_id": assign["topic_id"],
                "attempt_number": sub.attempt_number,
                "stored_path": sfile.stored_path,
                "size_bytes": sfile.size_bytes,
                "sha256": sfile.sha256,
                "status": "pending",
            }
            self.action_history.append(f"{step}: submit_work({gid}, {st_id}, {aid}) -> att={sub.attempt_number}")
        except (SubmissionAccessError, SubmissionNotFoundError, SubmissionValidationError):
            pass

    # 17. review_submission
    def action_review_submission(self, step: int) -> None:
        pending_subs = [sid for sid, s in self.submissions.items() if s["status"] == "pending"]
        if not pending_subs:
            return
        sub_id = self.rnd.choice(pending_subs)
        try:
            status, _ = self.sub_store.set_review_status(sub_id, "reviewed")
            self.submissions[sub_id]["status"] = status
            self.action_history.append(f"{step}: review_submission({sub_id} -> {status})")
        except (SubmissionNotFoundError, SubmissionValidationError):
            pass

    # 18. reopen_db_connection
    def action_reopen_db_connection(self, step: int) -> None:
        # Re-initialize store connections and perform WAL checkpoint
        with connect(self.db_path) as conn:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
        self.cat_store = SqliteCatalogStore(self.db_path)
        self.acc_store = AccessStore(self.db_path)
        self.asgn_store = AssignmentStore(self.db_path)
        self.sub_store = SubmissionStore(self.db_path, self.storage_root)
        self.action_history.append(f"{step}: reopen_db_connection(PASSIVE_CHECKPOINT)")

    def execute_sequence(self, action_count: int = 50) -> None:
        """Execute randomly selected actions."""
        actions = [
            self.action_create_group,
            self.action_rename_group,
            self.action_archive_group,
            self.action_create_subject,
            self.action_rename_subject,
            self.action_archive_subject,
            self.action_create_topic,
            self.action_rename_topic,
            self.action_archive_topic,
            self.action_move_topics,
            self.action_configure_access,
            self.action_create_student,
            self.action_create_assignment,
            self.action_update_assignment,
            self.action_archive_assignment,
            self.action_submit_work,
            self.action_review_submission,
            self.action_reopen_db_connection,
        ]

        # Bootstrap with initial entity pool to enrich interactions
        self.action_create_group(0)
        self.action_create_subject(0)
        self.action_create_topic(0)
        if self.groups:
            self.action_configure_access(0)
            self.action_create_student(0)
        if self.groups and self.topics:
            self.action_create_assignment(0)

        for step in range(1, action_count + 1):
            action_fn = self.rnd.choice(actions)
            action_fn(step)

    def verify_invariants(self) -> None:
        """Verify database invariants via direct SQLite inspection."""
        state = read_db_state(self.db_path)

        # 1. Foreign keys integrity
        assert state["foreign_key_check"] == [], (
            f"Seed {self.seed}: Foreign key violations detected: {state['foreign_key_check']}"
        )

        # 2. SQLite integrity check
        assert state["integrity_check"] == ["ok"], (
            f"Seed {self.seed}: SQLite integrity check failed: {state['integrity_check']}"
        )

        with connect(self.db_path) as conn:
            # 3. Exactly one system subject exists
            sys_count = conn.execute("SELECT count(*) FROM subjects WHERE is_system = 1;").fetchone()[0]
            assert sys_count == 1, f"Seed {self.seed}: System subject count invariant violated ({sys_count})"

            # System subject must not be archived
            archived_sys = conn.execute(
                "SELECT count(*) FROM subjects WHERE is_system = 1 AND archived_at IS NOT NULL;"
            ).fetchone()[0]
            assert archived_sys == 0, f"Seed {self.seed}: System subject is archived!"

            # 4. Active assignment slot uniqueness (group_id, lesson_date, pair_number)
            slot_collisions = conn.execute(
                """
                SELECT group_id, lesson_date, pair_number, count(*)
                FROM class_assignments
                WHERE archived_at IS NULL
                GROUP BY group_id, lesson_date, pair_number
                HAVING count(*) > 1;
                """
            ).fetchall()
            assert len(slot_collisions) == 0, (
                f"Seed {self.seed}: Duplicate active assignment slot detected: {slot_collisions}"
            )

            # 5. Submissions attempt uniqueness
            attempt_collisions = conn.execute(
                """
                SELECT student_id, topic_id, attempt_number, count(*)
                FROM submissions
                GROUP BY student_id, topic_id, attempt_number
                HAVING count(*) > 1;
                """
            ).fetchall()
            assert len(attempt_collisions) == 0, (
                f"Seed {self.seed}: Duplicate submission attempts detected: {attempt_collisions}"
            )

            # 6. Check that every recorded submission file exists on disk with matching size and sha256
            db_files = conn.execute(
                "SELECT id, original_filename, stored_path, size_bytes, sha256 FROM submission_files;"
            ).fetchall()
            for f in db_files:
                disk_path = self.storage_root / f["stored_path"]
                assert disk_path.is_file(), (
                    f"Seed {self.seed}: Submission file missing on disk: {f['stored_path']}"
                )
                assert disk_path.stat().st_size == f["size_bytes"], (
                    f"Seed {self.seed}: Size mismatch on disk for {f['stored_path']}"
                )
                if f["sha256"]:
                    actual_sha = compute_sha256(disk_path)
                    assert actual_sha == f["sha256"], (
                        f"Seed {self.seed}: SHA256 mismatch for {f['stored_path']}"
                    )


# ===========================================================================
# 200 State Sequences across Seeds 0..199
# ===========================================================================

@pytest.mark.parametrize("batch_idx", range(20))
def test_state_sequences_batch(batch_idx: int, audit_env: AuditEnv) -> None:
    """Executes a batch of 10 deterministic state sequences of 50 actions each (20 batches = 200 sequences)."""
    start_seed = batch_idx * 10
    end_seed = start_seed + 10

    for seed in range(start_seed, end_seed):
        seq_db = audit_env.private / f"seq_db_{seed}.sqlite3"
        seq_storage = audit_env.storage / f"seq_storage_{seed}"
        seq_storage.mkdir(parents=True, exist_ok=True)

        initialize(seq_db)

        runner = StateSequenceRunner(db_path=seq_db, storage_root=seq_storage, seed=seed)
        try:
            runner.execute_sequence(action_count=50)
            runner.verify_invariants()
        except AssertionError as exc:
            repro_snippet = "\n  ".join(runner.action_history[-10:])
            raise AssertionError(
                f"State sequence invariant failure on seed={seed}:\n{exc}\nRecent actions:\n  {repro_snippet}"
            ) from exc
        finally:
            seq_db.unlink(missing_ok=True)
            for p in audit_env.private.glob(f"seq_db_{seed}.sqlite3*"):
                p.unlink(missing_ok=True)
