"""REST API v1 blueprint for AI agents."""

from __future__ import annotations

import io
import json
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from flask import Blueprint, Response, g, jsonify, request, send_file

from .access import AccessStore
from .assignments import AssignmentStore
from .database import connect, validate_lesson_date, validate_pair_number
from .sqlite_catalog import SqliteCatalogStore
from .submissions import (
    SubmissionDetail,
    SubmissionNotFoundError,
    SubmissionStore,
    SubmissionValidationError,
)
from .task_content import TaskAttachment, TaskContent, TaskContentStore
from .task_rendering import render_task
from .tokens import ApiActor, TokenStore


class _ApiError(Exception):
    def __init__(self, status_code: int, error: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message


def _extract_bearer_token() -> str | None:
    auth_header = request.headers.get("Authorization", "")
    if auth_header:
        parts = auth_header.split(None, 1)
        if len(parts) == 2 and parts[0].casefold() == "bearer":
            token = parts[1].strip()
            if token:
                return token
        return None
    api_key = request.headers.get("X-API-Key", "").strip()
    return api_key or None


def _parse_uuid(raw: str, field_name: str = "id") -> UUID:
    try:
        return UUID(str(raw).strip())
    except (ValueError, TypeError) as err:
        raise _ApiError(400, "invalid_uuid", f"Invalid UUID for {field_name}: {raw!r}") from err


def _parse_bool_arg(name: str, default: bool = False) -> bool:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    norm = raw.strip().casefold()
    if norm in ("1", "true", "yes", "on"):
        return True
    if norm in ("0", "false", "no", "off"):
        return False
    raise _ApiError(400, "invalid_parameter", f"Invalid boolean value for {name}: {raw!r}")


def _parse_pagination() -> tuple[int, int]:
    raw_page = request.args.get("page", "1").strip()
    raw_per_page = request.args.get("per_page", "50").strip()
    try:
        page = int(raw_page)
        per_page = int(raw_per_page)
    except ValueError as err:
        raise _ApiError(400, "invalid_parameter", "page and per_page must be integers") from err
    if page < 1:
        raise _ApiError(400, "invalid_parameter", "page must be >= 1")
    if per_page < 1 or per_page > 500:
        raise _ApiError(400, "invalid_parameter", "per_page must be between 1 and 500")
    return page, per_page


def _parse_date_arg(name: str) -> str | None:
    raw = request.args.get(name, "").strip()
    if not raw:
        return None
    try:
        return validate_lesson_date(raw)
    except (ValueError, TypeError) as err:
        raise _ApiError(400, "invalid_parameter", str(err)) from err


def _serialize_attachment(
    att: TaskAttachment | None,
    *,
    download_url: str | None = None,
) -> dict[str, Any] | None:
    if att is None:
        return None
    return {
        "id": str(att.id),
        "original_name": att.original_name,
        "size_bytes": att.size_bytes,
        "created_at": att.created_at.isoformat(),
        "download_url": download_url or f"/api/v1/attachments/{att.id}/download",
    }


def _serialize_task_content(
    content: TaskContent | None,
    att: TaskAttachment | None,
    *,
    attachment_url: str | None = None,
) -> dict[str, Any]:
    if content is None:
        return {
            "has_task": False,
            "description": "",
            "description_format": "txt",
            "rendered_html": "",
            "updated_at": None,
            "attachment": None,
        }
    has_desc = bool(content.description and content.description.strip())
    rendered = render_task(content.description, content.description_format) if has_desc else ""
    return {
        "has_task": has_desc or att is not None,
        "description": content.description,
        "description_format": content.description_format,
        "rendered_html": rendered,
        "updated_at": content.updated_at.isoformat(),
        "attachment": _serialize_attachment(att, download_url=attachment_url),
    }


def _serialize_submission(sub: SubmissionDetail) -> dict[str, Any]:
    return {
        "id": str(sub.id),
        "student_id": str(sub.student_id),
        "student_name": sub.student_display_name,
        "group_id": str(sub.group_id),
        "group_name": sub.group_name,
        "subject_id": str(sub.subject_id) if sub.subject_id else None,
        "subject_name": sub.subject_name,
        "topic_id": str(sub.topic_id),
        "topic_name": sub.topic_name,
        "assignment_id": str(sub.assignment_id) if sub.assignment_id else None,
        "lesson_date": sub.lesson_date,
        "pair_number": sub.pair_number,
        "attempt_number": sub.attempt_number,
        "review_status": sub.review_status,
        "reviewed_at": sub.reviewed_at.isoformat() if sub.reviewed_at else None,
        "grade": sub.grade,
        "feedback": sub.feedback,
        "graded_at": sub.graded_at.isoformat() if sub.graded_at else None,
        "graded_by": sub.graded_by,
        "created_at": sub.created_at.isoformat(),
        "file": {
            "original_filename": sub.original_filename,
            "size_bytes": sub.size_bytes,
            "sha256": sub.sha256,
            "download_url": f"/api/v1/submissions/{sub.id}/download",
        },
    }


def _task_text_response(content: TaskContent, prefix: str) -> Response:
    ext = content.description_format if content.description_format in ("md", "txt", "html") else "txt"
    mime_map = {
        "md": "text/markdown; charset=utf-8",
        "txt": "text/plain; charset=utf-8",
        "html": "text/html; charset=utf-8",
    }
    resp = Response(content.description, status=200, mimetype=mime_map[ext])
    resp.headers["Content-Disposition"] = f'attachment; filename="{prefix}.{ext}"'
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def create_api_blueprint(
    *,
    db_path: Path,
    catalog_store: SqliteCatalogStore,
    access_store: AccessStore,
    assignment_store: AssignmentStore,
    submission_store: SubmissionStore,
    task_content_store: TaskContentStore,
    token_store: TokenStore,
    allowed_grades: frozenset[str] | None = None,
) -> Blueprint:
    """Create the /api/v1 Flask Blueprint for AI agents."""
    api_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

    @api_bp.errorhandler(_ApiError)
    def handle_api_error(err: _ApiError):
        return jsonify(error=err.error, message=err.message), err.status_code

    @api_bp.before_request
    def require_api_token():
        raw_token = _extract_bearer_token()
        if not raw_token:
            return (
                jsonify(error="unauthorized", message="Missing API token"),
                401,
                {"WWW-Authenticate": "Bearer"},
            )
        actor = token_store.authenticate(raw_token)
        if actor is None:
            return (
                jsonify(error="unauthorized", message="Invalid or revoked API token"),
                401,
                {"WWW-Authenticate": "Bearer"},
            )
        g.api_actor = actor

    def _get_json_body() -> dict[str, Any]:
        if not request.is_json:
            raise _ApiError(400, "invalid_request", "Request body must be JSON")
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise _ApiError(400, "invalid_request", "JSON body must be an object")
        return data

    def _load_group_access_map() -> dict[str, dict[str, Any]]:
        with connect(db_path) as conn:
            rows = conn.execute(
                "SELECT group_id, is_open, closes_at_utc, version FROM group_access;"
            ).fetchall()
        return {
            r["group_id"]: {
                "is_open": bool(r["is_open"]),
                "closes_at": r["closes_at_utc"],
                "version": int(r["version"]),
            }
            for r in rows
        }

    def _load_task_map(owner_kind: str) -> dict[str, tuple[TaskContent, TaskAttachment | None]]:
        with connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT tc.owner_kind, tc.owner_id, tc.description, tc.description_format,
                       tc.attachment_id, tc.updated_at,
                       ta.original_name AS att_name, ta.size_bytes AS att_size,
                       ta.created_at AS att_created_at
                FROM task_contents tc
                LEFT JOIN task_attachments ta ON ta.id = tc.attachment_id
                WHERE tc.owner_kind = ?;
                """,
                (owner_kind,),
            ).fetchall()
        result: dict[str, tuple[TaskContent, TaskAttachment | None]] = {}
        for r in rows:
            att_id = UUID(r["attachment_id"]) if r["attachment_id"] else None
            tc = TaskContent(
                owner_kind=r["owner_kind"],
                owner_id=UUID(r["owner_id"]),
                description=r["description"],
                description_format=r["description_format"],
                attachment_id=att_id,
                updated_at=datetime.fromisoformat(r["updated_at"]),
            )
            att = (
                TaskAttachment(
                    id=att_id,
                    original_name=r["att_name"],
                    size_bytes=int(r["att_size"]),
                    created_at=datetime.fromisoformat(r["att_created_at"]),
                )
                if att_id and r["att_name"] is not None
                else None
            )
            result[r["owner_id"]] = (tc, att)
        return result

    def _resolve_lesson_or_topic_task(
        assignment_id: UUID,
        topic_id: UUID,
        *,
        fallback_to_topic: bool,
    ) -> tuple[str | None, TaskContent | None, TaskAttachment | None]:
        lesson_tc = task_content_store.get("lesson", assignment_id)
        if lesson_tc is not None and (
            (lesson_tc.description and lesson_tc.description.strip()) or lesson_tc.attachment_id
        ):
            att = (
                task_content_store.get_attachment(lesson_tc.attachment_id)
                if lesson_tc.attachment_id
                else None
            )
            return "lesson", lesson_tc, att

        if fallback_to_topic:
            topic_tc = task_content_store.get("topic", topic_id)
            if topic_tc is not None:
                att = (
                    task_content_store.get_attachment(topic_tc.attachment_id)
                    if topic_tc.attachment_id
                    else None
                )
                return "topic", topic_tc, att

        if lesson_tc is not None:
            return "lesson", lesson_tc, None
        return None, None, None

    # --- System & Identity ---

    @api_bp.get("/me")
    def api_me():
        actor: ApiActor = g.api_actor
        with connect(db_path) as conn:
            counts = {
                "groups_active": conn.execute("SELECT COUNT(*) FROM groups WHERE archived_at IS NULL").fetchone()[0],
                "subjects_active": conn.execute("SELECT COUNT(*) FROM subjects WHERE archived_at IS NULL").fetchone()[0],
                "topics_active": conn.execute("SELECT COUNT(*) FROM topics WHERE archived_at IS NULL").fetchone()[0],
                "students_active": conn.execute("SELECT COUNT(*) FROM students WHERE archived_at IS NULL").fetchone()[0],
                "assignments_active": conn.execute("SELECT COUNT(*) FROM class_assignments WHERE archived_at IS NULL").fetchone()[0],
                "submissions_total": conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0],
                "submissions_pending": conn.execute("SELECT COUNT(*) FROM submissions WHERE review_status = 'pending'").fetchone()[0],
                "submissions_reviewed": conn.execute("SELECT COUNT(*) FROM submissions WHERE review_status = 'reviewed'").fetchone()[0],
                "submissions_graded": conn.execute("SELECT COUNT(*) FROM submissions WHERE grade IS NOT NULL").fetchone()[0],
            }
        return jsonify(
            actor={
                "token_id": str(actor.token_id) if actor.token_id else None,
                "name": actor.name,
                "label": actor.actor_label,
                "scopes": list(actor.scopes),
                "source": actor.source,
            },
            summary=counts,
        ), 200

    # --- Catalog, Groups, Students, Subjects, Topics ---

    @api_bp.get("/catalog")
    def api_catalog():
        include_archived = _parse_bool_arg("include_archived", False)
        access_map = _load_group_access_map()
        topic_tasks = _load_task_map("topic")

        with connect(db_path) as conn:
            g_where = "" if include_archived else "WHERE g.archived_at IS NULL"
            group_rows = conn.execute(
                f"""
                SELECT g.id, g.name, g.archived_at,
                       (SELECT COUNT(*) FROM students st WHERE st.group_id = g.id AND st.archived_at IS NULL) AS students_count
                FROM groups g
                {g_where}
                ORDER BY g.name;
                """
            ).fetchall()

            s_where = "" if include_archived else "WHERE archived_at IS NULL"
            subject_rows = conn.execute(
                f"""
                SELECT id, name, is_system, archived_at
                FROM subjects
                {s_where}
                ORDER BY is_system DESC, name;
                """
            ).fetchall()

            t_where = "" if include_archived else "WHERE t.archived_at IS NULL"
            topic_rows = conn.execute(
                f"""
                SELECT t.id, t.subject_id, s.name AS subject_name, t.name, t.archived_at
                FROM topics t
                JOIN subjects s ON s.id = t.subject_id
                {t_where}
                ORDER BY t.name;
                """
            ).fetchall()

            sys_row = conn.execute("SELECT id FROM subjects WHERE is_system = 1;").fetchone()

        return jsonify(
            unassigned_subject_id=sys_row["id"] if sys_row else None,
            groups=[
                {
                    "id": r["id"],
                    "name": r["name"],
                    "archived_at": r["archived_at"],
                    "students_count": int(r["students_count"]),
                    "access": access_map.get(r["id"]),
                }
                for r in group_rows
            ],
            subjects=[
                {
                    "id": r["id"],
                    "name": r["name"],
                    "is_system": bool(r["is_system"]),
                    "archived_at": r["archived_at"],
                }
                for r in subject_rows
            ],
            topics=[
                {
                    "id": r["id"],
                    "subject_id": r["subject_id"],
                    "subject_name": r["subject_name"],
                    "name": r["name"],
                    "archived_at": r["archived_at"],
                    "has_description": bool(
                        topic_tasks.get(r["id"])
                        and topic_tasks[r["id"]][0].description.strip()
                    ),
                    "has_attachment": bool(
                        topic_tasks.get(r["id"]) and topic_tasks[r["id"]][1] is not None
                    ),
                }
                for r in topic_rows
            ],
        ), 200

    @api_bp.get("/groups")
    def api_list_groups():
        include_archived = _parse_bool_arg("include_archived", False)
        access_map = _load_group_access_map()
        where = "" if include_archived else "WHERE g.archived_at IS NULL"
        with connect(db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT g.id, g.name, g.archived_at,
                       (SELECT COUNT(*) FROM students st WHERE st.group_id = g.id AND st.archived_at IS NULL) AS students_count,
                       (SELECT COUNT(*) FROM class_assignments ca WHERE ca.group_id = g.id AND ca.archived_at IS NULL) AS assignments_count
                FROM groups g
                {where}
                ORDER BY g.name;
                """
            ).fetchall()
        return jsonify(
            items=[
                {
                    "id": r["id"],
                    "name": r["name"],
                    "archived_at": r["archived_at"],
                    "access": access_map.get(r["id"]),
                    "students_count": int(r["students_count"]),
                    "assignments_count": int(r["assignments_count"]),
                }
                for r in rows
            ]
        ), 200

    @api_bp.get("/groups/<group_id>")
    def api_get_group(group_id: str):
        gid = _parse_uuid(group_id, "group_id")
        include_archived = _parse_bool_arg("include_archived", False)
        with connect(db_path) as conn:
            g_row = conn.execute(
                "SELECT id, name, archived_at FROM groups WHERE id = ?;",
                (str(gid),),
            ).fetchone()
            if g_row is None:
                raise _ApiError(404, "not_found", f"Group {gid} not found")
            acc_row = conn.execute(
                "SELECT is_open, closes_at_utc, version FROM group_access WHERE group_id = ?;",
                (str(gid),),
            ).fetchone()

        students = submission_store.list_students(gid, include_archived=include_archived)
        with connect(db_path) as conn:
            a_cond = "" if include_archived else "AND ca.archived_at IS NULL"
            a_rows = conn.execute(
                f"""
                SELECT ca.id, ca.topic_id, t.name AS topic_name, t.subject_id, s.name AS subject_name,
                       ca.lesson_date, ca.pair_number, ca.archived_at
                FROM class_assignments ca
                JOIN topics t ON t.id = ca.topic_id
                JOIN subjects s ON s.id = t.subject_id
                WHERE ca.group_id = ? {a_cond}
                ORDER BY ca.lesson_date ASC, ca.pair_number ASC;
                """,
                (str(gid),),
            ).fetchall()

        return jsonify(
            id=g_row["id"],
            name=g_row["name"],
            archived_at=g_row["archived_at"],
            access={
                "is_open": bool(acc_row["is_open"]),
                "closes_at": acc_row["closes_at_utc"],
                "version": int(acc_row["version"]),
            }
            if acc_row
            else None,
            students=[
                {
                    "id": str(st.id),
                    "group_id": str(st.group_id),
                    "display_name": st.display_name,
                    "archived_at": st.archived_at.isoformat() if st.archived_at else None,
                }
                for st in students
            ],
            assignments=[
                {
                    "id": r["id"],
                    "topic_id": r["topic_id"],
                    "topic_name": r["topic_name"],
                    "subject_id": r["subject_id"],
                    "subject_name": r["subject_name"],
                    "lesson_date": r["lesson_date"],
                    "pair_number": int(r["pair_number"]),
                    "archived_at": r["archived_at"],
                }
                for r in a_rows
            ],
        ), 200

    @api_bp.get("/groups/<group_id>/students")
    def api_group_students(group_id: str):
        gid = _parse_uuid(group_id, "group_id")
        include_archived = _parse_bool_arg("include_archived", False)
        if catalog_store.get_group(gid, include_archived=True) is None:
            raise _ApiError(404, "not_found", f"Group {gid} not found")

        arch_cond = "" if include_archived else "AND st.archived_at IS NULL"
        with connect(db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT st.id, st.group_id, st.display_name, st.archived_at,
                       COUNT(s.id) AS submissions_count,
                       SUM(CASE WHEN s.review_status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                       SUM(CASE WHEN s.review_status = 'reviewed' THEN 1 ELSE 0 END) AS reviewed_count
                FROM students st
                LEFT JOIN submissions s ON s.student_id = st.id
                WHERE st.group_id = ? {arch_cond}
                GROUP BY st.id
                ORDER BY st.display_name, st.id;
                """,
                (str(gid),),
            ).fetchall()

        return jsonify(
            group_id=str(gid),
            items=[
                {
                    "id": r["id"],
                    "group_id": r["group_id"],
                    "display_name": r["display_name"],
                    "archived_at": r["archived_at"],
                    "submissions_count": int(r["submissions_count"] or 0),
                    "pending_count": int(r["pending_count"] or 0),
                    "reviewed_count": int(r["reviewed_count"] or 0),
                }
                for r in rows
            ],
        ), 200

    @api_bp.get("/students")
    def api_list_students():
        include_archived = _parse_bool_arg("include_archived", False)
        page, per_page = _parse_pagination()
        raw_group = request.args.get("group_id", "").strip()
        q = request.args.get("q", "").strip()

        conditions: list[str] = []
        params: list[Any] = []
        if not include_archived:
            conditions.append("st.archived_at IS NULL")
        if raw_group:
            gid = _parse_uuid(raw_group, "group_id")
            conditions.append("st.group_id = ?")
            params.append(str(gid))
        if q:
            conditions.append("instr(cf(st.display_name), ?) > 0")
            params.append(unicodedata.normalize("NFC", q).casefold())

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        offset = (page - 1) * per_page

        with connect(db_path) as conn:
            conn.create_function(
                "cf",
                1,
                lambda v: unicodedata.normalize("NFC", v).casefold() if isinstance(v, str) else "",
            )
            total = int(
                conn.execute(f"SELECT COUNT(*) FROM students st {where};", params).fetchone()[0]
            )
            rows = conn.execute(
                f"""
                SELECT st.id, st.group_id, g.name AS group_name, st.display_name, st.archived_at,
                       COUNT(s.id) AS submissions_count,
                       SUM(CASE WHEN s.review_status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                       SUM(CASE WHEN s.review_status = 'reviewed' THEN 1 ELSE 0 END) AS reviewed_count
                FROM students st
                JOIN groups g ON g.id = st.group_id
                LEFT JOIN submissions s ON s.student_id = st.id
                {where}
                GROUP BY st.id
                ORDER BY g.name, st.display_name, st.id
                LIMIT ? OFFSET ?;
                """,
                [*params, per_page, offset],
            ).fetchall()

        return jsonify(
            items=[
                {
                    "id": r["id"],
                    "group_id": r["group_id"],
                    "group_name": r["group_name"],
                    "display_name": r["display_name"],
                    "archived_at": r["archived_at"],
                    "submissions_count": int(r["submissions_count"] or 0),
                    "pending_count": int(r["pending_count"] or 0),
                    "reviewed_count": int(r["reviewed_count"] or 0),
                }
                for r in rows
            ],
            total=total,
            page=page,
            per_page=per_page,
        ), 200

    @api_bp.get("/students/<student_id>")
    def api_get_student(student_id: str):
        sid = _parse_uuid(student_id, "student_id")
        with connect(db_path) as conn:
            st_row = conn.execute(
                """
                SELECT st.id, st.group_id, g.name AS group_name, st.display_name, st.archived_at
                FROM students st
                JOIN groups g ON g.id = st.group_id
                WHERE st.id = ?;
                """,
                (str(sid),),
            ).fetchone()
        if st_row is None:
            raise _ApiError(404, "not_found", f"Student {sid} not found")

        subs = submission_store.list_submissions(student_id=sid)
        return jsonify(
            id=st_row["id"],
            group_id=st_row["group_id"],
            group_name=st_row["group_name"],
            display_name=st_row["display_name"],
            archived_at=st_row["archived_at"],
            submissions=[_serialize_submission(s) for s in subs],
        ), 200

    @api_bp.get("/subjects")
    def api_list_subjects():
        include_archived = _parse_bool_arg("include_archived", False)
        where = "" if include_archived else "WHERE s.archived_at IS NULL"
        topic_filter = "" if include_archived else "AND t.archived_at IS NULL"
        with connect(db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT s.id, s.name, s.is_system, s.archived_at,
                       COUNT(t.id) AS topics_count
                FROM subjects s
                LEFT JOIN topics t ON t.subject_id = s.id {topic_filter}
                {where}
                GROUP BY s.id
                ORDER BY s.is_system DESC, s.name;
                """
            ).fetchall()
        return jsonify(
            items=[
                {
                    "id": r["id"],
                    "name": r["name"],
                    "is_system": bool(r["is_system"]),
                    "archived_at": r["archived_at"],
                    "topics_count": int(r["topics_count"] or 0),
                }
                for r in rows
            ]
        ), 200

    @api_bp.get("/subjects/<subject_id>")
    def api_get_subject(subject_id: str):
        sid = _parse_uuid(subject_id, "subject_id")
        include_archived = _parse_bool_arg("include_archived", False)
        with connect(db_path) as conn:
            s_row = conn.execute(
                "SELECT id, name, is_system, archived_at FROM subjects WHERE id = ?;",
                (str(sid),),
            ).fetchone()
        if s_row is None:
            raise _ApiError(404, "not_found", f"Subject {sid} not found")

        topics = catalog_store.list_topics(subject_id=sid, include_archived=include_archived)
        return jsonify(
            id=s_row["id"],
            name=s_row["name"],
            is_system=bool(s_row["is_system"]),
            archived_at=s_row["archived_at"],
            topics=[
                {"id": str(t.id), "subject_id": str(t.subject_id), "name": t.name}
                for t in topics
            ],
        ), 200

    @api_bp.get("/topics")
    def api_list_topics():
        include_archived = _parse_bool_arg("include_archived", False)
        raw_subj = request.args.get("subject_id", "").strip()
        topic_tasks = _load_task_map("topic")

        conditions: list[str] = []
        params: list[Any] = []
        if not include_archived:
            conditions.append("t.archived_at IS NULL")
        if raw_subj:
            sid = _parse_uuid(raw_subj, "subject_id")
            conditions.append("t.subject_id = ?")
            params.append(str(sid))

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with connect(db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT t.id, t.subject_id, s.name AS subject_name, t.name, t.archived_at
                FROM topics t
                JOIN subjects s ON s.id = t.subject_id
                {where}
                ORDER BY s.is_system DESC, s.name, t.name;
                """,
                params,
            ).fetchall()

        items = []
        for r in rows:
            tc_pair = topic_tasks.get(r["id"])
            tc = tc_pair[0] if tc_pair else None
            att = tc_pair[1] if tc_pair else None
            items.append(
                {
                    "id": r["id"],
                    "subject_id": r["subject_id"],
                    "subject_name": r["subject_name"],
                    "name": r["name"],
                    "archived_at": r["archived_at"],
                    "has_description": bool(tc and tc.description.strip()),
                    "description_format": tc.description_format if tc else None,
                    "attachment": _serialize_attachment(
                        att,
                        download_url=f"/api/v1/topics/{r['id']}/attachment" if att else None,
                    ),
                }
            )
        return jsonify(items=items), 200

    @api_bp.get("/topics/<topic_id>")
    def api_get_topic(topic_id: str):
        tid = _parse_uuid(topic_id, "topic_id")
        with connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT t.id, t.subject_id, s.name AS subject_name, t.name, t.archived_at
                FROM topics t
                JOIN subjects s ON s.id = t.subject_id
                WHERE t.id = ?;
                """,
                (str(tid),),
            ).fetchone()
        if row is None:
            raise _ApiError(404, "not_found", f"Topic {tid} not found")

        tc = task_content_store.get("topic", tid)
        att = (
            task_content_store.get_attachment(tc.attachment_id)
            if (tc and tc.attachment_id)
            else None
        )
        return jsonify(
            id=row["id"],
            subject_id=row["subject_id"],
            subject_name=row["subject_name"],
            name=row["name"],
            archived_at=row["archived_at"],
            task=_serialize_task_content(
                tc, att, attachment_url=f"/api/v1/topics/{tid}/attachment"
            ),
        ), 200

    @api_bp.get("/topics/<topic_id>/task")
    def api_get_topic_task(topic_id: str):
        tid = _parse_uuid(topic_id, "topic_id")
        if catalog_store.get_topic(tid, include_archived=True) is None:
            raise _ApiError(404, "not_found", f"Topic {tid} not found")

        fmt = request.args.get("format", "json").strip().casefold()
        if fmt not in ("json", "raw"):
            raise _ApiError(400, "invalid_parameter", "format must be 'json' or 'raw'")

        tc = task_content_store.get("topic", tid)
        if fmt == "raw":
            if tc is None or not tc.description.strip():
                raise _ApiError(404, "not_found", "Topic has no task description")
            return _task_text_response(tc, f"topic-{tid}")

        att = (
            task_content_store.get_attachment(tc.attachment_id)
            if (tc and tc.attachment_id)
            else None
        )
        return jsonify(
            topic_id=str(tid),
            **_serialize_task_content(
                tc, att, attachment_url=f"/api/v1/topics/{tid}/attachment"
            ),
        ), 200

    @api_bp.get("/topics/<topic_id>/attachment")
    def api_download_topic_attachment(topic_id: str):
        tid = _parse_uuid(topic_id, "topic_id")
        if catalog_store.get_topic(tid, include_archived=True) is None:
            raise _ApiError(404, "not_found", f"Topic {tid} not found")
        tc = task_content_store.get("topic", tid)
        if tc is None or tc.attachment_id is None:
            raise _ApiError(404, "not_found", "Topic has no attachment")
        att = task_content_store.get_attachment(tc.attachment_id)
        if att is None:
            raise _ApiError(404, "not_found", "Attachment metadata not found")
        path = task_content_store.get_attachment_path(att.id)
        if not path.is_file():
            raise _ApiError(404, "not_found", "Attachment file missing on disk")
        resp = send_file(path, as_attachment=True, download_name=att.original_name)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    # --- Assignments & Task Downloads ---

    @api_bp.get("/assignments")
    def api_list_assignments():
        include_archived = _parse_bool_arg("include_archived", False)
        page, per_page = _parse_pagination()
        raw_group = request.args.get("group_id", "").strip()
        raw_subject = request.args.get("subject_id", "").strip()
        raw_topic = request.args.get("topic_id", "").strip()
        lesson_date = _parse_date_arg("lesson_date")
        date_from = _parse_date_arg("date_from")
        date_to = _parse_date_arg("date_to")
        raw_pair = request.args.get("pair_number", "").strip()

        conditions: list[str] = []
        params: list[Any] = []
        if not include_archived:
            conditions.append("ca.archived_at IS NULL")
        if raw_group:
            conditions.append("ca.group_id = ?")
            params.append(str(_parse_uuid(raw_group, "group_id")))
        if raw_subject:
            conditions.append("t.subject_id = ?")
            params.append(str(_parse_uuid(raw_subject, "subject_id")))
        if raw_topic:
            conditions.append("ca.topic_id = ?")
            params.append(str(_parse_uuid(raw_topic, "topic_id")))
        if lesson_date:
            conditions.append("ca.lesson_date = ?")
            params.append(lesson_date)
        if date_from:
            conditions.append("ca.lesson_date >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("ca.lesson_date <= ?")
            params.append(date_to)
        if raw_pair:
            try:
                pair_num = validate_pair_number(int(raw_pair))
            except (ValueError, TypeError) as err:
                raise _ApiError(400, "invalid_parameter", str(err)) from err
            conditions.append("ca.pair_number = ?")
            params.append(pair_num)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        offset = (page - 1) * per_page

        lesson_tasks = _load_task_map("lesson")
        with connect(db_path) as conn:
            total = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM class_assignments ca
                    JOIN topics t ON t.id = ca.topic_id
                    {where};
                    """,
                    params,
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"""
                SELECT ca.id, ca.group_id, g.name AS group_name,
                       ca.topic_id, t.name AS topic_name,
                       t.subject_id, subj.name AS subject_name,
                       ca.lesson_date, ca.pair_number,
                       ca.created_at, ca.updated_at, ca.archived_at,
                       COUNT(s.id) AS submissions_count,
                       SUM(CASE WHEN s.review_status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                       SUM(CASE WHEN s.review_status = 'reviewed' THEN 1 ELSE 0 END) AS reviewed_count
                FROM class_assignments ca
                JOIN groups g ON g.id = ca.group_id
                JOIN topics t ON t.id = ca.topic_id
                JOIN subjects subj ON subj.id = t.subject_id
                LEFT JOIN submissions s ON s.assignment_id = ca.id
                {where}
                GROUP BY ca.id
                ORDER BY ca.lesson_date DESC, ca.pair_number ASC, ca.id ASC
                LIMIT ? OFFSET ?;
                """,
                [*params, per_page, offset],
            ).fetchall()

        items = []
        for r in rows:
            tc_pair = lesson_tasks.get(r["id"])
            tc = tc_pair[0] if tc_pair else None
            att = tc_pair[1] if tc_pair else None
            items.append(
                {
                    "id": r["id"],
                    "group_id": r["group_id"],
                    "group_name": r["group_name"],
                    "subject_id": r["subject_id"],
                    "subject_name": r["subject_name"],
                    "topic_id": r["topic_id"],
                    "topic_name": r["topic_name"],
                    "lesson_date": r["lesson_date"],
                    "pair_number": int(r["pair_number"]),
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "archived_at": r["archived_at"],
                    "has_description": bool(tc and tc.description.strip()),
                    "has_attachment": att is not None,
                    "submissions_count": int(r["submissions_count"] or 0),
                    "pending_count": int(r["pending_count"] or 0),
                    "reviewed_count": int(r["reviewed_count"] or 0),
                }
            )
        return jsonify(items=items, total=total, page=page, per_page=per_page), 200

    @api_bp.get("/assignments/<assignment_id>")
    def api_get_assignment(assignment_id: str):
        aid = _parse_uuid(assignment_id, "assignment_id")
        assignment = assignment_store.get(aid)
        if assignment is None:
            raise _ApiError(404, "not_found", f"Assignment {aid} not found")

        topic = catalog_store.get_topic(assignment.topic_id, include_archived=True)
        subject = catalog_store.get_subject(assignment.subject_id, include_archived=True)

        lesson_tc = task_content_store.get("lesson", aid)
        lesson_att = (
            task_content_store.get_attachment(lesson_tc.attachment_id)
            if (lesson_tc and lesson_tc.attachment_id)
            else None
        )
        topic_tc = task_content_store.get("topic", assignment.topic_id)
        topic_att = (
            task_content_store.get_attachment(topic_tc.attachment_id)
            if (topic_tc and topic_tc.attachment_id)
            else None
        )

        subs = submission_store.list_submissions(assignment_id=aid)
        return jsonify(
            id=str(assignment.id),
            group_id=str(assignment.group_id),
            group_name=assignment.group_name,
            subject_id=str(assignment.subject_id),
            subject_name=subject.name if subject else "",
            topic_id=str(assignment.topic_id),
            topic_name=topic.name if topic else "",
            lesson_date=assignment.lesson_date,
            pair_number=assignment.pair_number,
            created_at=assignment.created_at.isoformat(),
            updated_at=assignment.updated_at.isoformat(),
            archived_at=assignment.archived_at.isoformat() if assignment.archived_at else None,
            task=_serialize_task_content(
                lesson_tc,
                lesson_att,
                attachment_url=f"/api/v1/assignments/{aid}/attachment",
            ),
            topic_task=_serialize_task_content(
                topic_tc,
                topic_att,
                attachment_url=f"/api/v1/topics/{assignment.topic_id}/attachment",
            ),
            download_bundle_url=f"/api/v1/assignments/{aid}/bundle",
            submissions=[_serialize_submission(s) for s in subs],
        ), 200

    @api_bp.get("/assignments/<assignment_id>/task")
    def api_get_assignment_task(assignment_id: str):
        aid = _parse_uuid(assignment_id, "assignment_id")
        assignment = assignment_store.get(aid)
        if assignment is None:
            raise _ApiError(404, "not_found", f"Assignment {aid} not found")

        fmt = request.args.get("format", "json").strip().casefold()
        if fmt not in ("json", "raw"):
            raise _ApiError(400, "invalid_parameter", "format must be 'json' or 'raw'")
        fallback = _parse_bool_arg("fallback_to_topic", False)

        source, tc, att = _resolve_lesson_or_topic_task(
            aid, assignment.topic_id, fallback_to_topic=fallback
        )
        if fmt == "raw":
            if tc is None or not tc.description.strip():
                raise _ApiError(404, "not_found", "Assignment has no task description")
            return _task_text_response(tc, f"assignment-{assignment.lesson_date}-pair{assignment.pair_number}")

        att_url = (
            f"/api/v1/topics/{assignment.topic_id}/attachment"
            if source == "topic"
            else f"/api/v1/assignments/{aid}/attachment"
        )
        payload = _serialize_task_content(tc, att, attachment_url=att_url)
        payload["assignment_id"] = str(aid)
        payload["source"] = source
        return jsonify(payload), 200

    @api_bp.get("/assignments/<assignment_id>/attachment")
    def api_download_assignment_attachment(assignment_id: str):
        aid = _parse_uuid(assignment_id, "assignment_id")
        assignment = assignment_store.get(aid)
        if assignment is None:
            raise _ApiError(404, "not_found", f"Assignment {aid} not found")

        fallback = _parse_bool_arg("fallback_to_topic", False)
        _, tc, att = _resolve_lesson_or_topic_task(
            aid, assignment.topic_id, fallback_to_topic=fallback
        )
        if tc is None or att is None:
            raise _ApiError(404, "not_found", "Assignment has no attachment")

        path = task_content_store.get_attachment_path(att.id)
        if not path.is_file():
            raise _ApiError(404, "not_found", "Attachment file missing on disk")
        resp = send_file(path, as_attachment=True, download_name=att.original_name)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @api_bp.get("/assignments/<assignment_id>/bundle")
    def api_download_assignment_bundle(assignment_id: str):
        aid = _parse_uuid(assignment_id, "assignment_id")
        assignment = assignment_store.get(aid)
        if assignment is None:
            raise _ApiError(404, "not_found", f"Assignment {aid} not found")

        fallback = _parse_bool_arg("fallback_to_topic", True)
        topic = catalog_store.get_topic(assignment.topic_id, include_archived=True)
        subject = catalog_store.get_subject(assignment.subject_id, include_archived=True)
        source, tc, att = _resolve_lesson_or_topic_task(
            aid, assignment.topic_id, fallback_to_topic=fallback
        )

        meta = {
            "assignment_id": str(assignment.id),
            "group_id": str(assignment.group_id),
            "group_name": assignment.group_name,
            "subject_id": str(assignment.subject_id),
            "subject_name": subject.name if subject else "",
            "topic_id": str(assignment.topic_id),
            "topic_name": topic.name if topic else "",
            "lesson_date": assignment.lesson_date,
            "pair_number": assignment.pair_number,
            "task_source": source,
            "description_format": tc.description_format if tc else None,
            "attachment_name": att.original_name if att else None,
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "metadata.json",
                json.dumps(meta, indent=2, ensure_ascii=False),
            )
            if tc is not None and tc.description.strip():
                ext = tc.description_format if tc.description_format in ("md", "txt", "html") else "txt"
                zf.writestr(f"task.{ext}", tc.description)
            if att is not None:
                att_path = task_content_store.get_attachment_path(att.id)
                if att_path.is_file():
                    safe_name = Path(att.original_name).name or f"attachment-{att.id}"
                    zf.write(att_path, arcname=f"attachments/{safe_name}")

        buf.seek(0)
        bundle_name = f"assignment-{assignment.lesson_date}-pair{assignment.pair_number}-{str(aid)[:8]}.zip"
        resp = send_file(
            buf,
            mimetype="application/zip",
            as_attachment=True,
            download_name=bundle_name,
        )
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @api_bp.get("/attachments/<attachment_id>/download")
    def api_download_attachment_by_id(attachment_id: str):
        att_id = _parse_uuid(attachment_id, "attachment_id")
        att = task_content_store.get_attachment(att_id)
        if att is None:
            raise _ApiError(404, "not_found", f"Attachment {att_id} not found")
        path = task_content_store.get_attachment_path(att_id)
        if not path.is_file():
            raise _ApiError(404, "not_found", "Attachment file missing on disk")
        resp = send_file(path, as_attachment=True, download_name=att.original_name)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    # --- Submissions, Download, Grading & Review ---

    @api_bp.get("/submissions")
    def api_list_submissions():
        page, per_page = _parse_pagination()
        raw_group = request.args.get("group_id", "").strip()
        raw_subject = request.args.get("subject_id", "").strip()
        raw_topic = request.args.get("topic_id", "").strip()
        raw_assignment = request.args.get("assignment_id", "").strip()
        raw_student = request.args.get("student_id", "").strip()
        review_status = request.args.get("review_status", "").strip() or None
        raw_graded = request.args.get("graded", "").strip()
        grade_val = request.args.get("grade", "").strip() or None
        date_from = _parse_date_arg("date_from")
        date_to = _parse_date_arg("date_to")

        group_id = _parse_uuid(raw_group, "group_id") if raw_group else None
        subject_id = _parse_uuid(raw_subject, "subject_id") if raw_subject else None
        topic_id = _parse_uuid(raw_topic, "topic_id") if raw_topic else None
        assignment_id = _parse_uuid(raw_assignment, "assignment_id") if raw_assignment else None
        student_id = _parse_uuid(raw_student, "student_id") if raw_student else None
        graded = _parse_bool_arg("graded") if raw_graded else None

        try:
            total = submission_store.count_submissions(
                group_id=group_id,
                subject_id=subject_id,
                topic_id=topic_id,
                assignment_id=assignment_id,
                student_id=student_id,
                review_status=review_status,
                graded=graded,
                grade=grade_val,
                date_from=date_from,
                date_to=date_to,
            )
            items = submission_store.list_submissions(
                group_id=group_id,
                subject_id=subject_id,
                topic_id=topic_id,
                assignment_id=assignment_id,
                student_id=student_id,
                review_status=review_status,
                graded=graded,
                grade=grade_val,
                date_from=date_from,
                date_to=date_to,
                limit=per_page,
                offset=(page - 1) * per_page,
            )
        except SubmissionValidationError as err:
            raise _ApiError(400, "validation_error", str(err)) from err

        return jsonify(
            items=[_serialize_submission(s) for s in items],
            total=total,
            page=page,
            per_page=per_page,
        ), 200

    @api_bp.get("/submissions/<submission_id>")
    def api_get_submission(submission_id: str):
        sid = _parse_uuid(submission_id, "submission_id")
        detail = submission_store.get_submission(sid)
        if detail is None:
            raise _ApiError(404, "not_found", f"Submission {sid} not found")

        payload = _serialize_submission(detail)
        if detail.assignment_id is not None:
            source, tc, att = _resolve_lesson_or_topic_task(
                detail.assignment_id, detail.topic_id, fallback_to_topic=True
            )
            att_url = (
                f"/api/v1/topics/{detail.topic_id}/attachment"
                if source == "topic"
                else f"/api/v1/assignments/{detail.assignment_id}/attachment"
            )
            payload["task"] = {
                "source": source,
                **_serialize_task_content(tc, att, attachment_url=att_url),
            }
        else:
            tc = task_content_store.get("topic", detail.topic_id)
            att = (
                task_content_store.get_attachment(tc.attachment_id)
                if (tc and tc.attachment_id)
                else None
            )
            payload["task"] = {
                "source": "topic" if tc else None,
                **_serialize_task_content(
                    tc, att, attachment_url=f"/api/v1/topics/{detail.topic_id}/attachment"
                ),
            }
        return jsonify(payload), 200

    @api_bp.get("/submissions/<submission_id>/download")
    def api_download_submission(submission_id: str):
        sid = _parse_uuid(submission_id, "submission_id")
        try:
            full_path, original_filename = submission_store.get_file(sid)
        except (SubmissionNotFoundError, FileNotFoundError) as err:
            raise _ApiError(404, "not_found", f"Submission file not found: {sid}") from err

        resp = send_file(
            full_path,
            as_attachment=True,
            download_name=original_filename,
        )
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @api_bp.route("/submissions/<submission_id>/grade", methods=["POST", "PUT", "PATCH"])
    def api_grade_submission(submission_id: str):
        sid = _parse_uuid(submission_id, "submission_id")
        data = _get_json_body()
        if "grade" not in data:
            raise _ApiError(400, "validation_error", "Missing required field: grade")

        actor: ApiActor = g.api_actor
        clear_feedback = bool(data.get("clear_feedback", False))
        feedback = data.get("feedback")

        try:
            updated = submission_store.set_grade(
                sid,
                data["grade"],
                feedback=feedback,
                graded_by=actor.actor_label,
                clear_feedback=clear_feedback,
                allowed_grades=allowed_grades,
            )
        except SubmissionNotFoundError as err:
            raise _ApiError(404, "not_found", str(err)) from err
        except SubmissionValidationError as err:
            raise _ApiError(400, "validation_error", str(err)) from err

        return jsonify(_serialize_submission(updated)), 200

    @api_bp.delete("/submissions/<submission_id>/grade")
    def api_clear_submission_grade(submission_id: str):
        sid = _parse_uuid(submission_id, "submission_id")
        reset_status = _parse_bool_arg("reset_status", False)
        if not reset_status and request.is_json:
            body = request.get_json(silent=True)
            if isinstance(body, dict) and "reset_status" in body:
                reset_status = bool(body["reset_status"])

        try:
            updated = submission_store.clear_grade(sid, reset_status=reset_status)
        except SubmissionNotFoundError as err:
            raise _ApiError(404, "not_found", str(err)) from err

        return jsonify(_serialize_submission(updated)), 200

    @api_bp.post("/submissions/<submission_id>/review")
    def api_review_submission(submission_id: str):
        sid = _parse_uuid(submission_id, "submission_id")
        data = _get_json_body()
        status = str(data.get("status", "")).strip()
        feedback = data.get("feedback")

        try:
            submission_store.set_review_status(sid, status, feedback=feedback)
        except SubmissionNotFoundError as err:
            raise _ApiError(404, "not_found", str(err)) from err
        except SubmissionValidationError as err:
            raise _ApiError(400, "validation_error", str(err)) from err

        updated = submission_store.get_submission(sid)
        if updated is None:
            raise _ApiError(404, "not_found", f"Submission {sid} not found")
        return jsonify(_serialize_submission(updated)), 200

    return api_bp
