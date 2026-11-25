"""Pure read-only parser for legacy catalog.json formats (v1, v2, v3)."""

from __future__ import annotations

import json
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True, slots=True)
class LegacyGroup:
    id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class LegacySubject:
    id: UUID
    name: str
    is_system: bool = False


@dataclass(frozen=True, slots=True)
class LegacyTopic:
    id: UUID
    name: str
    subject_id: UUID


@dataclass(frozen=True, slots=True)
class LegacyCatalogData:
    version: int
    groups: list[LegacyGroup]
    subjects: list[LegacySubject]
    topics: list[LegacyTopic]
    unassigned_subject_id: UUID

    @property
    def system_subject_id(self) -> UUID:
        return self.unassigned_subject_id


def normalize_name_key(name: str) -> str:
    """Normalize a name with NFC and casefold for consistent keying."""
    return unicodedata.normalize("NFC", name.strip()).casefold()


def deterministic_uuid(kind: str, nfc_casefold_key: str) -> UUID:
    """Generate a deterministic UUID5 for a legacy entity without an ID."""
    payload = "homework-submissions:v2:" + json.dumps(
        [kind, nfc_casefold_key], ensure_ascii=False, separators=(",", ":")
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, payload)


def parse_legacy_catalog(catalog_path: Path | str) -> LegacyCatalogData:
    """Parse legacy catalog.json (formats v1, v2, v3) in a pure read-only manner.

    Does not modify or create files on disk. Preserves existing UUIDs for v3,
    and generates deterministic UUID5 identifiers for older unversioned or v1/v2 formats.
    """
    path = Path(catalog_path)
    if not path.is_file():
        raise FileNotFoundError(f"Catalog file not found: {path}")

    raw_text = path.read_text(encoding="utf-8")
    data = json.loads(raw_text)
    if not isinstance(data, dict):
        raise TypeError(f"Catalog root must be a JSON object, got {type(data).__name__}")

    raw_version = data.get("version")
    if raw_version == 3:
        return _parse_v3(data)
    elif raw_version == 2:
        return _parse_v2(data)
    else:
        return _parse_v1(data)


def _parse_v3(data: dict) -> LegacyCatalogData:
    unassigned_id_str = data.get("unassigned_subject_id")
    if not unassigned_id_str:
        raise ValueError("Catalog v3 missing 'unassigned_subject_id'")
    unassigned_subject_id = UUID(str(unassigned_id_str))

    groups: list[LegacyGroup] = []
    seen_groups: set[UUID] = set()
    for g in data.get("groups", []):
        gid = UUID(str(g["id"]))
        if gid not in seen_groups:
            seen_groups.add(gid)
            groups.append(LegacyGroup(id=gid, name=str(g["name"]).strip()))

    subjects: list[LegacySubject] = []
    seen_subjects: set[UUID] = set()
    for s in data.get("subjects", []):
        sid = UUID(str(s["id"]))
        if sid not in seen_subjects:
            seen_subjects.add(sid)
            name = str(s["name"]).strip()
            is_sys = (sid == unassigned_subject_id) or (normalize_name_key(name) == "без предмета")
            subjects.append(LegacySubject(id=sid, name=name, is_system=is_sys))

    topics: list[LegacyTopic] = []
    seen_topics: set[UUID] = set()
    for t in data.get("topics", []):
        tid = UUID(str(t["id"]))
        if tid not in seen_topics:
            seen_topics.add(tid)
            topics.append(
                LegacyTopic(
                    id=tid,
                    name=str(t["name"]).strip(),
                    subject_id=UUID(str(t["subject_id"])),
                )
            )

    return LegacyCatalogData(
        version=3,
        groups=groups,
        subjects=subjects,
        topics=topics,
        unassigned_subject_id=unassigned_subject_id,
    )


def _parse_v2(data: dict) -> LegacyCatalogData:
    groups: list[LegacyGroup] = []
    seen_groups: set[UUID] = set()
    for g in data.get("groups", []):
        if isinstance(g, dict):
            name = str(g.get("name", "")).strip()
            gid = UUID(str(g["id"])) if "id" in g else deterministic_uuid("group", normalize_name_key(name))
        else:
            name = str(g).strip()
            gid = deterministic_uuid("group", normalize_name_key(name))
        if gid not in seen_groups:
            seen_groups.add(gid)
            groups.append(LegacyGroup(id=gid, name=name))

    subjects: list[LegacySubject] = []
    seen_subjects: set[UUID] = set()
    unassigned_subject_id: UUID | None = None

    raw_subjects = data.get("subjects") or data.get("disciplines") or []
    if isinstance(raw_subjects, dict):
        raw_subjects_list = [{"name": k} for k in raw_subjects]
    else:
        raw_subjects_list = raw_subjects

    for s in raw_subjects_list:
        if isinstance(s, dict):
            name = str(s.get("name", "")).strip()
            sid = UUID(str(s["id"])) if "id" in s else deterministic_uuid("subject", normalize_name_key(name))
        else:
            name = str(s).strip()
            sid = deterministic_uuid("subject", normalize_name_key(name))
        if sid not in seen_subjects:
            seen_subjects.add(sid)
            is_sys = normalize_name_key(name) == "без предмета"
            if is_sys:
                unassigned_subject_id = sid
            subjects.append(LegacySubject(id=sid, name=name, is_system=is_sys))

    if unassigned_subject_id is None:
        unassigned_subject_id = deterministic_uuid("subject", "без предмета")
        subjects.append(LegacySubject(id=unassigned_subject_id, name="Без предмета", is_system=True))

    topics: list[LegacyTopic] = []
    seen_topics: set[UUID] = set()
    for t in data.get("topics", []):
        if isinstance(t, dict):
            name = str(t.get("name", "")).strip()
            sub_id = UUID(str(t["subject_id"])) if "subject_id" in t else unassigned_subject_id
            tid = UUID(str(t["id"])) if "id" in t else deterministic_uuid("topic", f"{sub_id}:{normalize_name_key(name)}")
        else:
            name = str(t).strip()
            sub_id = unassigned_subject_id
            tid = deterministic_uuid("topic", f"{sub_id}:{normalize_name_key(name)}")
        if tid not in seen_topics:
            seen_topics.add(tid)
            topics.append(LegacyTopic(id=tid, name=name, subject_id=sub_id))

    return LegacyCatalogData(
        version=2,
        groups=groups,
        subjects=subjects,
        topics=topics,
        unassigned_subject_id=unassigned_subject_id,
    )


def _parse_v1(data: dict) -> LegacyCatalogData:
    groups: list[LegacyGroup] = []
    seen_groups: set[UUID] = set()
    for g in data.get("groups", []):
        if isinstance(g, dict):
            name = str(g.get("name", "")).strip()
            gid = UUID(str(g["id"])) if "id" in g else deterministic_uuid("group", normalize_name_key(name))
        else:
            name = str(g).strip()
            gid = deterministic_uuid("group", normalize_name_key(name))
        if gid not in seen_groups:
            seen_groups.add(gid)
            groups.append(LegacyGroup(id=gid, name=name))

    subjects: list[LegacySubject] = []
    seen_subjects: set[UUID] = set()
    topics: list[LegacyTopic] = []
    seen_topics: set[UUID] = set()
    unassigned_subject_id: UUID | None = None

    disciplines = data.get("disciplines", {})
    if isinstance(disciplines, dict):
        for subj_name, topic_names in disciplines.items():
            sname = str(subj_name).strip()
            sid = deterministic_uuid("subject", normalize_name_key(sname))
            if sid not in seen_subjects:
                seen_subjects.add(sid)
                is_sys = normalize_name_key(sname) == "без предмета"
                if is_sys:
                    unassigned_subject_id = sid
                subjects.append(LegacySubject(id=sid, name=sname, is_system=is_sys))

            if isinstance(topic_names, list):
                for t in topic_names:
                    tname = (t.get("name", "") if isinstance(t, dict) else str(t)).strip()
                    tid = (
                        UUID(str(t["id"]))
                        if isinstance(t, dict) and "id" in t
                        else deterministic_uuid("topic", f"{sid}:{normalize_name_key(tname)}")
                    )
                    if tid not in seen_topics:
                        seen_topics.add(tid)
                        topics.append(LegacyTopic(id=tid, name=tname, subject_id=sid))
    if unassigned_subject_id is None:
        unassigned_subject_id = deterministic_uuid("subject", "без предмета")
        subjects.append(LegacySubject(id=unassigned_subject_id, name="Без предмета", is_system=True))

    return LegacyCatalogData(
        version=1,
        groups=groups,
        subjects=subjects,
        topics=topics,
        unassigned_subject_id=unassigned_subject_id,
    )
