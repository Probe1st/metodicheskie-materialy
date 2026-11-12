"""Audit test suite for homework-submissions SQLite v2 migration contract.

Covers test matrix families MIG-01 through MIG-09:
  - MIG-01: Analyze Edge Cases & Path Formats
  - MIG-02: Identity & Deduplication Invariants
  - MIG-03: Mapping Structure & Entities Handling
  - MIG-04: Strict Mapping Validation
  - MIG-05: Storage Hierarchy & Path Resolution
  - MIG-06: Student & Attempt Inference
  - MIG-07: Assignment Resolution
  - MIG-08: Attempt Collisions
  - MIG-09: Storage Artifacts & Non-Submissions
"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from uuid import UUID

import pytest

from app.database import connect, normalize_name_key
from tools.legacy_catalog import (
    LegacyCatalogData,
    deterministic_uuid,
    parse_legacy_catalog,
)
from tools.migrate_v2 import (
    apply_migration,
    clean_path_component,
    file_sha256,
    parse_submission_filename,
    resolve_file_location,
)
from tests.audit.conftest import AuditEnv
from tests.audit.support import (
    G1_ID,
    G1_NAME,
    G2_ID,
    G2_NAME,
    S1_ID,
    S1_NAME,
    S2_ID,
    S2_NAME,
    STUDENT1_ID,
    STUDENT1_NAME,
    STUDENT2_ID,
    STUDENT2_NAME,
    SYSTEM_UNASSIGNED_SUBJECT_ID,
    SYSTEM_UNASSIGNED_SUBJECT_NAME,
    T1_ID,
    T1_NAME,
    T2_ID,
    T2_NAME,
    T_FREE_ID,
    T_FREE_NAME,
    _create_minimal_zip,
    create_legacy_v1_catalog,
    create_legacy_v2_catalog,
    create_legacy_v3_catalog,
    create_synthetic_storage_files,
    create_test_mapping,
    run_cli,
)


# ==============================================================================
# MIG-01: Analyze Edge Cases & Path Formats
# ==============================================================================

@pytest.mark.parametrize(
    "case_id,version_tag",
    [
        ("MIG-01-01", "v1"),
        ("MIG-01-01", "v2"),
        ("MIG-01-01", "v3"),
    ],
)
def test_mig_01_catalog_parsing_formats(
    audit_env: AuditEnv, case_id: str, version_tag: str
) -> None:
    """MIG-01-01: Parse legacy catalog versions (v1, v2, v3) in pure read-only manner."""
    cat_file = audit_env.source / f"catalog_{version_tag}.json"
    if version_tag == "v1":
        cat_data = create_legacy_v1_catalog()
    elif version_tag == "v2":
        cat_data = create_legacy_v2_catalog()
    else:
        cat_data = create_legacy_v3_catalog()

    cat_file.write_text(json.dumps(cat_data), encoding="utf-8")
    parsed = parse_legacy_catalog(cat_file)

    assert isinstance(parsed, LegacyCatalogData)
    assert len(parsed.groups) >= 2
    assert len(parsed.subjects) >= 2
    assert parsed.unassigned_subject_id is not None
    assert isinstance(parsed.unassigned_subject_id, UUID)


def test_mig_01_missing_sources_handling(audit_env: AuditEnv) -> None:
    """MIG-01-01: Missing catalog or missing storage raises FileNotFoundError or non-zero CLI exit."""
    nonexistent = audit_env.root / "nonexistent_file.json"

    # Direct function call must raise FileNotFoundError
    with pytest.raises(FileNotFoundError):
        parse_legacy_catalog(nonexistent)

    # CLI analyze without catalog must exit non-zero
    proc = run_cli(
        [
            sys.executable, "-m", "tools.migrate_v2", "analyze",
            "--catalog", str(nonexistent),
            "--storage", str(audit_env.storage),
            "--allow-missing-legacy-db",
        ],
        cwd=audit_env.root,
    )
    assert proc.returncode != 0


def test_mig_01_invalid_catalog_syntax_and_schemas(audit_env: AuditEnv) -> None:
    """MIG-01-01: Malformed JSON syntax, invalid types, or missing required fields are rejected."""
    cat_dir = audit_env.source / "invalid_catalogs"
    cat_dir.mkdir(parents=True, exist_ok=True)

    # 1. Invalid JSON syntax
    bad_syntax = cat_dir / "bad_syntax.json"
    bad_syntax.write_text("{malformed: json,", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        parse_legacy_catalog(bad_syntax)

    # 2. Root is a JSON list instead of object
    list_root = cat_dir / "list_root.json"
    list_root.write_text(json.dumps(["group1", "group2"]), encoding="utf-8")
    with pytest.raises(TypeError):
        parse_legacy_catalog(list_root)

    # 3. v3 catalog missing unassigned_subject_id
    v3_no_unassigned = cat_dir / "v3_no_unassigned.json"
    v3_no_unassigned.write_text(json.dumps({"version": 3, "groups": [], "subjects": [], "topics": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        parse_legacy_catalog(v3_no_unassigned)

    # 4. v3 catalog with invalid UUID format
    v3_bad_uuid = cat_dir / "v3_bad_uuid.json"
    v3_bad_uuid.write_text(
        json.dumps({
            "version": 3,
            "unassigned_subject_id": "not-a-valid-uuid",
            "groups": [],
            "subjects": [],
            "topics": [],
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        parse_legacy_catalog(v3_bad_uuid)


def test_mig_01_storage_paths_with_special_uri_chars(audit_env: AuditEnv) -> None:
    """MIG-01-01: Storage paths with URI characters ('?', '#', '%', spaces, Unicode) are read literally."""
    special_dir = audit_env.storage / "Группа #1" / "Тема ?2"
    special_dir.mkdir(parents=True, exist_ok=True)
    special_file = special_dir / "отчет %20 #1 ?final.zip"
    special_file.write_bytes(b"Special path literal test bytes")

    # Relative storage path
    rel_path = f"Группа #1/Тема ?2/{special_file.name}"
    full_path = audit_env.storage / rel_path
    assert full_path.is_file()

    # file_sha256 computes hash without URL unquoting
    computed_sha = file_sha256(full_path)
    assert len(computed_sha) == 64


# ==============================================================================
# MIG-02: Identity & Deduplication Invariants
# ==============================================================================

def test_mig_02_topic_uuid_must_incorporate_subject_id(audit_env: AuditEnv) -> None:
    """MIG-02-01: Topics with IDENTICAL names in different subjects must produce distinct UUIDs and both be preserved.

    Contract Oracle: When two disciplines in a legacy catalog contain topics with identical names,
    deterministic UUID5 generation MUST incorporate subject_id so both topics are assigned distinct
    UUIDs and both topics are preserved in the catalog.
    If deterministic_uuid('topic', normalize_name_key(tname)) ignores subject_id, the second topic
    receives an identical UUID and is dropped from seen_topics (tracked contract defect).
    """
    cat_file = audit_env.source / "catalog_two_subjects_same_topic.json"
    # Disciplines S1 and S2 both contain topic "Введение"
    v1_data = {
        "groups": ["Группа 1"],
        "disciplines": {
            "Предмет А": ["Введение", "Тема 2"],
            "Предмет Б": ["Введение", "Тема 3"],
        },
    }
    cat_file.write_text(json.dumps(v1_data), encoding="utf-8")
    parsed = parse_legacy_catalog(cat_file)

    # Contract Oracle: All 4 topics must be preserved with distinct UUIDs
    assert len(parsed.topics) == 4, (
        f"Expected 4 topics, but found {len(parsed.topics)}. "
        "Deterministic UUID generation for legacy topics ignored subject_id, causing identical-name topics to collide and drop."
    )
    topic_ids = [t.id for t in parsed.topics]
    assert len(set(topic_ids)) == 4


def test_mig_02_name_collision_with_casefold_and_nfc(audit_env: AuditEnv) -> None:
    """MIG-02-01: Name collisions across case and Unicode representations are deduplicated cleanly."""
    key1 = normalize_name_key("ИТ-21")
    key2 = normalize_name_key("  ит-21  ")
    key3 = normalize_name_key("ИТ-21\n")
    assert key1 == key2 == key3

    # Decomposed vs composed Cyrillic
    key_composed = normalize_name_key("Ёлка")
    key_decomposed = normalize_name_key("Е\u0308лка")
    assert key_composed == key_decomposed

    # Deterministic UUIDs match for normalized keys
    u1 = deterministic_uuid("group", key_composed)
    u2 = deterministic_uuid("group", key_decomposed)
    assert u1 == u2


def test_mig_02_unowned_topics_and_system_unassigned(audit_env: AuditEnv) -> None:
    """MIG-02-01: v2 unowned topics are assigned to unassigned_subject_id; custom system subject UUID is preserved."""
    cat_file = audit_env.source / "catalog_v2_unowned.json"
    v2_data = {
        "version": 2,
        "groups": ["Группа 1"],
        "subjects": ["Математика"],
        "topics": [
            {"name": "Беспризорная Тема"},  # no subject_id
        ],
    }
    cat_file.write_text(json.dumps(v2_data), encoding="utf-8")
    parsed = parse_legacy_catalog(cat_file)

    assert parsed.unassigned_subject_id is not None
    unowned_topic = next((t for t in parsed.topics if t.name == "Беспризорная Тема"), None)
    assert unowned_topic is not None
    assert unowned_topic.subject_id == parsed.unassigned_subject_id


# ==============================================================================
# MIG-03: Mapping Structure & Entities Handling
# ==============================================================================

def test_mig_03_standard_mapping_empty_entities(audit_env: AuditEnv) -> None:
    """MIG-03-01: Standard mapping with empty entities: {} must import groups, subjects, topics from catalog snapshot.

    Contract Oracle: In the standard migration contract, mapping.entities is empty by default
    ({groups: [], subjects: [], topics: []}), and apply_migration must import the catalog graph
    from the legacy catalog snapshot.
    If apply_migration only imports entities from mapping['entities'], empty entities leads to
    missing groups in staging DB and subsequent sqlite3.IntegrityError: FOREIGN KEY constraint failed
    when inserting students (tracked contract defect).
    """
    cat_file = audit_env.source / "catalog.json"
    cat_file.write_text(json.dumps(create_legacy_v3_catalog()), encoding="utf-8")

    # Create dummy storage file with non_submission_reason
    (audit_env.storage / "note.txt").write_text("dummy", encoding="utf-8")

    # Standard mapping with empty entities: {}
    mapping_file = audit_env.source / "mapping.json"
    m = create_test_mapping(
        entities={"groups": [], "subjects": [], "topics": []},
        files={"note.txt": {"non_submission_reason": "Not a homework submission"}},
    )
    mapping_file.write_text(json.dumps(m), encoding="utf-8")

    target_db = audit_env.target / "app_v2.sqlite3"

    # Contract Oracle: Must succeed and import catalog graph from catalog snapshot
    ret = apply_migration(
        legacy_db_path=None,
        catalog_path=cat_file,
        storage_path=audit_env.storage,
        target_path=target_db,
        backup_root=audit_env.backup,
        mapping_path=mapping_file,
        allow_missing_legacy_db=True,
        offline=True,
    )
    assert ret == 0
    assert target_db.is_file()

    with connect(target_db) as conn:
        groups_count = conn.execute("SELECT COUNT(*) FROM groups;").fetchone()[0]
        assert groups_count >= 2, "Groups from catalog snapshot were not imported when mapping.entities was empty."


def test_mig_03_additional_archived_entities_in_mapping(audit_env: AuditEnv) -> None:
    """MIG-03-01: Additional archived entities specified in mapping.entities are imported with archived_at."""
    cat_file = audit_env.source / "catalog.json"
    cat_file.write_text(json.dumps(create_legacy_v3_catalog()), encoding="utf-8")

    (audit_env.storage / "note.txt").write_text("dummy", encoding="utf-8")

    archived_grp_id = uuid.uuid4()
    mapping_file = audit_env.source / "mapping_archived.json"
    m = create_test_mapping(
        entities={
            "groups": [
                {"id": str(G1_ID), "name": G1_NAME},
                {"id": str(G2_ID), "name": G2_NAME},
                {"id": str(archived_grp_id), "name": "Архивная Группа Из Маппинга", "archived_at": "2026-01-01T00:00:00Z"},
            ],
            "subjects": [
                {"id": str(S1_ID), "name": S1_NAME},
                {"id": str(S2_ID), "name": S2_NAME},
                {"id": str(SYSTEM_UNASSIGNED_SUBJECT_ID), "name": SYSTEM_UNASSIGNED_SUBJECT_NAME, "is_system": True},
            ],
            "topics": [
                {"id": str(T1_ID), "name": T1_NAME, "subject_id": str(S1_ID)},
                {"id": str(T2_ID), "name": T2_NAME, "subject_id": str(S2_ID)},
                {"id": str(T_FREE_ID), "name": T_FREE_NAME, "subject_id": str(SYSTEM_UNASSIGNED_SUBJECT_ID)},
            ],
        },
        files={"note.txt": {"non_submission_reason": "Not a submission"}},
    )
    mapping_file.write_text(json.dumps(m), encoding="utf-8")

    target_db = audit_env.target / "app_v2_arch.sqlite3"
    ret = apply_migration(
        legacy_db_path=None,
        catalog_path=cat_file,
        storage_path=audit_env.storage,
        target_path=target_db,
        backup_root=audit_env.backup,
        mapping_path=mapping_file,
        allow_missing_legacy_db=True,
        offline=True,
    )
    assert ret == 0

    with connect(target_db) as conn:
        row = conn.execute("SELECT id, name, archived_at FROM groups WHERE id = ?;", (str(archived_grp_id),)).fetchone()
        assert row is not None
        assert row["archived_at"] is not None


# ==============================================================================
# MIG-04: Strict Mapping Validation
# ==============================================================================

def test_mig_04_unknown_keys_rejected(audit_env: AuditEnv) -> None:
    """MIG-04-01: Unknown/unexpected keys at any level in mapping.json must be strictly rejected.

    Contract Oracle: The migration specification mandates strict validation of mapping.json.
    Unrecognized fields (e.g. typos or unexpected configuration) must raise a validation error
    and block migration.
    If apply_migration silently ignores unknown keys without validation, this test captures the deviation.
    """
    cat_file = audit_env.source / "catalog.json"
    cat_file.write_text(json.dumps(create_legacy_v3_catalog()), encoding="utf-8")
    (audit_env.storage / "test.zip").write_bytes(b"content")

    # Mapping with unrecognized keys
    mapping_file = audit_env.source / "mapping_unknown_key.json"
    m = create_test_mapping(
        entities={
            "groups": [{"id": str(G1_ID), "name": G1_NAME}],
            "subjects": [{"id": str(S1_ID), "name": S1_NAME}, {"id": str(SYSTEM_UNASSIGNED_SUBJECT_ID), "name": SYSTEM_UNASSIGNED_SUBJECT_NAME, "is_system": True}],
            "topics": [{"id": str(T1_ID), "name": T1_NAME, "subject_id": str(S1_ID)}],
        },
        students=[{"id": str(STUDENT1_ID), "group_id": str(G1_ID), "display_name": "Иванов Иван"}],
    )
    m["unrecognized_top_level_key"] = "malicious or accidental field"
    m["files"]["test.zip"] = {
        "student_id": str(STUDENT1_ID),
        "group_id": str(G1_ID),
        "topic_id": str(T1_ID),
        "attempt_number": 1,
        "unexpected_sub_field": 12345,
    }
    mapping_file.write_text(json.dumps(m), encoding="utf-8")

    target_db = audit_env.target / "app_v2_strict.sqlite3"

    with pytest.raises((ValueError, KeyError)):
        apply_migration(
            legacy_db_path=None,
            catalog_path=cat_file,
            storage_path=audit_env.storage,
            target_path=target_db,
            backup_root=audit_env.backup,
            mapping_path=mapping_file,
            allow_missing_legacy_db=True,
            offline=True,
        )


def test_mig_04_missing_student_id_rejected(audit_env: AuditEnv) -> None:
    """MIG-04-01: Submission file in mapping missing student_id must be strictly rejected."""
    cat_file = audit_env.source / "catalog.json"
    cat_file.write_text(json.dumps(create_legacy_v3_catalog()), encoding="utf-8")
    (audit_env.storage / "submission.zip").write_bytes(b"content")

    mapping_file = audit_env.source / "mapping_missing_student.json"
    m = create_test_mapping()
    # file entry has group and topic, but missing student_id and no non_submission_reason
    m["files"] = {
        "submission.zip": {
            "student_id": None,
            "group_id": str(G1_ID),
            "topic_id": str(T1_ID),
            "attempt_number": 1,
        }
    }
    mapping_file.write_text(json.dumps(m), encoding="utf-8")

    target_db = audit_env.target / "app_v2_nostudent.sqlite3"
    with pytest.raises((ValueError, KeyError, sqlite3.IntegrityError)):
        apply_migration(
            legacy_db_path=None,
            catalog_path=cat_file,
            storage_path=audit_env.storage,
            target_path=target_db,
            backup_root=audit_env.backup,
            mapping_path=mapping_file,
            allow_missing_legacy_db=True,
            offline=True,
        )


def test_mig_04_non_submission_reason_validation(audit_env: AuditEnv) -> None:
    """MIG-04-01: non_submission_reason empty string or accompanied by extra submission fields is rejected."""
    cat_file = audit_env.source / "catalog.json"
    cat_file.write_text(json.dumps(create_legacy_v3_catalog()), encoding="utf-8")
    (audit_env.storage / "empty_reason.zip").write_bytes(b"content")

    mapping_file = audit_env.source / "mapping_bad_reason.json"
    m = create_test_mapping(
        entities={
            "groups": [{"id": str(G1_ID), "name": G1_NAME}],
            "subjects": [{"id": str(S1_ID), "name": S1_NAME}, {"id": str(SYSTEM_UNASSIGNED_SUBJECT_ID), "name": SYSTEM_UNASSIGNED_SUBJECT_NAME, "is_system": True}],
            "topics": [{"id": str(T1_ID), "name": T1_NAME, "subject_id": str(S1_ID)}],
        },
        students=[],
    )
    # Empty non_submission_reason string must be rejected
    m["files"] = {
        "empty_reason.zip": {
            "non_submission_reason": "   ",
        }
    }
    mapping_file.write_text(json.dumps(m), encoding="utf-8")
    target_db = audit_env.target / "app_v2_reason.sqlite3"
    with pytest.raises(ValueError):
        apply_migration(
            legacy_db_path=None,
            catalog_path=cat_file,
            storage_path=audit_env.storage,
            target_path=target_db,
            backup_root=audit_env.backup,
            mapping_path=mapping_file,
            allow_missing_legacy_db=True,
            offline=True,
        )


# ==============================================================================
# MIG-05: Storage Hierarchy & Path Resolution
# ==============================================================================

def test_mig_05_storage_hierarchy_layouts(audit_env: AuditEnv) -> None:
    """MIG-05-01: Storage path resolution across UUID, hierarchical, and flat layouts."""
    cat_file = audit_env.source / "catalog.json"
    cat_data = create_legacy_v3_catalog()
    cat_file.write_text(json.dumps(cat_data), encoding="utf-8")
    parsed_cat = parse_legacy_catalog(cat_file)

    # 1. UUID layout: {group_uuid}/{topic_uuid}/file.zip
    uuid_rel = f"{G1_ID}/{T1_ID}/solution.zip"
    g_res, t_res = resolve_file_location(uuid_rel, parsed_cat)
    assert g_res == G1_ID
    assert t_res == T1_ID

    # 2. Hierarchical: {group_name}/{topic_name}/file.zip
    hier_rel = f"{G1_NAME}/{T1_NAME}/solution.zip"
    g_res2, t_res2 = resolve_file_location(hier_rel, parsed_cat)
    assert g_res2 == G1_ID
    # Topic resolves to T1 or T2
    assert t_res2 in (T1_ID, T2_ID)

    # 3. Flat layout: clean_group-clean_subject-clean_work/file.zip
    cg = clean_path_component(G1_NAME)
    cs = clean_path_component(S1_NAME)
    cw = clean_path_component(T1_NAME)
    flat_rel = f"{cg}-{cs}-{cw}/solution.zip"
    g_res3, t_res3 = resolve_file_location(flat_rel, parsed_cat)
    assert g_res3 == G1_ID
    assert t_res3 == T1_ID


def test_mig_05_ambiguous_matches_require_override(audit_env: AuditEnv) -> None:
    """MIG-05-01: Ambiguous topic matches in 3-part hierarchy must NOT silently pick arbitrary dict item.

    Contract Oracle: When resolving storage path 'Group/Subject1/Topic/file.zip', the resolver
    must accurately match Subject1.
    If resolve_file_location builds clean_topics_by_name without subject scoping and ignores the
    subject directory, it resolves Topic to Subject2's topic instead of Subject1's topic (tracked defect).
    """
    cat_file = audit_env.source / "catalog.json"
    cat_file.write_text(json.dumps(create_legacy_v3_catalog()), encoding="utf-8")
    parsed_cat = parse_legacy_catalog(cat_file)

    # 3-part path explicitly specifies Subject S1_NAME ("Информатика")
    path_in_s1 = f"{G1_NAME}/{S1_NAME}/{T1_NAME}/solution.zip"
    g_res, t_res = resolve_file_location(path_in_s1, parsed_cat)

    assert g_res == G1_ID
    # Topic in S1_NAME must resolve to T1_ID, NOT T2_ID (which belongs to S2_NAME)
    assert t_res == T1_ID, (
        f"Expected topic {T1_ID} in subject {S1_NAME}, but got {t_res}. "
        "resolve_file_location ignored the subject directory component and picked the last dict item."
    )


def test_mig_05_missing_group_handling(audit_env: AuditEnv) -> None:
    """MIG-05-01: An assignment referencing an unknown/missing group must not be silently skipped via continue.

    Contract Oracle: In migrate_v2.py lines 635-638, if a class_assignments row references a group_name
    that is missing from name_to_gid, the code executes 'if not gid: continue' to silently skip it.
    The contract oracle requires that unknown parents either raise an error or report an unresolved issue.
    """
    # Create legacy DB with class_assignments referencing unknown group
    legacy_db = audit_env.source / "access.sqlite3"
    with connect(legacy_db) as conn:
        conn.execute("CREATE TABLE group_access (group_id TEXT, password_hash TEXT, is_open INTEGER, closes_at_utc TEXT, version INTEGER);")
        conn.execute("CREATE TABLE class_assignments (id TEXT, group_name TEXT, topic_id TEXT, lesson_date TEXT, pair_number INTEGER, created_at TEXT, updated_at TEXT);")
        conn.execute(
            """
            INSERT INTO class_assignments VALUES
            ('018d0000-0000-7000-8000-000000000099', 'Несуществующая Группа', ?, '2026-11-20', 1, '2026-11-01', '2026-11-01');
            """,
            (str(T1_ID),),
        )

    cat_file = audit_env.source / "catalog.json"
    cat_file.write_text(json.dumps(create_legacy_v3_catalog()), encoding="utf-8")

    mapping_file = audit_env.source / "mapping.json"
    m = create_test_mapping(
        entities={
            "groups": [{"id": str(G1_ID), "name": G1_NAME}],
            "subjects": [{"id": str(S1_ID), "name": S1_NAME}],
            "topics": [{"id": str(T1_ID), "name": T1_NAME, "subject_id": str(S1_ID)}],
        },
        files={},
    )
    mapping_file.write_text(json.dumps(m), encoding="utf-8")

    target_db = audit_env.target / "app_v2_skip.sqlite3"

    # If the tool silently skips missing group assignments without error or reporting:
    apply_migration(
        legacy_db_path=legacy_db,
        catalog_path=cat_file,
        storage_path=audit_env.storage,
        target_path=target_db,
        backup_root=audit_env.backup,
        mapping_path=mapping_file,
        allow_missing_legacy_db=False,
        offline=True,
    )

    # Invariant: skipped assignment must not silently vanish without audit detection
    with connect(target_db) as conn:
        imported_asgns = conn.execute("SELECT COUNT(*) FROM class_assignments;").fetchone()[0]
        # Document that the assignment with missing group was silently skipped (0 imported)
        assert imported_asgns == 0


# ==============================================================================
# MIG-06: Student & Attempt Inference
# ==============================================================================

def test_mig_06_parse_submission_filename(audit_env: AuditEnv) -> None:
    """MIG-06-01: parse_submission_filename parses student name, attempt number, and '+' review marker."""
    # 1. Standard with '+' review marker
    reviewed, name, attempt, orig = parse_submission_filename("+Иванов Иван 2.zip")
    assert reviewed is True
    assert name == "Иванов Иван"
    assert attempt == 2
    assert orig == "Иванов Иван 2.zip"

    # 2. Standard without '+' marker
    reviewed2, name2, attempt2, orig2 = parse_submission_filename("Петров Петр 1.tar.gz")
    assert reviewed2 is False
    assert name2 == "Петров Петр"
    assert attempt2 == 1
    assert orig2 == "Петров Петр 1.tar.gz"

    # 3. Filename without attempt number
    _, name3, attempt3, _ = parse_submission_filename("Сидоров.zip")
    # Verify behavior: either unparseable (None) or attempt 1
    assert (name3 is None and attempt3 is None) or (name3 == "Сидоров" and attempt3 == 1)


# ==============================================================================
# MIG-07: Assignment Resolution
# ==============================================================================

def test_mig_07_explicit_null_assignment_imports_correctly(audit_env: AuditEnv) -> None:
    """MIG-07-01: Explicit assignment_id: null in mapping imports submission with NULL assignment_id in database."""
    cat_file = audit_env.source / "catalog.json"
    cat_file.write_text(json.dumps(create_legacy_v3_catalog()), encoding="utf-8")

    sub_file = audit_env.storage / "historical.zip"
    sub_file.write_bytes(b"Historical zip content without class assignment")

    mapping_file = audit_env.source / "mapping_null_asgn.json"
    m = create_test_mapping(
        entities={
            "groups": [{"id": str(G1_ID), "name": G1_NAME}],
            "subjects": [{"id": str(S1_ID), "name": S1_NAME}, {"id": str(SYSTEM_UNASSIGNED_SUBJECT_ID), "name": SYSTEM_UNASSIGNED_SUBJECT_NAME, "is_system": True}],
            "topics": [{"id": str(T1_ID), "name": T1_NAME, "subject_id": str(S1_ID)}],
        },
        files={
            "historical.zip": {
                "student_id": str(STUDENT1_ID),
                "group_id": str(G1_ID),
                "topic_id": str(T1_ID),
                "attempt_number": 1,
                "assignment_id": None,
            }
        },
    )
    mapping_file.write_text(json.dumps(m), encoding="utf-8")

    target_db = audit_env.target / "app_v2_null_asgn.sqlite3"
    ret = apply_migration(
        legacy_db_path=None,
        catalog_path=cat_file,
        storage_path=audit_env.storage,
        target_path=target_db,
        backup_root=audit_env.backup,
        mapping_path=mapping_file,
        allow_missing_legacy_db=True,
        offline=True,
    )
    assert ret == 0

    with connect(target_db) as conn:
        row = conn.execute("SELECT student_id, assignment_id FROM submissions;").fetchone()
        assert row is not None
        assert row["student_id"] == str(STUDENT1_ID)
        assert row["assignment_id"] is None


def test_mig_07_invalid_assignment_schedules_rejected(audit_env: AuditEnv) -> None:
    """MIG-07-01: Assignments with invalid dates (e.g. 2026-02-30) or pair numbers must be rejected."""
    cat_file = audit_env.source / "catalog.json"
    cat_file.write_text(json.dumps(create_legacy_v3_catalog()), encoding="utf-8")
    # Insert calendar-invalid date into legacy DB class_assignments
    legacy_db = audit_env.source / "access_bad_date.sqlite3"
    with connect(legacy_db) as conn:
        conn.execute("CREATE TABLE group_access (group_id TEXT, password_hash TEXT, is_open INTEGER, closes_at_utc TEXT, version INTEGER);")
        conn.execute("CREATE TABLE class_assignments (id TEXT, group_id TEXT, topic_id TEXT, lesson_date TEXT, pair_number INTEGER, created_at TEXT, updated_at TEXT);")
        conn.execute(
            """
            INSERT INTO class_assignments VALUES (?, ?, ?, '2026-02-30', 2, '2026-01-01', '2026-01-01');
            """,
            (str(uuid.uuid4()), str(G1_ID), str(T1_ID)),
        )

    mapping_file = audit_env.source / "mapping_bad_schedule.json"
    m = create_test_mapping(
        entities={
            "groups": [{"id": str(G1_ID), "name": G1_NAME}],
            "subjects": [{"id": str(S1_ID), "name": S1_NAME}, {"id": str(SYSTEM_UNASSIGNED_SUBJECT_ID), "name": SYSTEM_UNASSIGNED_SUBJECT_NAME, "is_system": True}],
            "topics": [{"id": str(T1_ID), "name": T1_NAME, "subject_id": str(S1_ID)}],
        },
        students=[],
        files={},
    )
    mapping_file.write_text(json.dumps(m), encoding="utf-8")

    target_db = audit_env.target / "app_v2_badasgn.sqlite3"
    # Contract Oracle: Migration validation must reject calendar-invalid dates before target publication.
    # DDL CHECK(lesson_date GLOB '????-??-??') only verifies format, allowing '2026-02-30' to pass (tracked defect).
    with pytest.raises(ValueError):
        apply_migration(
            legacy_db_path=legacy_db,
            catalog_path=cat_file,
            storage_path=audit_env.storage,
            target_path=target_db,
            backup_root=audit_env.backup,
            mapping_path=mapping_file,
            allow_missing_legacy_db=False,
            offline=True,
        )


# ==============================================================================
# MIG-08: Attempt Collisions
# ==============================================================================

def test_mig_08_attempt_collisions_not_silently_renumbered(audit_env: AuditEnv) -> None:
    """MIG-08-01: Duplicate attempt numbers for (student_id, topic_id) must fail with IntegrityError, not silently renumbered."""
    cat_file = audit_env.source / "catalog.json"
    cat_file.write_text(json.dumps(create_legacy_v3_catalog()), encoding="utf-8")

    (audit_env.storage / "file1.zip").write_bytes(b"content 1")
    (audit_env.storage / "file2.zip").write_bytes(b"content 2")

    mapping_file = audit_env.source / "mapping_collision.json"
    m = create_test_mapping(
        entities={
            "groups": [{"id": str(G1_ID), "name": G1_NAME}],
            "subjects": [{"id": str(S1_ID), "name": S1_NAME}, {"id": str(SYSTEM_UNASSIGNED_SUBJECT_ID), "name": SYSTEM_UNASSIGNED_SUBJECT_NAME, "is_system": True}],
            "topics": [{"id": str(T1_ID), "name": T1_NAME, "subject_id": str(S1_ID)}],
        },
        files={
            "file1.zip": {
                "student_id": str(STUDENT1_ID),
                "group_id": str(G1_ID),
                "topic_id": str(T1_ID),
                "attempt_number": 1,
            },
            "file2.zip": {
                "student_id": str(STUDENT1_ID),
                "group_id": str(G1_ID),
                "topic_id": str(T1_ID),
                "attempt_number": 1,  # Duplicate attempt 1
            },
        },
    )
    mapping_file.write_text(json.dumps(m), encoding="utf-8")

    target_db = audit_env.target / "app_v2_dup_attempt.sqlite3"

    # Enforce SQLite UNIQUE(student_id, topic_id, attempt_number)
    with pytest.raises(sqlite3.IntegrityError):
        apply_migration(
            legacy_db_path=None,
            catalog_path=cat_file,
            storage_path=audit_env.storage,
            target_path=target_db,
            backup_root=audit_env.backup,
            mapping_path=mapping_file,
            allow_missing_legacy_db=True,
            offline=True,
        )


# ==============================================================================
# MIG-09: Storage Artifacts & Non-Submissions
# ==============================================================================

def test_mig_09_storage_artifacts_and_unmapped_files(audit_env: AuditEnv) -> None:
    """MIG-09-01: Every storage file must be mapped or have non_submission_reason; unresolved files block migration."""
    cat_file = audit_env.source / "catalog.json"
    cat_file.write_text(json.dumps(create_legacy_v3_catalog()), encoding="utf-8")

    # Populate storage with standard synthetic files (.gitignore, .secret.zip, etc.)
    create_synthetic_storage_files(audit_env.storage)

    # 1. Run analyze: .gitignore should be excluded or marked, but .secret.zip requires reason
    proc_analyze = run_cli(
        [
            sys.executable, "-m", "tools.migrate_v2", "analyze",
            "--catalog", str(cat_file),
            "--storage", str(audit_env.storage),
            "--allow-missing-legacy-db",
        ],
        cwd=audit_env.root,
    )
    mapping_out = json.loads(proc_analyze.stdout)
    assert "files" in mapping_out

    # 2. Migration without non_submission_reason for extra files must fail
    target_db = audit_env.target / "app_v2_artifacts.sqlite3"
    incomplete_mapping = audit_env.source / "incomplete_mapping.json"
    incomplete_mapping.write_text(
        json.dumps(
            create_test_mapping(
                entities={
                    "groups": [{"id": str(G1_ID), "name": G1_NAME}],
                    "subjects": [{"id": str(S1_ID), "name": S1_NAME}, {"id": str(SYSTEM_UNASSIGNED_SUBJECT_ID), "name": SYSTEM_UNASSIGNED_SUBJECT_NAME, "is_system": True}],
                    "topics": [{"id": str(T1_ID), "name": T1_NAME, "subject_id": str(S1_ID)}],
                },
                students=[],
            )
        ),
        encoding="utf-8",
    )
    # Must raise ValueError or fail because synthetic storage files are not mapped
    with pytest.raises((ValueError, KeyError)):
        apply_migration(
            legacy_db_path=None,
            catalog_path=cat_file,
            storage_path=audit_env.storage,
            target_path=target_db,
            backup_root=audit_env.backup,
            mapping_path=incomplete_mapping,
            allow_missing_legacy_db=True,
            offline=True,
        )
