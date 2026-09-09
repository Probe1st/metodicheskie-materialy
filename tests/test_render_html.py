from pathlib import Path

from tools.render_html import render_page, write_html


def test_page_is_self_contained_russian_document_with_escaped_inputs() -> None:
    page = render_page(
        'Тема <script>alert("x")</script>',
        '<h2>Задание</h2><p class="callout">Выполните шаг.</p>',
        [
            (
                'pytest <руководство>',
                'https://docs.pytest.org/?next="value"&source=test',
            )
        ],
    )

    assert "<!doctype html>" in page.lower()
    assert '<html lang="ru">' in page
    assert "<style>" in page
    assert "<header>" in page
    assert "<main>" in page
    assert '<footer aria-label="Источники">' in page
    assert "<h2>Задание</h2>" in page
    assert "Тема &lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in page
    assert "pytest &lt;руководство&gt;" in page
    assert (
        'href="https://docs.pytest.org/?next=&quot;value&quot;&amp;source=test"'
        in page
    )
    assert "@media print" in page


def test_write_html_creates_utf8_file_with_parent_directories(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "page.html"

    write_html(output, "<p>Текст</p>")

    assert output.read_text(encoding="utf-8") == "<p>Текст</p>"
