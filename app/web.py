"""Flask application for homework submissions v2."""

from __future__ import annotations

import hmac
import os
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

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
from .database import DatabaseError, DatabaseValidationError, validate
from .sqlite_catalog import SqliteCatalogStore
from .storage import (
    DEFAULT_ALLOWED_UPLOAD_EXTENSIONS,
    normalize_upload_extensions,
    validated_upload_suffix,
)
from .submissions import (
    SubmissionAccessError,
    SubmissionNotFoundError,
    SubmissionStore,
    SubmissionValidationError,
)


def _format_date_short(iso_date: str) -> str:
    parts = iso_date.split("-")
    if len(parts) == 3:
        return f"{parts[2]}.{parts[1]}"
    return iso_date


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

    allowed_ext_raw = None
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

        raw_group = data.get("group_id") or data.get("group", "")
        raw_password = data.get("password", "")
        if not raw_group or not raw_password:
            abort(403)

        try:
            group_uuid = UUID(str(raw_group))
        except (ValueError, TypeError):
            # Fallback search by name if name was submitted
            found_group = next(
                (g for g in catalog_store.list_groups() if g.name.casefold() == str(raw_group).strip().casefold()),
                None,
            )
            if not found_group:
                abort(403)
            group_uuid = found_group.id

        ok, access = access_store.authorize(group_uuid, raw_password)
        if not ok or access is None:
            abort(403)

        group_item = catalog_store.get_group(group_uuid)
        group_name = group_item.name if group_item else ""

        session["student_group_id"] = str(group_uuid)
        session["student_access_version"] = access.version
        session["student_session_schema"] = 2

        assigned = assignment_store.list_for_group(group_uuid)
        snapshot = catalog_store.snapshot()
        subject_map = {s.id: s.name for s in snapshot.subjects}
        topic_map = {t.id: t.name for t in snapshot.topics}

        assignments_data = [
            {
                "id": str(a.id),
                "subject_id": str(a.subject_id),
                "subject_name": subject_map.get(a.subject_id, ""),
                "topic_id": str(a.topic_id),
                "topic_name": topic_map.get(a.topic_id, ""),
                "lesson_date": a.lesson_date,
                "pair_number": a.pair_number,
                "label": f"{_format_date_short(a.lesson_date)}, пара {a.pair_number} — {topic_map.get(a.topic_id, '')}",
            }
            for a in assigned
            if a.subject_id in subject_map and a.topic_id in topic_map
        ]

        students = submission_store.list_students(group_uuid, include_archived=False)
        students_data = [{"id": str(s.id), "display_name": s.display_name} for s in students]

        return jsonify(
            status="ok",
            group_id=str(group_uuid),
            group_name=group_name,
            assignments=assignments_data,
            students=students_data,
        ), 200

    @app.get("/student/assignments")
    def student_assignments():
        group_uuid, _ = verify_student_session()
        assigned = assignment_store.list_for_group(group_uuid)
        snapshot = catalog_store.snapshot()
        subject_map = {s.id: s.name for s in snapshot.subjects}
        topic_map = {t.id: t.name for t in snapshot.topics}

        assignments_data = [
            {
                "id": str(a.id),
                "subject_id": str(a.subject_id),
                "subject_name": subject_map.get(a.subject_id, ""),
                "topic_id": str(a.topic_id),
                "topic_name": topic_map.get(a.topic_id, ""),
                "lesson_date": a.lesson_date,
                "pair_number": a.pair_number,
                "label": f"{_format_date_short(a.lesson_date)}, пара {a.pair_number} — {topic_map.get(a.topic_id, '')}",
            }
            for a in assigned
            if a.subject_id in subject_map and a.topic_id in topic_map
        ]
        return jsonify(status="ok", group_id=str(group_uuid), assignments=assignments_data), 200

    @app.get("/student/students")
    def student_list_students():
        group_uuid, _ = verify_student_session()
        students = submission_store.list_students(group_uuid, include_archived=False)
        return jsonify([{"id": str(s.id), "display_name": s.display_name} for s in students]), 200

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
        display_name = unicodedata.normalize("NFC", raw_name.strip())
        if not display_name:
            return jsonify(error="display_name cannot be empty"), 400

        try:
            student = submission_store.create_student(group_uuid, display_name)
        except SubmissionValidationError as err:
            return jsonify(error=str(err)), 400
        except SubmissionNotFoundError:
            abort(404)

        return jsonify(id=str(student.id), display_name=student.display_name), 201

    @app.post("/submit")
    def submit():
        group_uuid, version = verify_student_session()

        req_group_id = request.form.get("group_id", "").strip()
        req_student_id = request.form.get("student_id", "").strip()
        req_assignment_id = request.form.get("assignment_id", "").strip()
        file = request.files.get("file")

        if req_group_id != str(group_uuid):
            abort(403)
        if not (req_group_id and req_student_id and req_assignment_id and file and file.filename):
            abort(400)

        try:
            validated_upload_suffix(file.filename, app.config["ALLOWED_UPLOAD_EXTENSIONS"])
        except ValueError:
            return jsonify({"error": "file_type_not_allowed", "message": "Недопустимый формат файла."}), 400

        try:
            student_uuid = UUID(req_student_id)
            assignment_uuid = UUID(req_assignment_id)
        except (ValueError, TypeError):
            abort(400)

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

        raw_group_id = request.form.get("group_id", "").strip()
        try:
            group_uuid = UUID(raw_group_id)
        except (ValueError, TypeError):
            # Fallback by name if group_name was submitted
            raw_group_name = request.form.get("group", "").strip()
            found = next((g for g in catalog_store.list_groups() if g.name == raw_group_name), None)
            if not found:
                abort(400)
            group_uuid = found.id

        password = request.form.get("password", "").strip() or None
        is_open = request.form.get("is_open") == "1"
        raw_deadline = request.form.get("closes_at", "")

        try:
            deadline = _parse_deadline_input(raw_deadline)
        except ValueError:
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
            # Check legacy group name param
            legacy_group_name = request.args.get("group", "").strip()
            matched = next((g for g in snapshot.groups if g.name == legacy_group_name), None)
            if matched:
                selected_group_id = str(matched.id)
            else:
                selected_group_id = str(snapshot.groups[0].id)

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
                "lesson_date_formatted": f"{a.lesson_date.split('-')[2]}.{a.lesson_date.split('-')[1]}.{a.lesson_date.split('-')[0]}",
                "pair_number": a.pair_number,
                "subject_id": str(a.subject_id),
                "subject_name": subject_map.get(a.subject_id, ""),
                "topic_id": str(a.topic_id),
                "topic_name": topic_map.get(a.topic_id, ""),
            }
            for a in assignments
        ]

        unique_subjects_count = len({a.subject_id for a in assignments})

        return render_template(
            "admin_assignments.html",
            section="assignments",
            groups=snapshot.groups,
            subjects=snapshot.subjects,
            topics=snapshot.topics,
            selected_group_id=selected_group_id,
            selected_subject_id=str(selected_subject_id) if selected_subject_id else "",
            assignments=items,
            unique_subjects_count=unique_subjects_count,
        )

    @app.post("/admin/assignments")
    def admin_create_assignment():
        if not is_admin():
            abort(403)

        raw_group = request.form.get("group_id", "").strip()
        if not raw_group:
            # Fallback to group_name
            legacy_name = request.form.get("group_name", "").strip()
            g_match = next((g for g in catalog_store.list_groups() if g.name == legacy_name), None)
            raw_group = str(g_match.id) if g_match else ""

        raw_topic = request.form.get("topic_id", "").strip()
        lesson_date = request.form.get("lesson_date", "").strip()
        raw_pair = request.form.get("pair_number", "").strip()

        try:
            group_id = UUID(raw_group)
            topic_id = UUID(raw_topic)
            pair_number = int(raw_pair)
        except (ValueError, TypeError):
            abort(400)

        try:
            item = assignment_store.create(group_id, topic_id, lesson_date, pair_number)
        except AssignmentConflictError as err:
            return jsonify(error="assignment_conflict", message=str(err)), 409
        except AssignmentValidationError as err:
            return jsonify(error="validation_error", message=str(err)), 400
        except AssignmentNotFoundError:
            abort(404)

        return jsonify(
            status="created",
            id=str(item.id),
            group_id=str(item.group_id),
            subject_id=str(item.subject_id),
            topic_id=str(item.topic_id),
            lesson_date=item.lesson_date,
            pair_number=item.pair_number,
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

        try:
            item = assignment_store.update(uid, lesson_date, pair_number, topic_id)
        except AssignmentConflictError as err:
            return jsonify(error="assignment_conflict", message=str(err)), 409
        except AssignmentNotFoundError:
            abort(404)
        except AssignmentInUseError as err:
            return jsonify(error="assignment_in_use", message=str(err)), 409
        except AssignmentValidationError as err:
            return jsonify(error="validation_error", message=str(err)), 400

        return jsonify(
            status="ok",
            id=str(item.id),
            lesson_date=item.lesson_date,
            pair_number=item.pair_number,
            topic_id=str(item.topic_id),
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
                item = catalog_store.rename_topic(uid, request.form.get("name", ""))
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
                elif key == "attempt_number" and int(sval) < 1:
                    abort(400)
                elif "date" in key:
                    datetime.fromisoformat(sval)
                elif key == "status" and sval not in ("pending", "reviewed", "all"):
                    abort(400)

            group_uuid = UUID(group_filter) if group_filter else None
            topic_uuid = UUID(topic_filter) if topic_filter else None
        except (ValueError, TypeError):
            abort(400)
        submissions = submission_store.list_submissions(
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

    @app.errorhandler(RequestEntityTooLarge)
    def large(_):
        return jsonify(error="file too large"), 413

    return app
