from __future__ import annotations

import hmac
import os
from pathlib import Path
from flask import Flask, abort, jsonify, render_template, request, session
from werkzeug.exceptions import RequestEntityTooLarge
from .catalog import load_catalog
from .storage import destination_directory, write_upload


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret"), SUBMISSION_PASSWORD=os.getenv("SUBMISSION_PASSWORD", "teacher"), MAX_UPLOAD_GB=int(os.getenv("MAX_UPLOAD_GB", "1")), STORAGE_ROOT=Path("storage"), CATALOG_PATH=Path("catalog.json"))
    if config: app.config.update(config)
    app.config["MAX_CONTENT_LENGTH"] = app.config["MAX_UPLOAD_GB"] * 1024**3
    def catalog(): return load_catalog(Path(app.config["CATALOG_PATH"]))
    @app.get("/")
    def index():
        return render_template("index.html", catalog=catalog(), limit=app.config["MAX_UPLOAD_GB"])
    @app.post("/unlock")
    def unlock():
        if not hmac.compare_digest(request.form.get("password", ""), app.config["SUBMISSION_PASSWORD"]): abort(403)
        session["unlocked"] = True
        return ("", 204)
    @app.post("/submit")
    def submit():
        if not session.get("unlocked"): abort(403)
        group, discipline, work, student = (request.form.get(key, "").strip() for key in ("group", "discipline", "work", "student_name"))
        file = request.files.get("file")
        if not all((group, discipline, work, student, file)) or not file.filename or not catalog().contains(group, discipline, work): abort(400)
        directory = destination_directory(Path(app.config["STORAGE_ROOT"]), group, discipline, work)
        saved = write_upload(file.stream, file.filename, student, directory)
        return jsonify(filename=saved.name), 201
    @app.errorhandler(RequestEntityTooLarge)
    def large(_): return jsonify(error="file too large"), 413
    return app
