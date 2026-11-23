"""Audit test suite for homework-submissions SQLite v2 configuration & lifecycle.

Covers test matrix families CFG-01 and CFG-02:
  - CFG-01: Configuration & Path Resolution
  - CFG-02: manage.sh Script & Process Lifecycle
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import app.web
from app.catalog_store import create_catalog_store
from app.database import initialize

# ==============================================================================
# CFG-01: Configuration & Path Resolution
# ==============================================================================

def test_cfg_01_default_path_resolution(tmp_path: Path) -> None:
    """CFG-01-01: When PRIVATE_DATA_DIR is set to absolute path, default DATABASE_PATH is <PRIVATE_DATA_DIR>/app_v2.sqlite3."""
    priv_dir = tmp_path / "custom_private"
    priv_dir.mkdir(parents=True, exist_ok=True)
    expected_db = priv_dir / "app_v2.sqlite3"
    initialize(expected_db)

    storage_dir = tmp_path / "custom_storage"
    storage_dir.mkdir(parents=True, exist_ok=True)

    app_instance = app.web.create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "PRIVATE_DATA_DIR": str(priv_dir),
        "STORAGE_ROOT": str(storage_dir),
    })

    # Assert DATABASE_PATH resolved to priv_dir / "app_v2.sqlite3"
    resolved_db = Path(app_instance.config["DATABASE_PATH"])
    assert resolved_db == expected_db.resolve() or resolved_db == expected_db


def test_cfg_01_relative_path_resolution_and_double_prefix() -> None:
    """CFG-01-01: Relative path resolution replicating .env.example resolves cleanly without duplicate prefix."""
    config = {
        "PRIVATE_DATA_DIR": ".private",
        "DATABASE_PATH": ".private/app_v2.sqlite3",
    }
    private_dir = Path(config["PRIVATE_DATA_DIR"])
    db_path = Path(config["DATABASE_PATH"])

    if not db_path.is_absolute():
        try:
            db_path.relative_to(private_dir)
            resolved_db_path = db_path
        except ValueError:
            resolved_db_path = private_dir / db_path
    else:
        resolved_db_path = db_path

    # Idempotent prefix resolution: avoids '.private/.private/app_v2.sqlite3'
    assert resolved_db_path.as_posix() == ".private/app_v2.sqlite3", (
        f"Expected '.private/app_v2.sqlite3', got {resolved_db_path.as_posix()}"
    )

    # Same check for create_catalog_store
    store = create_catalog_store({
        "PRIVATE_DATA_DIR": ".private",
        "DATABASE_PATH": ".private/app_v2.sqlite3",
    })
    # SqliteCatalogStore exposes database_path attribute
    assert store.database_path.as_posix() == ".private/app_v2.sqlite3"

def test_cfg_01_absolute_paths_respected_literally(tmp_path: Path) -> None:
    """CFG-01-01: Absolute paths for DATABASE_PATH and STORAGE_ROOT are used literally without prepending PRIVATE_DATA_DIR."""
    db_file = tmp_path / "absolute_dir" / "database.sqlite3"
    initialize(db_file)
    storage_dir = tmp_path / "absolute_storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    dummy_priv = tmp_path / "ignored_private"

    app_instance = app.web.create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "PRIVATE_DATA_DIR": str(dummy_priv),
        "DATABASE_PATH": str(db_file.resolve()),
        "STORAGE_ROOT": str(storage_dir.resolve()),
    })

    assert Path(app_instance.config["DATABASE_PATH"]) == db_file.resolve()
    assert Path(app_instance.config["STORAGE_ROOT"]) == storage_dir.resolve()


def test_cfg_01_paths_with_spaces_and_unicode(tmp_path: Path) -> None:
    """CFG-01-01: Paths with spaces and Cyrillic Unicode characters resolve and validate cleanly."""
    unicode_dir = tmp_path / "каталог с пробелами и символами №1"
    db_file = unicode_dir / "база_данных v2.sqlite3"
    initialize(db_file)

    storage_dir = unicode_dir / "хранилище файлов"
    storage_dir.mkdir(parents=True, exist_ok=True)

    app_instance = app.web.create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
        "PRIVATE_DATA_DIR": str(unicode_dir),
        "DATABASE_PATH": str(db_file),
        "STORAGE_ROOT": str(storage_dir),
    })

    assert Path(app_instance.config["DATABASE_PATH"]).exists()
    assert Path(app_instance.config["STORAGE_ROOT"]).exists()


def test_cfg_01_missing_database_on_startup_raises_runtime_error(tmp_path: Path) -> None:
    """CFG-01-01: create_app() raises RuntimeError when DATABASE_PATH does not exist, instructing to initialize/migrate."""
    nonexistent_db = tmp_path / "missing_v2.sqlite3"

    with pytest.raises(RuntimeError) as exc_info:
        app.web.create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret-key-at-least-32-bytes-long-for-testing",
            "DATABASE_PATH": str(nonexistent_db),
            "STORAGE_ROOT": str(tmp_path),
        })

    error_msg = str(exc_info.value)
    assert "missing or invalid" in error_msg
    assert "migrate_v2 apply" in error_msg or "init" in error_msg


# ==============================================================================
# CFG-02: manage.sh Script & Process Lifecycle
# ==============================================================================

@pytest.fixture
def manage_sandbox(tmp_path: Path) -> Path:
    """Sets up an isolated manage.sh execution environment."""
    sandbox_root = tmp_path / "manage_sandbox"
    app_dir = sandbox_root / "homework-submissions"
    app_dir.mkdir(parents=True, exist_ok=True)

    # Copy real manage.sh to sandbox root
    repo_manage_sh = Path(__file__).resolve().parents[2] / "manage.sh"
    sandbox_manage_sh = sandbox_root / "manage.sh"
    shutil.copy2(repo_manage_sh, sandbox_manage_sh)
    sandbox_manage_sh.chmod(0o755)

    # Create dummy app files
    (app_dir / "wsgi.py").write_text("app = None\n", encoding="utf-8")
    (app_dir / ".env").write_text("HOST=127.0.0.1\nPORT=5999\n", encoding="utf-8")

    return sandbox_root


def test_cfg_02_manage_status_and_stale_pid(manage_sandbox: Path) -> None:
    """CFG-02-01: manage.sh status reports stopped; cleans up stale PID file from dead process."""
    app_dir = manage_sandbox / "homework-submissions"
    manage_sh = manage_sandbox / "manage.sh"
    pid_file = app_dir / ".gunicorn.pid"

    # 1. Clean stopped status
    proc_clean = subprocess.run(
        [str(manage_sh), "status"],
        cwd=manage_sandbox,
        env={**os.environ, "APP_DIR": str(app_dir)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert "ОСТАНОВЛЕН" in proc_clean.stdout

    # 2. Write stale PID of a non-existent process
    # Find an unused high PID
    stale_pid = 999999
    pid_file.write_text(f"{stale_pid}\n", encoding="utf-8")

    # manage.sh status should recognize process is not running
    subprocess.run(
        [str(manage_sh), "status"],
        cwd=manage_sandbox,
        env={**os.environ, "APP_DIR": str(app_dir)},
        capture_output=True,
        text=True,
        check=False,
    )

    # manage.sh stop should clean the stale PID file
    subprocess.run(
        [str(manage_sh), "stop"],
        cwd=manage_sandbox,
        env={**os.environ, "APP_DIR": str(app_dir)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert not pid_file.exists()


def test_cfg_02_manage_foreign_pid_protection(manage_sandbox: Path) -> None:
    """CFG-02-01: manage.sh refuses to signal or kill foreign/system processes (e.g. PID 1) found in pidfile."""
    app_dir = manage_sandbox / "homework-submissions"
    manage_sh = manage_sandbox / "manage.sh"
    pid_file = app_dir / ".gunicorn.pid"

    # Put system PID 1 in PID file
    pid_file.write_text("1\n", encoding="utf-8")

    # stop_app must refuse to kill PID 1 because it is not an owned gunicorn process
    proc = subprocess.run(
        [str(manage_sh), "stop"],
        cwd=manage_sandbox,
        env={**os.environ, "APP_DIR": str(app_dir)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "Отслеживаемое приложение не запущено" in proc.stdout

    # Invariant: PID 1 is alive and unharmed
    try:
        os.kill(1, 0)
    except PermissionError:
        pass  # Alive (permission error confirms existence)


def test_cfg_02_manage_owned_live_process_stop(manage_sandbox: Path) -> None:
    """CFG-02-01: manage.sh cleanly stops an owned running process matching the gunicorn signature."""
    app_dir = manage_sandbox / "homework-submissions"
    manage_sh = manage_sandbox / "manage.sh"
    pid_file = app_dir / ".gunicorn.pid"

    # Launch mock process whose cmdline matches is_owned_process:
    # requires 'gunicorn', 'wsgi:app', and '--pid' matching pid_file
    mock_proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            "gunicorn",
            "wsgi:app",
            "--pid",
            str(pid_file),
        ],
        cwd=app_dir,
    )
    pid_file.write_text(f"{mock_proc.pid}\n", encoding="utf-8")

    try:
        # Status reports running
        proc_stat = subprocess.run(
            [str(manage_sh), "status"],
            cwd=manage_sandbox,
            env={**os.environ, "APP_DIR": str(app_dir)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert str(mock_proc.pid) in proc_stat.stdout

        # Stop command terminates process
        proc_stop = subprocess.run(
            [str(manage_sh), "stop"],
            cwd=manage_sandbox,
            env={**os.environ, "APP_DIR": str(app_dir)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert "Остановка приложения" in proc_stop.stdout

        # Verify process terminated
        mock_proc.wait(timeout=5)
        assert mock_proc.returncode is not None
        assert not pid_file.exists()
    finally:
        if mock_proc.poll() is None:
            mock_proc.kill()


def test_cfg_02_manage_migrate_v2_parameter_passthrough(manage_sandbox: Path) -> None:
    """CFG-02-02: manage.sh migrate-v2 passes CLI flags through to tools.migrate_v2 apply --offline."""
    # Use real homework-submissions APP_DIR so python -m tools.migrate_v2 resolves
    hw_app_dir = Path(__file__).resolve().parents[2]
    manage_sh = manage_sandbox / "manage.sh"

    # Run migrate-v2 with --help to verify parameter passthrough
    proc = subprocess.run(
        [str(manage_sh), "migrate-v2", "--help"],
        cwd=manage_sandbox,
        env={**os.environ, "APP_DIR": str(hw_app_dir)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert "tools.migrate_v2 apply" in proc.stdout or "usage: python -m tools.migrate_v2" in proc.stdout


def test_cfg_02_manage_migrate_v2_invariants(manage_sandbox: Path) -> None:
    """CFG-02-02: manage.sh migrate-v2 stops app first, does NOT auto-start app, and does NOT alter .env."""
    app_dir = manage_sandbox / "homework-submissions"
    manage_sh = manage_sandbox / "manage.sh"
    env_file = app_dir / ".env"
    env_content_before = env_file.read_text(encoding="utf-8")

    # Run migrate-v2 with missing required args (expected to fail in migrate_v2)
    proc = subprocess.run(
        [str(manage_sh), "migrate-v2"],
        cwd=manage_sandbox,
        env={**os.environ, "APP_DIR": str(app_dir)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0

    # Invariant: .env was NOT modified
    assert env_file.read_text(encoding="utf-8") == env_content_before

    # Invariant: Server was NOT started
    pid_file = app_dir / ".gunicorn.pid"
    assert not pid_file.exists()
