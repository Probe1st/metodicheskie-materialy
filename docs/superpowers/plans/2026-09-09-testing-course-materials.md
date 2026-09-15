# Testing Course Materials Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a deployable 49-session HTML/PDF course package for МДК.01.02 «Поддержка и тестирование программных модулей».

**Architecture:** A checked-in Python manifest names every pair and defines its type, learning outcome and topic. A generator renders self-contained Russian HTML pages and theory-only PDF slides from that manifest, then a verifier asserts the course topology and file integrity. The output contains no external CSS, JavaScript or network dependency.

**Tech Stack:** Python 3.12, pytest 9, reportlab, pypdf, standard-library HTML generation.

## Global Constraints

- Root output directory: `МДК.01.02 Поддержка и тестирование программных модулей`.
- Exactly 49 numbered session folders: 24 theory and 25 practice.
- Theoretical folders contain `metodicheskij-material.html`, `prezentaciya.pdf`, `prakticheskie-zadaniya.html`, `domashnee-zadanie.html`.
- Practical folders contain `prakticheskaya-rabota.html`, `prakticheskie-zadaniya.html`, `domashnee-zadanie.html`; no lecture HTML or PDF.
- Student-facing text is Russian, second-person/plural address; it must not mention college status, lesson duration or academic-hour accounting.
- Practical code targets Python 3.12 and pytest; all sample commands must work with `python -m pytest`.
- Sources must link primary documentation: ISTQB CTFL v4, pytest, Python docs, GitHub Docs and official standards catalogues where relevant.

---

### Task 1: Session manifest and source catalogue

**Files:**
- Create: `tools/course_manifest.py`
- Create: `tests/test_course_manifest.py`

**Interfaces:**
- Produces: `SESSIONS: tuple[Session, ...]`, where `Session(number, slug, title, kind, block, outcome, keywords)` and `kind in {"theory", "practice"}`.
- Consumes: no project modules.

- [ ] **Step 1: Write the manifest test**

```python
from tools.course_manifest import SESSIONS

def test_manifest_contains_the_course_load():
    assert len(SESSIONS) == 49
    assert [s.number for s in SESSIONS] == list(range(1, 50))
    assert sum(s.kind == "theory" for s in SESSIONS) == 24
    assert sum(s.kind == "practice" for s in SESSIONS) == 25
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_course_manifest.py -v`
Expected: FAIL because `tools.course_manifest` does not exist.

- [ ] **Step 3: Implement the complete course sequence**

Declare sessions 01–49 in order: `Тестирование в верификации` (01–02), `Ошибки и отладка` (03–04), `Методы тестирования` (05–06), `Уровни тестирования` (07–08), `Производительность` (09–10), `Регрессия` (11–12), practices «Белый ящик» (13–15), «Чёрный ящик» (16–18), «Модульное тестирование» (19–21), «Интеграционное тестирование» (22–24), `Средства и технологии документации` (25–29), `Документирование по ЕСПД` (30–34), `Автоматизация документации` (35–39), practices «Оформление документации» (40–49). Give each session a distinct outcome and three-to-five topical keywords.

- [ ] **Step 4: Run the manifest test**

Run: `python -m pytest tests/test_course_manifest.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/course_manifest.py tests/test_course_manifest.py
git commit -m "Add testing course session manifest"
```

### Task 2: Shared HTML renderer

**Files:**
- Create: `tools/render_html.py`
- Create: `tests/test_render_html.py`

**Interfaces:**
- Consumes: `Session` from `tools.course_manifest`.
- Produces: `render_page(title: str, body: str, sources: list[tuple[str, str]]) -> str` and `write_html(path: Path, html: str) -> None`.

- [ ] **Step 1: Write rendering tests**

```python
from tools.render_html import render_page

def test_page_is_a_self_contained_russian_html_document():
    page = render_page("Тема", "<h2>Задание</h2>", [("pytest", "https://docs.pytest.org/")])
    assert "<!doctype html>" in page.lower()
    assert "<style>" in page
    assert "https://docs.pytest.org/" in page
    assert "lang=\"ru\"" in page
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_render_html.py -v`
Expected: FAIL because `tools.render_html` does not exist.

- [ ] **Step 3: Implement semantic, printable pages**

Render `header`, `main`, hierarchical headings, callout classes, `pre><code>`, numbered task lists, checklist and source list. Embed responsive screen and print CSS. Escape interpolated text with `html.escape`; only precomposed instructional fragments may be inserted as HTML.

- [ ] **Step 4: Run renderer tests**

Run: `python -m pytest tests/test_render_html.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/render_html.py tests/test_render_html.py
git commit -m "Add self-contained course HTML renderer"
```

### Task 3: Generate all student-facing HTML

**Files:**
- Create: `tools/generate_course.py`
- Create: `tests/test_generated_html.py`
- Create: `МДК.01.02 Поддержка и тестирование программных модулей/**` (147 HTML files)

**Interfaces:**
- Consumes: `SESSIONS`, `render_page`.
- Produces: `generate(output_root: Path) -> None`.

- [ ] **Step 1: Write output-contract tests**

```python
from pathlib import Path
from tools.generate_course import generate

def test_each_session_has_exact_html_contract(tmp_path: Path):
    generate(tmp_path)
    folders = sorted(p for p in tmp_path.iterdir() if p.is_dir())
    assert len(folders) == 49
    assert (folders[0] / "metodicheskij-material.html").exists()
    assert (folders[12] / "prakticheskaya-rabota.html").exists()
    assert not (folders[12] / "metodicheskij-material.html").exists()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_generated_html.py -v`
Expected: FAIL because `tools.generate_course` does not exist.

- [ ] **Step 3: Implement theory and practice content templates**

Theory pages must explain terms, provide an executable Python/pytest example, include two comprehension checks and a graded practical sequence. Practice pages must provide setup, a starting fragment for the booking-service project, mandatory and advanced steps, expected artifacts, a submission checklist and assessment criteria. Generate a distinct HTML practical-task page and homework page for every session. Use sources relevant to the topic; use `pytest.raises`, fixtures, parametrization, `unittest.mock`, logging, `pdb`, `time.perf_counter`, contract boundaries, Markdown, Sphinx, MkDocs, docstrings and EСПД document types as applicable.

- [ ] **Step 4: Generate the root course directory**

Run: `python tools/generate_course.py --output "МДК.01.02 Поддержка и тестирование программных модулей"`
Expected: 49 directories and 147 HTML files.

- [ ] **Step 5: Run content tests**

Run: `python -m pytest tests/test_generated_html.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tools tests "МДК.01.02 Поддержка и тестирование программных модулей"
git commit -m "Generate student HTML testing materials"
```

### Task 4: Theory PDF presentations

**Files:**
- Create: `tools/render_pdf.py`
- Create: `tests/test_generated_pdfs.py`
- Create: `МДК.01.02 Поддержка и тестирование программных модулей/**/prezentaciya.pdf` (24 files)

**Interfaces:**
- Consumes: theory `Session` values.
- Produces: `render_presentation(session: Session, destination: Path) -> None`.

- [ ] **Step 1: Write PDF contract test**

```python
from pypdf import PdfReader
from tools.render_pdf import render_presentation

def test_presentation_has_readable_topic(tmp_path, theory_session):
    path = tmp_path / "prezentaciya.pdf"
    render_presentation(theory_session, path)
    assert path.read_bytes().startswith(b"%PDF-")
    assert theory_session.title in "".join(page.extract_text() or "" for page in PdfReader(path).pages)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_generated_pdfs.py -v`
Expected: FAIL because `tools.render_pdf` does not exist.

- [ ] **Step 3: Generate concise accessible slide decks**

Use an embedded Unicode font. Render title, objectives, key concepts, a diagram or comparison table, a code/example slide, a self-check slide and sources. Generate PDFs only for theory sessions. Do not introduce lectures or presentations in practical folders.

- [ ] **Step 4: Generate and test PDFs**

Run: `python tools/generate_course.py --output "МДК.01.02 Поддержка и тестирование программных модулей" && python -m pytest tests/test_generated_pdfs.py -v`
Expected: PASS; every theory directory has a non-empty PDF and every practical directory has none.

- [ ] **Step 5: Commit**

```bash
git add tools tests "МДК.01.02 Поддержка и тестирование программных модулей"
git commit -m "Add theory presentation PDFs"
```

### Task 5: End-to-end course verifier

**Files:**
- Create: `tools/verify_course.py`
- Create: `tests/test_verify_course.py`

**Interfaces:**
- Consumes: output root and `SESSIONS`.
- Produces: command `python tools/verify_course.py --root <path>`; exits zero only for a complete, readable package.

- [ ] **Step 1: Write a verifier test**

```python
from tools.verify_course import verify

def test_verifier_rejects_missing_homework(tmp_path):
    (tmp_path / "01-Тест").mkdir()
    errors = verify(tmp_path)
    assert any("domashnee-zadanie.html" in error for error in errors)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_verify_course.py -v`
Expected: FAIL because `tools.verify_course` does not exist.

- [ ] **Step 3: Implement checks and run the complete build**

Verify folder count and numbering, required/forbidden files by kind, non-empty HTML, UTF-8 decoding, student-facing source block, non-empty PDF signature and PDF text. Then run:

```bash
python tools/generate_course.py --output "МДК.01.02 Поддержка и тестирование программных модулей"
python tools/verify_course.py --root "МДК.01.02 Поддержка и тестирование программных модулей"
python -m pytest -v
```

Expected: verifier prints `49 sessions verified`; all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tools tests "МДК.01.02 Поддержка и тестирование программных модулей"
git commit -m "Verify deployable testing course package"
```
