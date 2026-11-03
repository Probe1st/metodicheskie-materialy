import sys
import pytest
from pathlib import Path
repo_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_dir))

from app.catalog_store import create_catalog_store

catalog_path = repo_dir / "catalog.json"
if not catalog_path.is_file():
    create_catalog_store({"CATALOG_PROVIDER": "json", "CATALOG_PATH": catalog_path}).snapshot()

from html.parser import HTMLParser


class CSRFTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "meta" and attrs_dict.get("name") == "csrf-token":
            self.token = attrs_dict.get("content")
        elif tag == "input" and attrs_dict.get("name") == "csrf_token":
            if not self.token:
                self.token = attrs_dict.get("value")


def get_csrf_token(client, page="/"):
    resp = client.get(page)
    parser = CSRFTokenParser()
    parser.feed(resp.get_data(as_text=True))
    return parser.token


def post_with_csrf(client, url, *, csrf_page="/", **kwargs):
    headers = dict(kwargs.get("headers") or {})
    if "X-CSRFToken" not in headers:
        token = get_csrf_token(client, csrf_page)
        if token:
            headers["X-CSRFToken"] = token
    kwargs["headers"] = headers
    return client.post(url, **kwargs)


@pytest.fixture(name="post_with_csrf")
def post_with_csrf_fixture():
    return post_with_csrf
