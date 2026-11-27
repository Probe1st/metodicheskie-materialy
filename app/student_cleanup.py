"""Database-level student cleanup analysis and safe merge/normalization execution."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.database import connect, validate
from app.student_names import (
    choose_canonical_display_name,
    find_fuzzy_duplicate_groups,
    normalize_student_name,
    student_match_key,
)
from app.submissions import SubmissionStore


def analyze_students_db(
    db_path: Path | str,
    group_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Inspect all students in the database and build a non-mutating cleanup plan."""
    db_file = Path(db_path).resolve()
    validate(db_file)

    filter_gid = str(UUID(str(group_id))) if group_id is not None else None

    uri = f"file:{db_file.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if filter_gid is not None:
            group_rows = conn.execute(
                "SELECT id, name, archived_at FROM groups WHERE id = ? ORDER BY name",
                (filter_gid,),
            ).fetchall()
        else:
            group_rows = conn.execute(
                "SELECT id, name, archived_at FROM groups ORDER BY name"
            ).fetchall()

        student_rows = conn.execute(
            "SELECT id, group_id, display_name, archived_at FROM students ORDER BY display_name, id"
        ).fetchall()

        sub_counts_rows = conn.execute(
            "SELECT student_id, COUNT(*) AS cnt FROM submissions GROUP BY student_id"
        ).fetchall()
        sub_counts: dict[str, int] = {r["student_id"]: int(r["cnt"]) for r in sub_counts_rows}
    finally:
        conn.close()

    students_by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in student_rows:
        students_by_group[row["group_id"]].append({
            "id": row["id"],
            "group_id": row["group_id"],
            "display_name": row["display_name"],
            "archived_at": row["archived_at"],
            "submissions_count": sub_counts.get(row["id"], 0),
        })

    groups_plan: list[dict[str, Any]] = []
    total_students = 0
    total_exact_merges = 0
    total_exact_sources = 0
    total_renames = 0
    total_fuzzy = 0
    total_paired = 0
    total_warnings = 0

    for g_row in group_rows:
        gid = g_row["id"]
        g_students = students_by_group.get(gid, [])
        total_students += len(g_students)

        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        paired_items: list[dict[str, Any]] = []

        for st in g_students:
            raw_name = st["display_name"]
            try:
                norm = normalize_student_name(raw_name)
            except ValueError:
                paired_items.append({
                    "student_id": st["id"],
                    "display_name": raw_name,
                    "submissions_count": st["submissions_count"],
                    "archived_at": st["archived_at"],
                    "parsed_parts": [],
                    "action": "manual",
                    "assign_student_id": None,
                    "warnings": ["Имя не содержит букв и требует ручной проверки."],
                })
                continue

            if norm.is_paired:
                paired_items.append({
                    "student_id": st["id"],
                    "display_name": raw_name,
                    "submissions_count": st["submissions_count"],
                    "archived_at": st["archived_at"],
                    "parsed_parts": [
                        {
                            "name": part,
                            "matched_student_id": None,
                            "matched_display_name": None,
                        }
                        for part in norm.paired_parts
                    ],
                    "action": "manual",
                    "assign_student_id": None,
                    "warnings": list(norm.warnings),
                })
                continue

            key = student_match_key(norm.display_name)
            buckets[key].append({
                **st,
                "normalized_name": norm.display_name,
                "warnings": list(norm.warnings),
                "warning_codes": list(norm.warning_codes),
            })

        exact_merges: list[dict[str, Any]] = []
        renames: list[dict[str, Any]] = []
        unchanged_warnings: list[dict[str, Any]] = []
        canonical_reps: list[tuple[str, str, int, list[str]]] = []

        bucket_target_by_key: dict[str, tuple[str, str]] = {}

        for key, items in sorted(buckets.items(), key=lambda kv: kv[0]):
            canonical_name = choose_canonical_display_name([it["display_name"] for it in items])
            # Prefer active record, then exact canonical spelling, then most submissions, then ID
            target_item = max(
                items,
                key=lambda it: (
                    it["archived_at"] is None,
                    it["display_name"] == canonical_name,
                    it["submissions_count"],
                    it["id"],
                ),
            )
            bucket_target_by_key[key] = (target_item["id"], canonical_name)
            member_ids = [it["id"] for it in items]
            total_bucket_subs = sum(it["submissions_count"] for it in items)
            canonical_reps.append((target_item["id"], canonical_name, total_bucket_subs, member_ids))

            if len(items) > 1:
                sources = [it for it in items if it["id"] != target_item["id"]]
                exact_merges.append({
                    "target_student_id": target_item["id"],
                    "target_display_name": canonical_name,
                    "target_current_name": target_item["display_name"],
                    "target_submissions_count": target_item["submissions_count"],
                    "source_students": [
                        {
                            "id": s["id"],
                            "display_name": s["display_name"],
                            "submissions_count": s["submissions_count"],
                            "archived_at": s["archived_at"],
                        }
                        for s in sources
                    ],
                    "total_submissions": total_bucket_subs,
                    "approved": True,
                })
                total_exact_merges += 1
                total_exact_sources += len(sources)
            else:
                single = items[0]
                if single["display_name"] != canonical_name:
                    renames.append({
                        "student_id": single["id"],
                        "old_name": single["display_name"],
                        "new_name": canonical_name,
                        "submissions_count": single["submissions_count"],
                        "warnings": single["warnings"],
                        "warning_codes": single["warning_codes"],
                        "approved": True,
                    })
                    total_renames += 1
                elif single["warnings"]:
                    unchanged_warnings.append({
                        "student_id": single["id"],
                        "display_name": single["display_name"],
                        "submissions_count": single["submissions_count"],
                        "warnings": single["warnings"],
                        "warning_codes": single["warning_codes"],
                    })
                if single["warnings"]:
                    total_warnings += 1

        # Match paired parts against existing non-paired buckets in this group
        for p_item in paired_items:
            for part_info in p_item["parsed_parts"]:
                part_key = student_match_key(part_info["name"])
                if part_key in bucket_target_by_key:
                    tid, tname = bucket_target_by_key[part_key]
                    part_info["matched_student_id"] = tid
                    part_info["matched_display_name"] = tname

        # Fuzzy duplicate candidates among distinct canonical buckets in the group
        rep_by_id: dict[str, dict[str, Any]] = {
            rid: {
                "id": rid,
                "display_name": rname,
                "submissions_count": rsubs,
                "member_ids": rmems,
            }
            for rid, rname, rsubs, rmems in canonical_reps
        }
        fuzzy_groups = find_fuzzy_duplicate_groups([(rid, rname) for rid, rname, _, _ in canonical_reps])
        fuzzy_candidates: list[dict[str, Any]] = []
        for fg in fuzzy_groups:
            fg_members = [rep_by_id[sid] for sid in fg.student_ids if sid in rep_by_id]
            if len(fg_members) < 2:
                continue
            best_target = max(
                fg_members,
                key=lambda m: (
                    len(str(m["display_name"]).split()) >= 2,
                    int(m["submissions_count"]),
                    str(m["id"]),
                ),
            )
            fuzzy_candidates.append({
                "target_student_id": best_target["id"],
                "suggested_name": fg.suggested_name,
                "reason": fg.reason,
                "confidence": fg.confidence,
                "students": fg_members,
                "approved": False,
            })

        total_fuzzy += len(fuzzy_candidates)
        total_paired += len(paired_items)
        total_warnings += sum(len(p["warnings"]) > 0 for p in paired_items)

        groups_plan.append({
            "group_id": gid,
            "group_name": g_row["name"],
            "archived_at": g_row["archived_at"],
            "students_count": len(g_students),
            "exact_merges": exact_merges,
            "renames": renames,
            "fuzzy_candidates": fuzzy_candidates,
            "paired_students": paired_items,
            "unchanged_warnings": unchanged_warnings,
        })

    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database_path": str(db_file),
        "summary": {
            "groups_count": len(groups_plan),
            "total_students": total_students,
            "exact_merge_groups_count": total_exact_merges,
            "exact_merge_sources_count": total_exact_sources,
            "renames_only_count": total_renames,
            "fuzzy_candidate_groups_count": total_fuzzy,
            "paired_students_count": total_paired,
            "warnings_count": total_warnings,
        },
        "groups": groups_plan,
    }


def apply_cleanup_plan(
    db_path: Path | str,
    storage_root: Path | str | None,
    plan: dict[str, Any],
    *,
    apply_exact: bool = True,
    apply_renames: bool = True,
    apply_fuzzy: bool = False,
    split_paired: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply a cleanup plan to a SQLite v2 database while preserving all submissions and files."""
    if plan.get("version") != 1:
        raise ValueError(f"Unsupported cleanup plan version: {plan.get('version')!r}")

    db_file = Path(db_path).resolve()
    validate(db_file)

    resolved_storage = Path(storage_root).resolve() if storage_root is not None else None

    # Check beforehand if any multi-target paired split requires storage_root
    needs_storage_for_split = False
    for grp in plan.get("groups", []):
        for p in grp.get("paired_students", []):
            action = p.get("action", "manual")
            should_split = action == "split" or (split_paired and action in ("manual", "split"))
            if should_split and len(p.get("parsed_parts", [])) >= 2 and int(p.get("submissions_count", 0)) > 0:
                needs_storage_for_split = True
                break

    if needs_storage_for_split and resolved_storage is None:
        raise ValueError("--storage is required when splitting paired submissions across multiple students")

    if dry_run:
        with tempfile.TemporaryDirectory(prefix="student-cleanup-dryrun-") as tmpdir:
            tmp_root = Path(tmpdir)
            tmp_db = tmp_root / db_file.name
            with sqlite3.connect(f"file:{db_file.as_posix()}?mode=ro", uri=True) as src, sqlite3.connect(tmp_db) as dst:
                src.backup(dst)

            tmp_storage = tmp_root / "storage"
            if resolved_storage is not None and resolved_storage.is_dir() and needs_storage_for_split:
                shutil.copytree(resolved_storage, tmp_storage, symlinks=False)
            else:
                tmp_storage.mkdir(parents=True, exist_ok=True)

            return _execute_plan_on_db(
                tmp_db,
                tmp_storage,
                plan,
                apply_exact=apply_exact,
                apply_renames=apply_renames,
                apply_fuzzy=apply_fuzzy,
                split_paired=split_paired,
                dry_run=True,
            )

    effective_storage = resolved_storage or (db_file.parent / "storage")
    return _execute_plan_on_db(
        db_file,
        effective_storage,
        plan,
        apply_exact=apply_exact,
        apply_renames=apply_renames,
        apply_fuzzy=apply_fuzzy,
        split_paired=split_paired,
        dry_run=False,
    )


def _execute_plan_on_db(
    db_file: Path,
    storage_root: Path,
    plan: dict[str, Any],
    *,
    apply_exact: bool,
    apply_renames: bool,
    apply_fuzzy: bool,
    split_paired: bool,
    dry_run: bool,
) -> dict[str, Any]:
    store = SubmissionStore(db_file, storage_root)

    with connect(db_file) as conn:
        students_before = int(conn.execute("SELECT COUNT(*) FROM students").fetchone()[0])
        submissions_before = int(conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0])

    merged_into: dict[str, str] = {}
    merged_groups_count = 0
    merged_sources_count = 0
    renamed_count = 0
    fuzzy_merged_count = 0
    paired_split_count = 0
    paired_assigned_count = 0
    copied_submissions_count = 0
    moved_submissions_count = 0

    def _resolve_id(sid: str) -> str:
        cur = sid
        seen: set[str] = set()
        while cur in merged_into and cur not in seen:
            seen.add(cur)
            cur = merged_into[cur]
        return cur

    for grp in plan.get("groups", []):
        gid = grp["group_id"]

        # 1. Exact merges
        if apply_exact:
            for m in grp.get("exact_merges", []):
                if m.get("approved", True) is False:
                    continue
                target_id = _resolve_id(m["target_student_id"])
                source_ids = []
                for s in m.get("source_students", []):
                    resolved_src = _resolve_id(s["id"])
                    if resolved_src != target_id and resolved_src not in source_ids:
                        source_ids.append(resolved_src)
                if not source_ids:
                    continue
                res = store.merge_students(
                    gid,
                    target_id,
                    source_ids,
                    target_display_name=m["target_display_name"],
                )
                for src_id in source_ids:
                    merged_into[src_id] = target_id
                merged_groups_count += 1
                merged_sources_count += len(source_ids)
                moved_submissions_count += res.moved_submissions

        # 2. Renames
        if apply_renames:
            for r in grp.get("renames", []):
                if r.get("approved", True) is False:
                    continue
                sid = _resolve_id(r["student_id"])
                current = store.get_student(sid)
                if current is None:
                    continue
                if current.display_name != r["new_name"]:
                    store.rename_student(sid, r["new_name"], normalize=True)
                    renamed_count += 1

        # 3. Approved fuzzy merges
        for fc in grp.get("fuzzy_candidates", []):
            if not (apply_fuzzy or fc.get("approved") is True):
                continue
            target_id = _resolve_id(fc["target_student_id"])
            source_ids = []
            for st in fc.get("students", []):
                cand_ids = st.get("member_ids") or [st["id"]]
                for cid in cand_ids:
                    resolved = _resolve_id(cid)
                    if resolved != target_id and resolved not in source_ids:
                        source_ids.append(resolved)
            if not source_ids:
                continue
            res = store.merge_students(
                gid,
                target_id,
                source_ids,
                target_display_name=fc.get("suggested_name"),
            )
            for src_id in source_ids:
                merged_into[src_id] = target_id
            fuzzy_merged_count += 1
            merged_sources_count += len(source_ids)
            moved_submissions_count += res.moved_submissions

        # 4. Paired student resolutions
        for p in grp.get("paired_students", []):
            action = p.get("action", "manual")
            if action == "skip":
                continue
            src_id = _resolve_id(p["student_id"])
            if store.get_student(src_id) is None:
                continue

            if action == "assign" and p.get("assign_student_id"):
                assign_id = _resolve_id(p["assign_student_id"])
                res = store.merge_students(gid, assign_id, [src_id])
                merged_into[src_id] = assign_id
                paired_assigned_count += 1
                moved_submissions_count += res.moved_submissions
            elif action == "split" or (split_paired and action in ("manual", "split")):
                targets: list[tuple[str | None, str]] = []
                for part in p.get("parsed_parts", []):
                    matched_id = part.get("matched_student_id")
                    resolved_matched = _resolve_id(matched_id) if matched_id else None
                    if resolved_matched and store.get_student(resolved_matched) is None:
                        resolved_matched = None
                    targets.append((resolved_matched, part["name"]))
                if targets:
                    split_res = store.split_paired_student(gid, src_id, targets)
                    if split_res.target_students:
                        merged_into[src_id] = str(split_res.target_students[0].id)
                    paired_split_count += 1
                    moved_submissions_count += split_res.moved_submissions
                    copied_submissions_count += split_res.copied_submissions

    with connect(db_file) as conn:
        ic = conn.execute("PRAGMA integrity_check;").fetchall()
        if not ic or ic[0][0] != "ok":
            raise RuntimeError(f"Integrity check failed after cleanup: {ic}")
        fk = conn.execute("PRAGMA foreign_key_check;").fetchall()
        if fk:
            raise RuntimeError(f"Foreign key violations after cleanup: {[dict(r) for r in fk]}")

        students_after = int(conn.execute("SELECT COUNT(*) FROM students").fetchone()[0])
        submissions_after = int(conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0])

    return {
        "status": "ok",
        "dry_run": dry_run,
        "students_before": students_before,
        "students_after": students_after,
        "submissions_before": submissions_before,
        "submissions_after": submissions_after,
        "merged_groups": merged_groups_count,
        "fuzzy_merged_groups": fuzzy_merged_count,
        "merged_sources": merged_sources_count,
        "renamed_students": renamed_count,
        "paired_split": paired_split_count,
        "paired_assigned": paired_assigned_count,
        "moved_submissions": moved_submissions_count,
        "copied_submissions": copied_submissions_count,
    }
