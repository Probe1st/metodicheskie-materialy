# Независимый аудит перехода на SQLite v2: Отчет о безопасности, надежности и готовности к эксплуатации

**Дата составления:** 2026-11-03  
**Объект аудита:** Репозиторий `homework-submissions` (переход с гибридной архитектуры JSON-каталога и множественных SQLite-БД на единую SQLite v2).  
**Рабочий каталог репозитория:** `/home/probe/education/homework-submissions`  
**Родительский репозиторий:** `/home/probe/education`  
**Идентификатор аудитора:** `Independent SQLite v2 Audit Team`  

---

## 1. Executive Summary & Verdict (Резюме и Экспертный вердикт)

### 1.1 Официальный вердикт аудита: 🔴 BLOCKED (Запретить релизный переход в production)
На основе исчерпывающего эмпирического тестирования (379 автоматизированных сценариев, сквозные браузерные тесты, стресс-нагрузка на 20 000 сдач, инъекции аварий файловой системы и транзакционных сбоев) **переход `homework-submissions` на единую SQLite v2 категорически запрещен до устранения критических дефектов целостности данных**.

### 1.2 Ключевые основания для блокировки:
1. **Критическая потеря данных в каталоге (`MIG-02-TOPIC-UUID-DROPPED`):**
   Генератор детерминированных UUID (`tools/legacy_catalog.py`) игнорирует `subject_id` при вычислении хеша тем. Если две дисциплины имеют темы с совпадающими названиями (например, «Введение» в курсах *«Информатика»* и *«Программирование»*), обе темы получают идентичный первичный ключ. Вторая тема **безвозвратно удаляется** при разборе каталога (`if tid not in seen_topics`), а связанные с ней сдачи студентов становятся сиротами.
2. **Полный сбой стандартного пути миграции (`MIG-03-EMPTY-ENTITIES-FK-FAILURE`):**
   Команда `tools.migrate_v2 apply` требует обязательного дублирования всех групп, предметов и тем в `mapping.json['entities']` вместо импорта графа из снимка каталога. Стандартный файл сопоставления (`entities: {}`) приводит к немедленному аварийному падению мигратора с `sqlite3.IntegrityError: FOREIGN KEY constraint failed` при вставке студентов.
3. **Ошибочная маршрутизация файлов в хранилище (`MIG-05-RESOLVER-IGNORES-SUBJECT-HIERARCHY`):**
   Алгоритм сопоставления путей хранилища (`tools/migrate_v2.py:resolve_file_location`) строит плоский словарь тем без привязки к предмету. В структуре каталогов вида `Группа/Предмет/Тема/файл.zip` папка предмета игнорируется, что приводит к привязке сданных работ к чужому предмету.
4. **Тихая потеря статуса архивности сущностей (`MIG-03-ARCHIVED-ENTITIES-INSERTED-AS-ACTIVE`):**
   Мигратор жестко прописывает `NULL` в поле `archived_at` для групп, предметов и тем (`INSERT INTO groups ... VALUES (?, ?, ?, NULL)`), игнорируя архивные отметки в маппинге. Архивные группы прошлых лет публикуются в базу как активные.
5. **Отсутствие строгой валидации маппинга (`MIG-04-NO-STRICT-SCHEMA-VALIDATION`, `MIG-04-WHITESPACE-NON-SUBMISSION-REASON`):**
   Любые опечатки в ключах маппинга молча игнорируются, а строки из одних пробелов (`"   "`) принимаются как уважительная причина пропуска файлов.
6. **Масштабируемость и деградация БД (`SCALE-01-STUDENTS-TABLE-SCAN`, `SCALE-02-UNPAGINATED-HISTORY-MEMORY-GROWTH`):**
   В схеме `app/schema_v2.sql` отсутствует индекс по `students(group_id)`, что вызывает полное сканирование таблицы (`SCAN students`) при каждом входе студента. В веб-слое отсутствует пагинация: выборка 20 000 сдач аллоцирует ~41.4 МБ оперативной памяти на каждый HTTP-запрос.

---

## 2. Объем, методология и среда аудита (Scope & Environment)

### 2.1 Среда выполнения (Baseline)
- **Операционная система:** `Linux probe-frihxx 6.18.49-1-MANJARO #1 SMP PREEMPT_DYNAMIC x86_64` (glibc 2.44, GCC 16.1.1).
- **Python Runtime:** `Python 3.14.7` (64-bit).
- **SQLite3 Library:** `3.53.4` (как встроенный binding Python, так и системный `libsqlite3`).
- **Web & Test Frameworks:** `Flask 3.1.3`, `Werkzeug 3.1.9`, `pytest 9.2.0`, `Playwright 1.64.1` (Chromium engine 156.0.8078.4).
- **Изоляция:** Все испытания проводились исключительно на синтетических данных в изолированных файловых системах `tmp_path` (`tmpfs` объемом 7.5 GB в `/tmp`). Исходные файлы пользователя (`.private`, `.env`, реальные базы и хранилище) защищены от модификации.

### 2.2 Исследованные компоненты:
- Ядро БД и схема: `app/database.py`, `app/schema_v2.sql`.
- Доменные хранилища: `app/sqlite_catalog.py`, `app/catalog_store.py`, `app/access.py`, `app/assignments.py`, `app/submissions.py`, `app/storage.py`.
- Веб-слой и безопасность: `app/web.py` (все 24 Flask-маршрута, сессии, авторизация, загрузка/скачивание).
- Утилиты миграции и аудита: `tools/legacy_catalog.py`, `tools/migrate_v2.py`, `tools/reconcile_storage.py`.
- Эксплуатационный скрипт: `manage.sh` (start, stop, status, logs, migrate-v2).

---

## 3. Статистика выполнения тестов (Test Execution Summary)

| Набор тестов (Suite) | Файл тестового набора | Собрано тестов | Успешно | Провалено | Характер падений |
|---|---|---|---|---|---|
| **Database Contract** | `test_database_contract.py` | 33 | 33 | 0 | Схема v2, FK constraints, WAL mode, lock timeout |
| **Domain Lifecycle** | `test_domain_contract.py` | 41 | 40 | 1 | `DOM-01`: Конфликт имен с архивными группами |
| **Submission Integrity** | `test_submission_integrity.py` | 39 | 39 | 0 | Стриминг, preflight, gatekeeping, safe paths |
| **Concurrency & Races** | `test_concurrency.py` | 18 | 17 | 1 | `RACE-02`: Lost update версии в `AccessStore.configure` |
| **Crash & Fault Recovery** | `test_crash_recovery.py` | 24 | 24 | 0 | Аварии EIO/ENOSPC, atomic replace, reconcile |
| **Migration Contract** | `test_migration_contract.py` | 22 | 15 | 7 | `MIG-02`, `MIG-03`, `MIG-04`, `MIG-05`, `MIG-07` |
| **Backup & Restore** | `test_backup_restore.py` | 28 | 28 | 0 | SQLite backup API, manifest v1, verify, restore |
| **CLI Roundtrip** | `test_cli_roundtrip.py` | 3 | 3 | 0 | Сквозной пайплайн analyze -> apply -> verify -> restore |
| **Config & Lifecycle** | `test_config_lifecycle.py` | 10 | 10 | 0 | Двойной префикс `.private/.private`, foreign PID, signals |
| **Query Scaling & Load** | `test_query_scaling.py` | 6 | 6 | 0 | EXPLAIN QUERY PLAN, 20k rows, memory, table scans |
| **State Sequences (Fuzz)**| `test_state_sequences.py` | 100 | 100 | 0 | 200 псевдослучайных цепочек по 50 действий (seeds 0..199)|
| **Web Security & Routes** | `test_web_security.py` | 50 | 47 | 3 | `WEB-03` (400 vs 409), `WEB-04` (500 crash), `SEC-01` (SameSite)|
| **Browser E2E Flow** | `test_browser_flow.py` | 1 | 1 | 0 | Chromium Playwright: unlock, upload, admin review, download |
| **Support Smoke** | `test_support_smoke.py` | 4 | 4 | 0 | Валидация самого тестового harness |
| **ИТОГО:** | **14 тестовых сьютов** | **379** | **367** | **12** | **12 подтвержденных воспроизводимых дефектов** |

---

## 4. Реестр выявленных дефектов и замечаний (Detailed Findings Register)

### 4.1 Критический уровень (Critical Severity)

#### `MIG-02-TOPIC-UUID-DROPPED`: Потеря одноименных тем разных предметов при разборе каталога
- **Файл и строки:** `tools/legacy_catalog.py:51–56, 170–176, 230–238`
- **Воспроизводящий тест:** `tests/audit/test_migration_contract.py::test_mig_02_topic_uuid_must_incorporate_subject_id`
- **Суть дефекта:** Функция `deterministic_uuid("topic", normalize_name_key(tname))` вычисляет UUID5 исключительно от названия темы. При разборе каталога v1/v2, если два предмета имеют тему с одинаковым названием, вторая тема получает UUID, совпадающий с первой, и отбрасывается условием `if tid not in seen_topics:`.
- **Доказательство:** Каталог с 4 темами (по 2 темы в 2 предметах, где одна тема называется «Введение») после парсинга вернул только 3 темы. Тема второго предмета была потеряна.
- **Влияние на безопасность и данные:** Безвозвратная потеря тем каталога. Все исторические сдачи студентов по этой теме лишаются привязки.
- **Варианты устранения:**
  - *Вариант A (Рекомендуемый):* Включить `subject_id` в вычисление детерминированного UUID темы: `deterministic_uuid("topic", f"{subject_id}:{normalize_name_key(tname)}")`.
    - *Плюсы:* Исключает коллизии тем между предметами, обеспечивает уникальность.
    - *Минусы:* Изменяет генерируемые UUID для старых каталогов v1/v2.
  - *Вариант B:* Добавлять числовой суффикс к повторяющимся именам тем перед хешированием.
    - *Плюсы:* Сохраняет UUID для первой темы.
    - *Минусы:* Зависимость от порядка обхода словаря.
- **Диагностический SQL для существующих БД:**
  ```sql
  SELECT name_key, COUNT(*) FROM topics GROUP BY name_key HAVING COUNT(*) > 1;
  ```

---

#### `MIG-03-EMPTY-ENTITIES-FK-FAILURE`: Невозможность применения стандартного маппинга миграции
- **Файл и строки:** `tools/migrate_v2.py:550–580`
- **Воспроизводящий тест:** `tests/audit/test_migration_contract.py::test_mig_03_standard_mapping_empty_entities`
- **Суть дефекта:** Функция `apply_migration` ожидает, что группы, предметы и темы обязательно присутствуют в секции `mapping["entities"]`. Если передан стандартный файл маппинга с `entities: {}` (где сущности должны браться из снимка каталога), таблицы групп и предметов остаются пустыми, и при импорте студентов возникает аварийная ошибка `sqlite3.IntegrityError: FOREIGN KEY constraint failed`.
- **Доказательство:** Запуск `python -m tools.migrate_v2 apply` со стандартным маппингом падает с кодом 1, целевая база не публикуется.
- **Влияние:** Срыв процесса миграции при следовании документации.
- **Варианты устранения:**
  - *Вариант A (Рекомендуемый):* Импортировать граф сущностей из объекта `catalog` (снимка), а затем накладывать дополнения и архивные сущности из `mapping["entities"]`.
  - *Вариант B:* Обязать команду `cmd_analyze` всегда выгружать весь каталог в `mapping["entities"]`.

---

### 4.2 Высокий уровень (High Severity)

#### `MIG-05-RESOLVER-IGNORES-SUBJECT-HIERARCHY`: Ошибочное сопоставление путей хранилища с темами
- **Файл и строки:** `tools/migrate_v2.py:240–255`
- **Воспроизводящий тест:** `tests/audit/test_migration_contract.py::test_mig_05_ambiguous_matches_require_override`
- **Суть дефекта:** В трехуровневой иерархии `storage/Группа/Предмет/Тема/файл.zip` функция `resolve_file_location` берет название группы `dir_parts[0]` и темы `dir_parts[-1]`, полностью игнорируя промежуточную папку предмета `dir_parts[1]`. Поиск темы производится по глобальному словарю `clean_topics_by_name`. Если тема с таким названием есть в другом предмете и перезаписала ключ словаря, файл привязывается к чужому предмету.
- **Доказательство:** Файл из `ПИ-201/Информатика/Лабораторная работа 1/` привязался к теме из предмета *«Программирование»*.
- **Варианты устранения:**
  - *Вариант A (Рекомендуемый):* Использовать составной ключ `(clean_subject, clean_topic)` для трехуровневых путей.
  - *Вариант B:* Требовать обязательного явного маппинга для всех файлов при наличии одноименных тем.

---

#### `SCALE-01-STUDENTS-TABLE-SCAN`: Отсутствие индекса по группе в таблице студентов
- **Файл и строки:** `app/schema_v2.sql:26–30`
- **Воспроизводящий тест:** `tests/audit/test_query_scaling.py::test_explain_query_plan_indexes`
- **Суть дефекта:** Таблица `students` имеет только ограничение `UNIQUE(id, group_id)`. Поскольку ведущей колонкой индекса является `id`, запросы вида `SELECT * FROM students WHERE group_id = ? ORDER BY display_name` не могут использовать этот индекс и выполняют полное сканирование (`SCAN students`) с сортировкой во временном B-Tree (`USE TEMP B-TREE FOR ORDER BY`).
- **Доказательство:** Отчет `EXPLAIN QUERY PLAN` на базе с 1 000 студентов подтвердил полный скан таблицы при каждом открытии страницы группы.
- **Варианты устранения:**
  - *Вариант A (Рекомендуемый):* Добавить покрывающий индекс `CREATE INDEX idx_students_group ON students(group_id, archived_at, display_name);`.
  - *Вариант B:* Изменить порядок колонок в составном ограничении на `UNIQUE(group_id, id)`.

---

#### `SCALE-02-UNPAGINATED-HISTORY-MEMORY-GROWTH`: Линейный рост памяти из-за отсутствия пагинации истории
- **Файл и строки:** `app/submissions.py:470–520`, `app/web.py:715–735`
- **Воспроизводящий тест:** `tests/audit/test_query_scaling.py::test_query_scaling_1x_vs_10x_and_memory`
- **Суть дефекта:** Метод `list_submissions()` и маршрут `/admin/submissions` выбирают все записи без `LIMIT` и `OFFSET`. При 20 000 сдач один запрос аллоцирует 42.4 МБ оперативной памяти и занимает 1.2 секунды CPU. Несколько параллельных запросов администратора способны вызвать OOM-падение воркера Gunicorn.
- **Варианты устранения:**
  - *Вариант A (Рекомендуемый):* Внедрить постраничную навигацию (LIMIT/OFFSET или keyset pagination по `created_at`).
  - *Вариант B:* Ограничить выборку обязательным фильтром по группе и дате.

---

### 4.3 Средний уровень (Medium Severity)

#### `MIG-03-ARCHIVED-ENTITIES-INSERTED-AS-ACTIVE`: Сброс архивного статуса сущностей при импорте
- **Файл и строки:** `tools/migrate_v2.py:564, 574, 584`
- **Воспроизводящий тест:** `tests/audit/test_migration_contract.py::test_mig_03_additional_archived_entities_in_mapping`
- **Суть:** Значение `archived_at` жестко заменено на `NULL` в запросах вставки. Архивные сущности из маппинга становятся активными.

#### `MIG-04-NO-STRICT-SCHEMA-VALIDATION`: Отсутствие валидации неизвестных полей маппинга
- **Файл и строки:** `tools/migrate_v2.py:540–555`
- **Воспроизводящий тест:** `tests/audit/test_migration_contract.py::test_mig_04_unknown_keys_rejected`
- **Суть:** Мигратор не проверяет ключи `mapping.json` на соответствие схеме, пропуская опечатки без предупреждений.

#### `MIG-04-WHITESPACE-NON-SUBMISSION-REASON`: Принятие пробельных строк как причины пропуска файлов
- **Файл и строки:** `tools/migrate_v2.py:696–698`
- **Воспроизводящий тест:** `tests/audit/test_migration_contract.py::test_mig_04_non_submission_reason_validation`
- **Суть:** Условие `if finfo.get("non_submission_reason"): continue` принимает строку из одних пробелов (`"   "`), позволяя пропускать файлы без указания причины.

#### `MIG-07-CALENDAR-INVALID-DATES`: Публикация невалидных календарных дат (2026-02-30)
- **Файл и строки:** `app/schema_v2.sql:35`, `tools/migrate_v2.py:645–655`
- **Воспроизводящий тест:** `tests/audit/test_migration_contract.py::test_mig_07_invalid_assignment_schedules_rejected`
- **Суть:** Проверка `CHECK(lesson_date GLOB '????-??-??')` валидирует только маску, пропуская несуществующие даты (30 февраля). Мигратор не вызывает `validate_lesson_date()`.

#### `DOM-01-NAME-CONFLICT-ARCHIVED`: Невозможность повторного использования имен архивных групп
- **Файл и строки:** `app/schema_v2.sql:2–4`, `app/sqlite_catalog.py:100–120`
- **Воспроизводящий тест:** `tests/audit/test_domain_contract.py::test_dom_01_name_conflict_with_archived_item`
- **Суть:** Ограничение `name_key TEXT NOT NULL UNIQUE` действует на всю таблицу, включая архивные записи. Создание новой группы с именем архивной блокируется ошибкой `CatalogConflictError`.
- **Рекомендация:** Заменить ограничение на частичный индекс `CREATE UNIQUE INDEX idx_groups_active ON groups(name_key) WHERE archived_at IS NULL;`.

#### `RACE-02-ACCESS-VERSION-LOST-UPDATE`: Гонка версий доступа при параллельных настройках
- **Файл и строки:** `app/access.py:115–135`
- **Воспроизводящий тест:** `tests/audit/test_concurrency.py::test_race_02_03_concurrent_access_configuration_version[RACE-02-03]`
- **Суть:** Версия инкрементируется через Python-код после SELECT, а не атомарно в SQL `UPDATE ... SET version = version + 1`, что приводит к потере инкремента при параллельных запросах администраторов.

#### `WEB-04-TAMPERED-FILTER-HTTP-500`: Падение сервера с кодом 500 при невалидном UUID фильтра
- **Файл и строки:** `app/web.py:700–715`
- **Воспроизводящий тест:** `tests/audit/test_web_security.py::test_web_04_01_admin_submissions_tampered_filter_contract[WEB-04-01]`
- **Суть:** Конструкция `UUID(group_filter)` без перехвата `ValueError` роняет обработчик с 500 ошибкой при передаче битого UUID в URL.

#### `CFG-01-DOUBLE-PRIVATE-PREFIX`: Двойной префикс каталога базы при дефолтной конфигурации
- **Файл и строки:** `app/web.py:110–120`, `app/catalog_store.py:165–170`
- **Воспроизводящий тест:** `tests/audit/test_config_lifecycle.py::test_cfg_01_relative_path_resolution_and_double_prefix`
- **Суть:** При настройках из `.env.example` (`PRIVATE_DATA_DIR=.private` и `DATABASE_PATH=.private/app_v2.sqlite3`) относительный путь дополняется повторно, приводя к `.private/.private/app_v2.sqlite3`.

---

### 4.4 Низкий уровень (Low Severity)

#### `WEB-03-UPDATE-TOPIC-HTTP-400-INSTEAD-OF-409`: Возврат кода 400 вместо 409 при изменении темы сданного задания
- **Файл и строки:** `app/web.py:545–555`
- **Воспроизводящий тест:** `tests/audit/test_web_security.py::test_web_03_06_admin_update_assignment_in_use_contract[WEB-03-06]`
- **Суть:** Ошибка `AssignmentInUseError` перехватывается общим блоком `except (AssignmentValidationError, AssignmentInUseError)` и возвращает HTTP 400 вместо специфицированного HTTP 409 Conflict (`assignment_in_use`).

#### `SEC-01-SESSION-COOKIE-SAMESITE-MISSING`: Отсутствие явного флага SameSite=Lax в сессионных cookie
- **Файл и строки:** `app/web.py:80–90`
- **Воспроизводящий тест:** `tests/audit/test_web_security.py::test_sec_01_01_cookie_security_flags[SEC-01-01]`
- **Суть:** В конфигурации Flask не задан параметр `SESSION_COOKIE_SAMESITE = 'Lax'`, что снижает защиту от CSRF в устаревших браузерах.

---

## 5. Опровергнутые гипотезы (Ruled-out Hypotheses)

1. **Гипотеза о сбое вложенной транзакции при `with conn:` перед `BEGIN IMMEDIATE` (`RULED-OUT-01`):**
   - *Предположение:* Использование контекстного менеджера `with connect(...) as conn:` с последующим выполнением `conn.execute("BEGIN IMMEDIATE;")` вызовет ошибку драйвера SQLite `cannot start a transaction within a transaction`.
   - *Результат проверки:* Опровергнута. Стандартный драйвер `sqlite3` в Python при ручном вызове `BEGIN IMMEDIATE` переключает состояние транзакции без падений. Более 300 тестов параллельной загрузки и сбоев подтвердили корректность фиксации и отката.
2. **Гипотеза о падении схемы DDL при архивных сдачах без `reviewed_at` (`RULED-OUT-02`):**
   - *Предположение:* Архивные сдачи со статусом `reviewed`, у которых отсутствует timestamp `reviewed_at`, нарушат ограничение `CHECK(review_status='reviewed' OR reviewed_at IS NULL)`.
   - *Результат проверки:* Опровергнута. Логическая дизъюнкция `review_status='reviewed' OR ...` истинна для всех записей со статусом `reviewed` независимо от наличия `reviewed_at`. Тесты импорта исторических сдач прошли успешно.

---

## 6. Эксплуатационные пределы и масштабируемость (Scaling Limits)

На синтетическом наборе данных (100 групп, 1 000 студентов, 10 000 занятий, 20 000 сдач) зафиксированы следующие метрики:
- **Размер БД:** 25.7 МБ основной файл базы, WAL-файл в режиме покоя 0 МБ.
- **Скорость генерации:** 20 000 сдач созданы пакетной вставкой за 0.27 секунды.
- **Время выборки расписания группы (`list_for_group`, 100 строк):** `3.29 мс`, пиковая память `117.8 КБ`.
- **Время выборки сдач по фильтру группы и темы (20 строк):** `1.82 мс`, пиковая память `47.5 КБ`.
- **Время полной непагинированной выборки истории (20 000 строк):** `1 214 мс`, пиковая память `42.4 МБ`.
- **Скачивание файлов:** Средняя задержка разрешения и проверки файла на диске составила `0.716 мс`.
- **Конкурентная блокировка на запись:** При удержании транзакции первым писателем (150 мс), второй писатель ожидает в рамках `busy_timeout` (зафиксировано время ожидания `179.8 мс`) и успешно фиксирует изменения без падений.
- **Узкие места архитектуры:**
  1. Однопоточная запись SQLite: операции загрузки файлов сериализуются единственным write-lock.
  2. Сканирование таблицы студентов (`SCAN students`).
  3. Непагинированная административная история сдач.

---

## 7. Сквозная браузерная верификация (Browser E2E Flow)

Сквозной браузерный сценарий выполнен на реальном движке Chromium (Playwright 1.64.1) на изолированном loopback-сервере Flask:
1. **Студенческий портал (`01_landing.png`):** Выбор группы, ввод пароля, валидация сессии.
2. **Разблокированное состояние (`02_unlocked.png`):** Отображение однофамильцев с уникальными ID, выбор темы и пары.
3. **Регистрация студента (`03_new_student.png`):** Добавление нового студента без перезагрузки страницы.
4. **Успешная загрузка (`04_submission_success.png`):** Drag-and-drop отправка архива, расчет SHA-256, присвоение попытки 1.
5. **Панель преподавателя (`05_admin_dashboard.png`):** Вход администратора по мастер-паролю.
6. **Реестр сдач (`06_admin_submissions.png`):** Фильтрация по группе и теме, просмотр метаданных.
7. **Рецензирование работы (`07_admin_reviewed.png`):** Переключение статуса на `reviewed`, скачивание файла с корректным заголовком `Content-Disposition`.
Все скриншоты сохранены в каталоге `docs/audits/sqlite-v2/screenshots/`.

---

## 8. Инвентаризация отклонений чистой отсечки (Clean Cutover Deviations)

В кодовой базе выявлены следующие легаси-алиасы и обратная совместимость:
1. `app/assignments.py:293`: Алиас `delete = archive` в классе `AssignmentStore`. Сохранен для совместимости со старыми вызовами.
2. `app/assignments.py:80–105, 172–185`: Двойные сигнатуры методов `create` и `update`, поддерживающие 5 аргументов `(group_id, subject_id, topic_id, lesson_date, pair_number)` наряду с новой 4-аргументной сигнатурой `(group_id, topic_id, lesson_date, pair_number)`.
3. `app/web.py:630`: Регистрация алиаса маршрута `@app.post("/admin/<kind>/<item_id>/update")`, перенаправляющего на `admin_catalog_rename`.
4. Старый удаляющий маршрут `POST /admin/assignments/<id>/delete` полностью удален и возвращает HTTP 404/405.

---

## 9. Остаточные риски и пошаговый план устранения (Residual Risks & Next Steps)

Для снятия блокировки релиза необходимо реализовать следующие исправления:

| Шаг | Затронутый файл | Действие | Устраняемый дефект |
|---|---|---|---|
| **1** | `tools/legacy_catalog.py` | Включить `subject_id` в детерминированный UUID темы | `MIG-02-TOPIC-UUID-DROPPED` |
| **2** | `tools/migrate_v2.py` | Импортировать граф каталога из снимка перед чтением mapping | `MIG-03-EMPTY-ENTITIES-FK-FAILURE` |
| **3** | `tools/migrate_v2.py` | Учитывать папку предмета в `resolve_file_location` | `MIG-05-RESOLVER-IGNORES-SUBJECT-HIERARCHY` |
| **4** | `tools/migrate_v2.py` | Передавать `archived_at` из mapping в запросы INSERT | `MIG-03-ARCHIVED-ENTITIES-INSERTED-AS-ACTIVE` |
| **5** | `tools/migrate_v2.py` | Добавить валидацию ключей маппинга и непустых причин пропуска | `MIG-04-NO-STRICT-SCHEMA-VALIDATION` |
| **6** | `app/schema_v2.sql` | Добавить индекс `CREATE INDEX idx_students_group ON students(group_id, archived_at, display_name);` | `SCALE-01-STUDENTS-TABLE-SCAN` |
| **7** | `app/schema_v2.sql` | Заменить `UNIQUE(name_key)` групп/предметов на частичный индекс `WHERE archived_at IS NULL` | `DOM-01-NAME-CONFLICT-ARCHIVED` |
| **8** | `app/access.py` | Использовать `UPDATE group_access SET version = version + 1` | `RACE-02-ACCESS-VERSION-LOST-UPDATE` |
| **9** | `app/web.py` | Исправить относительное разрешение `DATABASE_PATH` | `CFG-01-DOUBLE-PRIVATE-PREFIX` |
| **10**| `app/web.py` | Обернуть разбор UUID фильтров в try/except и возвращать 409 для topic conflict | `WEB-04-TAMPERED-FILTER-HTTP-500`, `WEB-03` |
| **11**| `app/web.py` | Установить `SESSION_COOKIE_SAMESITE = 'Lax'` | `SEC-01-SESSION-COOKIE-SAMESITE-MISSING` |
| **12**| `app/web.py` | Добавить пагинацию в список сдач (`LIMIT 50 OFFSET ?`) | `SCALE-02-UNPAGINATED-HISTORY-MEMORY-GROWTH` |

### Заключение
Переход на единую схему SQLite v2 обладает высоким качеством в части транзакционной модели хранения файлов, защиты от атак обхода путей и изоляции резервных копий. Однако миграционный слой и отдельные ограничения схемы содержат дефекты, создающие риск потери и искажения данных. Релиз возможен строго после исправления перечисленных замечаний и повторного прогона верификационного набора тестов.
