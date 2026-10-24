#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Скрипт управления приложением homework-submissions
# Команды: start | stop | restart | status | logs
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${APP_DIR:-}" ]]; then
    APP_DIR="$(cd "${APP_DIR}" && pwd)"
elif [[ -f "./wsgi.py" ]]; then
    APP_DIR="$(pwd)"
elif [[ -d "${SCRIPT_DIR}/homework-submissions" ]]; then
    APP_DIR="${SCRIPT_DIR}/homework-submissions"
else
    APP_DIR="${SCRIPT_DIR}"
fi

PID_FILE="${APP_DIR}/.gunicorn.pid"
LOG_FILE="${APP_DIR}/app.log"
PORT="${PORT:-5000}"
HOST="${HOST:-127.0.0.1}"

# Поиск gunicorn в виртуальном окружении (портативный поиск без жестких путей)
if [[ -x "${APP_DIR}/.venv/bin/gunicorn" ]]; then
    GUNICORN="${APP_DIR}/.venv/bin/gunicorn"
elif [[ -x "${SCRIPT_DIR}/.venv/bin/gunicorn" ]]; then
    GUNICORN="${SCRIPT_DIR}/.venv/bin/gunicorn"
elif git rev-parse --git-common-dir >/dev/null 2>&1 && [[ -x "$(cd "$(git rev-parse --git-common-dir)/.." && pwd)/homework-submissions/.venv/bin/gunicorn" ]]; then
    GUNICORN="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)/homework-submissions/.venv/bin/gunicorn"
elif command -v gunicorn >/dev/null 2>&1; then
    GUNICORN="$(command -v gunicorn)"
else
    echo "❌ Ошибка: gunicorn не найден в виртуальном окружении (.venv/bin/gunicorn)" >&2
    exit 1
fi
PYTHON_BIN="${GUNICORN%/gunicorn}/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="$(command -v python3 || command -v python || true)"
fi

get_pid() {
    if [[ -f "${PID_FILE}" ]]; then
        local raw_pid
        raw_pid="$(head -n 1 "${PID_FILE}" 2>/dev/null | tr -d '[:space:]' || true)"
        if [[ "${raw_pid}" =~ ^[0-9]+$ ]] && [[ "${raw_pid}" -gt 1 ]]; then
            echo "${raw_pid}"
        fi
    fi
}

is_owned_process() {
    local pid="${1:-}"
    [[ "${pid}" =~ ^[0-9]+$ ]] && [[ "${pid}" -gt 1 ]] || return 1
    kill -0 "${pid}" 2>/dev/null || return 1

    if [[ -r "/proc/${pid}/status" ]] && grep -q '^State:[[:space:]]*Z' "/proc/${pid}/status" 2>/dev/null; then
        return 1
    fi

    local proc_cwd real_app_dir
    proc_cwd="$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)"
    real_app_dir="$(readlink -f "${APP_DIR}" 2>/dev/null || true)"
    [[ -n "${proc_cwd}" && -n "${real_app_dir}" && "${proc_cwd}" == "${real_app_dir}" ]] || return 1

    if [[ -r "/proc/${pid}/cmdline" ]]; then
        tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null | grep -q -E "gunicorn.*wsgi:app|wsgi:app" || return 1
    elif ps -p "${pid}" -o args= 2>/dev/null | grep -q -E "gunicorn.*wsgi:app|wsgi:app"; then
        return 0
    else
        return 1
    fi
    return 0
}

is_running() {
    local pid
    pid="$(get_pid)"
    if [[ -n "${pid}" ]] && is_owned_process "${pid}"; then
        return 0
    fi
    return 1
}

start_app() {
    if is_running; then
        local pid
        pid="$(get_pid)"
        echo "ℹ️  Приложение уже запущено (PID: ${pid})"
        echo "   Адрес: http://${HOST}:${PORT}"
        return 0
    fi

    # Проверка, свободен ли bind адрес/порт перед запуском
    if [[ -x "${PYTHON_BIN}" ]]; then
        if ! "${PYTHON_BIN}" -c '
import socket, sys
host = sys.argv[1]
port = int(sys.argv[2])
s = socket.socket(socket.AF_INET if ":" not in host else socket.AF_INET6, socket.SOCK_STREAM)
try:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
except Exception:
    sys.exit(1)
finally:
    s.close()
' "${HOST}" "${PORT}" 2>/dev/null; then
            echo "❌ Ошибка: Адрес ${HOST}:${PORT} уже занят другим процессом." >&2
            return 1
        fi
    fi

    echo "🚀 Запуск приложения..."
    cd "${APP_DIR}"

    # Очистка устаревшего PID-файла, если он остался от мертвого процесса
    local stale_pid
    stale_pid="$(get_pid)"
    if [[ -n "${stale_pid}" ]] && ! is_owned_process "${stale_pid}"; then
        rm -f "${PID_FILE}"
    fi

    # Запуск Gunicorn в фоновом режиме
    nohup "${GUNICORN}" \
        --bind "${HOST}:${PORT}" \
        --pid "${PID_FILE}" \
        --workers 2 \
        --access-logfile - \
        --error-logfile - \
        wsgi:app >> "${LOG_FILE}" 2>&1 &
    local started_pid=$!

    local probe_host="${HOST}"
    if [[ "${probe_host}" == "0.0.0.0" ]]; then
        probe_host="127.0.0.1"
    elif [[ "${probe_host}" == "::" ]]; then
        probe_host="[::1]"
    elif [[ "${probe_host}" == *:* && "${probe_host}" != \[*\] ]]; then
        probe_host="[${probe_host}]"
    fi
    local health_url="http://${probe_host}:${PORT}/"

    local ready=0
    local waited=0
    while [[ ${waited} -lt 20 ]]; do
        sleep 0.5
        waited=$((waited + 1))

        if ! is_owned_process "${started_pid}"; then
            echo "❌ Ошибка: процесс Gunicorn завершился сбоем при запуске." >&2
            break
        fi

        local current_pid
        current_pid="$(get_pid)"
        if [[ "${current_pid}" != "${started_pid}" ]]; then
            continue
        fi

        local http_code
        http_code="$(curl -s -o /dev/null -w "%{http_code}" -m 1 "${health_url}" 2>/dev/null || echo "000")"
        if [[ "${http_code}" == "200" ]]; then
            if is_owned_process "${started_pid}"; then
                ready=1
                break
            fi
        fi
    done

    if [[ ${ready} -eq 1 ]]; then
        echo "✅ Приложение успешно запущено!"
        echo "   PID: ${started_pid}"
        echo "   Студенческий портал: http://${HOST}:${PORT}/"
        echo "   Панель преподавателя: http://${HOST}:${PORT}/admin"
        echo "   Логи: ${LOG_FILE}"
        return 0
    else
        echo "❌ Ошибка: Не удалось запустить приложение (таймаут готовности или сбой процесса)." >&2
        echo "   Проверьте логи: ${LOG_FILE}" >&2
        if is_owned_process "${started_pid}"; then
            kill -TERM "${started_pid}" 2>/dev/null || true
            local cleanup_waited=0
            while is_owned_process "${started_pid}" && [[ ${cleanup_waited} -lt 6 ]]; do
                sleep 0.5
                cleanup_waited=$((cleanup_waited + 1))
            done
            if is_owned_process "${started_pid}"; then
                kill -KILL "${started_pid}" 2>/dev/null || true
            fi
        fi
        if [[ "$(get_pid)" == "${started_pid}" ]]; then
            rm -f "${PID_FILE}"
        fi
        return 1
    fi
}

stop_app() {
    local pid
    pid="$(get_pid)"

    if [[ -z "${pid}" ]] || ! is_owned_process "${pid}"; then
        if [[ -f "${PID_FILE}" ]]; then
            rm -f "${PID_FILE}"
        fi
        echo "ℹ️  Отслеживаемое приложение не запущено."
        return 0
    fi

    echo "🛑 Остановка приложения (PID: ${pid})..."
    kill -TERM "${pid}" 2>/dev/null || true

    local waited=0
    while is_owned_process "${pid}" && [[ ${waited} -lt 20 ]]; do
        sleep 0.5
        waited=$((waited + 1))
    done

    if is_owned_process "${pid}"; then
        echo "⚠️  Принудительное завершение (SIGKILL)..."
        kill -KILL "${pid}" 2>/dev/null || true
        local kill_waited=0
        while is_owned_process "${pid}" && [[ ${kill_waited} -lt 10 ]]; do
            sleep 0.5
            kill_waited=$((kill_waited + 1))
        done
    fi

    if is_owned_process "${pid}"; then
        echo "❌ Ошибка: Не удалось завершить процесс ${pid}." >&2
        return 1
    fi

    rm -f "${PID_FILE}"
    echo "✅ Приложение остановлено."
    return 0
}

status_app() {
    if is_running; then
        local pid
        pid="$(get_pid)"
        echo "🟢 Статус: РАБОТАЕТ"
        echo "   PID: ${pid}"
        echo "   Порт: ${PORT}"
        echo "   Студенческий портал: http://${HOST}:${PORT}/"
        echo "   Панель преподавателя: http://${HOST}:${PORT}/admin"

        local http_code
        http_code="$(curl -s -o /dev/null -w "%{http_code}" -m 2 "http://${HOST}:${PORT}/" 2>/dev/null || echo "000")"
        if [[ "${http_code}" == "200" ]]; then
            echo "   HTTP Health: 200 OK (доступен)"
        else
            echo "   HTTP Health: статус ${http_code}"
        fi
    else
        echo "🔴 Статус: ОСТАНОВЛЕН"
    fi
}

show_logs() {
    if [[ ! -f "${LOG_FILE}" ]]; then
        echo "ℹ️  Файл логов пока пуст: ${LOG_FILE}"
        return 0
    fi
    echo "📜 Последние строки лога (${LOG_FILE}):"
    tail -n 30 "${LOG_FILE}"
    echo "--- (для отслеживания в реальном времени: tail -f ${LOG_FILE}) ---"
}

case "${1:-}" in
    start)
        start_app
        ;;
    stop)
        stop_app
        ;;
    restart)
        stop_app || exit 1
        sleep 1
        start_app
        ;;
    status)
        status_app
        ;;
    logs)
        show_logs
        ;;
    *)
        echo "Использование: $0 {start|stop|restart|status|logs}"
        echo ""
        echo "Команды:"
        echo "  start    - запустить приложение в фоновом режиме"
        echo "  stop     - остановить приложение"
        echo "  restart  - перезапустить приложение"
        echo "  status   - проверить статус работы"
        echo "  logs     - показать последние записи логов"
        exit 1
        ;;
esac
