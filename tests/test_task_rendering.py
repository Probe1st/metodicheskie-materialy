import pytest
from app.task_rendering import render_task


def test_empty_description_returns_empty():
    assert render_task("", "txt") == ""
    assert render_task("   \n\t  ", "md") == ""
    assert render_task("", "html") == ""


def test_invalid_format_raises_value_error():
    with pytest.raises(ValueError):
        render_task("Hello", "pdf")
    with pytest.raises(ValueError):
        render_task("Hello", ".md")


def test_txt_rendering():
    raw = "Line 1: <script>alert('xss')</script>\nLine 2: https://example.com/test.\nLine 3: done"
    rendered = render_task(raw, "txt")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "<br>" not in rendered
    assert '<a href="https://example.com/test"' in rendered
    assert 'rel="noopener noreferrer"' in rendered
    assert "Line 3: done" in rendered

    # Test preserving LF, CRLF, CR, empty lines, and leading spaces
    multiline = "Строка 1\r\nСтрока 2\r\n\r\n  Строка 4\r  Строка 5"
    res = render_task(multiline, "txt")
    assert "<br>" not in res
    assert res == "Строка 1\nСтрока 2\n\n  Строка 4\n  Строка 5"

def test_md_rendering_basic_and_tables():
    md = """# Заголовок

Параграф с **жирным** текстом и [ссылкой](https://example.com).

| Колонка 1 | Колонка 2 |
|---|---|
| Данные 1 | Данные 2 |
"""
    rendered = render_task(md, "md")
    assert "<h1>Заголовок</h1>" in rendered
    assert "<strong>жирным</strong>" in rendered
    assert '<a href="https://example.com"' in rendered
    assert "<table>" in rendered
    assert "<thead>" in rendered
    assert "<th>Колонка 1</th>" in rendered
    assert "<td>Данные 1</td>" in rendered


def test_md_sanitization_removes_scripts_and_handlers():
    md = """# Title
<script>alert(1)</script>
<img src="x.jpg" onerror="alert(2)">
<a href="javascript:alert(3)">evil</a>
<p style="color:red">styled</p>
"""
    rendered = render_task(md, "md")
    assert "<script>" not in rendered
    assert "alert" not in rendered
    assert "<img" not in rendered
    assert "onerror" not in rendered
    assert "javascript:" not in rendered
    assert 'style="color:red"' not in rendered
    assert "Title" in rendered


def test_md_autolinks_text_but_not_code():
    md = "Посетите https://example.org/home или выполните `curl https://example.org/api`."
    rendered = render_task(md, "md")
    assert '<a href="https://example.org/home"' in rendered
    assert "<code>curl https://example.org/api</code>" in rendered
    # URL inside code must not be wrapped in <a>
    assert "<code>curl <a" not in rendered


def test_html_rendering_sanitization_and_autolinks():
    html_input = """
<h1>Задание</h1>
<style>body { font-size: 100px; }</style>
<form action="/steal"><button>Click</button></form>
<iframe src="https://evil.com"></iframe>
<p onclick="steal()">Текст задания со ссылкой: https://study.university.ru/lecture</p>
<pre><code>curl -X POST https://api.internal/endpoint</code></pre>
<p>Существующая ссылка: <a href="https://docs.org" title="Документация">Документация</a></p>
"""
    rendered = render_task(html_input, "html")
    assert "<h1>Задание</h1>" in rendered
    assert "<style>" not in rendered
    assert "<form>" not in rendered
    assert "<button>" not in rendered
    assert "<iframe>" not in rendered
    assert "onclick" not in rendered
    assert '<a href="https://study.university.ru/lecture"' in rendered
    assert 'title="Документация"' in rendered
    # pre/code url not autolinked
    assert "<code>curl -X POST https://api.internal/endpoint</code>" in rendered


def test_url_parentheses_and_punctuation():
    txt = "См. (https://en.wikipedia.org/wiki/Python_(programming_language))."
    rendered = render_task(txt, "txt")
    assert '<a href="https://en.wikipedia.org/wiki/Python_(programming_language)"' in rendered
    assert ").<br>" in rendered or ").\n" in rendered or rendered.endswith(").")
