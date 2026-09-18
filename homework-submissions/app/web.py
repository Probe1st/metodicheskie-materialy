from __future__ import annotations

import os
from pathlib import Path
from flask import Flask, abort, jsonify, render_template, request, session
from werkzeug.exceptions import RequestEntityTooLarge

from .access import AccessStore
from .catalog import load_catalog
from .storage import destination_directory, write_upload


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

    @app.errorhandler(RequestEntityTooLarge)
    def large(_):
        return jsonify(error="file too large"), 413

    return app
