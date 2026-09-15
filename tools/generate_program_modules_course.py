"""Generate the Academy TOP 2025 package for MДК.01.01."""

from __future__ import annotations

import argparse
import re
import shutil
from html import escape
from pathlib import Path

try:  # Support both ``python tools/...`` and package imports.
    from tools.program_modules_manifest import (
        COURSE_TITLE,
        INSTITUTION_LABEL,
        PROJECT_TITLE,
        ProgramSession,
        SESSIONS,
    )
    from tools.render_html import render_page, write_html
    from tools.render_pdf import PresentationContent, render_presentation
except ModuleNotFoundError:  # pragma: no cover - exercised through the CLI.
    from program_modules_manifest import (
        COURSE_TITLE,
        INSTITUTION_LABEL,
        PROJECT_TITLE,
        ProgramSession,
        SESSIONS,
    )
    from render_html import render_page, write_html
    from render_pdf import PresentationContent, render_presentation


DEFAULT_OUTPUT_ROOT = Path("ММКЦТ_Академия_ТОП_ПМ.01_Разработка_программных_модулей_набор_2025")
SOURCES = [
    ("Документация Python", "https://docs.python.org/3/"),
    ("PEP 8 — Style Guide for Python Code", "https://peps.python.org/pep-0008/"),
    ("Tkinter — Python interface to Tcl/Tk", "https://docs.python.org/3/library/tkinter.html"),
    ("SQLite Documentation", "https://www.sqlite.org/docs.html"),
    ("PostgreSQL Documentation", "https://www.postgresql.org/docs/"),
]


def _paragraph(text: str) -> str:
    return f"    <p>{escape(text)}</p>"


def _items(items: tuple[str, ...] | list[str], tag: str = "ol") -> str:
    return "    <{tag}>\n{items}\n    </{tag}>".format(
        tag=tag,
        items="\n".join(f"      <li>{escape(item)}</li>" for item in items),
    )


def _code(title: str, code: str) -> str:
    return f"    <h3>{escape(title)}</h3>\n    <pre><code>{escape(code)}</code></pre>"


def _folder_name(session: ProgramSession) -> str:
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", session.title)
    slug = "-".join(word.replace("ё", "е").replace("Ё", "Е") for word in words)
    return f"{session.number:03d}-{slug}"


def _page_title(session: ProgramSession, document: str) -> str:
    return f"Занятие {session.number:03d}. {session.title} — {document}"


def _common_intro(session: ProgramSession) -> str:
    return "\n".join(
        (
            "    <h2>Ваш результат</h2>",
            _paragraph(f"Вы сможете: {session.outcome}."),
            f"    <p class=\"callout\">Сквозной кейс: {PROJECT_TITLE}. "
            f"Раздел: {escape(session.block)}. Ключевые слова: {escape(', '.join(session.keywords))}.</p>",
        )
    )


def _example_for(session: ProgramSession) -> tuple[str, str]:
    if session.block == "Основы доступа к данным":
        return (
            "booking_repository.py",
            "import sqlite3\n\n\ndef reservations_for(resource_id: int) -> list[tuple[int, str]]:\n"
            "    with sqlite3.connect('booking.db') as connection:\n"
            "        return connection.execute(\n"
            "            'SELECT id, status FROM reservation WHERE resource_id = ?',\n"
            "            (resource_id,),\n"
            "        ).fetchall()",
        )
    if session.block in {"Событийно-управляемое программирование", "Разработка пользовательского интерфейса"}:
        return (
            "booking_view.py",
            "import tkinter as tk\n\n\ndef show_status(label: tk.Label, status: str) -> None:\n"
            "    label.configure(text=f'Статус бронирования: {status}')\n\n\nroot = tk.Tk()\n"
            "status_label = tk.Label(root, text='Статус бронирования: черновик')\n"
            "status_label.pack()",
        )
    if session.block == "Оптимизация и рефакторинг кода":
        return (
            "booking_metrics.py",
            "from time import perf_counter\n\n\ndef measure(action):\n"
            "    started = perf_counter()\n"
            "    result = action()\n"
            "    return result, perf_counter() - started",
        )
    if session.block in {"Объектно-ориентированное программирование", "Паттерны проектирования"}:
        return (
            "booking_models.py",
            "from dataclasses import dataclass\n\n\n@dataclass(frozen=True, slots=True)\n"
            "class Reservation:\n"
            "    resource_id: int\n"
            "    student_id: int\n"
            "    status: str = 'черновик'",
        )
    return (
        "booking_rules.py",
        "def available_slots(capacity: int, confirmed: int) -> int:\n"
        "    if capacity < 0 or confirmed < 0:\n"
        "        raise ValueError('Количество не может быть отрицательным')\n"
        "    return max(capacity - confirmed, 0)",
    )


def _steps(session: ProgramSession) -> tuple[str, str, str, str]:
    primary, secondary, *_ = session.keywords
    return (
        f"Сформулируйте правило темы «{session.title}» для сервиса бронирования и назовите входные данные.",
        f"Примените понятия «{primary}» и «{secondary}» на отдельном небольшом модуле.",
        f"Зафиксируйте наблюдаемый результат этапа «{session.phase}» в коде, схеме или SQL-скрипте.",
        "Проверьте результат обратным условием, тестом или воспроизводимым пользовательским сценарием.",
    )


def _theory_material(session: ProgramSession) -> str:
    filename, code = _example_for(session)
    return "\n".join(
        (
            _common_intro(session),
            "    <h2>Методический разбор</h2>",
            _paragraph(
                f"Тема «{session.title}» рассматривается через конкретную границу модуля. "
                f"Сначала определите правило и входы, затем выберите реализацию и только после этого делайте вывод по наблюдению."
            ),
            "    <h3>Понятия для работы</h3>",
            _items([f"{word}: объясните роль термина в текущем модуле." for word in session.keywords], "ul"),
            "    <h2>Выполнимый фрагмент Python</h2>",
            _paragraph("Сохраните фрагмент отдельно. До изменения назовите предусловие, ожидаемый результат и способ проверки."),
            _code(filename, code),
            "    <h2>Практический маршрут</h2>",
            _items(_steps(session)),
            "    <h2>Рефлексия</h2>",
            _paragraph("Запишите факт, который подтвердили, и условие, при котором вывод перестанет быть верным."),
        )
    )


def _practice_work(session: ProgramSession) -> str:
    filename, code = _example_for(session)
    return "\n".join(
        (
            _common_intro(session),
            "    <h2>Подготовка</h2>",
            _paragraph("Создайте отдельную ветку или папку занятия. Не меняйте работающий модуль, пока не описали ожидаемый результат."),
            _code("Команды проверки", "python -m venv .venv\nsource .venv/bin/activate\npython -m pip install pytest\npython -m pytest -q"),
            "    <h2>Стартовый фрагмент</h2>",
            _code(filename, code),
            "    <h2>Обязательная работа</h2>",
            _items(_steps(session)),
            "    <h2>Что сдать</h2>",
            _items(
                (
                    "исходный код, схема или SQL-скрипт с понятным названием",
                    "команду либо сценарий воспроизведения и его фактический результат",
                    "краткое объяснение связи результата с правилом темы",
                ),
                "ul",
            ),
            "    <h2>Критерии проверки</h2>",
            _items(
                (
                    "артефакт отражает все обязательные пункты задания",
                    "вывод основан на наблюдаемом результате, а не на предположении",
                    "термины и ограничения темы применены последовательно",
                ),
                "ul",
            ),
        )
    )


def _tasks(session: ProgramSession) -> str:
    return "\n".join(
        (
            _common_intro(session),
            "    <h2>Практические задания</h2>",
            _items(_steps(session)),
            "    <h2>Самопроверка</h2>",
            _items(
                (
                    "для каждого действия указан наблюдаемый результат",
                    "входные данные и ограничение темы зафиксированы явно",
                    "другой студент сможет воспроизвести проверку по приложенным файлам",
                ),
                "ul",
            ),
        )
    )


def _homework(session: ProgramSession) -> str:
    primary = session.keywords[0]
    return "\n".join(
        (
            _common_intro(session),
            "    <h2>Самостоятельное продолжение</h2>",
            _paragraph(
                f"Измените условие в кейсе «{PROJECT_TITLE}»: добавьте правило, связанное с понятием «{primary}». "
                "Не повторяйте входные данные аудиторной работы."
            ),
            "    <h2>Формат результата</h2>",
            _items(
                (
                    "файлы Python, схема или SQL-скрипт, необходимые для воспроизведения",
                    "краткая заметка: входные данные, действие, наблюдение и вывод",
                    "одно ограничение решения и условие, при котором потребуется другой подход",
                ),
                "ul",
            ),
            "    <h2>Критерии самопроверки</h2>",
            _items(
                (
                    "задание продолжает тему и добавляет новое правило",
                    "результат можно проверить по приложенным материалам",
                    "вывод не выходит за границы выполненного сценария",
                ),
                "ul",
            ),
        )
    )


def _presentation_content(session: ProgramSession) -> PresentationContent:
    first, second, third, fourth = session.keywords
    return PresentationContent(
        f"Тема «{session.title}» даёт ценность только тогда, когда решение связано с правилом сервиса и проверяемым результатом.",
        ("Условие задачи", session.phase, "Артефакт модуля", "Проверяемый результат"),
        (
            (first, f"Как это понятие ограничивает решение?"),
            (second, f"Какой факт подтвердит применение «{second}»?"),
            (third, f"Как «{third}» связан с пользовательским сценарием?"),
        ),
        f"В сервисе бронирования примените «{first}» при обработке запроса на ресурс.",
        (
            "Зафиксируйте входные данные и правило до изменения кода.",
            "Создайте минимальный артефакт: функцию, схему, форму или SQL-команду.",
            "Сопоставьте фактический результат с записанным правилом.",
        ),
        (
            f"Могу ли я объяснить роль понятия «{first}»?",
            f"Какое ограничение выражает «{fourth}»?",
            "Как другой студент воспроизведёт результат без моих пояснений?",
        ),
    )


def _curriculum() -> str:
    rows = "\n".join(
        "      <tr>"
        f"<td>{session.number:03d}</td><td>{escape(session.block)}</td>"
        f"<td>{'Теория' if session.kind == 'theory' else 'Практика'}</td>"
        f"<td>{escape(session.title)}</td><td>{escape(session.outcome)}.</td>"
        "</tr>"
        for session in SESSIONS
    )
    return "\n".join(
        (
            "    <h2>Назначение курса</h2>",
            _paragraph(
                "Курс формирует компетенции ПК 1.1–ПК 1.5: разработка алгоритмов и модулей, отладка, тестирование, рефакторинг и оптимизация кода."
            ),
            "    <h2>Сквозной кейс</h2>",
            _paragraph(
                f"Все занятия развивают «{PROJECT_TITLE}»: от алгоритма и моделей до интерфейса, базы данных и проверяемого результата."
            ),
            "    <h2>Тематический план: 210 аудиторных часов</h2>",
            "    <table><thead><tr><th>№</th><th>Глава</th><th>Формат</th><th>Тема</th><th>Результат</th></tr></thead><tbody>",
            rows,
            "    </tbody></table>",
            "    <h2>Текущий контроль</h2>",
            _items(
                (
                    "модули Python с явными контрактами и воспроизводимой проверкой",
                    "схемы, результаты измерений и обоснование выбора алгоритма или паттерна",
                    "интерфейсный сценарий и данные SQLite/PostgreSQL для завершающих тем",
                ),
                "ul",
            ),
        )
    )


def generate(output_root: Path = DEFAULT_OUTPUT_ROOT) -> None:
    """Create a clean 105-session 2025 Academy TOP course package."""
    replacement_root = output_root.with_name(f".{output_root.name}.replacement")
    if replacement_root.exists():
        shutil.rmtree(replacement_root)
    replacement_root.mkdir(parents=True)

    write_html(
        replacement_root / "00_Рабочая_программа.html",
        render_page(f"{COURSE_TITLE} — рабочая программа", _curriculum(), SOURCES, eyebrow=INSTITUTION_LABEL),
    )

    for session in SESSIONS:
        folder = replacement_root / _folder_name(session)
        if session.kind == "theory":
            pages = (
                ("metodicheskij-material.html", _page_title(session, "методический материал"), _theory_material(session)),
                ("prakticheskie-zadaniya.html", _page_title(session, "практические задания"), _tasks(session)),
                ("domashnee-zadanie.html", _page_title(session, "самостоятельное задание"), _homework(session)),
            )
        else:
            pages = (
                ("prakticheskaya-rabota.html", _page_title(session, "практическая работа"), _practice_work(session)),
                ("prakticheskie-zadaniya.html", _page_title(session, "практические задания"), _tasks(session)),
                ("domashnee-zadanie.html", _page_title(session, "самостоятельное задание"), _homework(session)),
            )

        for filename, title, body in pages:
            write_html(folder / filename, render_page(title, body, SOURCES, eyebrow=INSTITUTION_LABEL))
        if session.kind == "theory":
            render_presentation(
                session,
                folder / "prezentaciya.pdf",
                sources=tuple(SOURCES),
                content=_presentation_content(session),
                course_title=COURSE_TITLE,
                institution_label=INSTITUTION_LABEL,
            )

    if output_root.exists():
        shutil.rmtree(output_root)
    replacement_root.replace(output_root)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Создать комплект МДК.01.01 для Академии ТОП, набор 2025")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = _parse_args()
    generate(arguments.output)
    print(f"Создан HTML-комплект: {arguments.output}")
