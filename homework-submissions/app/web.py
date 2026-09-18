from __future__ import annotations

import hmac
import os
from datetime import UTC, datetime
from pathlib import Path
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from .access import AccessStore
from .catalog import load_catalog
from .storage import destination_directory, write_upload


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
    )
    if config:
        app.config.update(config)

    app.config["MAX_CONTENT_LENGTH"] = app.config["MAX_UPLOAD_GB"] * 1024**3

    def catalog():
        return load_catalog(Path(app.config["CATALOG_PATH"]))

    def access_store() -> AccessStore:
        db_path = Path(app.config["PRIVATE_DATA_DIR"]) / "access.sqlite3"
        return AccessStore(db_path, catalog().groups)

    def is_admin() -> bool:
        return bool(session.get("admin"))

    @app.get("/")
    def index():
        return render_template("index.html", catalog=catalog(), limit=app.config["MAX_UPLOAD_GB"])

    @app.post("/unlock")
    def unlock():
        group = request.form.get("group", "").strip()
        password = request.form.get("password", "")
        if not group or not password:
            abort(403)

        store = access_store()
        version = store.authorize(group, password)
        if version is None:
            abort(403)

        session["student_group"] = group
        session["student_access_version"] = version
        return ("", 204)

    @app.post("/submit")
    def submit():
        session_group = session.get("student_group")
        session_version = session.get("student_access_version")
        if not session_group or session_version is None:
            abort(403)

        group, discipline, work, student = (
            request.form.get(key, "").strip()
            for key in ("group", "discipline", "work", "student_name")
        )
        file = request.files.get("file")

        if group != session_group:
            abort(403)

        if not all((group, discipline, work, student, file)) or not file.filename or not catalog().contains(group, discipline, work):
            abort(400)

        store = access_store()
        if not store.permits(session_group, int(session_version)):
            abort(403)

        directory = destination_directory(Path(app.config["STORAGE_ROOT"]), group, discipline, work)
        saved = write_upload(file.stream, file.filename, student, directory)
        return jsonify(filename=saved.name), 201

    @app.get("/admin")
    def admin_dashboard():
        if not is_admin():
            return render_template("admin.html", authenticated=False)

        groups = [
            {
                "group": item.group,
                "is_open": item.is_open,
                "closes_at": _format_local_input(item.closes_at),
                "version": item.version,
            }
            for item in access_store().groups()
        ]
        return render_template("admin.html", authenticated=True, groups=groups)

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

    @app.post("/admin/groups")
    def admin_update_group():
        if not is_admin():
            abort(403)

        group = request.form.get("group", "").strip()
        if not group or group not in catalog().groups:
            abort(400)

        password = request.form.get("password", "").strip() or None
        is_open = request.form.get("is_open") == "1"
        raw_deadline = request.form.get("closes_at", "")

        try:
            deadline = _parse_deadline_input(raw_deadline)
        except ValueError:
            abort(400)

        store = access_store()
        store.configure(group, password, is_open, deadline)
        return ("", 204)

    @app.errorhandler(RequestEntityTooLarge)
    def large(_):
        return jsonify(error="file too large"), 413

    return app
