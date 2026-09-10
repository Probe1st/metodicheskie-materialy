"""Verify a generated student-facing course package."""

from __future__ import annotations

import argparse
import subprocess
from collections import defaultdict
from pathlib import Path

try:  # Support both `python tools/verify_course.py` and package imports.
    from tools.course_manifest import SESSIONS
except ModuleNotFoundError:  # pragma: no cover - exercised by the CLI form.
    from course_manifest import SESSIONS


THEORY_HTML_FILES = frozenset(
    {
        "metodicheskij-material.html",
        "prakticheskie-zadaniya.html",
        "domashnee-zadanie.html",
    }
)
PRACTICE_HTML_FILES = frozenset(
    {
        "prakticheskaya-rabota.html",
        "prakticheskie-zadaniya.html",
        "domashnee-zadanie.html",
    }
)
PRESENTATION_FILE = "prezentaciya.pdf"


def _session_folders(root: Path, errors: list[str]) -> dict[int, list[Path]]:
    try:
        folders = [path for path in root.iterdir() if path.is_dir()]
    except OSError as error:
        errors.append(f"cannot read output root {root}: {error}")
        return {}

    expected_numbers = {session.number for session in SESSIONS}
    by_number: dict[int, list[Path]] = defaultdict(list)
    for folder in folders:
        prefix, separator, _ = folder.name.partition("-")
        if not separator or not prefix.isdigit() or len(prefix) != 2:
            errors.append(f"invalid session folder name: {folder.name}")
            continue
        number = int(prefix)
        if number not in expected_numbers:
            errors.append(f"unexpected session folder: {folder.name}")
            continue
        by_number[number].append(folder)

    if len(folders) != len(SESSIONS):
        errors.append(f"expected {len(SESSIONS)} session folders, found {len(folders)}")

    for session in SESSIONS:
        matches = by_number[session.number]
        if not matches:
            errors.append(f"missing folder for session {session.number:02d}")
        elif len(matches) > 1:
            errors.append(f"multiple folders for session {session.number:02d}")
    return by_number


def _verify_html(path: Path, errors: list[str]) -> str | None:
    if not path.is_file():
        errors.append(f"missing required HTML file: {path}")
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        errors.append(f"cannot read UTF-8 HTML file {path}: {error}")
        return None
    if not text.strip():
        errors.append(f"empty HTML file: {path}")
    return text


def _verify_pdf(path: Path, errors: list[str]) -> None:
    if not path.is_file():
        errors.append(f"missing required PDF file: {path}")
        return
    try:
        with path.open("rb") as document:
            signature = document.read(5)
    except OSError as error:
        errors.append(f"cannot read PDF file {path}: {error}")
        return
    if signature != b"%PDF-":
        errors.append(f"invalid PDF signature: {path}")
        return

    try:
        result = subprocess.run(
            ["pdftotext", str(path), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        errors.append("pdftotext is required to verify PDF text")
        return
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or "pdftotext failed"
        errors.append(f"cannot extract text from PDF {path}: {detail}")
        return
    if not result.stdout.strip():
        errors.append(f"PDF has no extractable text: {path}")


def _expected_html_files(kind: str) -> frozenset[str] | None:
    if kind == "theory":
        return THEORY_HTML_FILES
    if kind == "practice":
        return PRACTICE_HTML_FILES
    return None


def _verify_session(folder: Path, number: int, kind: str, errors: list[str]) -> None:
    expected_html = _expected_html_files(kind)
    if expected_html is None:
        errors.append(f"session {number:02d} has unsupported kind: {kind}")
        return

    try:
        html_files = {path.name for path in folder.glob("*.html") if path.is_file()}
        pdf_files = {path.name for path in folder.glob("*.pdf") if path.is_file()}
    except OSError as error:
        errors.append(f"cannot inspect session {number:02d}: {error}")
        return

    missing_html = expected_html - html_files
    unexpected_html = html_files - expected_html
    for name in sorted(missing_html):
        errors.append(f"session {number:02d} missing required file: {name}")
    for name in sorted(unexpected_html):
        errors.append(f"session {number:02d} has forbidden HTML file: {name}")

    expected_pdfs = {PRESENTATION_FILE} if kind == "theory" else set()
    missing_pdfs = expected_pdfs - pdf_files
    unexpected_pdfs = pdf_files - expected_pdfs
    for name in sorted(missing_pdfs):
        errors.append(f"session {number:02d} missing required file: {name}")
    for name in sorted(unexpected_pdfs):
        errors.append(f"session {number:02d} has forbidden PDF file: {name}")

    pages = {
        name: _verify_html(folder / name, errors)
        for name in expected_html
        if name not in missing_html
    }
    source_page = (
        "metodicheskij-material.html" if kind == "theory" else "prakticheskaya-rabota.html"
    )
    source_text = pages.get(source_page)
    if source_text is not None and "<pre><code>" not in source_text:
        errors.append(f"session {number:02d} lacks a direct student source block")

    if kind == "theory" and PRESENTATION_FILE not in missing_pdfs:
        _verify_pdf(folder / PRESENTATION_FILE, errors)


def verify(root: Path) -> list[str]:
    """Return all package-contract violations without raising for incomplete output."""
    errors: list[str] = []
    if not root.is_dir():
        return [f"output root is not a directory: {root}"]

    folders = _session_folders(root, errors)
    for session in SESSIONS:
        matches = folders.get(session.number, [])
        if len(matches) == 1:
            _verify_session(matches[0], session.number, session.kind, errors)
    return errors


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Проверить комплект учебных материалов")
    parser.add_argument("--root", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = _parse_args()
    errors = verify(arguments.root)
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"{len(SESSIONS)} sessions verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
