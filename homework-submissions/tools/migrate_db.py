#!/usr/bin/env python3
"""
Database migration and historical timetable seeding tool for homework submissions.

Ensures class_assignments schema exists and seeds historical timetable (26 lessons).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import unicodedata
import uuid
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HistoricalLesson:
    group_name: str
    subject_name: str
    topic_name: str
    lesson_date: str
    pair_number: int


HISTORICAL_LESSONS: list[HistoricalLesson] = [
    # Group "ИС 25/9-3В", "Архитектура аппаратных средств":
    HistoricalLesson("ИС 25/9-3В", "Архитектура аппаратных средств", "Виды памяти и принцип работы. Основные характеристики", "2026-09-21", 1),
    HistoricalLesson("ИС 25/9-3В", "Архитектура аппаратных средств", "Единицы количества информации. Коды чисел", "2026-09-28", 1),
    HistoricalLesson("ИС 25/9-3В", "Архитектура аппаратных средств", "Модули памяти. Логическое распределение памяти", "2026-10-05", 1),
    HistoricalLesson("ИС 25/9-3В", "Архитектура аппаратных средств", "Закон Мура. Основные характеристики процессоров", "2026-10-12", 1),
    HistoricalLesson("ИС 25/9-3В", "Архитектура аппаратных средств", "Энергонезависимая память. Иерархия памяти", "2026-10-17", 1),

    # Group "РУПО 26/11", "Архитектура аппаратных средств":
    HistoricalLesson("РУПО 26/11", "Архитектура аппаратных средств", "Арифметика и коды чисел", "2026-09-30", 1),
    HistoricalLesson("РУПО 26/11", "Архитектура аппаратных средств", "Единицы количества информации. Коды чисел", "2026-10-02", 1),
    HistoricalLesson("РУПО 26/11", "Архитектура аппаратных средств", "Логические основы ЭВМ, элементы и узлы", "2026-10-04", 1),
    HistoricalLesson("РУПО 26/11", "Архитектура аппаратных средств", "Виды памяти и принцип работы. Основные характеристики", "2026-10-16", 1),
    HistoricalLesson("РУПО 26/11", "Архитектура аппаратных средств", "Модули памяти. Логическое распределение памяти", "2026-10-17", 1),

    # Group "ИС 25/9-1П", "Архитектура аппаратных средств":
    HistoricalLesson("ИС 25/9-1П", "Архитектура аппаратных средств", "Арифметика и коды чисел", "2026-09-25", 1),

    # Group "ИС 25/9-2П", "Архитектура аппаратных средств":
    HistoricalLesson("ИС 25/9-2П", "Архитектура аппаратных средств", "Логические основы ЭВМ, элементы и узлы", "2026-09-28", 1),
    HistoricalLesson("ИС 25/9-2П", "Архитектура аппаратных средств", "Закон Мура. Основные характеристики процессоров", "2026-10-13", 1),

    # Group "ИС 25/9-4В":
    HistoricalLesson("ИС 25/9-4В", "Архитектура аппаратных средств", "Энергонезависимая память. Иерархия памяти", "2026-10-15", 1),
    HistoricalLesson("ИС 25/9-4В", "Архитектура аппаратных средств", "Закон Мура. Основные характеристики процессоров", "2026-10-15", 2),
    HistoricalLesson("ИС 25/9-4В", "Архитектура аппаратных средств", "Виды памяти и принцип работы. Основные характеристики", "2026-10-17", 1),
    HistoricalLesson("ИС 25/9-4В", "Основы проектирования баз данных", "Задание ключей и создание проекта базы данных", "2026-10-17", 2),

    # Group "ИС 24/9-3П":
    HistoricalLesson("ИС 24/9-3П", "Обеспечение качества функционирования компьютерных систем", 'Задачка "Количество гласных"', "2026-09-26", 1),
    HistoricalLesson("ИС 24/9-3П", "Обеспечение качества функционирования компьютерных систем", 'Задачка "Square Digits Sequence"', "2026-10-03", 1),
    HistoricalLesson("ИС 24/9-3П", "Разработка программных модулей", 'Задачка "Tic-Tac-Toe Checker"', "2026-10-10", 1),
    HistoricalLesson("ИС 24/9-3П", "Разработка программных модулей", 'Задачка "Расчет стоимости поездки"', "2026-09-26", 2),
    HistoricalLesson("ИС 24/9-3П", "Разработка программных модулей", 'Задачка "Sort the odd"', "2026-09-29", 1),
    HistoricalLesson("ИС 24/9-3П", "Разработка программных модулей", "Music theory - Minor or Major chords", "2026-09-29", 2),
    HistoricalLesson("ИС 24/9-3П", "Разработка программных модулей", 'Задачка "No ifs no buts"', "2026-10-02", 1),
    HistoricalLesson("ИС 24/9-3П", "Разработка программных модулей", 'Задачка "Number Of Occurrences"', "2026-10-03", 2),
    HistoricalLesson("ИС 24/9-3П", "Разработка программных модулей", 'Задачка "Transpose two strings in an array"', "2026-10-03", 3),
]


SCHEMA_SQL = """
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
"""


def normalize_name(s: str) -> str:
    return unicodedata.normalize("NFC", s).strip()


def get_default_catalog_path() -> Path:
    candidates = [
        Path("homework-submissions/catalog.json"),
        Path("catalog.json"),
        Path(__file__).resolve().parent.parent / "catalog.json",
    ]
    for c in candidates:
        if c.is_file():
            return c
    for c in candidates:
        example = c.with_name("catalog.example.json")
        if example.is_file():
            return example
    return candidates[0]


def get_default_db_path() -> Path:
    candidates = [
        Path(".private/access.sqlite3"),
        Path("homework-submissions/.private/access.sqlite3"),
        Path(__file__).resolve().parent.parent / ".private" / "access.sqlite3",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return Path(".private/access.sqlite3")


@contextmanager
def connect_db(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        yield conn
    finally:
        conn.close()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def resolve_assignments(
    catalog_path_or_data: Path | dict,
    lessons: Sequence[HistoricalLesson] = HISTORICAL_LESSONS,
) -> list[dict]:
    """
    Match historical lessons with subject and topic IDs from catalog.
    """
    if isinstance(catalog_path_or_data, Path):
        if not catalog_path_or_data.is_file():
            raise FileNotFoundError(f"Catalog file not found: {catalog_path_or_data}")
        with open(catalog_path_or_data, "r", encoding="utf-8") as f:
            catalog_data = json.load(f)
    else:
        catalog_data = catalog_path_or_data

    subjects_by_name: dict[str, str] = {}
    for s in catalog_data.get("subjects", []):
        if isinstance(s, dict) and "name" in s and "id" in s:
            subjects_by_name[normalize_name(s["name"])] = str(s["id"])

    topics_by_subj_and_name: dict[tuple[str, str], str] = {}
    for t in catalog_data.get("topics", []):
        if isinstance(t, dict) and "name" in t and "id" in t and "subject_id" in t:
            s_id = str(t["subject_id"])
            t_name = normalize_name(t["name"])
            topics_by_subj_and_name[(s_id, t_name)] = str(t["id"])

    resolved: list[dict] = []
    for item in lessons:
        norm_subj = normalize_name(item.subject_name)
        if norm_subj not in subjects_by_name:
            raise ValueError(f"Subject not found in catalog: {item.subject_name!r}")
        subject_id = subjects_by_name[norm_subj]

        norm_topic = normalize_name(item.topic_name)
        topic_id = topics_by_subj_and_name.get((subject_id, norm_topic))
        if not topic_id:
            # Flexible match in case of quotation mark differences
            cleaned_target = norm_topic.replace("«", '"').replace("»", '"')
            for (sid, tname), tid in topics_by_subj_and_name.items():
                if sid == subject_id and tname.replace("«", '"').replace("»", '"') == cleaned_target:
                    topic_id = tid
                    break
        if not topic_id:
            raise ValueError(
                f"Topic {item.topic_name!r} not found under subject {item.subject_name!r} (id: {subject_id})"
            )

        assignment_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"urn:assignment:{item.group_name}:{item.lesson_date}:{item.pair_number}",
            )
        )
        timestamp = f"{item.lesson_date}T00:00:00+00:00"

        resolved.append({
            "id": assignment_id,
            "group_name": item.group_name,
            "subject_id": subject_id,
            "topic_id": topic_id,
            "lesson_date": item.lesson_date,
            "pair_number": item.pair_number,
            "created_at": timestamp,
            "updated_at": timestamp,
            "subject_name": item.subject_name,
            "topic_name": item.topic_name,
        })

    return resolved


def dry_run_db_migration(
    db_path: Path,
    catalog_path: Path,
    lessons: Sequence[HistoricalLesson] = HISTORICAL_LESSONS,
) -> dict:
    resolved = resolve_assignments(catalog_path, lessons)
    db_exists = db_path.is_file()

    already_existing: list[dict] = []
    to_insert: list[dict] = []

    if db_exists:
        with connect_db(db_path) as conn:
            table_check = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='class_assignments';"
            ).fetchone()
            if table_check:
                for item in resolved:
                    row = conn.execute(
                        "SELECT id FROM class_assignments WHERE group_name = ? AND lesson_date = ? AND pair_number = ?;",
                        (item["group_name"], item["lesson_date"], item["pair_number"]),
                    ).fetchone()
                    if row:
                        already_existing.append(item)
                    else:
                        to_insert.append(item)
            else:
                to_insert = list(resolved)
    else:
        to_insert = list(resolved)

    return {
        "mode": "dry-run",
        "db_path": str(db_path),
        "db_exists": db_exists,
        "total_lessons": len(resolved),
        "to_insert": len(to_insert),
        "already_existing": len(already_existing),
        "details": resolved,
    }


def execute_db_migration(
    db_path: Path,
    catalog_path: Path,
    lessons: Sequence[HistoricalLesson] = HISTORICAL_LESSONS,
) -> dict:
    resolved = resolve_assignments(catalog_path, lessons)
    is_new = not db_path.exists()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        db_path.parent.chmod(0o700)
    except OSError:
        pass

    inserted_count = 0
    skipped_count = 0

    with connect_db(db_path) as conn:
        ensure_schema(conn)
        for item in resolved:
            cur = conn.execute(
                """
                INSERT INTO class_assignments (
                    id, group_name, subject_id, topic_id, lesson_date, pair_number, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_name, lesson_date, pair_number) DO NOTHING;
                """,
                (
                    item["id"],
                    item["group_name"],
                    item["subject_id"],
                    item["topic_id"],
                    item["lesson_date"],
                    item["pair_number"],
                    item["created_at"],
                    item["updated_at"],
                ),
            )
            if cur.rowcount > 0:
                inserted_count += 1
            else:
                skipped_count += 1
        conn.commit()

    if is_new:
        try:
            db_path.chmod(0o600)
        except OSError:
            pass

    return {
        "status": "success",
        "inserted": inserted_count,
        "skipped": skipped_count,
        "total": len(resolved),
    }


def rollback_db_migration(
    db_path: Path,
    lessons: Sequence[HistoricalLesson] = HISTORICAL_LESSONS,
    dry_run: bool = False,
) -> dict:
    if not db_path.is_file():
        return {
            "status": "skipped",
            "reason": f"Database file does not exist: {db_path}",
            "deleted": 0,
        }

    with connect_db(db_path) as conn:
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='class_assignments';"
        ).fetchone()
        if not table_check:
            return {
                "status": "skipped",
                "reason": "Table class_assignments does not exist",
                "deleted": 0,
            }

        matching_rows = 0
        for item in lessons:
            row = conn.execute(
                "SELECT id FROM class_assignments WHERE group_name = ? AND lesson_date = ? AND pair_number = ?;",
                (item.group_name, item.lesson_date, item.pair_number),
            ).fetchone()
            if row:
                matching_rows += 1

        if dry_run:
            return {
                "mode": "dry-run",
                "would_delete": matching_rows,
            }

        deleted_count = 0
        for item in lessons:
            cur = conn.execute(
                "DELETE FROM class_assignments WHERE group_name = ? AND lesson_date = ? AND pair_number = ?;",
                (item.group_name, item.lesson_date, item.pair_number),
            )
            deleted_count += cur.rowcount
        conn.commit()

    return {
        "status": "rolled_back",
        "deleted": deleted_count,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate homework submissions database and seed class assignments."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to SQLite database file (default: .private/access.sqlite3)",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=None,
        help="Path to catalog.json (default: homework-submissions/catalog.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Simulate database migration without modifying database",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Perform schema creation and seed assignments",
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        default=False,
        help="Remove seeded historical assignments from database",
    )

    args = parser.parse_args(argv)

    db_path = (args.db or get_default_db_path()).resolve()
    catalog_path = (args.catalog or get_default_catalog_path()).resolve()

    if args.rollback:
        mode = "rollback"
    elif args.execute:
        mode = "execute"
    else:
        mode = "dry-run"

    print("=== Database Migration Tool ===")
    print(f"Mode:    {mode}")
    print(f"DB:      {db_path}")
    print(f"Catalog: {catalog_path}")

    if mode == "dry-run":
        try:
            result = dry_run_db_migration(db_path, catalog_path)
            print("\n[DRY RUN SUMMARY]")
            print(f"Database exists:   {result['db_exists']}")
            print(f"Total lessons:     {result['total_lessons']}")
            print(f"To insert:         {result['to_insert']}")
            print(f"Already existing:  {result['already_existing']}")
            print("\nLessons to seed:")
            for item in result["details"]:
                print(
                    f"  {item['lesson_date']} p.{item['pair_number']} | "
                    f"{item['group_name']} | {item['subject_name']} -> {item['topic_name']}"
                )
            return 0
        except (OSError, ValueError, sqlite3.Error) as e:
            print(f"\n[ERROR] Dry run failed: {e}", file=sys.stderr)
            return 1

    elif mode == "execute":
        print("\nExecuting database migration...")
        try:
            result = execute_db_migration(db_path, catalog_path)
            print(
                f"Successfully completed: {result['inserted']} inserted, "
                f"{result['skipped']} skipped (already existed)."
            )
            return 0
        except (OSError, ValueError, sqlite3.Error) as e:
            print(f"\n[ERROR] Execution failed: {e}", file=sys.stderr)
            return 1

    elif mode == "rollback":
        print("\nRolling back database migration...")
        try:
            result = rollback_db_migration(db_path, dry_run=args.dry_run)
            if args.dry_run:
                print(f"[DRY RUN] Would delete {result['would_delete']} historical assignments.")
            else:
                print(f"Successfully rolled back: {result['deleted']} assignments removed.")
            return 0
        except (OSError, ValueError, sqlite3.Error) as e:
            print(f"\n[ERROR] Rollback failed: {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
