import subprocess
from pathlib import Path

from tools.course_manifest import SESSIONS
from tools.generate_course import generate
from tools.render_pdf import render_presentation


def _pdf_text(path: Path) -> str:
    result = subprocess.run(
        ["pdftotext", str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_presentation_has_readable_russian_topic(tmp_path: Path) -> None:
    theory_session = next(session for session in SESSIONS if session.kind == "theory")
    path = tmp_path / "prezentaciya.pdf"

    render_presentation(theory_session, path)

    assert path.read_bytes().startswith(b"%PDF-")
    assert theory_session.title in _pdf_text(path)


def test_generator_creates_presentations_only_for_theory_sessions(tmp_path: Path) -> None:
    generate(tmp_path)

    for session in SESSIONS:
        folder = next(tmp_path.glob(f"{session.number:02d}-*"))
        presentation = folder / "prezentaciya.pdf"
        if session.kind == "theory":
            assert presentation.is_file()
            assert presentation.stat().st_size > 0
            assert session.title in _pdf_text(presentation)
        else:
            assert not presentation.exists()
