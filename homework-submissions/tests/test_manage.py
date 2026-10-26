import http.server
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

MANAGE_SH_SRC = Path(__file__).resolve().parents[2] / "manage.sh"
VENV_GUNICORN = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "gunicorn"


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def clean_env(extra=None):
    env = {k: v for k, v in os.environ.items() if k not in ("GUNICORN_CMD_ARGS", "PYTHONPATH")}
    if extra:
        env.update(extra)
    return env


def is_pid_alive_and_not_zombie(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    status_file = Path(f"/proc/{pid}/status")
    if status_file.exists():
        try:
            status_text = status_file.read_text(encoding="utf-8")
            if "State:\tZ" in status_text or "State: Z" in status_text:
                return False
        except OSError:
            pass
    return True


def get_worker_pids(master_pid):
    workers = set()
    children_file = Path(f"/proc/{master_pid}/task/{master_pid}/children")
    if children_file.exists():
        try:
            content = children_file.read_text(encoding="utf-8").strip()
            for part in content.split():
                if part.isdigit():
                    workers.add(int(part))
        except OSError:
            pass
    if not workers and Path("/proc").exists():
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit():
                stat_file = entry / "stat"
                if stat_file.exists():
                    try:
                        stat_parts = stat_file.read_text(encoding="utf-8").split()
                        if len(stat_parts) > 3 and stat_parts[3] == str(master_pid):
                            workers.add(int(entry.name))
                    except OSError:
                        pass
    return list(workers)


@pytest.fixture
def isolated_env(tmp_path):
    root_dir = tmp_path / "root with spaces"
    root_dir.mkdir()

    app_dir = root_dir / "homework-submissions"
    app_dir.mkdir()

    manage_sh = root_dir / "manage.sh"
    shutil.copy2(MANAGE_SH_SRC, manage_sh)
    manage_sh.chmod(0o755)

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

    return {
        "root_dir": root_dir,
        "app_dir": app_dir,
        "manage_sh": manage_sh,
    }


def test_script_relative_app_dir_resolution(isolated_env, tmp_path):
    manage_sh = isolated_env["manage_sh"]
    app_dir = isolated_env["app_dir"]

    # Создаем второй проект со своим wsgi.py
    other_project = tmp_path / "other project with spaces"
    other_project.mkdir()
    (other_project / "wsgi.py").write_text("raise RuntimeError('other project')\n", encoding="utf-8")

    port = find_free_port()
    env = clean_env({"PORT": str(port), "HOST": "127.0.0.1"})

    # Запускаем manage_sh абсолютным путем, находясь в other_project БЕЗ APP_DIR
    res = subprocess.run(
        [str(manage_sh), "status"],
        cwd=str(other_project),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert res.returncode == 0
    assert "ОСТАНОВЛЕН" in res.stdout
    # PID файл должен проверяться именно в app_dir (isolated_env), а не в other_project
    assert not (other_project / ".gunicorn.pid").exists()
    assert not (app_dir / ".gunicorn.pid").exists()


def test_stop_does_not_kill_foreign_port_listener_and_cleans_stale_pid(isolated_env):
    app_dir = isolated_env["app_dir"]
    manage_sh = isolated_env["manage_sh"]
    port = find_free_port()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    env = clean_env({"PORT": str(port), "HOST": "127.0.0.1", "APP_DIR": str(app_dir)})

    try:
        # 1. PID-файл отсутствует
        res = subprocess.run(
            [str(manage_sh), "stop"],
            cwd=str(app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert res.returncode == 0
        assert "Отслеживаемое приложение не запущено" in res.stdout

        with socket.create_connection(("127.0.0.1", port), timeout=1) as s:
            assert s is not None

        # 2. PID-файл содержит PID стороннего процесса
        pid_file = app_dir / ".gunicorn.pid"
        pid_file.write_text(str(os.getpid()), encoding="utf-8")

        res = subprocess.run(
            [str(manage_sh), "stop"],
            cwd=str(app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert res.returncode == 0
        assert "Отслеживаемое приложение не запущено" in res.stdout
        assert not pid_file.exists()

        with socket.create_connection(("127.0.0.1", port), timeout=1) as s:
            assert s is not None

        # 3. PID-файл содержит фрагменты цифр через пробел (не должен склеиваться)
        my_pid_str = str(os.getpid())
        if len(my_pid_str) >= 2:
            split_pid = f"{my_pid_str[:1]} {my_pid_str[1:]}\n"
        else:
            split_pid = "1 2\n"
        pid_file.write_text(split_pid, encoding="utf-8")

        res = subprocess.run(
            [str(manage_sh), "stop"],
            cwd=str(app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert res.returncode == 0
        assert "Отслеживаемое приложение не запущено" in res.stdout
        assert not pid_file.exists()

        # 4. PID-файл многострочный
        pid_file.write_text(f"{os.getpid()}\n99999\n", encoding="utf-8")
        res = subprocess.run(
            [str(manage_sh), "stop"],
            cwd=str(app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert res.returncode == 0
        assert not pid_file.exists()
    finally:
        server.shutdown()
        server.server_close()


def test_stop_does_not_kill_foreign_processes_with_similar_args(isolated_env, tmp_path):
    app_dir = isolated_env["app_dir"]
    manage_sh = isolated_env["manage_sh"]
    port = find_free_port()
    env = clean_env({"PORT": str(port), "HOST": "127.0.0.1", "APP_DIR": str(app_dir)})
    pid_file = app_dir / ".gunicorn.pid"

    # Вариант A: процесс в том же cwd с аргументом wsgi:app
    proc_a = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "wsgi:app"],
        cwd=str(app_dir),
    )

    # Вариант B: процесс в том же cwd с текстом gunicorn
    proc_b = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "gunicorn_fake_service"],
        cwd=str(app_dir),
    )

    # Вариант C: Gunicorn из другого каталога
    other_dir = tmp_path / "other_app"
    other_dir.mkdir()
    (other_dir / "wsgi.py").write_text("def app(e, s): s('200 OK', []); return [b'OK']\n", encoding="utf-8")
    other_port = find_free_port()
    other_pid_file = other_dir / ".gunicorn.pid"
    other_gunicorn = subprocess.Popen(
        [
            str(VENV_GUNICORN),
            "--bind", f"127.0.0.1:{other_port}",
            "--pid", str(other_pid_file),
            "--workers", "1",
            "wsgi:app",
        ],
        cwd=str(other_dir),
    )

    try:
        for p in (proc_a, proc_b, other_gunicorn):
            pid_file.write_text(str(p.pid), encoding="utf-8")

            res_status = subprocess.run(
                [str(manage_sh), "status"],
                cwd=str(app_dir),
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            assert "ОСТАНОВЛЕН" in res_status.stdout

            res_stop = subprocess.run(
                [str(manage_sh), "stop"],
                cwd=str(app_dir),
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            assert res_stop.returncode == 0
            assert "Отслеживаемое приложение не запущено" in res_stop.stdout
            assert is_pid_alive_and_not_zombie(p.pid)
            assert not pid_file.exists()
    finally:
        for p in (proc_a, proc_b, other_gunicorn):
            try:
                p.terminate()
                p.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    p.kill()
                    p.wait(timeout=5)
                except OSError:
                    pass
        pid_file.unlink(missing_ok=True)
        other_pid_file.unlink(missing_ok=True)


def test_start_fails_when_port_busy_by_http_server(isolated_env):
    app_dir = isolated_env["app_dir"]
    manage_sh = isolated_env["manage_sh"]
    port = find_free_port()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Foreign HTTP server")

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    env = clean_env({"PORT": str(port), "HOST": "127.0.0.1", "APP_DIR": str(app_dir)})
    pid_file = app_dir / ".gunicorn.pid"

    try:
        res = subprocess.run(
            [str(manage_sh), "start"],
            cwd=str(app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert res.returncode != 0
        assert "Приложение успешно запущено" not in res.stdout
        assert "уже занят другим процессом" in res.stderr
        assert not pid_file.exists()

        # Сторонний сервер должен продолжать отвечать 200
        req = subprocess.run(
            ["curl", "-s", f"http://127.0.0.1:{port}/"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        assert "Foreign HTTP server" in req.stdout
    finally:
        server.shutdown()
        server.server_close()


def test_start_fails_when_port_is_busy_by_raw_socket(isolated_env):
    app_dir = isolated_env["app_dir"]
    manage_sh = isolated_env["manage_sh"]
    port = find_free_port()

    busy_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    busy_sock.bind(("127.0.0.1", port))
    busy_sock.listen(1)

    env = clean_env({"PORT": str(port), "HOST": "127.0.0.1", "APP_DIR": str(app_dir)})

    try:
        res = subprocess.run(
            [str(manage_sh), "start"],
            cwd=str(app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert res.returncode != 0
        assert "уже занят другим процессом" in res.stderr
        assert not (app_dir / ".gunicorn.pid").exists()
    finally:
        busy_sock.close()


def test_start_fails_when_app_crashes_on_import(isolated_env):
    app_dir = isolated_env["app_dir"]
    manage_sh = isolated_env["manage_sh"]
    port = find_free_port()

    (app_dir / "wsgi.py").write_text("this is a syntax error and will crash import\n", encoding="utf-8")
    env = clean_env({"PORT": str(port), "HOST": "127.0.0.1", "APP_DIR": str(app_dir)})

    res = subprocess.run(
        [str(manage_sh), "start"],
        cwd=str(app_dir),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert res.returncode != 0
    assert "Не удалось запустить приложение" in res.stderr or "завершился сбоем" in res.stderr
    assert not (app_dir / ".gunicorn.pid").exists()

    # Порт должен быть свободен для bind
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))


def test_start_fails_when_app_returns_http_500(isolated_env):
    app_dir = isolated_env["app_dir"]
    manage_sh = isolated_env["manage_sh"]
    port = find_free_port()

    (app_dir / "wsgi.py").write_text(
        "def app(environ, start_response):\n"
        "    start_response('500 Internal Server Error', [('Content-Type', 'text/plain')])\n"
        "    return [b'Server Error']\n",
        encoding="utf-8",
    )
    env = clean_env({"PORT": str(port), "HOST": "127.0.0.1", "APP_DIR": str(app_dir)})
    pid_file = app_dir / ".gunicorn.pid"

    res = subprocess.run(
        [str(manage_sh), "start"],
        cwd=str(app_dir),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert res.returncode != 0
    assert "Не удалось запустить приложение" in res.stderr
    assert not pid_file.exists()

    # Порт должен быть полностью свободен
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))


def test_lifecycle_success_with_worker_cleanup(isolated_env):
    app_dir = isolated_env["app_dir"]
    manage_sh = isolated_env["manage_sh"]
    port = find_free_port()
    env = clean_env({"PORT": str(port), "HOST": "127.0.0.1", "APP_DIR": str(app_dir)})
    pid_file = app_dir / ".gunicorn.pid"

    master_pid = None
    worker_pids = []

    try:
        # 1. Start
        res = subprocess.run(
            [str(manage_sh), "start"],
            cwd=str(app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert res.returncode == 0
        assert "Приложение успешно запущено" in res.stdout
        assert pid_file.exists()
        master_pid = int(pid_file.read_text().strip())
        assert is_pid_alive_and_not_zombie(master_pid)

        # Ждем запуска воркеров
        time.sleep(1)
        worker_pids = get_worker_pids(master_pid)
        assert len(worker_pids) > 0, "У Gunicorn master должны быть запущены workers"

        # 2. Повторный start (идемпотентность)
        res_repeat = subprocess.run(
            [str(manage_sh), "start"],
            cwd=str(app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert res_repeat.returncode == 0
        assert "Приложение уже запущено" in res_repeat.stdout
        assert int(pid_file.read_text().strip()) == master_pid

        # 3. Status
        res_status = subprocess.run(
            [str(manage_sh), "status"],
            cwd=str(app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert res_status.returncode == 0
        assert "РАБОТАЕТ" in res_status.stdout
        assert "HTTP Health: 200 OK" in res_status.stdout

        # 4. Restart
        res_restart = subprocess.run(
            [str(manage_sh), "restart"],
            cwd=str(app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert res_restart.returncode == 0
        assert "Приложение остановлено" in res_restart.stdout
        assert "Приложение успешно запущено" in res_restart.stdout

        # Старый master и воркеры должны быть завершены
        assert not is_pid_alive_and_not_zombie(master_pid)
        for w in worker_pids:
            assert not is_pid_alive_and_not_zombie(w)

        new_master_pid = int(pid_file.read_text().strip())
        assert new_master_pid != master_pid
        master_pid = new_master_pid

        time.sleep(1)
        worker_pids = get_worker_pids(master_pid)

        # 5. Stop
        res_stop = subprocess.run(
            [str(manage_sh), "stop"],
            cwd=str(app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert res_stop.returncode == 0
        assert "Приложение остановлено" in res_stop.stdout
        assert not pid_file.exists()

        # Master и воркеры завершены
        assert not is_pid_alive_and_not_zombie(master_pid)
        for w in worker_pids:
            assert not is_pid_alive_and_not_zombie(w)

        # 6. Повторный stop безопасен
        res_stop_again = subprocess.run(
            [str(manage_sh), "stop"],
            cwd=str(app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert res_stop_again.returncode == 0
        assert "Отслеживаемое приложение не запущено" in res_stop_again.stdout

        # 7. Status после stop
        res_status_after = subprocess.run(
            [str(manage_sh), "status"],
            cwd=str(app_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert res_status_after.returncode == 0
        assert "ОСТАНОВЛЕН" in res_status_after.stdout
    finally:
        # Безопасная очистка только для подтвержденных PID этого теста
        for p in [master_pid, *worker_pids]:
            if p and is_pid_alive_and_not_zombie(p):
                try:
                    os.kill(p, 9)
                except OSError:
                    pass
        pid_file.unlink(missing_ok=True)
