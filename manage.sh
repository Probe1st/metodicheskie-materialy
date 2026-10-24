#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Скрипт управления приложением homework-submissions
# Команды: start | stop | restart | status | logs
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -d "${SCRIPT_DIR}/homework-submissions" ]]; then
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

get_pid() {
    if [[ -f "${PID_FILE}" ]]; then
        cat "${PID_FILE}" 2>/dev/null || true
    fi
}

is_running() {
    local pid
    pid="$(get_pid)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
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

    echo "🚀 Запуск приложения..."
    cd "${APP_DIR}"

    # Запуск Gunicorn в фоновом режиме
    nohup "${GUNICORN}" \
        --bind "${HOST}:${PORT}" \
        --pid "${PID_FILE}" \
        --workers 2 \
        --access-logfile - \
        --error-logfile - \
        wsgi:app >> "${LOG_FILE}" 2>&1 &

    # Ожидание готовности сервера (до 10 секунд)
    local waited=0
    while ! is_running && [[ ${waited} -lt 10 ]]; do
        sleep 0.5
        waited=$((waited + 1))
    done

    # Дополнительная проверка ответа порта
    local check_success=0
    for _ in {1..10}; do
        if curl -s -o /dev/null -m 1 "http://${HOST}:${PORT}/" 2>/dev/null; then
            check_success=1
            break
        fi
        sleep 0.5
    done

    if [[ ${check_success} -eq 1 ]]; then
        local pid
        pid="$(get_pid)"
        echo "✅ Приложение успешно запущено!"
        echo "   PID: ${pid}"
        echo "   Студенческий портал: http://${HOST}:${PORT}/"
        echo "   Панель преподавателя: http://${HOST}:${PORT}/admin"
        echo "   Логи: ${LOG_FILE}"
    else
        echo "⚠️  Процесс запущен, но HTTP-проверка не ответила вовремя."
        echo "   Проверьте логи: ${LOG_FILE}"
    fi
}

stop_app() {
    local pid
    pid="$(get_pid)"

    if [[ -z "${pid}" ]] || ! kill -0 "${pid}" 2>/dev/null; then
        # Проверим, не слушает ли кто-то ещё этот порт
        local port_pids
        port_pids="$(ss -tlnp 2>/dev/null | grep ":${PORT} " | grep -o 'pid=[0-9]*' | cut -d= -f2 || true)"
        if [[ -n "${port_pids}" ]]; then
            echo "🛑 Остановка процессов на порту ${PORT} (PID: ${port_pids})..."
            for p in ${port_pids}; do
                kill -TERM "${p}" 2>/dev/null || true
            done
            rm -f "${PID_FILE}"
            echo "✅ Процессы на порту ${PORT} остановлены."
            return 0
        fi
        echo "ℹ️  Приложение не запущено."
        rm -f "${PID_FILE}"
        return 0
    fi

    echo "🛑 Остановка приложения (PID: ${pid})..."
    kill -TERM "${pid}" 2>/dev/null || true

    local waited=0
    while kill -0 "${pid}" 2>/dev/null && [[ ${waited} -lt 10 ]]; do
        sleep 0.5
        waited=$((waited + 1))
    done

    if kill -0 "${pid}" 2>/dev/null; then
        echo "⚠️  Принудительное завершение (SIGKILL)..."
        kill -KILL "${pid}" 2>/dev/null || true
    fi

    rm -f "${PID_FILE}"
    echo "✅ Приложение остановлено."
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
        stop_app
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
