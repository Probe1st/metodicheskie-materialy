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

MANAGE_SH_SRC = Path(__file__).resolve().parents[1] / "manage.sh"
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


def get_process_starttime(pid):
    stat_file = Path(f"/proc/{pid}/stat")
    if not stat_file.exists():
        return None
    try:
        content = stat_file.read_text(encoding="utf-8")
        after_comm = content.rsplit(")", 1)[-1].strip()
        tokens = after_comm.split()
        if len(tokens) >= 20:
            return int(tokens[19])
    except (OSError, ValueError, IndexError):
        pass
    return None


def is_identity_alive(pid, starttime):
    if not is_pid_alive_and_not_zombie(pid):
        return False
    return get_process_starttime(pid) == starttime


def format_process_diagnostics(pid):
    try:
        stat_file = Path(f"/proc/{pid}/stat")
        stat_text = stat_file.read_text(encoding="utf-8") if stat_file.exists() else "missing"
        cmdline_file = Path(f"/proc/{pid}/cmdline")
        cmdline_text = (
            cmdline_file.read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
            if cmdline_file.exists()
            else "missing"
        )
        return f"PID {pid}: stat=({stat_text.strip()}), cmdline=({cmdline_text.strip()})"
    except Exception as e:
        return f"PID {pid}: error reading info: {e}"


def assert_no_leaked_processes(app_dir, observed_identities):
    leaked = []
    for pid, st in observed_identities:
        if is_identity_alive(pid, st):
            leaked.append(format_process_diagnostics(pid))
    real_app_dir = app_dir.resolve()
    if Path("/proc").exists():
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit():
                pid = int(entry.name)
                try:
                    cwd_link = Path(f"/proc/{pid}/cwd")
                    if cwd_link.exists() and cwd_link.resolve() == real_app_dir:
                        cmdline_file = Path(f"/proc/{pid}/cmdline")
                        if cmdline_file.exists() and b"gunicorn" in cmdline_file.read_bytes():
                            if is_pid_alive_and_not_zombie(pid):
                                diag = format_process_diagnostics(pid)
                                if diag not in leaked:
                                    leaked.append(diag)
                except OSError:
                    pass
    assert not leaked, f"Processes leaked after startup failure: {leaked}"


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

    app_dir = root_dir

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

    pid_file = app_dir / ".gunicorn.pid"
    proc = subprocess.Popen(
        [str(manage_sh), "start"],
        cwd=str(app_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    observed_identities = set()
    t0 = time.monotonic()
    while proc.poll() is None:
        if pid_file.exists():
            try:
                content = pid_file.read_text().strip()
                if content.isdigit():
                    m_pid = int(content)
                    m_st = get_process_starttime(m_pid)
                    if m_st is not None:
                        observed_identities.add((m_pid, m_st))
                    for w_pid in get_worker_pids(m_pid):
                        w_st = get_process_starttime(w_pid)
                        if w_st is not None:
                            observed_identities.add((w_pid, w_st))
            except OSError:
                pass
        time.sleep(0.05)
        if time.monotonic() - t0 > 65:
            proc.kill()
            break

    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode != 0
    assert "Не удалось запустить приложение" in stderr or "завершился сбоем" in stderr
    assert not pid_file.exists()
    assert_no_leaked_processes(app_dir, observed_identities)

    # Порт должен быть свободен для bind и listen
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.listen(1)
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

    proc = subprocess.Popen(
        [str(manage_sh), "start"],
        cwd=str(app_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    observed_identities = set()
    t0 = time.monotonic()
    while proc.poll() is None:
        if pid_file.exists():
            try:
                content = pid_file.read_text().strip()
                if content.isdigit():
                    m_pid = int(content)
                    m_st = get_process_starttime(m_pid)
                    if m_st is not None:
                        observed_identities.add((m_pid, m_st))
                    for w_pid in get_worker_pids(m_pid):
                        w_st = get_process_starttime(w_pid)
                        if w_st is not None:
                            observed_identities.add((w_pid, w_st))
            except OSError:
                pass
        time.sleep(0.05)
        if time.monotonic() - t0 > 65:
            proc.kill()
            break

    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode != 0
    assert "Не удалось запустить приложение" in stderr
    assert not pid_file.exists()
    assert_no_leaked_processes(app_dir, observed_identities)

    # Порт должен быть полностью свободен
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.listen(1)
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


def test_force_stop_terminates_orphaned_workers(isolated_env, tmp_path):
    import signal
    app_dir = isolated_env["app_dir"]
    manage_sh = isolated_env["manage_sh"]
    port = find_free_port()
    env = clean_env({"PORT": str(port), "HOST": "127.0.0.1", "APP_DIR": str(app_dir)})
    pid_file = app_dir / ".gunicorn.pid"

    (app_dir / "wsgi.py").write_text(
        "import signal\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "def app(environ, start_response):\n"
        "    start_response('200 OK', [('Content-Type', 'text/plain')])\n"
        "    return [b'OK']\n",
        encoding="utf-8",
    )

    other_dir = tmp_path / "control_dir"
    other_dir.mkdir()
    control_proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=str(other_dir),
    )

    master_pid = None
    worker_pids = []
    recorded_identities = []

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
        assert res.returncode == 0
        assert pid_file.exists()
        master_pid = int(pid_file.read_text().strip())
        master_st = get_process_starttime(master_pid)
        assert master_st is not None
        recorded_identities.append((master_pid, master_st))

        for _ in range(25):
            worker_pids = get_worker_pids(master_pid)
            if len(worker_pids) >= 2:
                break
            time.sleep(0.2)
        assert len(worker_pids) >= 2, f"Expected >= 2 workers, got {worker_pids}"

        for w in worker_pids:
            w_st = get_process_starttime(w)
            assert w_st is not None
            recorded_identities.append((w, w_st))

        # Send SIGSTOP to master
        os.kill(master_pid, signal.SIGSTOP)

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
        assert not pid_file.exists()

        # Check that both master and workers are terminated
        for pid, st in recorded_identities:
            assert not is_identity_alive(pid, st), (
                f"Process {pid} survived force stop! Diagnostics: {format_process_diagnostics(pid)}"
            )

        # Control process in other dir must survive
        assert control_proc.poll() is None
    finally:
        for pid, _ in recorded_identities:
            try:
                os.kill(pid, signal.SIGCONT)
            except OSError:
                pass
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        if control_proc.poll() is None:
            control_proc.kill()
            control_proc.wait()

