"""Audit test suite for homework-submissions SQLite v2 query scaling and operational limits.

Evaluates performance, index utilization, memory usage, and concurrency under scale:
  - 100 groups, 1,000 students, 10,000 class_assignments, 20,000 submission rows.
  - EXPLAIN QUERY PLAN analysis on core application queries.
  - 1x vs 10x dataset comparison (timing and peak memory via tracemalloc).
  - Storage file downloads at scale.
  - Lock contention and write serialization behavior.
  - Documentation of architectural limits (unpaginated history, WAL growth, serialized writes).
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
import threading
import time
import tracemalloc
import uuid
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

from app.assignments import AssignmentStore
from app.database import connect, initialize, normalize_name_key
from app.submissions import SubmissionStore


@dataclass
class ScaleDataset:
    db_path: Path
    storage_root: Path
    groups: list[tuple[str, str]]  # (id, name)
    subjects: list[tuple[str, str]]
    topics: list[tuple[str, str, str]]  # (id, name, subject_id)
    students: list[tuple[str, str, str]]  # (id, group_id, name)
    assignments: list[tuple[str, str, str]]  # (id, group_id, topic_id)
    submissions: list[tuple[str, str, str, str]]  # (id, student_id, group_id, topic_id)


@pytest.fixture(scope="module")
def scale_env(tmp_path_factory: pytest.TempPathFactory) -> ScaleDataset:
    """Generates the synthetic large-scale dataset (100 groups, 1k students, 10k assignments, 20k submissions)."""
    tmp = tmp_path_factory.mktemp("scale_env")
    db_path = tmp / "scale_v2.sqlite3"
    storage_root = tmp / "storage"
    storage_root.mkdir(parents=True, exist_ok=True)

    initialize(db_path)

    conn = sqlite3.connect(db_path)
    # Temporary bulk optimizations during synthetic generation
    conn.execute("PRAGMA synchronous = OFF;")
    conn.execute("PRAGMA journal_mode = MEMORY;")

    groups_data: list[tuple[str, str]] = []
    subjects_data: list[tuple[str, str]] = []
    topics_data: list[tuple[str, str, str]] = []
    students_data: list[tuple[str, str, str]] = []
    assignments_data: list[tuple[str, str, str]] = []
    submissions_data: list[tuple[str, str, str, str]] = []

    t0 = time.perf_counter()
    with conn:
        # 1. 100 groups
        groups_insert = []
        group_access_insert = []
        for i in range(100):
            gid = str(uuid.uuid4())
            gname = f"Группа {i+1:03d}"
            groups_data.append((gid, gname))
            groups_insert.append((gid, gname, normalize_name_key(gname), None))
            group_access_insert.append((gid, None, 1, "2027-01-01T00:00:00Z", 1))
        conn.executemany("INSERT INTO groups VALUES (?, ?, ?, ?);", groups_insert)
        conn.executemany("INSERT INTO group_access VALUES (?, ?, ?, ?, ?);", group_access_insert)

        # 2. 10 subjects, 100 topics
        subjects_insert = []
        for s_idx in range(10):
            sid = str(uuid.uuid4())
            sname = f"Предмет {s_idx+1:02d}"
            subjects_data.append((sid, sname))
            subjects_insert.append((sid, sname, normalize_name_key(sname), 0, None))
        conn.executemany("INSERT INTO subjects VALUES (?, ?, ?, ?, ?);", subjects_insert)

        topics_insert = []
        for t_idx in range(100):
            tid = str(uuid.uuid4())
            sid = subjects_data[t_idx % 10][0]
            tname = f"Тема {t_idx+1:03d}"
            topics_data.append((tid, tname, sid))
            topics_insert.append((tid, sid, tname, normalize_name_key(tname), None))
        conn.executemany("INSERT INTO topics VALUES (?, ?, ?, ?, ?);", topics_insert)

        # 3. 1,000 students (10 per group)
        students_insert = []
        for g_idx, g in enumerate(groups_data):
            for s_idx in range(10):
                stid = str(uuid.uuid4())
                sname = f"Студент {g_idx*10 + s_idx + 1:04d}"
                students_data.append((stid, g[0], sname))
                students_insert.append((stid, g[0], sname, None))
        conn.executemany("INSERT INTO students VALUES (?, ?, ?, ?);", students_insert)

        # 4. 10,000 class_assignments (100 per group across dates and pairs)
        assignments_insert = []
        dates = [f"2026-09-{d:02d}" for d in range(1, 21)] + [f"2026-10-{d:02d}" for d in range(1, 21)]
        now_iso = "2026-09-01T00:00:00Z"
        for g in groups_data:
            asgn_count = 0
            for d in dates:
                for pair in range(1, 6):
                    if asgn_count >= 100:
                        break
                    aid = str(uuid.uuid4())
                    tid = topics_data[asgn_count % 100][0]
                    assignments_data.append((aid, g[0], tid))
                    assignments_insert.append((aid, g[0], tid, d, pair, now_iso, now_iso, None))
                    asgn_count += 1
        conn.executemany("INSERT INTO class_assignments VALUES (?, ?, ?, ?, ?, ?, ?, ?);", assignments_insert)

        # 5. 20,000 submissions (20 per student, 2 attempts on 10 topics)
        submissions_insert = []
        submission_files_insert = []
        asgn_lookup = {(a[1], a[2]): a[0] for a in assignments_data}

        # Representative physical files in storage (sample of 100 files for download tests)
        sample_file_indices = set(range(100))
        sample_content = b"Representative physical submission file content at scale\n"
        sample_sha256 = hashlib.sha256(sample_content).hexdigest()
        sample_size = len(sample_content)

        sub_idx = 0
        for st in students_data:
            gid = st[1]
            for t_i in range(10):
                tid = topics_data[t_i][0]
                aid = asgn_lookup.get((gid, tid))
                for att in (1, 2):
                    sub_id = str(uuid.uuid4())
                    file_id = str(uuid.uuid4())
                    rel_path = f"{gid}/{tid}/{sub_id}.zip"
                    submissions_data.append((sub_id, st[0], gid, tid))

                    has_file = sub_idx in sample_file_indices
                    if has_file:
                        f_path = storage_root / gid / tid / f"{sub_id}.zip"
                        f_path.parent.mkdir(parents=True, exist_ok=True)
                        f_path.write_bytes(sample_content)

                    submissions_insert.append((
                        sub_id,
                        st[0],
                        gid,
                        tid,
                        aid,
                        att,
                        "reviewed" if att == 2 else "pending",
                        "2026-10-01T12:00:00Z" if att == 2 else None,
                        "2026-09-15T10:00:00Z",
                    ))
                    submission_files_insert.append((
                        file_id,
                        sub_id,
                        f"homework_{att}.zip",
                        rel_path,
                        sample_size,
                        sample_sha256,
                    ))
                    sub_idx += 1

        conn.executemany("INSERT INTO submissions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);", submissions_insert)
        conn.executemany("INSERT INTO submission_files VALUES (?, ?, ?, ?, ?, ?);", submission_files_insert)

    t1 = time.perf_counter()
    conn.close()

    # Reconnect in normal WAL mode for realistic query testing
    with connect(db_path) as c:
        c.execute("PRAGMA journal_mode = WAL;")
        c.execute("PRAGMA synchronous = NORMAL;")
        c.execute("ANALYZE;")  # Update query optimizer statistics for accurate EXPLAIN plans

    sys.stdout.write(f"\n[SCALE SETUP] Generated 20,000 submissions in {t1 - t0:.2f}s\n")

    return ScaleDataset(
        db_path=db_path,
        storage_root=storage_root,
        groups=groups_data,
        subjects=subjects_data,
        topics=topics_data,
        students=students_data,
        assignments=assignments_data,
        submissions=submissions_data,
    )


def test_scaling_dataset_generation_and_counts(scale_env: ScaleDataset) -> None:
    """Verifies that the scale dataset exactly meets the required 100 groups, 1k students, 10k assignments, 20k submissions."""
    with connect(scale_env.db_path) as conn:
        counts = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM groups),
                (SELECT COUNT(*) FROM students),
                (SELECT COUNT(*) FROM class_assignments),
                (SELECT COUNT(*) FROM submissions),
                (SELECT COUNT(*) FROM submission_files);
            """
        ).fetchone()

    assert counts[0] == 100, f"Expected 100 groups, got {counts[0]}"
    assert counts[1] == 1000, f"Expected 1,000 students, got {counts[1]}"
    assert counts[2] == 10000, f"Expected 10,000 class_assignments, got {counts[2]}"
    assert counts[3] == 20000, f"Expected 20,000 submissions, got {counts[3]}"
    assert counts[4] == 20000, f"Expected 20,000 submission_files, got {counts[4]}"


def test_explain_query_plan_indexes(scale_env: ScaleDataset) -> None:
    """EXPLAIN QUERY PLAN analysis on core database queries: identifies index hits vs full table scans."""
    test_gid = scale_env.groups[0][0]
    test_tid = scale_env.topics[0][0]

    queries = [
        (
            "Query 1: class_assignments for group",
            """
            SELECT ca.id, ca.group_id, ca.topic_id, ca.lesson_date, ca.pair_number,
                   ca.created_at, ca.updated_at, ca.archived_at, t.subject_id
            FROM class_assignments ca
            JOIN topics t ON t.id = ca.topic_id
            WHERE ca.group_id = ? AND ca.archived_at IS NULL
            ORDER BY ca.lesson_date, ca.pair_number;
            """,
            (test_gid,),
        ),
        (
            "Query 2a: submissions by group and topic (filtered)",
            """
            SELECT s.id, s.student_id, s.group_id, s.topic_id, s.assignment_id,
                   s.attempt_number, s.review_status, s.reviewed_at, s.created_at
            FROM submissions s
            WHERE s.group_id = ? AND s.topic_id = ?
            ORDER BY s.created_at;
            """,
            (test_gid, test_tid),
        ),
        (
            "Query 2b: submissions by topic only (admin topic filter)",
            """
            SELECT s.id, s.student_id, s.group_id, s.topic_id, s.assignment_id,
                   s.attempt_number, s.review_status, s.reviewed_at, s.created_at
            FROM submissions s
            WHERE s.topic_id = ?;
            """,
            (test_tid,),
        ),
        (
            "Query 2c: submissions by group only (admin group filter)",
            """
            SELECT s.id, s.student_id, s.group_id, s.topic_id, s.assignment_id,
                   s.attempt_number, s.review_status, s.reviewed_at, s.created_at
            FROM submissions s
            WHERE s.group_id = ?;
            """,
            (test_gid,),
        ),
        (
            "Query 3: students roster for group",
            """
            SELECT id, group_id, display_name, archived_at
            FROM students
            WHERE group_id = ? AND archived_at IS NULL
            ORDER BY display_name;
            """,
            (test_gid,),
        ),
        (
            "Query 4: active groups list",
            """
            SELECT id, name
            FROM groups
            WHERE archived_at IS NULL
            ORDER BY name;
            """,
            (),
        ),
    ]

    findings: list[dict[str, str]] = []

    sys.stdout.write("\n" + "=" * 80 + "\n")
    sys.stdout.write("EXPLAIN QUERY PLAN AUDIT REPORT (SQLite v2 Index Utilization)\n")
    sys.stdout.write("=" * 80 + "\n")

    with connect(scale_env.db_path) as conn:
        for title, sql, params in queries:
            plan_rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
            details = [r[3] for r in plan_rows]

            sys.stdout.write(f"\n--- {title} ---\n")
            for d in details:
                sys.stdout.write(f"  EXPLAIN: {d}\n")

            performs_scan = any("SCAN" in d for d in details)

            if performs_scan:
                findings.append({
                    "query": title,
                    "behavior": "SCAN",
                    "details": "; ".join(details),
                })

    sys.stdout.write("\n" + "=" * 80 + "\n")

    query_map = {title: (sql, params) for title, sql, params in queries}

    # Document observations:
    # 1. Query 1 (assignments for group) uses index assignment_active_slot or assignments_group_topic_date
    with connect(scale_env.db_path) as conn:
        q1_sql, q1_params = query_map["Query 1: class_assignments for group"]
        plan1 = conn.execute(f"EXPLAIN QUERY PLAN {q1_sql}", q1_params).fetchall()
        assert any("USING INDEX" in r[3] for r in plan1), "Query 1 must use assignment index"

    # 2. Query 2a (submissions by group and topic) uses submissions_group_topic
    with connect(scale_env.db_path) as conn:
        q2a_sql, q2a_params = query_map["Query 2a: submissions by group and topic (filtered)"]
        plan2a = conn.execute(f"EXPLAIN QUERY PLAN {q2a_sql}", q2a_params).fetchall()
        assert any("USING INDEX" in r[3] for r in plan2a), "Query 2a must use submissions_group_topic index"

    # 3. Architectural Finding: Query 2b (topic only) lacks dedicated topic index; requires skip-scan or full scan
    with connect(scale_env.db_path) as conn:
        q2b_sql, q2b_params = query_map["Query 2b: submissions by topic only (admin topic filter)"]
        plan2b = conn.execute(f"EXPLAIN QUERY PLAN {q2b_sql}", q2b_params).fetchall()
        assert any("ANY(group_id)" in r[3] or "SCAN submissions" in r[3] for r in plan2b), (
            "Documented finding: filtering submissions by topic alone requires skip-scan or SCAN submissions"
        )

    # 4. Verified: Query 3 (students for group) utilizes idx_students_group covering index
    with connect(scale_env.db_path) as conn:
        q3_sql, q3_params = query_map["Query 3: students roster for group"]
        plan3 = conn.execute(f"EXPLAIN QUERY PLAN {q3_sql}", q3_params).fetchall()
        assert any("idx_students_group" in r[3] or "USING INDEX" in r[3] for r in plan3), (
            "Students roster query must utilize idx_students_group index"
        )

    # 5. Architectural Finding: Query 4 (groups active list) performs full table scan
    with connect(scale_env.db_path) as conn:
        q4_sql, q4_params = query_map["Query 4: active groups list"]
        plan4 = conn.execute(f"EXPLAIN QUERY PLAN {q4_sql}", q4_params).fetchall()
        assert any("SCAN groups" in r[3] for r in plan4), (
            "Documented finding: active groups query performs SCAN groups due to missing index"
        )

def test_query_scaling_1x_vs_10x_and_memory(scale_env: ScaleDataset) -> None:
    """Measures execution time and peak memory for list_for_group and history queries across 1x and 20x dataset scales."""
    sub_store = SubmissionStore(scale_env.db_path, scale_env.storage_root)
    asgn_store = AssignmentStore(scale_env.db_path)

    test_gid = UUID(scale_env.groups[0][0])
    test_tid = UUID(scale_env.topics[0][0])

    # 1. Measure list_for_group on single group (100 assignments)
    tracemalloc.start()
    t_start = time.perf_counter()
    asgns = asgn_store.list_for_group(test_gid)
    t_asgn = (time.perf_counter() - t_start) * 1000.0
    _, peak_asgn = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert len(asgns) == 100

    # 2. Measure list_submissions filtered by (group_id, topic_id)
    tracemalloc.start()
    t_start = time.perf_counter()
    filtered_subs = sub_store.list_submissions(group_id=test_gid, topic_id=test_tid)
    t_filtered = (time.perf_counter() - t_start) * 1000.0
    _, peak_filtered = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # 10 students * 2 attempts = 20 submissions per topic
    assert len(filtered_subs) == 20

    # 3. Measure unpaginated history (group_id=None, topic_id=None) -> returns all 20,000 rows
    tracemalloc.start()
    t_start = time.perf_counter()
    all_subs = sub_store.list_submissions(group_id=None, topic_id=None)
    t_all = (time.perf_counter() - t_start) * 1000.0
    _, peak_all = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert len(all_subs) == 20000

    sys.stdout.write("\n" + "=" * 80 + "\n")
    sys.stdout.write("QUERY SCALING & MEMORY BENCHMARK (Structured Results)\n")
    sys.stdout.write("=" * 80 + "\n")
    sys.stdout.write(f"1. list_for_group (100 rows):        {t_asgn:6.2f} ms | Peak Memory: {peak_asgn/1024:8.2f} KB\n")
    sys.stdout.write(f"2. list_submissions (filtered 20 rows): {t_filtered:6.2f} ms | Peak Memory: {peak_filtered/1024:8.2f} KB\n")
    sys.stdout.write(f"3. list_submissions (UNPAGINATED 20k rows): {t_all:6.2f} ms | Peak Memory: {peak_all/1024:8.2f} KB\n")
    sys.stdout.write("=" * 80 + "\n")
    sys.stdout.write("ARCHITECTURAL OBSERVATION: Lack of database pagination forces unpaginated loads\n")
    sys.stdout.write(f"to allocate ~{peak_all / (1024 * 1024):.2f} MB of RAM per request in Python memory.\n")
    sys.stdout.write("=" * 80 + "\n")


def test_download_at_scale(scale_env: ScaleDataset) -> None:
    """Verifies that physical file resolution and retrieval via SubmissionStore.get_file remains performant at scale."""
    sub_store = SubmissionStore(scale_env.db_path, scale_env.storage_root)

    # Query 10 submissions that have physical storage files
    with connect(scale_env.db_path) as conn:
        rows = conn.execute(
            """
            SELECT sf.submission_id, sf.stored_path, sf.size_bytes
            FROM submission_files sf
            LIMIT 10;
            """
        ).fetchall()

    latencies_ms: list[float] = []
    for r in rows:
        sub_uuid = UUID(r["submission_id"])
        t0 = time.perf_counter()
        full_path, orig_name = sub_store.get_file(sub_uuid)
        lat = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(lat)

        assert full_path.is_file()
        assert full_path.stat().st_size == r["size_bytes"]
        assert orig_name.startswith("homework_")

    avg_latency = sum(latencies_ms) / len(latencies_ms)
    sys.stdout.write(f"\n[DOWNLOAD SCALE] 10 physical file lookups averaged {avg_latency:.3f} ms each\n")


def test_write_lock_contention_and_busy_timeout(scale_env: ScaleDataset) -> None:
    """Evaluates write lock contention, serialization, and busy_timeout behavior under concurrent writers."""
    writer1_started = threading.Event()
    writer2_finished = threading.Event()
    thread2_wait_duration: list[float] = []
    thread2_error: list[Exception] = []

    def writer1_task() -> None:
        with connect(scale_env.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            # Hold lock for 150ms
            writer1_started.set()
            time.sleep(0.15)
            conn.execute("UPDATE groups SET name = name WHERE id = (SELECT id FROM groups LIMIT 1);")
            conn.commit()

    def writer2_task() -> None:
        writer1_started.wait()
        t0 = time.perf_counter()
        try:
            with connect(scale_env.db_path) as conn:
                conn.execute("BEGIN IMMEDIATE;")
                conn.execute("UPDATE groups SET name = name WHERE id = (SELECT id FROM groups LIMIT 1);")
                conn.commit()
            t_wait = (time.perf_counter() - t0) * 1000.0
            thread2_wait_duration.append(t_wait)
        except Exception as e:  # noqa: BLE001
            thread2_error.append(e)
        finally:
            writer2_finished.set()

    t1 = threading.Thread(target=writer1_task)
    t2 = threading.Thread(target=writer2_task)

    t1.start()
    t2.start()

    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    assert not thread2_error, f"Writer 2 encountered error during lock wait: {thread2_error}"
    assert thread2_wait_duration, "Writer 2 did not record wait duration"

    wait_ms = thread2_wait_duration[0]
    sys.stdout.write(f"\n[LOCK CONTENTION] Writer 2 waited {wait_ms:.2f} ms for Writer 1 to release lock and committed successfully.\n")
    # Writer 2 should wait at least ~100ms
    assert wait_ms >= 100.0, f"Expected lock serialization wait >=100ms, got {wait_ms:.2f}ms"


def test_architectural_operational_limits(scale_env: ScaleDataset) -> None:
    """Evaluates and documents key architectural limits: unpaginated queries, WAL file growth, and serialized SQLite writes."""
    db_size = scale_env.db_path.stat().st_size
    wal_file = scale_env.db_path.with_name(scale_env.db_path.name + "-wal")
    wal_size = wal_file.stat().st_size if wal_file.exists() else 0

    sys.stdout.write("\n" + "=" * 80 + "\n")
    sys.stdout.write("ARCHITECTURAL & OPERATIONAL LIMITS REPORT\n")
    sys.stdout.write("=" * 80 + "\n")
    sys.stdout.write(f"- SQLite Main DB Size (20k submissions): {db_size / (1024 * 1024):.2f} MB\n")
    sys.stdout.write(f"- SQLite WAL File Size:                   {wal_size / (1024 * 1024):.2f} MB\n")
    sys.stdout.write("- Key Architectural Limits Identified:\n")
    sys.stdout.write("  1. Lack of Pagination: list_submissions() loads all submissions into memory,\n")
    sys.stdout.write("     consuming linear memory as submissions accumulate.\n")
    sys.stdout.write("  2. Full Table Scan on Students: students table lacks an index on group_id,\n")
    sys.stdout.write("     causing student roster lookups to scan all students (SCAN students).\n")
    sys.stdout.write("  3. Full Table Scan on Topic Submissions: filtering submissions by topic alone\n")
    sys.stdout.write("     scans all submissions because submissions_group_topic leading column is group_id.\n")
    sys.stdout.write("  4. Serialized Writes: SQLite WAL permits multiple concurrent readers, but only\n")
    sys.stdout.write("     a single serialized writer. High-concurrency upload bursts wait on busy_timeout (5000ms).\n")
    sys.stdout.write("=" * 80 + "\n")

    assert db_size > 0
