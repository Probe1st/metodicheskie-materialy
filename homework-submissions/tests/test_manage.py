import http.server
import os
import socket
import subprocess
import threading
from pathlib import Path

import pytest

MANAGE_SH_PATH = Path(__file__).resolve().parents[2] / "manage.sh"
VENV_GUNICORN = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "gunicorn"


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def isolated_app_dir(tmp_path):
    app_dir = tmp_path / "app dir with spaces"
    app_dir.mkdir()

    wsgi_py = app_dir / "wsgi.py"
    wsgi_py.write_text(
        "def app(environ, start_response):\n"
        "    start_response('200 OK', [('Content-Type', 'text/plain')])\n"
        "    return [b'OK from test app']\n",
        encoding="utf-8",
    )

    venv_link = app_dir / ".venv"
    if VENV_GUNICORN.exists():
        venv_link.symlink_to(VENV_GUNICORN.parent.parent)

    return app_dir


def test_stop_does_not_kill_foreign_port_listener_and_cleans_stale_pid(isolated_app_dir):
    port = find_free_port()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    env = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1", "APP_DIR": str(isolated_app_dir)}

    try:
        # 1. PID-файл отсутствует
        res = subprocess.run(
            [str(MANAGE_SH_PATH), "stop"],
            cwd=str(isolated_app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0
        assert "Отслеживаемое приложение не запущено" in res.stdout

        # Сторонний сервер должен продолжать работать
        with socket.create_connection(("127.0.0.1", port), timeout=1) as s:
            assert s is not None

        # 2. PID-файл содержит PID стороннего процесса (наш python процесс)
        pid_file = isolated_app_dir / ".gunicorn.pid"
        pid_file.write_text(str(os.getpid()), encoding="utf-8")

        res = subprocess.run(
            [str(MANAGE_SH_PATH), "stop"],
            cwd=str(isolated_app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0
        assert "Отслеживаемое приложение не запущено" in res.stdout
        assert not pid_file.exists()

        # Сторонний сервер и текущий процесс живы!
        with socket.create_connection(("127.0.0.1", port), timeout=1) as s:
            assert s is not None

        # 3. PID-файл содержит нечисловую строку
        pid_file.write_text("invalid_pid\n", encoding="utf-8")
        res = subprocess.run(
            [str(MANAGE_SH_PATH), "stop"],
            cwd=str(isolated_app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0
        assert not pid_file.exists()
    finally:
        server.shutdown()
        server.server_close()


def test_start_fails_when_port_is_busy(isolated_app_dir):
    port = find_free_port()

    busy_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    busy_sock.bind(("127.0.0.1", port))
    busy_sock.listen(1)

    env = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1", "APP_DIR": str(isolated_app_dir)}

    try:
        res = subprocess.run(
            [str(MANAGE_SH_PATH), "start"],
            cwd=str(isolated_app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode != 0
        assert "уже занят другим процессом" in res.stderr
        assert not (isolated_app_dir / ".gunicorn.pid").exists()
    finally:
        busy_sock.close()


def test_start_fails_when_app_crashes(isolated_app_dir):
    port = find_free_port()

    (isolated_app_dir / "wsgi.py").write_text("this is a syntax error and will crash import\n")

    env = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1", "APP_DIR": str(isolated_app_dir)}

    res = subprocess.run(
        [str(MANAGE_SH_PATH), "start"],
        cwd=str(isolated_app_dir),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "Не удалось запустить приложение" in res.stderr or "завершился сбоем" in res.stderr
    assert not (isolated_app_dir / ".gunicorn.pid").exists()


def test_lifecycle_success(isolated_app_dir):
    port = find_free_port()
    env = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1", "APP_DIR": str(isolated_app_dir)}

    pid_file = isolated_app_dir / ".gunicorn.pid"

    try:
        # 1. Start
        res = subprocess.run(
            [str(MANAGE_SH_PATH), "start"],
            cwd=str(isolated_app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0
        assert "Приложение успешно запущено" in res.stdout
        assert pid_file.exists()
        initial_pid = int(pid_file.read_text().strip())

        # 2. Повторный start (идемпотентность)
        res_repeat = subprocess.run(
            [str(MANAGE_SH_PATH), "start"],
            cwd=str(isolated_app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res_repeat.returncode == 0
        assert "Приложение уже запущено" in res_repeat.stdout
        assert int(pid_file.read_text().strip()) == initial_pid

        # 3. Status
        res_status = subprocess.run(
            [str(MANAGE_SH_PATH), "status"],
            cwd=str(isolated_app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res_status.returncode == 0
        assert "РАБОТАЕТ" in res_status.stdout
        assert "HTTP Health: 200 OK" in res_status.stdout

        # 4. Restart
        res_restart = subprocess.run(
            [str(MANAGE_SH_PATH), "restart"],
            cwd=str(isolated_app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res_restart.returncode == 0
        assert "Приложение остановлено" in res_restart.stdout
        assert "Приложение успешно запущено" in res_restart.stdout
        new_pid = int(pid_file.read_text().strip())
        assert new_pid != initial_pid

        # 5. Stop
        res_stop = subprocess.run(
            [str(MANAGE_SH_PATH), "stop"],
            cwd=str(isolated_app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res_stop.returncode == 0
        assert "Приложение остановлено" in res_stop.stdout
        assert not pid_file.exists()

        # 6. Status после stop
        res_status_after = subprocess.run(
            [str(MANAGE_SH_PATH), "status"],
            cwd=str(isolated_app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res_status_after.returncode == 0
        assert "ОСТАНОВЛЕН" in res_status_after.stdout
    finally:
        if pid_file.exists():
            try:
                p = int(pid_file.read_text().strip())
                os.kill(p, 9)
            except OSError:
                pass
            pid_file.unlink(missing_ok=True)
