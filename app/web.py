"""Flask application for homework submissions v2."""

from __future__ import annotations

import hmac
import os
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.exceptions import RequestEntityTooLarge

from .access import AccessStore
from .assignments import (
    AssignmentConflictError,
    AssignmentInUseError,
    AssignmentNotFoundError,
    AssignmentStore,
    AssignmentValidationError,
)
from .catalog_store import (
    CatalogConflictError,
    CatalogNotFoundError,
    CatalogOperationForbiddenError,
    CatalogValidationError,
)
from .database import (
    DatabaseError,
    DatabaseValidationError,
    connect,
    validate,
)
from .sqlite_catalog import SqliteCatalogStore
from .storage import (
    DEFAULT_ALLOWED_UPLOAD_EXTENSIONS,
    normalize_upload_extensions,
    validated_upload_suffix,
)
from .student_cleanup import analyze_students_db, apply_cleanup_plan
from .student_names import normalize_student_name
from .submissions import (
    SubmissionAccessError,
    SubmissionNotFoundError,
    SubmissionStore,
    SubmissionValidationError,
)
from .task_content import TaskContentStore
from .task_rendering import render_task


def _format_date_short(iso_date: str) -> str:
    parts = iso_date.split("-")
    if len(parts) == 3:
        return f"{parts[2]}.{parts[1]}"
    return iso_date


def _format_bytes(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} Б"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} КБ"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} МБ"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} ГБ"


def _parse_deadline_input(value: str) -> datetime | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        parsed = parsed.replace(tzinfo=local_tz)
    return parsed.astimezone(timezone.utc)


def _format_local_input(value: datetime | None) -> str:
    if value is None:
        return ""
    local_tz = datetime.now().astimezone().tzinfo
    local_dt = value.astimezone(local_tz)
    return local_dt.strftime("%Y-%m-%dT%H:%M")


def _validate_secret_key(value: object) -> None:
    error_msg = "SECRET_KEY must be a non-placeholder secret of at least 32 bytes"
    if not isinstance(value, (str, bytes)):
        raise TypeError(error_msg)

    if isinstance(value, str):
        stripped = value.strip().casefold()
        if not stripped or stripped in ("dev-secret", "replace-with-a-long-random-secret"):
            raise RuntimeError(error_msg)
        if len(value.encode("utf-8")) < 32:
            raise RuntimeError(error_msg)
    else:
        stripped_b = value.strip().lower()
        if not stripped_b or stripped_b in (b"dev-secret", b"replace-with-a-long-random-secret"):
            raise RuntimeError(error_msg)
        if len(value) < 32:
            raise RuntimeError(error_msg)


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY"),
        ADMIN_PASSWORD=os.getenv("ADMIN_PASSWORD", ""),
        PRIVATE_DATA_DIR=Path(os.getenv("PRIVATE_DATA_DIR", ".private")),
        STORAGE_ROOT=Path(os.getenv("STORAGE_ROOT", "storage")),
    )
    if config:
        app.config.update(config)

    _validate_secret_key(app.config.get("SECRET_KEY"))

    if "ALLOWED_UPLOAD_EXTENSIONS" in app.config:
        allowed_ext_raw = app.config["ALLOWED_UPLOAD_EXTENSIONS"]
    elif "ALLOWED_UPLOAD_EXTENSIONS" in os.environ:
        allowed_ext_raw = os.environ["ALLOWED_UPLOAD_EXTENSIONS"]
    else:
        allowed_ext_raw = DEFAULT_ALLOWED_UPLOAD_EXTENSIONS

    app.config["ALLOWED_UPLOAD_EXTENSIONS"] = normalize_upload_extensions(allowed_ext_raw)
    app.config.setdefault("WTF_CSRF_METHODS", {"POST", "PUT", "PATCH", "DELETE"})
    app.config.setdefault("WTF_CSRF_TIME_LIMIT", 3600)
    if "SESSION_COOKIE_HTTPONLY" not in (config or {}):
        app.config["SESSION_COOKIE_HTTPONLY"] = True
    if "SESSION_COOKIE_SAMESITE" not in (config or {}):
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    CSRFProtect(app)

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        message = "Сессия устарела. Обновите страницу и повторите действие."
        accept = request.headers.get("Accept", "")
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        if "application/json" in accept or request.is_json or is_ajax:
            return jsonify({"error": "csrf_error", "message": message}), 400
        return Response(message, status=400, mimetype="text/plain; charset=utf-8")

    upload_mb = app.config.get("MAX_UPLOAD_MB")
    if upload_mb is None and "MAX_UPLOAD_MB" in os.environ:
        upload_mb = int(os.environ["MAX_UPLOAD_MB"])

    upload_gb = app.config.get("MAX_UPLOAD_GB")
    if upload_gb is None and "MAX_UPLOAD_GB" in os.environ:
        upload_gb = int(os.environ["MAX_UPLOAD_GB"])

    if upload_mb is not None:
        upload_mb = int(upload_mb)
        app.config["MAX_UPLOAD_MB"] = upload_mb
        app.config["MAX_CONTENT_LENGTH"] = upload_mb * 1024 * 1024
        app.config["LIMIT_LABEL"] = f"{upload_mb} МБ"
    elif upload_gb is not None:
        upload_gb = int(upload_gb)
        app.config["MAX_UPLOAD_GB"] = upload_gb
        app.config["MAX_CONTENT_LENGTH"] = upload_gb * 1024 * 1024 * 1024
        app.config["LIMIT_LABEL"] = f"{upload_gb} ГБ"
    else:
        max_upload_mb = int(os.getenv("MAX_UPLOAD_MB", "200"))
        app.config["MAX_UPLOAD_MB"] = max_upload_mb
        app.config["MAX_CONTENT_LENGTH"] = max_upload_mb * 1024 * 1024
        app.config["LIMIT_LABEL"] = f"{max_upload_mb} МБ"

    private_dir = Path(app.config.get("PRIVATE_DATA_DIR", ".private"))
    db_path = app.config.get("DATABASE_PATH")
    if db_path is None:
        db_path = Path(os.getenv("DATABASE_PATH", str(private_dir / "app_v2.sqlite3")))
    db_path = Path(db_path)
    if not db_path.is_absolute():
        try:
            db_path.relative_to(private_dir)
        except ValueError:
            db_path = private_dir / db_path
    app.config["DATABASE_PATH"] = db_path

    storage_root = app.config.get("STORAGE_ROOT")
    if storage_root is None:
        storage_root = Path(os.getenv("STORAGE_ROOT", "storage"))
    storage_root = Path(storage_root)
    if not storage_root.is_absolute():
        storage_root = Path(app.root_path) / storage_root
    app.config["STORAGE_ROOT"] = storage_root

    # Database strict validation
    try:
        validate(db_path)
    except (DatabaseValidationError, DatabaseError, FileNotFoundError) as e:
        raise RuntimeError(
            f"Database at {db_path} is missing or invalid ({e}). Please run 'migrate_v2 apply' or 'init' before starting the app."
        ) from e

    catalog_store = SqliteCatalogStore(db_path)
    access_store = AccessStore(db_path)
    assignment_store = AssignmentStore(db_path)
    submission_store = SubmissionStore(db_path, app.config["STORAGE_ROOT"])
    task_attachments_dir = Path(
        app.config.get("TASK_ATTACHMENTS_DIR", private_dir / "task-attachments")
    )
    task_content_store = TaskContentStore(db_path, task_attachments_dir)

    def is_admin() -> bool:
        return bool(session.get("admin"))

    def form_uuid(field: str) -> UUID:
        try:
            return UUID(request.form.get(field, ""))
        except (ValueError, TypeError):
            abort(400)

    def topic_json(item):
        return {"id": str(item.id), "name": item.name, "subject_id": str(item.subject_id)}

    def name_conflict(error: CatalogConflictError, topics: bool = False):
        if topics:
            return jsonify(error="topic_name_conflict", names=error.names), 400
        return jsonify(error="name_conflict"), 400

    def resolve_group_uuid(raw_group: str) -> UUID | None:
        cleaned = raw_group.strip()
        if not cleaned:
            return None
        try:
            return UUID(cleaned)
        except (ValueError, TypeError):
            found = next(
                (
                    g
                    for g in catalog_store.list_groups()
                    if g.name.casefold() == cleaned.casefold()
                ),
                None,
            )
            return found.id if found else None

    def serialize_student_assignments(assigned, snapshot) -> list[dict]:
        subject_map = {s.id: s.name for s in snapshot.subjects}
        topic_map = {t.id: t.name for t in snapshot.topics}
        result: list[dict] = []
        for a in assigned:
            if a.subject_id not in subject_map or a.topic_id not in topic_map:
                continue
            tc = task_content_store.get("lesson", a.id)
            has_desc = bool(tc and tc.description and tc.description.strip())
            has_att = bool(tc and tc.attachment_id)
            result.append(
                {
                    "id": str(a.id),
                    "subject_id": str(a.subject_id),
                    "subject_name": subject_map.get(a.subject_id, ""),
                    "topic_id": str(a.topic_id),
                    "topic_name": topic_map.get(a.topic_id, ""),
                    "lesson_date": a.lesson_date,
                    "pair_number": a.pair_number,
                    "has_description": has_desc,
                    "has_attachment": has_att,
                    "label": (
                        f"{_format_date_short(a.lesson_date)}, пара {a.pair_number} — "
                        f"{topic_map.get(a.topic_id, '')}"
                    ),
                }
            )
        return result

    def stage_task_payload(
        *, restrict_existing_att_id: UUID | None = None
    ) -> tuple[str, str, UUID | None, tuple[UUID, str, int] | None]:
        """Parse and stage task description/attachment from request form and files."""
        description = request.form.get("description", "")
        description_format = request.form.get("description_format", "txt").strip().lower()
        if description_format not in ("html", "md", "txt"):
            abort(400)

        raw_att_id = request.form.get("attachment_id", "").strip()
        remove_attachment = request.form.get("remove_attachment", "0").strip() == "1"
        file = request.files.get("attachment")
        has_new_file = bool(file and file.filename and file.filename.strip())

        if remove_attachment and has_new_file:
            abort(400)

        staged_att: tuple[UUID, str, int] | None = None
        if has_new_file and file is not None and file.filename:
            file_bytes = file.read()
            if len(file_bytes) > app.config["MAX_CONTENT_LENGTH"]:
                abort(413)
            new_att_id = uuid4()
            task_content_store.write_attachment_file(new_att_id, file_bytes)
            staged_att = (new_att_id, file.filename.strip(), len(file_bytes))
            final_att_id: UUID | None = new_att_id
        elif remove_attachment:
            final_att_id = None
        elif raw_att_id:
            try:
                req_att = UUID(raw_att_id)
            except ValueError:
                abort(400)
            if restrict_existing_att_id is not None:
                if req_att != restrict_existing_att_id:
                    abort(400)
            else:
                att = task_content_store.get_attachment(req_att)
                if att is None or not task_content_store.get_attachment_path(req_att).is_file():
                    abort(400)
            final_att_id = req_att
        else:
            final_att_id = None

        return description, description_format, final_att_id, staged_att

    def verify_student_session() -> tuple[UUID, int]:
        if session.get("student_session_schema") != 2:
            abort(403)
        session_group_id = session.get("student_group_id")
        session_version = session.get("student_access_version")
        if not session_group_id or session_version is None:
            abort(403)
        try:
            group_uuid = UUID(str(session_group_id))
            version = int(session_version)
        except (ValueError, TypeError):
            abort(403)

        if not access_store.permits(group_uuid, version):
            abort(403)
        return group_uuid, version

    @app.get("/favicon.ico")
    def favicon():
        return app.send_static_file("favicon.ico")

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            catalog=catalog_store.snapshot(),
            limit=app.config.get("LIMIT_LABEL", "200 МБ"),
            max_bytes=app.config.get("MAX_CONTENT_LENGTH", 200 * 1024 * 1024),
            allowed_extensions=sorted(app.config["ALLOWED_UPLOAD_EXTENSIONS"]),
        )

    @app.post("/unlock")
    def unlock():
        if request.is_json:
            data = request.get_json(silent=True) or {}
        else:
            data = request.form

        raw_group = str(data.get("group_id") or data.get("group", ""))
        raw_password = str(data.get("password", ""))
        if not raw_group or not raw_password:
            abort(403)

        group_uuid = resolve_group_uuid(raw_group)
        if not group_uuid:
            abort(403)

        ok, access = access_store.authorize(group_uuid, raw_password)
        if not ok or access is None:
            abort(403)

        group_item = catalog_store.get_group(group_uuid)
        group_name = group_item.name if group_item else ""

        session["student_group_id"] = str(group_uuid)
        session["student_group"] = group_name
        session["student_access_version"] = access.version
        session["student_session_schema"] = 2

        assigned = assignment_store.list_for_group(group_uuid)
        snapshot = catalog_store.snapshot()
        assignments_data = serialize_student_assignments(assigned, snapshot)

        students = submission_store.list_students(group_uuid, include_archived=False)
        students_data = [{"id": str(s.id), "display_name": s.display_name} for s in students]

        return jsonify(
            status="ok",
            group_id=str(group_uuid),
            group_name=group_name,
            group=group_name,
            assignments=assignments_data,
            students=students_data,
        ), 200

    @app.get("/student/assignments")
    def student_assignments():
        group_uuid, _ = verify_student_session()
        group_item = catalog_store.get_group(group_uuid)
        group_name = group_item.name if group_item else ""
        assigned = assignment_store.list_for_group(group_uuid)
        snapshot = catalog_store.snapshot()
        assignments_data = serialize_student_assignments(assigned, snapshot)
        return jsonify(
            status="ok",
            group_id=str(group_uuid),
            group=group_name,
            assignments=assignments_data,
        ), 200

    @app.get("/student/assignments/<assignment_id>")
    def student_assignment_page(assignment_id):
        group_uuid, _ = verify_student_session()
        try:
            uid = UUID(assignment_id)
        except (ValueError, TypeError):
            abort(400)

        group_item = catalog_store.get_group(group_uuid)
        if group_item is None:
            abort(403)

        assignment = assignment_store.get(uid)
        if assignment is None or assignment.archived_at is not None or assignment.group_id != group_uuid:
            abort(404)

        topic = catalog_store.get_topic(assignment.topic_id)
        subject = catalog_store.get_subject(assignment.subject_id) if topic else None
        if subject is None or topic is None:
            abort(404)

        content = task_content_store.get("lesson", uid)
        if content and content.description.strip():
            rendered_description = render_task(content.description, content.description_format)
            description_format = content.description_format
        else:
            rendered_description = ""
            description_format = "txt"

        attachment = None
        attachment_size_formatted = ""
        if content and content.attachment_id:
            attachment = task_content_store.get_attachment(content.attachment_id)
            if attachment:
                attachment_size_formatted = _format_bytes(attachment.size_bytes)

        query_params = {}
        for key in ("q", "subject_id", "date"):
            val = request.args.get(key, "").strip()
            if val:
                query_params[key] = val
        return_url = url_for("index", **query_params)

        date_parts = assignment.lesson_date.split("-")
        date_formatted = (
            f"{date_parts[2]}.{date_parts[1]}.{date_parts[0]}"
            if len(date_parts) == 3
            else assignment.lesson_date
        )

        return render_template(
            "student_assignment.html",
            assignment=assignment,
            lesson_date_formatted=date_formatted,
            subject=subject,
            topic=topic,
            group_name=group_item.name,
            rendered_description=rendered_description,
            description_format=description_format,
            attachment=attachment,
            attachment_size_formatted=attachment_size_formatted,
            return_url=return_url,
            limit=app.config["LIMIT_LABEL"],
            max_bytes=app.config["MAX_CONTENT_LENGTH"],
            allowed_extensions=sorted(app.config["ALLOWED_UPLOAD_EXTENSIONS"]),
        )

    @app.get("/student/assignments/<assignment_id>/attachment")
    def student_download_attachment(assignment_id):
        group_uuid, _ = verify_student_session()
        try:
            uid = UUID(assignment_id)
        except (ValueError, TypeError):
            abort(400)

        group_item = catalog_store.get_group(group_uuid)
        if group_item is None:
            abort(403)

        assignment = assignment_store.get(uid)
        if assignment is None or assignment.archived_at is not None or assignment.group_id != group_uuid:
            abort(404)

        content = task_content_store.get("lesson", uid)
        if content is None or content.attachment_id is None:
            abort(404)

        att = task_content_store.get_attachment(content.attachment_id)
        if att is None:
            abort(404)

        path = task_content_store.get_attachment_path(att.id)
        if not path.is_file():
            abort(404)

        response = send_file(path, as_attachment=True, download_name=att.original_name)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/student/students")
    def student_list_students():
        group_uuid, _ = verify_student_session()
        students = submission_store.list_students(group_uuid, include_archived=False)
        return jsonify([{"id": str(s.id), "display_name": s.display_name} for s in students]), 200

    @app.post("/student/students/check")
    def student_check_student_name():
        group_uuid, _ = verify_student_session()
        if request.is_json:
            data = request.get_json(silent=True) or {}
        else:
            data = request.form

        raw_name = data.get("display_name", "")
        if not isinstance(raw_name, str):
            abort(400)

        if not raw_name.strip():
            return jsonify(
                normalized_name="",
                warnings=[],
                warning_codes=[],
                was_modified=False,
                is_paired=False,
                paired_parts=[],
                exact_matches=[],
                fuzzy_matches=[],
            ), 200

        try:
            norm = normalize_student_name(raw_name)
        except ValueError as err:
            return jsonify(
                error="validation_error",
                message=str(err),
                normalized_name="",
                warnings=["Введите фамилию и имя буквами — формат может быть неверным."],
                warning_codes=["invalid_name"],
                was_modified=False,
                is_paired=False,
                paired_parts=[],
                exact_matches=[],
                fuzzy_matches=[],
            ), 200

        exact_matches, fuzzy_matches = submission_store.find_matching_students(
            group_uuid, norm.display_name, include_archived=False
        )
        return jsonify(
            normalized_name=norm.display_name,
            warnings=list(norm.warnings),
            warning_codes=list(norm.warning_codes),
            was_modified=norm.was_modified,
            is_paired=norm.is_paired,
            paired_parts=list(norm.paired_parts),
            exact_matches=[{"id": str(s.id), "display_name": s.display_name} for s in exact_matches],
            fuzzy_matches=[{"id": str(s.id), "display_name": s.display_name} for s in fuzzy_matches],
        ), 200

    @app.post("/student/students")
    def student_create_student():
        group_uuid, _ = verify_student_session()
        if request.is_json:
            data = request.get_json(silent=True) or {}
        else:
            data = request.form

        raw_name = data.get("display_name", "")
        if not isinstance(raw_name, str):
            abort(400)
        cleaned_raw = unicodedata.normalize("NFC", raw_name.strip())
        if not cleaned_raw:
            return jsonify(error="display_name cannot be empty"), 400

        try:
            norm = normalize_student_name(cleaned_raw)
        except ValueError as err:
            return jsonify(error=str(err)), 400

        if norm.is_paired:
            return jsonify(
                error="paired_names",
                message="Укажите только одного студента в формате «Фамилия Имя».",
                warnings=list(norm.warnings),
                paired_parts=list(norm.paired_parts),
            ), 400

        def _is_truthy(val: object) -> bool:
            return val is True or (isinstance(val, str) and val.strip().lower() in ("1", "true", "yes"))

        confirm_duplicate = _is_truthy(data.get("confirm_duplicate", False))
        confirm_similar = confirm_duplicate or _is_truthy(data.get("confirm_similar", False))

        exact_matches, fuzzy_matches = submission_store.find_matching_students(
            group_uuid, norm.display_name, include_archived=False
        )
        if exact_matches and not confirm_duplicate:
            if len(exact_matches) == 1:
                matched = exact_matches[0]
                return jsonify(
                    id=str(matched.id),
                    display_name=matched.display_name,
                    existing=True,
                    normalized_name=norm.display_name,
                    existing_students=[
                        {"id": str(matched.id), "display_name": matched.display_name}
                    ],
                    warnings=list(norm.warnings),
                    warning_codes=list(norm.warning_codes),
                    fuzzy_matches=[
                        {"id": str(s.id), "display_name": s.display_name} for s in fuzzy_matches
                    ],
                ), 200
            return jsonify(
                error="multiple_exact_students",
                message=(
                    "В списке группы найдено несколько однофамильцев. "
                    "Выберите нужную запись или подтвердите создание новой."
                ),
                normalized_name=norm.display_name,
                existing_students=[
                    {"id": str(s.id), "display_name": s.display_name} for s in exact_matches
                ],
                fuzzy_matches=[
                    {"id": str(s.id), "display_name": s.display_name} for s in fuzzy_matches
                ],
                warnings=list(norm.warnings),
                warning_codes=list(norm.warning_codes),
            ), 409

        if fuzzy_matches and not confirm_similar:
            return jsonify(
                error="similar_students",
                message=(
                    "В списке группы уже есть похожий студент. "
                    "Выберите его из списка или подтвердите создание нового."
                ),
                normalized_name=norm.display_name,
                fuzzy_matches=[
                    {"id": str(s.id), "display_name": s.display_name} for s in fuzzy_matches
                ],
                warnings=list(norm.warnings),
                warning_codes=list(norm.warning_codes),
            ), 409

        try:
            student, info = submission_store.create_student_with_info(group_uuid, cleaned_raw)
        except SubmissionValidationError as err:
            return jsonify(error=str(err)), 400
        except SubmissionNotFoundError:
            abort(404)

        return jsonify(
            id=str(student.id),
            display_name=student.display_name,
            existing=False,
            warnings=list(info.warnings),
            warning_codes=list(info.warning_codes),
            was_modified=info.was_modified,
            fuzzy_matches=[{"id": str(s.id), "display_name": s.display_name} for s in fuzzy_matches],
        ), 201

    @app.post("/submit")
    def submit():
        group_uuid, version = verify_student_session()
        group_item = catalog_store.get_group(group_uuid)
        if group_item is None:
            abort(403)

        req_group_id = request.form.get("group_id", "").strip()
        req_group_name = request.form.get("group", "").strip()
        if req_group_id:
            if req_group_id != str(group_uuid):
                abort(403)
        elif req_group_name:
            if req_group_name != group_item.name:
                abort(403)
        else:
            abort(403)

        file = request.files.get("file")
        if not (file and file.filename):
            abort(400)

        req_student_id = request.form.get("student_id", "").strip()
        req_student_name = request.form.get("student_name", "").strip()
        student_warnings: list[str] = []
        student_warning_codes: list[str] = []
        student_display_name: str | None = None

        if req_student_id:
            try:
                student_uuid = UUID(req_student_id)
            except (ValueError, TypeError):
                abort(400)
            st_obj = submission_store.get_student(student_uuid)
            if st_obj is not None:
                student_display_name = st_obj.display_name
        elif req_student_name:
            try:
                norm = normalize_student_name(req_student_name)
            except ValueError as err:
                return jsonify(error="validation_error", message=str(err)), 400

            if norm.is_paired:
                return jsonify(
                    error="validation_error",
                    message="Укажите одного студента в формате «Фамилия Имя».",
                    warnings=list(norm.warnings),
                ), 400

            student_warnings = list(norm.warnings)
            student_warning_codes = list(norm.warning_codes)
            exact_matches, _ = submission_store.find_matching_students(
                group_uuid, norm.display_name, include_archived=False
            )
            if exact_matches:
                student_uuid = exact_matches[0].id
                student_display_name = exact_matches[0].display_name
            else:
                try:
                    created_st, info = submission_store.create_student_with_info(
                        group_uuid, req_student_name
                    )
                    student_uuid = created_st.id
                    student_display_name = created_st.display_name
                    student_warnings = list(info.warnings)
                    student_warning_codes = list(info.warning_codes)
                except SubmissionValidationError as err:
                    return jsonify(error="validation_error", message=str(err)), 400
        else:
            abort(400)

        req_assignment_id = request.form.get("assignment_id", "").strip()
        if req_assignment_id:
            try:
                assignment_uuid = UUID(req_assignment_id)
            except (ValueError, TypeError):
                abort(400)
        else:
            discipline = request.form.get("discipline", "").strip()
            work = request.form.get("work", "").strip()
            if not (discipline and work):
                abort(400)
            snapshot = catalog_store.snapshot()
            subj_match = next(
                (s for s in snapshot.subjects if s.name.casefold() == discipline.casefold()),
                None,
            )
            topic_match = (
                next(
                    (
                        t
                        for t in snapshot.topics
                        if t.subject_id == subj_match.id and t.name.casefold() == work.casefold()
                    ),
                    None,
                )
                if subj_match
                else None
            )
            if not topic_match:
                abort(400)
            matching_assignments = [
                a
                for a in assignment_store.list_for_group(group_uuid)
                if a.topic_id == topic_match.id
            ]
            if not matching_assignments:
                abort(400)
            assignment_uuid = matching_assignments[-1].id

        try:
            validated_upload_suffix(file.filename, app.config["ALLOWED_UPLOAD_EXTENSIONS"])
        except ValueError:
            return jsonify({"error": "file_type_not_allowed", "message": "Недопустимый формат файла."}), 400

        try:
            sub, sub_file = submission_store.submit(
                group_id=group_uuid,
                student_id=student_uuid,
                assignment_id=assignment_uuid,
                source=file.stream,
                original_filename=file.filename,
                access_version=version,
            )
        except SubmissionAccessError as err:
            return jsonify(error="access_error", message=str(err)), 403
        except SubmissionNotFoundError as err:
            return jsonify(error="not_found", message=str(err)), 400
        except SubmissionValidationError as err:
            return jsonify(error="validation_error", message=str(err)), 400

        return jsonify(
            submission_id=str(sub.id),
            attempt_number=sub.attempt_number,
            filename=sub_file.original_filename,
            student_display_name=student_display_name,
            warnings=student_warnings,
            warning_codes=student_warning_codes,
        ), 201

    # --- Admin Routes ---

    @app.get("/admin")
    def admin_dashboard():
        if not is_admin():
            return render_template("admin.html", authenticated=False)
        return render_template("admin.html", authenticated=True)

    @app.post("/admin/login")
    def admin_login():
        admin_password = str(app.config.get("ADMIN_PASSWORD", ""))
        submitted = request.form.get("password", "")
        if not admin_password or not hmac.compare_digest(submitted, admin_password):
            abort(403)

        session["admin"] = True
        return ("", 204)

    @app.post("/admin/logout")
    def admin_logout():
        session.pop("admin", None)
        return redirect(url_for("admin_dashboard"))

    @app.get("/admin/access")
    def admin_access():
        if not is_admin():
            abort(403)
        active_groups = catalog_store.list_groups(include_archived=False)
        groups_data = []
        for g in active_groups:
            acc = access_store.get(g.id)
            groups_data.append(
                {
                    "group_id": str(g.id),
                    "group_name": g.name,
                    "is_open": acc.is_open if acc else False,
                    "closes_at": _format_local_input(acc.closes_at) if acc else "",
                }
            )
        return render_template("admin_access.html", groups=groups_data, section="access")

    @app.post("/admin/access")
    def admin_update_group():
        if not is_admin():
            abort(403)

        raw_group = request.form.get("group_id") or request.form.get("group", "")
        group_uuid = resolve_group_uuid(str(raw_group))
        if not group_uuid:
            abort(400)

        password = request.form.get("password", "").strip() or None
        is_open = request.form.get("is_open") == "1"
        raw_deadline = request.form.get("closes_at", "")

        try:
            deadline = _parse_deadline_input(raw_deadline)
        except ValueError:
            abort(400)

        group_obj = catalog_store.get_group(group_uuid)
        if not group_obj:
            abort(400)

        try:
            access_store.configure(group_uuid, is_open, password, deadline)
        except ValueError:
            abort(400)

        return ("", 204)

    @app.get("/admin/assignments")
    def admin_assignments_page():
        if not is_admin():
            abort(403)
        snapshot = catalog_store.snapshot()
        selected_group_id = request.args.get("group_id", "").strip()
        if not selected_group_id and snapshot.groups:
            legacy_group_name = request.args.get("group", "").strip()
            matched = next((g for g in snapshot.groups if g.name == legacy_group_name), None)
            selected_group_id = str(matched.id) if matched else str(snapshot.groups[0].id)

        selected_group_obj = next(
            (g for g in snapshot.groups if str(g.id) == selected_group_id), None
        )
        selected_group_name = selected_group_obj.name if selected_group_obj else ""

        selected_subject_id = None
        raw_subj = request.args.get("subject_id", "").strip()
        if raw_subj:
            try:
                selected_subject_id = UUID(raw_subj)
            except ValueError:
                pass

        if selected_group_id and selected_subject_id:
            assignments = assignment_store.list_for_group_and_subject(
                UUID(selected_group_id), selected_subject_id
            )
        elif selected_group_id:
            assignments = assignment_store.list_for_group(UUID(selected_group_id))
        else:
            assignments = assignment_store.list_all()

        subject_map = {s.id: s.name for s in snapshot.subjects}
        topic_map = {t.id: t.name for t in snapshot.topics}

        items = [
            {
                "id": str(a.id),
                "lesson_date": a.lesson_date,
                "lesson_date_formatted": (
                    f"{a.lesson_date.split('-')[2]}.{a.lesson_date.split('-')[1]}.{a.lesson_date.split('-')[0]}"
                ),
                "pair_number": a.pair_number,
                "subject_id": str(a.subject_id),
                "subject_name": subject_map.get(a.subject_id, ""),
                "topic_id": str(a.topic_id),
                "topic_name": topic_map.get(a.topic_id, ""),
            }
            for a in assignments
        ]

        return render_template(
            "admin_assignments.html",
            section="assignments",
            groups=snapshot.groups,
            subjects=snapshot.subjects,
            topics=snapshot.topics,
            selected_group_id=selected_group_id,
            selected_group=selected_group_name,
            selected_subject_id=str(selected_subject_id) if selected_subject_id else "",
            assignments=items,
            unique_subjects_count=len({a.subject_id for a in assignments}),
        )

    @app.get("/admin/assignments/new")
    def admin_new_assignment_page():
        if not is_admin():
            abort(403)
        snapshot = catalog_store.snapshot()
        groups = snapshot.groups
        subjects = snapshot.subjects
        topics = snapshot.topics

        raw_group = request.args.get("group", "").strip() or request.args.get("group_id", "").strip()
        matched_group = next(
            (g for g in groups if str(g.id) == raw_group or g.name == raw_group), None
        )
        if matched_group:
            selected_group = matched_group.name
        elif groups:
            selected_group = groups[0].name
        else:
            selected_group = ""

        selected_subject_id = request.args.get("subject_id", "").strip()
        if not selected_subject_id and subjects:
            selected_subject_id = str(subjects[0].id)
        selected_date = request.args.get("date", "").strip()
        if not selected_date:
            selected_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            selected_pair = int(request.args.get("pair", "1"))
            if selected_pair < 1 or selected_pair > 6:
                selected_pair = 1
        except ValueError:
            selected_pair = 1

        return render_template(
            "admin_task_editor.html",
            mode="new_lesson",
            form_action=url_for("admin_create_assignment"),
            cancel_url=url_for(
                "admin_assignments_page", group=selected_group, subject_id=selected_subject_id
            ),
            page_title="Новое занятие с заданием",
            hero_eyebrow="Панель преподавателя",
            hero_lead="Запланируйте дату и пару, подготовьте описание задания и прикрепите файлы.",
            section="assignments",
            groups=groups,
            subjects=subjects,
            topics=topics,
            selected_group=selected_group,
            selected_subject_id=selected_subject_id,
            selected_topic_id="",
            selected_date=selected_date,
            selected_pair=selected_pair,
            task_content=None,
            attachment=None,
            attachment_size_formatted="",
            limit_label=app.config["LIMIT_LABEL"],
            allowed_extensions=sorted(app.config["ALLOWED_UPLOAD_EXTENSIONS"]),
        )

    @app.get("/admin/assignments/<assignment_id>/edit")
    def admin_edit_assignment_page(assignment_id):
        if not is_admin():
            abort(403)
        try:
            uid = UUID(assignment_id)
        except (ValueError, TypeError):
            abort(400)

        assignment = assignment_store.get(uid)
        if assignment is None or assignment.archived_at is not None:
            abort(404)

        snapshot = catalog_store.snapshot()
        subjects = snapshot.subjects
        topics = [t for t in snapshot.topics if t.subject_id == assignment.subject_id]
        subject = next((s for s in subjects if s.id == assignment.subject_id), None)

        content = task_content_store.get("lesson", uid)
        att = (
            task_content_store.get_attachment(content.attachment_id)
            if (content and content.attachment_id)
            else None
        )

        return render_template(
            "admin_task_editor.html",
            mode="edit_lesson",
            form_action=url_for("admin_update_assignment", assignment_id=assignment.id),
            cancel_url=url_for(
                "admin_assignments_page",
                group_id=str(assignment.group_id),
                subject_id=str(assignment.subject_id),
            ),
            page_title="Редактирование занятия и задания",
            hero_eyebrow="Панель преподавателя",
            hero_lead="Измените параметры расписания или отредактируйте задание и материалы к занятию.",
            section="assignments",
            assignment=assignment,
            subject=subject,
            topics=topics,
            task_content=content,
            attachment=att,
            attachment_size_formatted=_format_bytes(att.size_bytes) if att else "",
            limit_label=app.config["LIMIT_LABEL"],
            allowed_extensions=sorted(app.config["ALLOWED_UPLOAD_EXTENSIONS"]),
        )

    @app.post("/admin/assignments")
    def admin_create_assignment():
        if not is_admin():
            abort(403)

        raw_group = request.form.get("group_id", "").strip() or request.form.get("group_name", "").strip()
        group_id = resolve_group_uuid(raw_group)
        raw_topic = request.form.get("topic_id", "").strip()
        lesson_date = request.form.get("lesson_date", "").strip()
        raw_pair = request.form.get("pair_number", "").strip()

        if group_id is None:
            abort(400)

        try:
            topic_id = UUID(raw_topic)
            pair_number = int(raw_pair)
        except (ValueError, TypeError):
            abort(400)

        has_task_payload = any(
            k in request.form
            for k in ("description", "description_format", "attachment_id", "remove_attachment")
        ) or bool(
            request.files.get("attachment")
            and request.files["attachment"].filename
            and request.files["attachment"].filename.strip()
        )
        description = ""
        description_format = "txt"
        final_att_id: UUID | None = None
        staged_att: tuple[UUID, str, int] | None = None
        if has_task_payload:
            description, description_format, final_att_id, staged_att = stage_task_payload()

        orphans_to_discard: list[UUID] = []
        try:
            with assignment_store.connect() as conn:
                item = assignment_store.create(
                    group_id, topic_id, lesson_date, pair_number, conn=conn
                )
                if staged_att:
                    task_content_store.register_attachment(
                        staged_att[0], staged_att[1], staged_att[2], conn=conn
                    )
                if has_task_payload:
                    _, orphans = task_content_store.set_content(
                        "lesson", item.id, description, description_format, final_att_id, conn=conn
                    )
                    orphans_to_discard.extend(orphans)
                conn.commit()
        except AssignmentConflictError as err:
            if staged_att:
                task_content_store.discard_attachment_file(staged_att[0])
            return jsonify(error="assignment_conflict", message=str(err)), 409
        except AssignmentValidationError as err:
            if staged_att:
                task_content_store.discard_attachment_file(staged_att[0])
            return jsonify(error="validation_error", message=str(err)), 400
        except AssignmentNotFoundError:
            if staged_att:
                task_content_store.discard_attachment_file(staged_att[0])
            abort(404)
        except Exception:
            if staged_att:
                task_content_store.discard_attachment_file(staged_att[0])
            raise

        for orphan_id in orphans_to_discard:
            task_content_store.discard_attachment_file(orphan_id)

        redirect_url = url_for(
            "admin_assignments_page", group_id=str(item.group_id), subject_id=str(item.subject_id)
        )
        return jsonify(
            status="created",
            id=str(item.id),
            group_id=str(item.group_id),
            group_name=item.group_name,
            subject_id=str(item.subject_id),
            topic_id=str(item.topic_id),
            lesson_date=item.lesson_date,
            pair_number=item.pair_number,
            redirect_url=redirect_url,
        ), 201

    @app.post("/admin/assignments/<assignment_id>/update")
    def admin_update_assignment(assignment_id):
        if not is_admin():
            abort(403)
        try:
            uid = UUID(assignment_id)
            topic_id = UUID(request.form.get("topic_id", ""))
            pair_number = int(request.form.get("pair_number", ""))
        except (ValueError, TypeError):
            abort(400)

        lesson_date = request.form.get("lesson_date", "").strip()
        has_task_payload = any(
            k in request.form
            for k in ("description", "description_format", "attachment_id", "remove_attachment")
        ) or bool(
            request.files.get("attachment")
            and request.files["attachment"].filename
            and request.files["attachment"].filename.strip()
        )
        description = ""
        description_format = "txt"
        final_att_id: UUID | None = None
        staged_att: tuple[UUID, str, int] | None = None
        if has_task_payload:
            description, description_format, final_att_id, staged_att = stage_task_payload()

        orphans_to_discard: list[UUID] = []
        try:
            with assignment_store.connect() as conn:
                item = assignment_store.update(
                    uid, lesson_date, pair_number, topic_id, conn=conn
                )
                if staged_att:
                    task_content_store.register_attachment(
                        staged_att[0], staged_att[1], staged_att[2], conn=conn
                    )
                if has_task_payload:
                    _, orphans = task_content_store.set_content(
                        "lesson", uid, description, description_format, final_att_id, conn=conn
                    )
                    orphans_to_discard.extend(orphans)
                conn.commit()
        except AssignmentConflictError as err:
            if staged_att:
                task_content_store.discard_attachment_file(staged_att[0])
            return jsonify(error="assignment_conflict", message=str(err)), 409
        except AssignmentNotFoundError:
            if staged_att:
                task_content_store.discard_attachment_file(staged_att[0])
            abort(404)
        except AssignmentInUseError as err:
            if staged_att:
                task_content_store.discard_attachment_file(staged_att[0])
            return jsonify(error="assignment_in_use", message=str(err)), 409
        except AssignmentValidationError as err:
            if staged_att:
                task_content_store.discard_attachment_file(staged_att[0])
            return jsonify(error="validation_error", message=str(err)), 400
        except Exception:
            if staged_att:
                task_content_store.discard_attachment_file(staged_att[0])
            raise

        for orphan_id in orphans_to_discard:
            task_content_store.discard_attachment_file(orphan_id)

        redirect_url = url_for(
            "admin_assignments_page", group_id=str(item.group_id), subject_id=str(item.subject_id)
        )
        return jsonify(
            status="ok",
            id=str(item.id),
            lesson_date=item.lesson_date,
            pair_number=item.pair_number,
            topic_id=str(item.topic_id),
            redirect_url=redirect_url,
        ), 200

    @app.post("/admin/assignments/<assignment_id>/archive")
    def admin_archive_assignment(assignment_id):
        if not is_admin():
            abort(403)
        try:
            uid = UUID(assignment_id)
        except (ValueError, TypeError):
            abort(400)

        try:
            assignment_store.archive(uid)
        except AssignmentNotFoundError:
            abort(404)
        return ("", 204)

    # --- Task Content Admin Routes ---

    @app.get("/admin/topics/<topic_id>/task")
    def admin_topic_task_page(topic_id):
        if not is_admin():
            abort(403)
        try:
            uid = UUID(topic_id)
        except (ValueError, TypeError):
            abort(400)

        topic = catalog_store.get_topic(uid)
        if topic is None:
            abort(404)

        content = task_content_store.get("topic", uid)
        att = (
            task_content_store.get_attachment(content.attachment_id)
            if (content and content.attachment_id)
            else None
        )

        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            if content is None:
                return jsonify(has_task=False)
            return jsonify(
                has_task=True,
                description=content.description,
                description_format=content.description_format,
                attachment={
                    "id": str(att.id),
                    "original_name": att.original_name,
                    "size_bytes": att.size_bytes,
                }
                if att
                else None,
            )

        subject = catalog_store.get_subject(topic.subject_id)
        return render_template(
            "admin_task_editor.html",
            mode="topic",
            form_action=url_for("admin_save_topic_task", topic_id=topic.id),
            cancel_url=url_for("admin_catalog_page", kind="topics"),
            page_title=f"Задание для темы «{topic.name}»",
            hero_eyebrow="Каталог тем",
            hero_lead="Задайте заготовленное задание к теме, которое можно будет копировать в расписание занятий.",
            section="topics",
            topic=topic,
            subject=subject,
            task_content=content,
            attachment=att,
            attachment_size_formatted=_format_bytes(att.size_bytes) if att else "",
            limit_label=app.config["LIMIT_LABEL"],
        )

    @app.post("/admin/topics/<topic_id>/task")
    def admin_save_topic_task(topic_id):
        if not is_admin():
            abort(403)
        try:
            uid = UUID(topic_id)
        except (ValueError, TypeError):
            abort(400)

        topic = catalog_store.get_topic(uid)
        if topic is None:
            abort(404)

        current = task_content_store.get("topic", uid)
        allowed_att_id = current.attachment_id if current else None
        description, description_format, final_att_id, staged_att = stage_task_payload(
            restrict_existing_att_id=allowed_att_id
        )

        orphans_to_discard: list[UUID] = []
        try:
            with connect(db_path) as conn:
                if staged_att:
                    task_content_store.register_attachment(
                        staged_att[0], staged_att[1], staged_att[2], conn=conn
                    )
                _, orphans = task_content_store.set_content(
                    "topic", uid, description, description_format, final_att_id, conn=conn
                )
                orphans_to_discard.extend(orphans)
                conn.commit()
        except Exception:
            if staged_att:
                task_content_store.discard_attachment_file(staged_att[0])
            raise

        for orphan_id in orphans_to_discard:
            task_content_store.discard_attachment_file(orphan_id)

        if (
            request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html
        ) or request.is_json:
            return jsonify(
                status="ok", redirect_url=url_for("admin_catalog_page", kind="topics")
            ), 200
        return redirect(url_for("admin_catalog_page", kind="topics"))

    @app.post("/admin/tasks/preview")
    def admin_task_preview():
        if not is_admin():
            abort(403)
        description = request.form.get("description", "")
        description_format = request.form.get("description_format", "txt").strip().lower()
        if description_format not in ("html", "md", "txt"):
            abort(400)
        return jsonify(html=render_task(description, description_format))

    @app.get("/admin/tasks/attachments/<attachment_id>")
    def admin_download_attachment(attachment_id):
        if not is_admin():
            abort(403)
        try:
            uid = UUID(attachment_id)
        except (ValueError, TypeError):
            abort(400)

        att = task_content_store.get_attachment(uid)
        if att is None:
            abort(404)
        path = task_content_store.get_attachment_path(uid)
        if not path.is_file():
            abort(404)
        response = send_file(path, as_attachment=True, download_name=att.original_name)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    # --- Catalog Admin Routes ---

    @app.get("/admin/<kind>")
    def admin_catalog_page(kind):
        if not is_admin():
            abort(403)
        if kind not in ("groups", "subjects", "topics"):
            abort(404)
        snapshot = catalog_store.snapshot()
        items = getattr(snapshot, kind)
        return render_template(
            "admin_catalog.html",
            kind=kind,
            items=items,
            section=kind,
            subjects=snapshot.subjects,
            topics=snapshot.topics,
            unassigned_subject_id=snapshot.unassigned_subject_id,
        )

    @app.post("/admin/<kind>")
    def admin_catalog_create(kind):
        if not is_admin():
            abort(403)
        if kind not in ("groups", "subjects", "topics"):
            abort(404)
        singular = kind[:-1]
        try:
            if kind == "topics":
                item = catalog_store.create_topic(
                    form_uuid("subject_id"), request.form.get("name", "")
                )
            else:
                item = getattr(catalog_store, f"create_{singular}")(request.form.get("name", ""))
        except CatalogNotFoundError:
            abort(404)
        except CatalogConflictError as error:
            return name_conflict(error, kind == "topics")
        except (ValueError, CatalogValidationError):
            abort(400)

        if kind == "topics":
            return jsonify(topic_json(item)), 201
        return jsonify(id=str(item.id), name=item.name), 201

    @app.post("/admin/<kind>/<item_id>/rename")
    @app.post("/admin/<kind>/<item_id>/update")
    def admin_catalog_rename(kind, item_id):
        if not is_admin():
            abort(403)
        if kind not in ("groups", "subjects", "topics"):
            abort(404)
        try:
            uid = UUID(item_id)
        except (ValueError, TypeError):
            abort(400)

        singular = kind[:-1]
        try:
            if kind == "topics":
                raw_subject = request.form.get("subject_id", "").strip()
                target_subject = UUID(raw_subject) if raw_subject else None
                item = catalog_store.update_topic(
                    uid, request.form.get("name", ""), subject_id=target_subject
                )
                return jsonify(topic_json(item))
            item = getattr(catalog_store, f"rename_{singular}")(uid, request.form.get("name", ""))
            return jsonify(id=str(item.id), name=item.name)
        except CatalogNotFoundError:
            abort(404)
        except CatalogConflictError as error:
            return name_conflict(error, kind == "topics")
        except CatalogOperationForbiddenError as err:
            return jsonify(error="forbidden", message=str(err)), 400
        except (ValueError, CatalogValidationError):
            abort(400)

    @app.post("/admin/<kind>/<item_id>/archive")
    def admin_catalog_archive(kind, item_id):
        if not is_admin():
            abort(403)
        if kind not in ("groups", "subjects", "topics"):
            abort(404)
        try:
            uid = UUID(item_id)
        except (ValueError, TypeError):
            abort(400)

        singular = kind[:-1]
        try:
            getattr(catalog_store, f"archive_{singular}")(uid)
        except CatalogNotFoundError:
            abort(404)
        except CatalogOperationForbiddenError as err:
            return jsonify(error="forbidden", message=str(err)), 400
        return ("", 204)

    @app.post("/admin/topics/move")
    def admin_move_topics():
        if not is_admin():
            abort(403)
        item_ids = request.form.getlist("topic_ids")
        try:
            ids = [UUID(value) for value in item_ids]
            target_sub = form_uuid("subject_id")
            catalog_store.move_topics(ids, target_sub)
        except CatalogNotFoundError:
            abort(404)
        except CatalogConflictError as error:
            return name_conflict(error, topics=True)
        except CatalogOperationForbiddenError as err:
            return jsonify(error="forbidden", message=str(err)), 400
        except (ValueError, CatalogValidationError):
            abort(400)

        moved_topics = catalog_store.list_topics(subject_id=target_sub)
        return jsonify(topics=[topic_json(item) for item in moved_topics])

    # --- Submissions Admin Routes ---

    @app.get("/admin/submissions")
    def admin_submissions_page():
        if not is_admin():
            abort(403)

        group_filter = request.args.get("group_id", "").strip() or None
        topic_filter = request.args.get("topic_id", "").strip() or None

        try:
            for key, val in request.args.items():
                sval = val.strip()
                if not sval:
                    continue
                if key in ("group_id", "topic_id", "student_id", "assignment_id") or key.endswith("_id"):
                    UUID(sval)
                elif key in ("page", "per_page", "attempt_number") and int(sval) < 1:
                    abort(400)
                elif "date" in key:
                    datetime.fromisoformat(sval)
                elif key == "status" and sval not in ("pending", "reviewed", "all"):
                    abort(400)

            group_uuid = UUID(group_filter) if group_filter else None
            topic_uuid = UUID(topic_filter) if topic_filter else None
        except (ValueError, TypeError):
            abort(400)

        limit = None
        offset = None
        page = 1
        per_page = 50
        if "page" in request.args or "per_page" in request.args:
            page = max(1, int(request.args.get("page", "1")))
            per_page = min(500, max(1, int(request.args.get("per_page", "50"))))
            limit = per_page
            offset = (page - 1) * per_page

        submissions = submission_store.list_submissions(
            group_id=group_uuid, topic_id=topic_uuid, limit=limit, offset=offset
        )
        total_count = submission_store.count_submissions(
            group_id=group_uuid, topic_id=topic_uuid
        )

        all_groups = catalog_store.list_groups(include_archived=True)
        all_topics = catalog_store.list_topics(include_archived=True)

        return render_template(
            "admin_submissions.html",
            section="submissions",
            submissions=submissions,
            groups=all_groups,
            topics=all_topics,
            selected_group_id=str(group_uuid) if group_uuid else "",
            selected_topic_id=str(topic_uuid) if topic_uuid else "",
            page=page,
            per_page=per_page,
            total_count=total_count,
        )

    @app.get("/admin/submissions/<submission_id>/download")
    def admin_download_submission(submission_id):
        if not is_admin():
            abort(403)
        try:
            sub_uuid = UUID(submission_id)
        except (ValueError, TypeError):
            abort(404)

        try:
            full_path, original_filename = submission_store.get_file(sub_uuid)
        except (SubmissionNotFoundError, FileNotFoundError):
            abort(404, description="Файл работы не найден на диске.")

        return send_file(
            full_path,
            as_attachment=True,
            download_name=original_filename,
        )

    @app.post("/admin/submissions/<submission_id>/review")
    def admin_review_submission(submission_id):
        if not is_admin():
            abort(403)
        try:
            sub_uuid = UUID(submission_id)
        except (ValueError, TypeError):
            abort(400)

        if request.is_json:
            data = request.get_json(silent=True) or {}
        else:
            data = request.form

        status = data.get("status", "").strip()
        try:
            status, reviewed_at = submission_store.set_review_status(sub_uuid, status)
        except SubmissionNotFoundError:
            abort(404)
        except SubmissionValidationError as err:
            return jsonify(error="validation_error", message=str(err)), 400

        wants_json = (
            request.is_json
            or request.headers.get("Accept") == "application/json"
            or "application/json" in request.headers.get("Accept", "")
        )
        if wants_json:
            return jsonify(
                id=str(sub_uuid),
                review_status=status,
                reviewed_at=reviewed_at,
            ), 200

        group_id_param = request.form.get("group_id", "")
        topic_id_param = request.form.get("topic_id", "")
        return redirect(
            url_for(
                "admin_submissions_page",
                group_id=group_id_param or None,
                topic_id=topic_id_param or None,
            ),
            code=303,
        )

    def _request_payload() -> dict[str, object]:
        if request.is_json:
            parsed = request.get_json(silent=True)
            return parsed if isinstance(parsed, dict) else {}
        return {key: request.form.get(key) for key in request.form}

    def _parse_bool(value: object, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("1", "true", "yes", "on"):
                return True
            if low in ("0", "false", "no", "off", ""):
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        return default

    def _wants_json() -> bool:
        accept = request.headers.get("Accept", "")
        return request.is_json or "application/json" in accept

    def _student_dict(st) -> dict[str, object]:
        return {
            "id": str(st.id),
            "group_id": str(st.group_id),
            "display_name": st.display_name,
            "archived_at": st.archived_at.isoformat() if st.archived_at else None,
        }

    @app.get("/admin/students")
    def admin_students_page():
        if not is_admin():
            abort(403)

        raw_group_id = request.args.get("group_id", "").strip()
        group_uuid: UUID | None = None
        if raw_group_id:
            try:
                group_uuid = UUID(raw_group_id)
            except (ValueError, TypeError):
                abort(400)

        include_archived = _parse_bool(request.args.get("include_archived"), False)
        issues_only = _parse_bool(request.args.get("issues"), False)
        notice = request.args.get("notice", "").strip()

        all_groups = catalog_store.list_groups(include_archived=True)
        plan = analyze_students_db(db_path, group_uuid, include_archived=include_archived)
        exact_ids: set[str] = set()
        fuzzy_ids: set[str] = set()
        for grp in plan["groups"]:
            for merge_item in grp["exact_merges"]:
                exact_ids.add(str(merge_item["target_student_id"]))
                for src in merge_item["source_students"]:
                    exact_ids.add(str(src["id"]))
            for fuzzy_item in grp["fuzzy_candidates"]:
                for st_item in fuzzy_item["students"]:
                    fuzzy_ids.add(str(st_item["id"]))
                    for member_id in st_item.get("member_ids", []):
                        fuzzy_ids.add(str(member_id))

        raw_students = submission_store.list_students_with_stats(
            group_id=group_uuid,
            include_archived=include_archived,
        )
        student_rows = []
        for st in raw_students:
            sid_str = str(st.id)
            try:
                norm = normalize_student_name(st.display_name)
                suggested_name = norm.display_name
                warnings = list(norm.warnings)
                is_paired = norm.is_paired
                needs_rename = (not is_paired) and (st.display_name != suggested_name)
            except ValueError:
                suggested_name = ""
                warnings = ["Имя не содержит букв — проверьте или исправьте вручную."]
                is_paired = True
                needs_rename = True

            in_exact = sid_str in exact_ids
            in_fuzzy = sid_str in fuzzy_ids
            has_issue = bool(warnings or needs_rename or is_paired or in_exact or in_fuzzy)
            if issues_only and not has_issue:
                continue

            student_rows.append({
                "student": st,
                "suggested_name": suggested_name,
                "warnings": warnings,
                "is_paired": is_paired,
                "needs_rename": needs_rename,
                "in_exact_merge": in_exact,
                "in_fuzzy_group": in_fuzzy,
            })

        return render_template(
            "admin_students.html",
            section="students",
            groups=all_groups,
            selected_group_id=str(group_uuid) if group_uuid else "",
            include_archived=include_archived,
            issues_only=issues_only,
            notice=notice,
            plan=plan,
            summary=plan["summary"],
            student_rows=student_rows,
        )

    @app.post("/admin/students/normalize-group")
    def admin_students_normalize_group():
        if not is_admin():
            abort(403)

        data = _request_payload()
        raw_gid = str(data.get("group_id") or "").strip()
        group_uuid: UUID | None = None
        if raw_gid:
            try:
                group_uuid = UUID(raw_gid)
            except (ValueError, TypeError):
                return jsonify(error="validation_error", message="Некорректный ID группы."), 400
            if catalog_store.get_group(group_uuid) is None:
                abort(404)

        apply_fuzzy = _parse_bool(data.get("apply_fuzzy"), False)
        split_paired = _parse_bool(data.get("split_paired"), False)
        plan = analyze_students_db(db_path, group_uuid)
        result = apply_cleanup_plan(
            db_path,
            app.config["STORAGE_ROOT"],
            plan,
            apply_exact=True,
            apply_renames=True,
            apply_fuzzy=apply_fuzzy,
            split_paired=split_paired,
            dry_run=False,
        )

        if _wants_json():
            return jsonify(result), 200

        notice = (
            f"Готово: объединено групп дублей — {result['merged_groups']}, "
            f"переименовано — {result['renamed_students']}."
        )
        return redirect(
            url_for("admin_students_page", group_id=raw_gid or None, notice=notice),
            code=303,
        )

    @app.post("/admin/students/merge")
    def admin_students_merge():
        if not is_admin():
            abort(403)

        data = _request_payload()
        try:
            group_uuid = UUID(str(data.get("group_id") or "").strip())
            target_uuid = UUID(str(data.get("target_student_id") or "").strip())
        except (ValueError, TypeError):
            return jsonify(error="validation_error", message="Укажите корректные group_id и target_student_id."), 400

        raw_sources = data.get("source_student_ids")
        if raw_sources is None and not request.is_json:
            raw_sources = request.form.getlist("source_student_ids")
        elif isinstance(raw_sources, str):
            raw_sources = [part.strip() for part in raw_sources.split(",") if part.strip()]

        if not isinstance(raw_sources, list) or not raw_sources:
            return jsonify(error="validation_error", message="Не выбраны записи для объединения."), 400

        try:
            source_uuids = [UUID(str(item).strip()) for item in raw_sources]
        except (ValueError, TypeError):
            return jsonify(error="validation_error", message="Некорректный ID исходного студента."), 400

        raw_target_name = data.get("target_display_name")
        target_name = str(raw_target_name).strip() if isinstance(raw_target_name, str) and raw_target_name.strip() else None
        if target_name is not None:
            try:
                norm_target = normalize_student_name(target_name)
            except (ValueError, TypeError) as err:
                return jsonify(error="validation_error", message=str(err)), 400
            if norm_target.is_paired:
                return jsonify(
                    error="validation_error",
                    message="Итоговое имя должно содержать одного студента в формате «Фамилия Имя».",
                ), 400
            target_name = norm_target.display_name

        try:
            res = submission_store.merge_students(
                group_uuid,
                target_uuid,
                source_uuids,
                target_display_name=target_name,
            )
        except SubmissionNotFoundError as err:
            return jsonify(error="not_found", message=str(err)), 404
        except SubmissionValidationError as err:
            return jsonify(error="validation_error", message=str(err)), 400

        if _wants_json():
            return jsonify(
                status="ok",
                target_student=_student_dict(res.target_student),
                merged_source_ids=[str(uid) for uid in res.merged_source_ids],
                moved_submissions=res.moved_submissions,
                total_submissions=res.total_submissions,
            ), 200

        return redirect(
            url_for("admin_students_page", group_id=str(group_uuid)),
            code=303,
        )

    @app.post("/admin/students/<student_id>/rename")
    def admin_students_rename(student_id):
        if not is_admin():
            abort(403)
        try:
            st_uuid = UUID(student_id)
        except (ValueError, TypeError):
            abort(400)

        data = _request_payload()
        raw_name = data.get("display_name", "")
        if not isinstance(raw_name, str) or not raw_name.strip():
            return jsonify(error="validation_error", message="Имя студента не может быть пустым."), 400

        normalize_flag = _parse_bool(data.get("normalize"), True)
        if normalize_flag:
            try:
                probe = normalize_student_name(raw_name)
            except (ValueError, TypeError) as err:
                return jsonify(error="validation_error", message=str(err)), 400
            if probe.is_paired:
                return jsonify(
                    error="validation_error",
                    message="Укажите одного студента. Для двух студентов используйте разделение парной записи.",
                ), 400

        try:
            student, norm_res = submission_store.rename_student(
                st_uuid,
                raw_name,
                normalize=normalize_flag,
            )
        except SubmissionNotFoundError as err:
            return jsonify(error="not_found", message=str(err)), 404
        except SubmissionValidationError as err:
            return jsonify(error="validation_error", message=str(err)), 400

        if _wants_json():
            return jsonify(
                status="ok",
                student=_student_dict(student),
                warnings=list(norm_res.warnings) if norm_res else [],
                warning_codes=list(norm_res.warning_codes) if norm_res else [],
            ), 200

        group_id_param = str(data.get("group_id") or "").strip()
        return redirect(
            url_for("admin_students_page", group_id=group_id_param or None),
            code=303,
        )

    @app.post("/admin/students/<student_id>/split")
    def admin_students_split(student_id):
        if not is_admin():
            abort(403)
        try:
            st_uuid = UUID(student_id)
        except (ValueError, TypeError):
            abort(400)

        data = _request_payload()
        raw_gid = str(data.get("group_id") or "").strip()
        if raw_gid:
            try:
                group_uuid = UUID(raw_gid)
            except (ValueError, TypeError):
                return jsonify(error="validation_error", message="Некорректный ID группы."), 400
        else:
            existing = submission_store.get_student(st_uuid)
            if existing is None:
                abort(404)
            group_uuid = existing.group_id

        targets_tuples: list[tuple[UUID | str | None, str]] = []
        raw_targets = data.get("targets")
        if isinstance(raw_targets, list):
            for entry in raw_targets:
                if not isinstance(entry, dict):
                    continue
                t_name = str(entry.get("display_name") or "").strip()
                t_id = entry.get("student_id")
                if t_name:
                    targets_tuples.append((t_id, t_name))
        elif not request.is_json:
            form_names = request.form.getlist("target_names")
            for raw_n in form_names:
                for piece in raw_n.splitlines():
                    cleaned = piece.strip()
                    if cleaned:
                        targets_tuples.append((None, cleaned))

        if not targets_tuples:
            return jsonify(error="validation_error", message="Укажите хотя бы одного студента для разделения."), 400

        try:
            res = submission_store.split_paired_student(group_uuid, st_uuid, targets_tuples)
        except SubmissionNotFoundError as err:
            return jsonify(error="not_found", message=str(err)), 404
        except SubmissionValidationError as err:
            return jsonify(error="validation_error", message=str(err)), 400
        except FileNotFoundError as err:
            return jsonify(error="missing_file", message=str(err)), 409

        if _wants_json():
            return jsonify(
                status="ok",
                source_student_id=str(res.source_student_id),
                target_students=[_student_dict(s) for s in res.target_students],
                moved_submissions=res.moved_submissions,
                copied_submissions=res.copied_submissions,
            ), 200

        return redirect(
            url_for("admin_students_page", group_id=str(group_uuid)),
            code=303,
        )

    def _toggle_student_archive(student_id: str, archived: bool):
        if not is_admin():
            abort(403)
        try:
            st_uuid = UUID(student_id)
        except (ValueError, TypeError):
            abort(400)
        try:
            st = submission_store.set_student_archived(st_uuid, archived)
        except SubmissionNotFoundError:
            abort(404)
        if _wants_json():
            return jsonify(_student_dict(st)), 200
        group_id_param = request.form.get("group_id", "").strip()
        return redirect(
            url_for("admin_students_page", group_id=group_id_param or None),
            code=303,
        )

    @app.post("/admin/students/<student_id>/archive")
    def admin_students_archive(student_id):
        return _toggle_student_archive(student_id, True)

    @app.post("/admin/students/<student_id>/restore")
    def admin_students_restore(student_id):
        return _toggle_student_archive(student_id, False)

    @app.errorhandler(RequestEntityTooLarge)
    def large(_):
        return jsonify(error="file too large"), 413

    return app
