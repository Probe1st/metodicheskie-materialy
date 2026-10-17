from __future__ import annotations

import fcntl
import hmac
import os
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from .access import AccessStore
from .assignments import (
    AssignmentConflictError,
    AssignmentNotFoundError,
    AssignmentStore,
    AssignmentValidationError,
)
from .catalog_store import CatalogItemNotFound, CatalogNameConflict, create_catalog_store
from .storage import destination_directory, write_upload


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
        MAX_UPLOAD_GB=int(os.getenv("MAX_UPLOAD_GB", "1")),
        PRIVATE_DATA_DIR=Path(os.getenv("PRIVATE_DATA_DIR", ".private")),
        STORAGE_ROOT=Path("storage"),
        CATALOG_PATH=Path("catalog.json"),
        CATALOG_PROVIDER=os.getenv("CATALOG_PROVIDER", "json"),
    )
    if config:
        app.config.update(config)

    app.config["MAX_CONTENT_LENGTH"] = app.config["MAX_UPLOAD_GB"] * 1024**3
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
        return render_template("index.html", catalog=catalog(), limit=app.config["MAX_UPLOAD_GB"])

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

        return jsonify(status="ok", group=session_group, assignments=assignments_data), 200

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
                    return jsonify(topic_json(item))
                if action == "rename":
                    item = getattr(catalog_store, f"rename_{singular}")(uid, request.form.get("name", ""))
                    if access:
                        access.rename_group(old.name, item.name)
                        assignment_store().rename_group(old.name, item.name)
                    return jsonify(id=str(item.id), name=item.name)
                if access:
                    access.delete_group(old.name)
                    assignment_store().delete_group(old.name)
                getattr(catalog_store, f"delete_{singular}")(uid)
                if kind == "subjects" and action == "delete":
                    assignment_store().delete_subject(uid)
                elif kind == "topics" and action == "delete":
                    assignment_store().delete_topic(uid)
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
            moved = catalog_store.move_topics(ids, form_uuid("subject_id"))
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

        unique_subjects_count = len({a.subject_id for a in assignments})

        return render_template(
            "admin_assignments.html",
            section="assignments",
            groups=snapshot.groups,
            subjects=snapshot.subjects,
            topics=snapshot.topics,
            selected_group=selected_group,
            selected_subject_id=str(selected_subject_id) if selected_subject_id else "",
            assignments=items,
            unique_subjects_count=unique_subjects_count,
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

        with group_state_lock():
            snapshot = catalog()
            if group_name not in {g.name for g in snapshot.groups}:
                abort(400)
            if not any(s.id == subject_id for s in snapshot.subjects):
                abort(400)
            topic = next((t for t in snapshot.topics if t.id == topic_id), None)
            if topic is None or topic.subject_id != subject_id:
                abort(400)

            store = assignment_store()
            try:
                item = store.create(group_name, subject_id, topic_id, lesson_date, pair_number)
            except AssignmentConflictError as err:
                return jsonify(error="assignment_conflict", message=str(err)), 409
            except AssignmentValidationError as err:
                return jsonify(error="validation_error", message=str(err)), 400

        return jsonify(
            status="created",
            id=str(item.id),
            group_name=item.group_name,
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

        with group_state_lock():
            store = assignment_store()
            existing = store.get(uid)
            if existing is None:
                abort(404)
            snapshot = catalog()
            topic = next((t for t in snapshot.topics if t.id == topic_id), None)
            if topic is None or topic.subject_id != existing.subject_id:
                abort(400)

            try:
                item = store.update(uid, lesson_date, pair_number, topic_id)
            except AssignmentConflictError as err:
                return jsonify(error="assignment_conflict", message=str(err)), 409
            except AssignmentNotFoundError:
                abort(404)
            except AssignmentValidationError as err:
                return jsonify(error="validation_error", message=str(err)), 400

        return jsonify(
            status="ok",
            id=str(item.id),
            lesson_date=item.lesson_date,
            pair_number=item.pair_number,
            topic_id=str(item.topic_id),
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
            assignment_store().delete(uid)
        return ("", 204)

    @app.errorhandler(RequestEntityTooLarge)
    def large(_):
        return jsonify(error="file too large"), 413

    return app
