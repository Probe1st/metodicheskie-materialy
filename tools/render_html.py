"""Render self-contained printable HTML pages for course materials."""

from html import escape
from pathlib import Path


_STYLE = """
:root {
  color-scheme: light;
  font-family: "PT Sans", Arial, sans-serif;
  color: #17212b;
  background: #eef3f7;
  line-height: 1.55;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: #eef3f7;
}

header, main, footer {
  max-width: 920px;
  margin: 0 auto;
  padding-left: 48px;
  padding-right: 48px;
}

header {
  padding-top: 48px;
  padding-bottom: 28px;
  color: #fff;
  background: #164a6b;
}

header h1 {
  max-width: 920px;
  margin: 0 auto;
  font-size: 2rem;
  line-height: 1.2;
}

.eyebrow {
  margin: 0 0 10px;
  font-size: .82rem;
  font-weight: 700;
  letter-spacing: .04em;
  text-transform: uppercase;
  color: #d9eaf5;
}

@media print {
  .eyebrow { color: #164a6b; }
}


main, footer {
  background: #fff;
}

main {
  padding-top: 36px;
  padding-bottom: 36px;
}

footer {
  padding-top: 24px;
  padding-bottom: 40px;
  border-top: 1px solid #c9d7e2;
}

h2, h3 { line-height: 1.25; }
h2 { margin-top: 2rem; color: #164a6b; }
h3 { margin-top: 1.5rem; }
a { color: #075d9b; }

.callout {
  padding: 16px 20px;
  border-left: 4px solid #e5a400;
  background: #fff7df;
}

pre {
  overflow-x: auto;
  padding: 16px;
  border-radius: 4px;
  color: #edf5fb;
  background: #1d2c3a;
}

code { font-family: "SFMono-Regular", Consolas, monospace; }
ol { padding-left: 1.5rem; }
.checklist { padding-left: 0; list-style: none; }
.checklist li::before { content: "☐ "; }
.sources { padding-left: 1.25rem; }

@media (max-width: 640px) {
  header, main, footer {
    padding-left: 24px;
    padding-right: 24px;
  }

  header { padding-top: 32px; }
  header h1 { font-size: 1.6rem; }
}

@media print {
  @page { margin: 18mm; }

  :root, body { background: #fff; }
  header, main, footer { max-width: none; padding-left: 0; padding-right: 0; }
  header { padding-top: 0; color: #000; background: transparent; }
  main { padding-top: 16px; }
  footer { padding-bottom: 0; }
  a { color: #000; text-decoration: underline; }
  pre { white-space: pre-wrap; color: #000; border: 1px solid #777; background: transparent; }
  h2, h3 { break-after: avoid; }
  pre, .callout { break-inside: avoid; }
}
""".strip()


def render_page(
    title: str,
    body: str,
    sources: list[tuple[str, str]],
    *,
    eyebrow: str | None = None,
) -> str:
    source_items = "\n".join(
        f'        <li><a href="{escape(url, quote=True)}">'
        f"{escape(label, quote=True)}</a></li>"
        for label, url in sources
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title, quote=True)}</title>
  <style>
{_STYLE}
  </style>
</head>
<body>
  <header>
    {f'<p class="eyebrow">{escape(eyebrow, quote=True)}</p>' if eyebrow else ""}
    <h1>{escape(title, quote=True)}</h1>
  </header>
  <main>
{body}
  </main>
  <footer aria-label="Источники">
    <h2>Источники</h2>
    <ul class="sources">
{source_items}
    </ul>
  </footer>
</body>
</html>
"""


def write_html(path: Path, html: str) -> None:
    """Write rendered HTML as UTF-8, creating parent directories if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
