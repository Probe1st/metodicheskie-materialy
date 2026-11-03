from __future__ import annotations

import html
import re

import nh3
from markdown_it import MarkdownIt

ALLOWED_TAGS = frozenset({
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "strong", "em", "b", "i", "s",
    "blockquote", "ul", "ol", "li",
    "pre", "code", "a",
    "table", "thead", "tbody", "tr", "th", "td",
})

ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
}

ALLOWED_SCHEMES = frozenset({"http", "https", "mailto"})

URL_PATTERN = re.compile(r"(https?://[^\s<>\"]+)")
TAG_PATTERN = re.compile(r"(</?[a-zA-Z0-9]+(?:\s+[^>]*)?>)")
IGNORE_AUTOLINK_TAGS = frozenset({"a", "pre", "code"})

_markdown_renderer = MarkdownIt("commonmark").enable("table").enable("strikethrough")


def _autolink_text_fragments(html_str: str) -> str:
    tokens = TAG_PATTERN.split(html_str)
    inside_ignore = 0
    result: list[str] = []

    for token in tokens:
        if not token:
            continue
        m = re.match(r"^<(/)?([a-zA-Z0-9]+)", token)
        if m:
            is_close, raw_tag = m.group(1), m.group(2).lower()
            if raw_tag in IGNORE_AUTOLINK_TAGS:
                if is_close:
                    inside_ignore = max(0, inside_ignore - 1)
                else:
                    inside_ignore += 1
            result.append(token)
        else:
            if inside_ignore == 0:
                def replace_url(match: re.Match[str]) -> str:
                    raw_url = match.group(1)
                    url = raw_url
                    trailing = ""
                    while url and (url[-1] in ".,;:!?" or (url[-1] == ")" and url.count("(") < url.count(")"))):
                        trailing = url[-1] + trailing
                        url = url[:-1]
                    return f'<a href="{url}">{url}</a>{trailing}'

                token = URL_PATTERN.sub(replace_url, token)
            result.append(token)

    return "".join(result)


def sanitize_html(html_str: str) -> str:
    return nh3.clean(
        html_str,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_SCHEMES,
    )


def render_task(description: str, description_format: str) -> str:
    if not isinstance(description, str):
        raise TypeError("description must be a string")
    if not description.strip():
        return ""

    fmt = description_format.lower().strip()
    if fmt == "txt":
        normalized = description.replace("\r\n", "\n").replace("\r", "\n")
        escaped = html.escape(normalized)
        autolinked = _autolink_text_fragments(escaped)
        return sanitize_html(autolinked)
    elif fmt == "md":
        rendered = _markdown_renderer.render(description)
        autolinked = _autolink_text_fragments(rendered)
        return sanitize_html(autolinked)
    elif fmt == "html":
        autolinked = _autolink_text_fragments(description)
        return sanitize_html(autolinked)
    else:
        raise ValueError(f"Unsupported format: {description_format!r}")
