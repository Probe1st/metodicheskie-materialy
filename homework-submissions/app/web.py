from __future__ import annotations

import fcntl
import hmac
import os
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge

from .access import AccessStore
from .assignments import (
    AssignmentConflictError,
    AssignmentNotFoundError,
    AssignmentStore,
    AssignmentValidationError,
)
from .catalog_store import (
    CatalogItemNotFound,
    CatalogNameConflict,
    create_catalog_store,
)
from .storage import destination_directory, write_upload
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
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} КБ"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} МБ"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} ГБ"

def _parse_deadline_input(value: str) -> datetime | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        parsed = parsed.replace(tzinfo=local_tz)
    return parsed.astimezone(UTC)


def _format_local_input(value: datetime | None) -> str:
    if value is None:
        return ""
    local_tz = datetime.now().astimezone().tzinfo
    local_dt = value.astimezone(local_tz)
    return local_dt.strftime("%Y-%m-%dT%H:%M")


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret"),
        ADMIN_PASSWORD=os.getenv("ADMIN_PASSWORD", ""),
        PRIVATE_DATA_DIR=Path(os.getenv("PRIVATE_DATA_DIR", ".private")),
        STORAGE_ROOT=Path("storage"),
        CATALOG_PATH=Path("catalog.json"),
        CATALOG_PROVIDER=os.getenv("CATALOG_PROVIDER", "json"),
    )
    if config:
        app.config.update(config)

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
    catalog_store = create_catalog_store(app.config)

    @contextmanager
    def group_state_lock():
        path = Path(app.config["PRIVATE_DATA_DIR"]) / "group-state.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        with open(path, "a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def catalog():
        return catalog_store.snapshot()

    def access_store() -> AccessStore:
        db_path = Path(app.config["PRIVATE_DATA_DIR"]) / "access.sqlite3"
        return AccessStore(db_path, (item.name for item in catalog().groups))


    def assignment_store() -> AssignmentStore:
        db_path = Path(app.config["PRIVATE_DATA_DIR"]) / "access.sqlite3"
        return AssignmentStore(db_path)

    def task_content_store() -> TaskContentStore:
        db_path = Path(app.config["PRIVATE_DATA_DIR"]) / "access.sqlite3"
        att_dir = Path(app.config["PRIVATE_DATA_DIR"]) / "task-attachments"
        return TaskContentStore(db_path, att_dir)

    def is_admin() -> bool:
        return bool(session.get("admin"))

    def form_uuid(field: str) -> UUID:
        try:
            return UUID(request.form.get(field, ""))
        except ValueError:
            abort(400)

    def topic_json(item):
        return {"id": str(item.id), "name": item.name, "subject_id": str(item.subject_id)}

    def name_conflict(error: CatalogNameConflict, topics: bool = False):
        if topics:
            return jsonify(error="topic_name_conflict", names=error.names), 400
        return jsonify(error="name_conflict"), 400

    @app.get("/favicon.ico")
    def favicon():
        return app.send_static_file("favicon.ico")

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            catalog=catalog(),
            limit=app.config.get("LIMIT_LABEL", "200 МБ"),
            max_bytes=app.config.get("MAX_CONTENT_LENGTH", 200 * 1024 * 1024),
        )
    def serialize_student_assignments(assigned, snapshot):
        tc_store = task_content_store()
        subject_map = {s.id: s.name for s in snapshot.subjects}
        topic_map = {t.id: t.name for t in snapshot.topics}
        result = []
        for a in assigned:
            if a.subject_id not in subject_map or a.topic_id not in topic_map:
                continue
            tc = tc_store.get("lesson", a.id)
            has_desc = bool(tc and tc.description and tc.description.strip())
            has_att = bool(tc and tc.attachment_id)
            result.append({
                "id": str(a.id),
                "subject_id": str(a.subject_id),
                "subject_name": subject_map.get(a.subject_id, ""),
                "topic_id": str(a.topic_id),
                "topic_name": topic_map.get(a.topic_id, ""),
                "lesson_date": a.lesson_date,
                "pair_number": a.pair_number,
                "has_description": has_desc,
                "has_attachment": has_att,
                "label": f"{_format_date_short(a.lesson_date)}, пара {a.pair_number} — {topic_map.get(a.topic_id, '')}",
            })
        return result

    @app.post("/unlock")
    def unlock():
        group = request.form.get("group", "").strip()
        password = request.form.get("password", "")
        if not group or not password:
            abort(403)

        with group_state_lock():
            store = access_store()
            version = store.authorize(group, password)
            if version is None:
                abort(403)

            item = next((item for item in catalog().groups if item.name == group), None)
            if item is None:
                abort(403)
            session["student_group"] = group
            session["student_group_id"] = str(item.id)
            session["student_access_version"] = version

            assigned = assignment_store().list_for_group(group)
            snapshot = catalog()
            assignments_data = serialize_student_assignments(assigned, snapshot)

        wants_json = (
            request.headers.get("Accept") == "application/json"
            or "application/json" in request.headers.get("Accept", "")
            or (request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html)
        )
        if wants_json:
            return jsonify(status="ok", group=group, assignments=assignments_data), 200
        return ("", 204)

    @app.get("/student/assignments")
    def student_assignments():
        session_group = session.get("student_group")
        session_version = session.get("student_access_version")
        if not session_group or session_version is None:
            abort(403)

        with group_state_lock():
            if not access_store().permits(session_group, int(session_version)):
                abort(403)

            assigned = assignment_store().list_for_group(session_group)
            snapshot = catalog()
            assignments_data = serialize_student_assignments(assigned, snapshot)

        return jsonify(status="ok", group=session_group, assignments=assignments_data), 200

    @app.get("/student/assignments/<assignment_id>")
    def student_assignment_page(assignment_id):
        session_group = session.get("student_group")
        session_version = session.get("student_access_version")
        session_group_id = session.get("student_group_id")
        if not session_group or session_version is None:
            abort(403)

        try:
            uid = UUID(assignment_id)
        except (ValueError, TypeError):
            abort(400)

        with group_state_lock():
            store = access_store()
            if not store.permits(session_group, int(session_version)):
                abort(403)

            group_item = next((item for item in catalog().groups if item.name == session_group), None)
            if group_item is None or (session_group_id and str(group_item.id) != str(session_group_id)):
                abort(403)

            assignment = assignment_store().get(uid)
            if assignment is None or assignment.group_name != session_group:
                abort(404)

            snapshot = catalog()
            subject = next((s for s in snapshot.subjects if s.id == assignment.subject_id), None)
            topic = next((t for t in snapshot.topics if t.id == assignment.topic_id), None)
            if subject is None or topic is None:
                abort(404)

            tc_store = task_content_store()
            content = tc_store.get("lesson", uid)
            if content and content.description.strip():
                rendered_description = render_task(content.description, content.description_format)
                description_format = content.description_format
            else:
                rendered_description = ""
                description_format = "txt"

            if content and content.attachment_id:
                attachment = tc_store.get_attachment(content.attachment_id)
                attachment_size_formatted = _format_bytes(attachment.size_bytes) if attachment else ""
            else:
                attachment = None
                attachment_size_formatted = ""

        query_params = {}
        for key in ("q", "subject_id", "date"):
            val = request.args.get(key, "").strip()
            if val:
                query_params[key] = val
        return_url = url_for("index", **query_params)

        date_parts = assignment.lesson_date.split("-")
        date_formatted = f"{date_parts[2]}.{date_parts[1]}.{date_parts[0]}" if len(date_parts) == 3 else assignment.lesson_date

        return render_template(
            "student_assignment.html",
            assignment=assignment,
            lesson_date_formatted=date_formatted,
            subject=subject,
            topic=topic,
            group_name=session_group,
            rendered_description=rendered_description,
            description_format=description_format,
            attachment=attachment,
            attachment_size_formatted=attachment_size_formatted,
            return_url=return_url,
            limit=app.config["LIMIT_LABEL"],
            max_bytes=app.config["MAX_CONTENT_LENGTH"],
        )

    @app.get("/student/assignments/<assignment_id>/attachment")
    def student_download_attachment(assignment_id):
        session_group = session.get("student_group")
        session_version = session.get("student_access_version")
        session_group_id = session.get("student_group_id")
        if not session_group or session_version is None:
            abort(403)

        try:
            uid = UUID(assignment_id)
        except (ValueError, TypeError):
            abort(400)

        with group_state_lock():
            store = access_store()
            if not store.permits(session_group, int(session_version)):
                abort(403)

            group_item = next((item for item in catalog().groups if item.name == session_group), None)
            if group_item is None or (session_group_id and str(group_item.id) != str(session_group_id)):
                abort(403)

            assignment = assignment_store().get(uid)
            if assignment is None or assignment.group_name != session_group:
                abort(404)

            tc_store = task_content_store()
            content = tc_store.get("lesson", uid)
            if content is None or content.attachment_id is None:
                abort(404)

            att = tc_store.get_attachment(content.attachment_id)
            if att is None:
                abort(404)

            path = tc_store.get_attachment_path(att.id)
            if not path.is_file():
                abort(404)

        response = send_file(path, as_attachment=True, download_name=att.original_name)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    @app.post("/submit")
    def submit():
        session_group = session.get("student_group")
        session_version = session.get("student_access_version")
        if not session_group or session_version is None:
            abort(403)
        current_group_id = session.get("student_group_id")

        group, discipline, work, student = (
            request.form.get(key, "").strip()
            for key in ("group", "discipline", "work", "student_name")
        )
        file = request.files.get("file")

        if group != session_group:
            abort(403)

        if not all((group, discipline, work, student, file)) or not file.filename:
            abort(400)
        with group_state_lock():
            snapshot = catalog()
            if not any(item.name == group and str(item.id) == current_group_id for item in snapshot.groups):
                abort(403)
            if not snapshot.contains(group, discipline, work):
                abort(400)
            if not access_store().permits(session_group, int(session_version)):
                abort(403)

        directory = destination_directory(Path(app.config["STORAGE_ROOT"]), group, discipline, work)
        saved = write_upload(file.stream, file.filename, student, directory)
        return jsonify(filename=saved.name), 201

    @app.get("/admin")
    def admin_dashboard():
        if not is_admin():
            return render_template("admin.html", authenticated=False)
        return render_template("admin.html", authenticated=True)

    @app.get("/admin/access")
    def admin_access():
        if not is_admin():
            abort(403)
        with group_state_lock():
            names = {group.name for group in catalog().groups}
            groups = [
                {"group": item.group, "is_open": item.is_open,
                 "closes_at": _format_local_input(item.closes_at)}
                for item in access_store().groups()
                if item.group in names
            ]
        return render_template("admin_access.html", groups=groups, section="access")

    @app.get("/admin/<kind>")
    def admin_catalog_page(kind):
        if not is_admin():
            abort(403)
        if kind not in ("groups", "subjects", "topics"):
            abort(404)
        snapshot = catalog()
        items = getattr(snapshot, kind)
        return render_template("admin_catalog.html", kind=kind, items=items, section=kind,
                               subjects=snapshot.subjects, topics=snapshot.topics,
                               unassigned_subject_id=snapshot.unassigned_subject_id)

    @app.post("/admin/<kind>")
    def admin_catalog_create(kind):
        if not is_admin():
            abort(403)
        if kind not in ("groups", "subjects", "topics"):
            abort(404)
        singular = kind[:-1]
        with group_state_lock() if kind == "groups" else nullcontext():
            try:
                if kind == "topics":
                    item = catalog_store.create_topic(request.form.get("name", ""), form_uuid("subject_id"))
                else:
                    item = getattr(catalog_store, f"create_{singular}")(request.form.get("name", ""))
            except CatalogItemNotFound:
                abort(404)
            except CatalogNameConflict as error:
                return name_conflict(error, kind == "topics")
            except ValueError:
                abort(400)
        if kind == "topics":
            return jsonify(topic_json(item)), 201
        return jsonify(id=str(item.id), name=item.name), 201

    @app.post("/admin/<kind>/<item_id>/<action>")
    def admin_catalog_mutate(kind, item_id, action):
        if not is_admin():
            abort(403)
        if kind not in ("groups", "subjects", "topics") or action not in ("rename", "update", "delete"):
            abort(404)
        if (kind == "topics") != (action == "update") and action != "delete":
            abort(404)
        try:
            uid = UUID(item_id)
        except ValueError:
            abort(400)
        singular = kind[:-1]
        with group_state_lock() if kind == "groups" else nullcontext():
            old = next((item for item in getattr(catalog_store, f"list_{kind}")() if item.id == uid), None)
            if old is None:
                abort(404)
            access = access_store() if kind == "groups" else None
            try:
                if action == "update":
                    item = catalog_store.update_topic(uid, request.form.get("name", ""), form_uuid("subject_id"))
                    assignment_store().reassign_subject_for_topics([item.id], item.subject_id)
                    return jsonify(topic_json(item))
                if action == "rename":
                    item = getattr(catalog_store, f"rename_{singular}")(uid, request.form.get("name", ""))
                    if access:
                        access.rename_group(old.name, item.name)
                        assignment_store().rename_group(old.name, item.name)
                    return jsonify(id=str(item.id), name=item.name)
                if kind == "groups" and action == "delete":
                    getattr(catalog_store, f"delete_{singular}")(uid)
                    as_store = assignment_store()
                    tc_store = task_content_store()
                    orphans_to_discard = []
                    with as_store.connect() as conn:
                        lesson_ids = as_store.get_ids_for_group(old.name, conn=conn)
                        orphans = tc_store.delete_contents_for_owners("lesson", lesson_ids, conn=conn)
                        orphans_to_discard.extend(orphans)
                        as_store.delete_group(old.name, conn=conn)
                        conn.commit()
                    if access:
                        access.delete_group(old.name)
                    for orphan_id in orphans_to_discard:
                        tc_store.discard_attachment_file(orphan_id)
                elif kind == "subjects" and action == "delete":
                    getattr(catalog_store, f"delete_{singular}")(uid)
                    as_store = assignment_store()
                    tc_store = task_content_store()
                    orphans_to_discard = []
                    with as_store.connect() as conn:
                        lesson_ids = as_store.get_ids_for_subject(uid, conn=conn)
                        orphans = tc_store.delete_contents_for_owners("lesson", lesson_ids, conn=conn)
                        orphans_to_discard.extend(orphans)
                        as_store.delete_subject(uid, conn=conn)
                        conn.commit()
                    for orphan_id in orphans_to_discard:
                        tc_store.discard_attachment_file(orphan_id)
                elif kind == "topics" and action == "delete":
                    getattr(catalog_store, f"delete_{singular}")(uid)
                    as_store = assignment_store()
                    tc_store = task_content_store()
                    orphans_to_discard = []
                    with as_store.connect() as conn:
                        lesson_ids = as_store.get_ids_for_topic(uid, conn=conn)
                        orphans_lesson = tc_store.delete_contents_for_owners("lesson", lesson_ids, conn=conn)
                        orphans_topic = tc_store.delete_content("topic", uid, conn=conn)
                        orphans_to_discard.extend(orphans_lesson)
                        orphans_to_discard.extend(orphans_topic)
                        as_store.delete_topic(uid, conn=conn)
                        conn.commit()
                    for orphan_id in set(orphans_to_discard):
                        tc_store.discard_attachment_file(orphan_id)
            except CatalogItemNotFound:
                abort(404)
            except CatalogNameConflict as error:
                topic_conflict = kind == "topics" or (kind == "subjects" and action == "delete")
                return name_conflict(error, topics=topic_conflict)
            except ValueError:
                abort(400)
        return ("", 204)

    @app.post("/admin/topics/move")
    def admin_move_topics():
        if not is_admin():
            abort(403)
        item_ids = request.form.getlist("topic_ids")
        try:
            ids = tuple(UUID(value) for value in item_ids)
            target_subject_id = form_uuid("subject_id")
            moved = catalog_store.move_topics(ids, target_subject_id)
            if moved:
                assignment_store().reassign_subject_for_topics([item.id for item in moved], target_subject_id)
        except CatalogItemNotFound:
            abort(404)
        except CatalogNameConflict as error:
            return name_conflict(error, topics=True)
        except ValueError:
            abort(400)
        return jsonify(topics=[topic_json(item) for item in moved])

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

    @app.post("/admin/access")
    def admin_update_group():
        if not is_admin():
            abort(403)

        group = request.form.get("group", "").strip()

        password = request.form.get("password", "").strip() or None
        is_open = request.form.get("is_open") == "1"
        raw_deadline = request.form.get("closes_at", "")

        try:
            deadline = _parse_deadline_input(raw_deadline)
        except ValueError:
            abort(400)

        with group_state_lock():
            if not group or group not in (item.name for item in catalog().groups):
                abort(400)
            access_store().configure(group, password, is_open, deadline)
        return ("", 204)

    @app.get("/admin/assignments")
    def admin_assignments_page():
        if not is_admin():
            abort(403)
        snapshot = catalog()
        selected_group = request.args.get("group", "").strip()
        if not selected_group and snapshot.groups:
            selected_group = snapshot.groups[0].name

        selected_subject_id = None
        raw_subj = request.args.get("subject_id", "").strip()
        if raw_subj:
            try:
                selected_subject_id = UUID(raw_subj)
            except ValueError:
                pass

        store = assignment_store()
        if selected_group and selected_subject_id:
            assignments = store.list_for_group_and_subject(selected_group, selected_subject_id)
        elif selected_group:
            assignments = store.list_for_group(selected_group)
        else:
            assignments = store.list_all()

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

        return render_template(
            "admin_assignments.html",
            section="assignments",
            groups=snapshot.groups,
            subjects=snapshot.subjects,
            topics=snapshot.topics,
            selected_group=selected_group,
            selected_subject_id=str(selected_subject_id) if selected_subject_id else "",
            assignments=items,
        )
    @app.get("/admin/assignments/new")
    def admin_new_assignment_page():
        if not is_admin():
            abort(403)
        snapshot = catalog()
        groups = snapshot.groups
        subjects = snapshot.subjects
        topics = snapshot.topics

        selected_group = request.args.get("group", "").strip()
        if not selected_group and groups:
            selected_group = groups[0].name
        selected_subject_id = request.args.get("subject_id", "").strip()
        if not selected_subject_id and subjects:
            selected_subject_id = str(subjects[0].id)
        selected_date = request.args.get("date", "").strip()
        if not selected_date:
            selected_date = datetime.now(UTC).strftime("%Y-%m-%d")
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
            cancel_url=url_for("admin_assignments_page", group=selected_group, subject_id=selected_subject_id),
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
        )

    @app.get("/admin/assignments/<assignment_id>/edit")
    def admin_edit_assignment_page(assignment_id):
        if not is_admin():
            abort(403)
        try:
            uid = UUID(assignment_id)
        except (ValueError, TypeError):
            abort(400)

        assignment = assignment_store().get(uid)
        if assignment is None:
            abort(404)

        snapshot = catalog()
        subjects = snapshot.subjects
        topics = [t for t in snapshot.topics if t.subject_id == assignment.subject_id]
        subject = next((s for s in subjects if s.id == assignment.subject_id), None)

        tc_store = task_content_store()
        content = tc_store.get("lesson", uid)
        att = tc_store.get_attachment(content.attachment_id) if (content and content.attachment_id) else None

        return render_template(
            "admin_task_editor.html",
            mode="edit_lesson",
            form_action=url_for("admin_update_assignment", assignment_id=assignment.id),
            cancel_url=url_for("admin_assignments_page", group=assignment.group_name, subject_id=str(assignment.subject_id)),
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
        )

    @app.post("/admin/assignments")
    def admin_create_assignment():
        if not is_admin():
            abort(403)
        group_name = request.form.get("group_name", "").strip()
        raw_subject = request.form.get("subject_id", "").strip()
        raw_topic = request.form.get("topic_id", "").strip()
        lesson_date = request.form.get("lesson_date", "").strip()
        raw_pair = request.form.get("pair_number", "").strip()

        try:
            subject_id = UUID(raw_subject)
            topic_id = UUID(raw_topic)
            pair_number = int(raw_pair)
        except (ValueError, TypeError):
            abort(400)

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

        tc_store = task_content_store()
        topic_task = tc_store.get("topic", topic_id)
        allowed_topic_att = topic_task.attachment_id if topic_task else None

        staged_att = None
        if has_new_file:
            file_bytes = file.read()
            if len(file_bytes) > app.config["MAX_CONTENT_LENGTH"]:
                abort(413)
            new_att_id = uuid4()
            tc_store.write_attachment_file(new_att_id, file_bytes)
            staged_att = (new_att_id, file.filename.strip(), len(file_bytes))
            final_att_id = new_att_id
        elif remove_attachment:
            final_att_id = None
        elif raw_att_id:
            try:
                req_att = UUID(raw_att_id)
            except ValueError:
                abort(400)
            if req_att != allowed_topic_att:
                abort(400)
            final_att_id = req_att
        else:
            final_att_id = None

        with group_state_lock():
            snapshot = catalog()
            if group_name not in {g.name for g in snapshot.groups}:
                if staged_att:
                    tc_store.discard_attachment_file(staged_att[0])
                abort(400)
            if not any(s.id == subject_id for s in snapshot.subjects):
                if staged_att:
                    tc_store.discard_attachment_file(staged_att[0])
                abort(400)
            topic = next((t for t in snapshot.topics if t.id == topic_id), None)
            if topic is None or topic.subject_id != subject_id:
                if staged_att:
                    tc_store.discard_attachment_file(staged_att[0])
                abort(400)

            as_store = assignment_store()
            orphans_to_discard = []
            try:
                with as_store.connect() as conn:
                    item = as_store.create(group_name, subject_id, topic_id, lesson_date, pair_number, conn=conn)
                    if staged_att:
                        tc_store.register_attachment(staged_att[0], staged_att[1], staged_att[2], conn=conn)
                    _, orphans = tc_store.set_content("lesson", item.id, description, description_format, final_att_id, conn=conn)
                    orphans_to_discard.extend(orphans)
                    conn.commit()
            except AssignmentConflictError as err:
                if staged_att:
                    tc_store.discard_attachment_file(staged_att[0])
                return jsonify(error="assignment_conflict", message=str(err)), 409
            except AssignmentValidationError as err:
                if staged_att:
                    tc_store.discard_attachment_file(staged_att[0])
                return jsonify(error="validation_error", message=str(err)), 400
            except Exception:
                if staged_att:
                    tc_store.discard_attachment_file(staged_att[0])
                raise

            for orphan_id in orphans_to_discard:
                tc_store.discard_attachment_file(orphan_id)

        redirect_url = url_for("admin_assignments_page", group=item.group_name, subject_id=str(item.subject_id))
        return jsonify(
            status="created",
            id=str(item.id),
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

        tc_store = task_content_store()
        current_task = tc_store.get("lesson", uid)
        topic_task = tc_store.get("topic", topic_id)
        allowed_att_ids = set()
        if current_task and current_task.attachment_id:
            allowed_att_ids.add(current_task.attachment_id)
        if topic_task and topic_task.attachment_id:
            allowed_att_ids.add(topic_task.attachment_id)

        staged_att = None
        if has_new_file:
            file_bytes = file.read()
            if len(file_bytes) > app.config["MAX_CONTENT_LENGTH"]:
                abort(413)
            new_att_id = uuid4()
            tc_store.write_attachment_file(new_att_id, file_bytes)
            staged_att = (new_att_id, file.filename.strip(), len(file_bytes))
            final_att_id = new_att_id
        elif remove_attachment:
            final_att_id = None
        elif raw_att_id:
            try:
                req_att = UUID(raw_att_id)
            except ValueError:
                abort(400)
            if req_att not in allowed_att_ids:
                abort(400)
            final_att_id = req_att
        else:
            final_att_id = None

        with group_state_lock():
            as_store = assignment_store()
            existing = as_store.get(uid)
            if existing is None:
                if staged_att:
                    tc_store.discard_attachment_file(staged_att[0])
                abort(404)
            snapshot = catalog()
            topic = next((t for t in snapshot.topics if t.id == topic_id), None)
            if topic is None or topic.subject_id != existing.subject_id:
                if staged_att:
                    tc_store.discard_attachment_file(staged_att[0])
                abort(400)

            orphans_to_discard = []
            try:
                with as_store.connect() as conn:
                    item = as_store.update(uid, lesson_date, pair_number, topic_id, conn=conn)
                    if staged_att:
                        tc_store.register_attachment(staged_att[0], staged_att[1], staged_att[2], conn=conn)
                    _, orphans = tc_store.set_content("lesson", uid, description, description_format, final_att_id, conn=conn)
                    orphans_to_discard.extend(orphans)
                    conn.commit()
            except AssignmentConflictError as err:
                if staged_att:
                    tc_store.discard_attachment_file(staged_att[0])
                return jsonify(error="assignment_conflict", message=str(err)), 409
            except AssignmentNotFoundError:
                if staged_att:
                    tc_store.discard_attachment_file(staged_att[0])
                abort(404)
            except AssignmentValidationError as err:
                if staged_att:
                    tc_store.discard_attachment_file(staged_att[0])
                return jsonify(error="validation_error", message=str(err)), 400
            except Exception:
                if staged_att:
                    tc_store.discard_attachment_file(staged_att[0])
                raise

            for orphan_id in orphans_to_discard:
                tc_store.discard_attachment_file(orphan_id)

        redirect_url = url_for("admin_assignments_page", group=existing.group_name, subject_id=str(existing.subject_id))
        return jsonify(
            status="ok",
            id=str(item.id),
            lesson_date=item.lesson_date,
            pair_number=item.pair_number,
            topic_id=str(item.topic_id),
            redirect_url=redirect_url,
        ), 200

    @app.post("/admin/assignments/<assignment_id>/delete")
    def admin_delete_assignment(assignment_id):
        if not is_admin():
            abort(403)
        try:
            uid = UUID(assignment_id)
        except ValueError:
            abort(400)

        with group_state_lock():
            as_store = assignment_store()
            tc_store = task_content_store()
            orphans_to_discard = []
            with as_store.connect() as conn:
                orphans = tc_store.delete_content("lesson", uid, conn=conn)
                orphans_to_discard.extend(orphans)
                as_store.delete(uid, conn=conn)
                conn.commit()
            for orphan_id in orphans_to_discard:
                tc_store.discard_attachment_file(orphan_id)
        return ("", 204)

    @app.get("/admin/topics/<topic_id>/task")
    def admin_topic_task_page(topic_id):
        if not is_admin():
            abort(403)
        try:
            uid = UUID(topic_id)
        except (ValueError, TypeError):
            abort(400)

        snapshot = catalog()
        topic = next((t for t in snapshot.topics if t.id == uid), None)
        if topic is None:
            abort(404)

        tc_store = task_content_store()
        content = tc_store.get("topic", uid)
        att = tc_store.get_attachment(content.attachment_id) if (content and content.attachment_id) else None

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
                } if att else None,
            )

        subject = next((s for s in snapshot.subjects if s.id == topic.subject_id), None)
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

        snapshot = catalog()
        topic = next((t for t in snapshot.topics if t.id == uid), None)
        if topic is None:
            abort(404)

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

        tc_store = task_content_store()
        current = tc_store.get("topic", uid)
        allowed_att_id = current.attachment_id if current else None

        staged_att = None
        if has_new_file:
            file_bytes = file.read()
            if len(file_bytes) > app.config["MAX_CONTENT_LENGTH"]:
                abort(413)
            new_att_id = uuid4()
            tc_store.write_attachment_file(new_att_id, file_bytes)
            staged_att = (new_att_id, file.filename.strip(), len(file_bytes))
            final_att_id = new_att_id
        elif remove_attachment:
            final_att_id = None
        elif raw_att_id:
            try:
                requested_att = UUID(raw_att_id)
            except ValueError:
                abort(400)
            if requested_att != allowed_att_id:
                abort(400)
            final_att_id = requested_att
        else:
            final_att_id = None

        try:
            orphans_to_discard = []
            with tc_store._connect() as conn:
                if staged_att:
                    tc_store.register_attachment(staged_att[0], staged_att[1], staged_att[2], conn=conn)
                _, orphans = tc_store.set_content("topic", uid, description, description_format, final_att_id, conn=conn)
                orphans_to_discard.extend(orphans)
                conn.commit()
        except Exception:
            if staged_att:
                tc_store.discard_attachment_file(staged_att[0])
            raise

        for orphan_id in orphans_to_discard:
            tc_store.discard_attachment_file(orphan_id)

        if (request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html) or request.is_json:
            return jsonify(status="ok", redirect_url=url_for("admin_catalog_page", kind="topics")), 200
        return redirect(url_for("admin_catalog_page", kind="topics"))

    @app.post("/admin/tasks/preview")
    def admin_task_preview():
        if not is_admin():
            abort(403)
        description = request.form.get("description", "")
        description_format = request.form.get("description_format", "txt").strip().lower()
        if description_format not in ("html", "md", "txt"):
            abort(400)
        rendered = render_task(description, description_format)
        return jsonify(html=rendered)

    @app.get("/admin/tasks/attachments/<attachment_id>")
    def admin_download_attachment(attachment_id):
        if not is_admin():
            abort(403)
        try:
            uid = UUID(attachment_id)
        except (ValueError, TypeError):
            abort(400)

        tc_store = task_content_store()
        att = tc_store.get_attachment(uid)
        if att is None:
            abort(404)
        path = tc_store.get_attachment_path(uid)
        if not path.is_file():
            abort(404)
        response = send_file(path, as_attachment=True, download_name=att.original_name)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    @app.errorhandler(RequestEntityTooLarge)
    def large(_):
        return jsonify(error="file too large"), 413

    return app
