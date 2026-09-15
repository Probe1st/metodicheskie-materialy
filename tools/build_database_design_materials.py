"""Generate the 2025 СИЭУиП package for ОП.08 «Основы проектирования баз данных»."""

from __future__ import annotations

import html
import shutil
from dataclasses import dataclass
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.pagesizes import landscape, A4


OUTPUT_ROOT = Path("АНПОО_СИЭУиП_25_ОП.08_Основы_проектирования_баз_данных")
INSTITUTION = "АНПОО «Сургутский институт экономики, управления и права» (СИЭУиП)"
COURSE = "ОП.08 «Основы проектирования баз данных»"
YEAR = "Набор 2025 · 2025–2026 учебный год"
FONT_PATH = Path("/usr/share/fonts/TTF/DejaVuSans.ttf")


@dataclass(frozen=True, slots=True)
class Lesson:
    number: int
    slug: str
    title: str
    kind: str
    outcome: str
    focus: str
    sql: str
    practice: str


LESSONS = (
    Lesson(1, "osnovnye-ponyatiya-i-predmetnaya-oblast", "Основные понятия баз данных и предметная область", "theory", "Объяснить назначение базы данных и выделить сущности предметной области.", "данные, СУБД, предметная область, сущность, атрибут и экземпляр сущности", "CREATE TABLE readers (reader_id INTEGER PRIMARY KEY, full_name TEXT NOT NULL);", "Опишите предметную область библиотеки: читатель, книга, экземпляр, выдача."),
    Lesson(2, "praktika-klyuchi-i-osnovnye-obekty", "Практическая работа № 1. Ключи и основные объекты БД", "practice", "Создать таблицы с первичными ключами и проверить уникальность идентификаторов.", "первичный ключ, кандидатный ключ, внешний ключ, таблица и запись", "CREATE TABLE books (book_id INTEGER PRIMARY KEY, isbn TEXT UNIQUE NOT NULL, title TEXT NOT NULL);", "Создайте таблицы readers и books; добавьте по три записи и объясните выбор ключей."),
    Lesson(3, "praktika-sozdanie-i-modifikatsiya-tablits", "Практическая работа № 1. Создание и модификация таблиц", "practice", "Создать базу данных и изменить структуру таблицы без потери смысла данных.", "DDL, CREATE TABLE, ALTER TABLE, тип данных и ограничение", "ALTER TABLE books ADD COLUMN publication_year INTEGER CHECK (publication_year BETWEEN 1450 AND 2100);", "Добавьте год издания и ограничение допустимого диапазона."),
    Lesson(4, "modeli-dannyh-i-relyatsionnyy-podhod", "Модели данных и реляционный подход", "theory", "Сопоставить модели данных и обосновать выбор реляционной модели.", "иерархическая, сетевая, документная и реляционная модели; отношение, кортеж и домен", "CREATE TABLE loans (loan_id INTEGER PRIMARY KEY, reader_id INTEGER NOT NULL REFERENCES readers(reader_id), book_id INTEGER NOT NULL REFERENCES books(book_id));", "Для библиотеки выберите модель данных и объясните, почему связи удобнее хранить внешними ключами."),
    Lesson(5, "praktika-normalizatsiya", "Практическая работа № 2. Нормализация реляционной БД", "practice", "Нормализовать исходную таблицу до третьей нормальной формы.", "функциональная зависимость, 1НФ, 2НФ, 3НФ и аномалия обновления", "CREATE TABLE authors (author_id INTEGER PRIMARY KEY, full_name TEXT NOT NULL);\nCREATE TABLE book_authors (book_id INTEGER REFERENCES books(book_id), author_id INTEGER REFERENCES authors(author_id), PRIMARY KEY (book_id, author_id));", "Разделите таблицу «Книга–автор–телефон автора» на отношения без повторяющихся групп."),
    Lesson(6, "praktika-sushchnosti-i-svyazi", "Практическая работа № 3. Преобразование отношений в сущности и связи", "practice", "Построить ER-модель по описанию предметной области.", "ER-модель, сущность, атрибут, кардинальность и обязательность связи", "-- Reader 1:N Loan; Book 1:N Loan\nCREATE TABLE loans (loan_id INTEGER PRIMARY KEY, reader_id INTEGER NOT NULL REFERENCES readers(reader_id), book_id INTEGER NOT NULL REFERENCES books(book_id));", "Нарисуйте ER-модель библиотеки и укажите кардинальности всех связей."),
    Lesson(7, "praktika-proektirovanie-i-normalizatsiya", "Практическая работа № 4. Проектирование реляционной БД", "practice", "Спроектировать нормализованную схему и аргументировать устранение аномалий.", "концептуальная, логическая и физическая модель; нормализация и целостность", "CREATE TABLE copies (copy_id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL REFERENCES books(book_id), inventory_no TEXT UNIQUE NOT NULL, status TEXT NOT NULL CHECK (status IN ('available', 'loaned', 'repair')));", "Подготовьте логическую схему из пяти таблиц: readers, books, authors, copies, loans."),
    Lesson(8, "etapy-proektirovaniya-bd", "Этапы проектирования баз данных", "theory", "Выполнить последовательность от требований до физической схемы базы данных.", "сбор требований, концептуальное, логическое и физическое проектирование; целостность данных", "CREATE TABLE loans (loan_id INTEGER PRIMARY KEY, reader_id INTEGER NOT NULL REFERENCES readers(reader_id), copy_id INTEGER NOT NULL REFERENCES copies(copy_id), issued_on DATE NOT NULL, due_on DATE NOT NULL, CHECK (due_on >= issued_on));", "Составьте маршрут проектирования БД библиотеки и определите результат каждого этапа."),
    Lesson(9, "praktika-zapisi-i-usloviya", "Практическая работа № 5. Записи и логические условия", "practice", "Добавлять, изменять и удалять записи по явным условиям.", "INSERT, UPDATE, DELETE, WHERE и безопасное изменение данных", "UPDATE copies SET status = 'loaned' WHERE copy_id = 17 AND status = 'available';", "Добавьте выдачу книги; измените статус только доступного экземпляра и объясните условие WHERE."),
    Lesson(10, "praktika-tablitsy-klyuchi-indeksy-i-svyazi", "Практическая работа № 6. Ключевые поля, индексы и связи", "practice", "Настроить ключи, индексы и ссылочную целостность таблиц.", "внешний ключ, индекс, ссылочная целостность, RESTRICT и CASCADE", "CREATE INDEX idx_loans_reader_id ON loans(reader_id);", "Создайте индекс для истории выдач читателя и проверьте, что нельзя выдать несуществующий экземпляр."),
    Lesson(11, "praktika-sortirovka-i-filtratsiya", "Практическая работа № 7. Сортировка и фильтрация данных", "practice", "Получить выборку данных с фильтрацией и упорядочиванием.", "SELECT, WHERE, ORDER BY, ASC, DESC, NULL и предикат", "SELECT title, publication_year FROM books WHERE publication_year >= 2020 ORDER BY publication_year DESC, title ASC;", "Выведите книги новее 2020 года в обратном хронологическом порядке."),
    Lesson(12, "praktika-poisk-po-polyam", "Практическая работа № 8. Поиск по одному и нескольким полям", "practice", "Сформировать поиск по нескольким полям без неявных условий.", "LIKE, AND, OR, параметр запроса и поиск по составному условию", "SELECT book_id, title FROM books WHERE title LIKE :query OR isbn = :isbn;", "Подготовьте два параметризованных запроса: по названию и по названию вместе с годом издания."),
    Lesson(13, "struktury-bd-i-polzovatelskiy-interfeys", "Проектирование структур БД и пользовательского интерфейса", "theory", "Спроектировать структуру БД и форму ввода, учитывая ограничения полей.", "структура БД, форма, справочник, обязательное поле, маска ввода и проверка значения", "CREATE TABLE categories (category_id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);\nALTER TABLE books ADD COLUMN category_id INTEGER REFERENCES categories(category_id);", "Спроектируйте форму «Выдача книги»: определите поля, источники списков и проверки до сохранения."),
    Lesson(14, "praktika-proekt-i-vhodnaya-forma", "Практическая работа № 9. Проект БД и входная форма", "practice", "Создать проект БД и спецификацию формы ввода выдачи.", "главная форма, навигация, обязательные поля, список выбора и сообщение об ошибке", "INSERT INTO loans (loan_id, reader_id, copy_id, issued_on, due_on) VALUES (101, :reader_id, :copy_id, CURRENT_DATE, :due_on);", "Опишите форму «Выдача»: поля, источники списков, действие «Сохранить» и три проверки."),
    Lesson(15, "praktika-ispolnenie-proekta-i-upravlenie", "Практическая работа № 10. Исполнение проекта и управление БД", "practice", "Проверить пользовательский сценарий и зафиксировать результат выполнения проекта.", "сценарий, тестовые данные, журнал изменений, резервная копия и права доступа", "SELECT l.loan_id, r.full_name, b.title, l.due_on FROM loans AS l JOIN readers AS r ON r.reader_id = l.reader_id JOIN copies AS c ON c.copy_id = l.copy_id JOIN books AS b ON b.book_id = c.book_id;", "Пройдите сценарий «выдать — найти — вернуть экземпляр» и зафиксируйте ожидаемые результаты."),
    Lesson(16, "sql-osnovnye-ponyatiya-i-tipy", "Организация запросов SQL: понятия, синтаксис и типы данных", "theory", "Выбрать типы данных и составить корректный SQL-запрос.", "SQL, DDL, DML, типы данных, NULL, выражение и параметр запроса", "CREATE TABLE reservations (reservation_id INTEGER PRIMARY KEY, reader_id INTEGER NOT NULL REFERENCES readers(reader_id), reserved_at TIMESTAMP NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE);", "Объясните разницу между NULL, пустой строкой и нулём на примере полей базы библиотеки."),
    Lesson(17, "praktika-formy-i-vneshniy-vid", "Практическая работа № 11. Формы и внешний вид", "practice", "Специфицировать форму с понятной последовательностью ввода и навигацией.", "метка поля, порядок табуляции, группа элементов, действие и обратная связь", "SELECT reader_id, full_name FROM readers ORDER BY full_name;", "Создайте макет формы «Читатель»: поля, подписи, порядок ввода и сообщение после сохранения."),
    Lesson(18, "praktika-ogranicheniya-poley", "Практическая работа № 12. Значения и ограничения полей", "practice", "Задать ограничения, исключающие недопустимые значения до сохранения записи.", "NOT NULL, UNIQUE, CHECK, DEFAULT и ограничение домена", "ALTER TABLE readers ADD COLUMN email TEXT UNIQUE CHECK (email LIKE '%@%');", "Добавьте ограничения для email, номера экземпляра и статуса книги; приведите по одному отвергаемому значению."),
    Lesson(19, "praktika-proverka-vvoda-i-tipy", "Практическая работа № 13. Проверка ввода и отображение типов", "practice", "Проверить значение до записи и корректно отобразить дату и число.", "валидация, дата, числовой тип, формат отображения и сообщение об ошибке", "SELECT loan_id, issued_on, due_on, julianday(due_on) - julianday(issued_on) AS loan_days FROM loans;", "Проверьте, что дата возврата не раньше даты выдачи; подготовьте сообщение об ошибке и пример корректного отображения."),
    Lesson(20, "praktika-ddl", "Практическая работа № 14. Создание и модификация таблиц SQL", "practice", "Создать и изменить таблицу SQL с ограничениями целостности.", "CREATE TABLE, ALTER TABLE, DROP TABLE, схема и миграция", "CREATE TABLE publishers (publisher_id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);\nALTER TABLE books ADD COLUMN publisher_id INTEGER REFERENCES publishers(publisher_id);", "Создайте таблицу издательств и свяжите её с книгами; объясните, почему DROP TABLE не используют вместо изменения структуры."),
    Lesson(21, "praktika-dml-tranzaktsii-i-zashchita", "Практические работы № 15–16. Выборка, изменение, транзакции и защита", "practice", "Выполнить выборку и изменение в транзакции, сохранив целостность данных.", "JOIN, GROUP BY, транзакция, COMMIT, ROLLBACK, роль и наименьшие привилегии", "BEGIN;\nUPDATE copies SET status = 'available' WHERE copy_id = :copy_id AND status = 'loaned';\nDELETE FROM loans WHERE loan_id = :loan_id;\nCOMMIT;", "Сформируйте отчёт по просроченным выдачам; затем опишите транзакцию возврата и права роли библиотекаря."),
)


STYLE = """
body{max-width:1000px;margin:auto;padding:28px;font:16px/1.55 Arial,sans-serif;color:#18212b}header{border-top:8px solid #075985;padding:18px 0}h1{color:#0b3d64}h2{color:#0b5d74;margin-top:1.6em}.brand{font-weight:bold;color:#075985}.meta{color:#52606d}.callout{background:#eef7fb;border-left:5px solid #0b5d74;padding:14px}.task{background:#eef8ee;padding:14px}pre{background:#17212b;color:#f8fafc;padding:14px;overflow:auto}code{font-family:ui-monospace,monospace}table{border-collapse:collapse;width:100%}th,td{border:1px solid #9ab;padding:8px;text-align:left;vertical-align:top}@media print{body{max-width:none}}
"""
SOURCES = "<li><a href=\"https://www.postgresql.org/docs/current/sql.html\">PostgreSQL: SQL Commands</a></li><li><a href=\"https://sqlite.org/lang.html\">SQLite SQL Language</a></li><li><a href=\"https://www.iso.org/standard/63555.html\">ISO/IEC 9075 — SQL</a></li>"


def page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{html.escape(title)}</title><style>{STYLE}</style></head><body><header><p class=\"brand\">{INSTITUTION}</p><p class=\"meta\">{COURSE}<br>{YEAR}</p><h1>{html.escape(title)}</h1></header><main>{body}</main><footer><h2>Источники</h2><ul>{SOURCES}</ul></footer></body></html>"""


def code_block(sql: str) -> str:
    return f"<pre><code>{html.escape(sql)}</code></pre>"


def rubric() -> str:
    return "<h2>Критерии проверки</h2><table><tr><th>Критерий</th><th>Наблюдаемый результат</th></tr><tr><td>Корректность</td><td>Запрос или модель отражает условие и использует подходящие ограничения.</td></tr><tr><td>Целостность</td><td>Ключи, связи и проверки не допускают противоречивые данные.</td></tr><tr><td>Обоснование</td><td>Каждое решение связано с требованием предметной области.</td></tr></table>"


def methodical(lesson: Lesson) -> str:
    return page(lesson.title, f"<section class=\"callout\"><h2>Результат изучения</h2><p>{lesson.outcome}</p></section><section><h2>Ключевые понятия</h2><p>{lesson.focus}.</p><p>Работайте со сквозным кейсом «Учебная библиотека»: читатели берут экземпляры книг, библиотекарь фиксирует выдачу и возврат, а система сохраняет непротиворечивую историю.</p></section><section><h2>Правило проектирования</h2><p>Сначала зафиксируйте факт предметной области, затем выберите сущности и связи, после этого определите ключи, типы и ограничения. Не заменяйте ограничение БД только проверкой в форме.</p></section><section><h2>Пример SQL</h2>{code_block(lesson.sql)}<p>Прочитайте код: назовите объект, ключи, ограничение и правило предметной области, которое они выражают.</p></section><section class=\"task\"><h2>Мини-задание</h2><ol><li>{lesson.practice}</li><li>Назовите одно недопустимое состояние данных.</li><li>Запишите ограничение или запрос, который его предотвратит либо обнаружит.</li></ol></section>{rubric()}")


def practical(lesson: Lesson) -> str:
    return page(lesson.title, f"<section class=\"callout\"><h2>Результат работы</h2><p>{lesson.outcome}</p></section><section><h2>Исходные данные</h2><p>Сквозной кейс — учебная библиотека. Используйте сущности readers, books, copies и loans; при необходимости добавляйте только обоснованные сущности.</p></section><section class=\"task\"><h2>Практическая работа</h2><ol><li>{lesson.practice}</li><li>Выполните или адаптируйте SQL-код для своей схемы.</li><li>Проверьте один корректный и один некорректный случай.</li><li>Сохраните схему, запросы и краткий вывод с результатами проверки.</li></ol>{code_block(lesson.sql)}</section><section><h2>Проверка себя</h2><p>Какой факт предметной области защищает это решение? Что произойдёт при нарушении ограничения или условия?</p></section>{rubric()}")


def assignment(lesson: Lesson) -> str:
    return page(f"Практические задания — {lesson.title}", f"<section class=\"callout\"><h2>Задание</h2><p>Подготовьте проверяемое решение по теме «{lesson.title}».</p></section><section class=\"task\"><ol><li>Сформулируйте два правила предметной области библиотеки.</li><li>Покажите, где каждое правило выражено: в ключе, ограничении, связи или запросе.</li><li>Выполните запрос и приложите ожидаемый результат в виде таблицы из трёх строк.</li><li>Опишите одно ограничение решения и способ его проверить.</li></ol>{code_block(lesson.sql)}</section>{rubric()}")


def homework(lesson: Lesson) -> str:
    return page(f"Домашнее задание — {lesson.title}", f"<section class=\"callout\"><h2>Цель</h2><p>Закрепить {lesson.focus} на новом сценарии.</p></section><section class=\"task\"><ol><li>Замените библиотеку на прокат спортивного инвентаря.</li><li>Выделите минимум три сущности и назначьте каждой первичный ключ.</li><li>Сформулируйте один запрос или ограничение по образцу.</li><li>Приведите один пример данных, который должен быть отклонён.</li></ol>{code_block(lesson.sql)}</section><p><strong>Сдача:</strong> один документ с моделью, SQL и обоснованием.</p>{rubric()}")


def render_presentation(lesson: Lesson, path: Path) -> None:
    if "DejaVuSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVuSans", str(FONT_PATH)))
    canvas = Canvas(str(path), pagesize=landscape(A4))
    width, height = landscape(A4)
    slides = (
        (lesson.title, lesson.outcome),
        ("Контекст", "Сквозной кейс: учебная библиотека и непротиворечивые данные."),
        ("Ключевые понятия", lesson.focus),
        ("Правило", "Сначала требование предметной области, затем модель, ключи и ограничения."),
        ("Пример SQL", lesson.sql.replace("\n", " ")),
        ("Типичная ошибка", "Хранить повторяющиеся факты в одной таблице или доверять только проверке формы."),
        ("Мини-задание", lesson.practice),
        ("Самопроверка", "Какое ограничение защищает данные? Как проверить корректный и некорректный случай?"),
    )
    for heading, text in slides:
        canvas.setFillColorRGB(0.04, 0.24, 0.39)
        canvas.setFont("DejaVuSans", 25)
        canvas.drawString(48, height - 62, heading)
        canvas.setFillColorRGB(0.1, 0.13, 0.17)
        canvas.setFont("DejaVuSans", 14)
        text_object = canvas.beginText(48, height - 110)
        text_object.setFont("DejaVuSans", 14)
        for paragraph in text.split("\n"):
            for line_start in range(0, len(paragraph) or 1, 88):
                text_object.textLine(paragraph[line_start:line_start + 88])
        canvas.drawText(text_object)
        canvas.setFont("DejaVuSans", 10)
        canvas.drawRightString(width - 48, 32, f"{COURSE} · {YEAR}")
        canvas.showPage()
    canvas.save()


def generate(output_root: Path = OUTPUT_ROOT) -> None:
    if output_root.exists():
        shutil.rmtree(output_root)
    for lesson in LESSONS:
        folder = output_root / f"{lesson.number:02d}-{lesson.slug}"
        folder.mkdir(parents=True)
        (folder / "prakticheskaya-rabota.html").write_text(practical(lesson), encoding="utf-8")
        (folder / "prakticheskie-zadaniya.html").write_text(assignment(lesson), encoding="utf-8")
        (folder / "domashnee-zadanie.html").write_text(homework(lesson), encoding="utf-8")
        if lesson.kind == "theory":
            (folder / "metodicheskij-material.html").write_text(methodical(lesson), encoding="utf-8")
            render_presentation(lesson, folder / "prezentaciya.pdf")


if __name__ == "__main__":
    generate()
