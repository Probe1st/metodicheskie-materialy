import subprocess
from pathlib import Path

from tools.generate_course import SOURCES, THEORY, generate
from tools.render_pdf import PRESENTATION_CONTENT, _sources_for, _student_outcome, render_presentation


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


def test_presentation_outcome_preserves_punctuation_and_transforms_all_verbs() -> None:
    outcome = (
        "Объясняет назначение ЕСПД и определяет применимые стандарты, "
        "а также оформляет ссылку."
    )

    assert _student_outcome(outcome) == (
        "Объяснить назначение ЕСПД и определить применимые стандарты, "
        "а также оформить ссылку."
    )


def test_presentation_uses_theory_focus_sources_for_each_espd_topic() -> None:
    by_number = {session.number: session for session in SESSIONS}

    standard_sources = _sources_for(by_number[30], THEORY[30].sources)
    technical_assignment_sources = _sources_for(by_number[31], THEORY[31].sources)

    assert standard_sources == tuple(SOURCES[key] for key in THEORY[30].sources)
    assert technical_assignment_sources == tuple(SOURCES[key] for key in THEORY[31].sources)
    assert standard_sources != technical_assignment_sources


def test_presentations_contain_authored_topic_specific_content() -> None:
    theory_numbers = {session.number for session in SESSIONS if session.kind == "theory"}

    assert set(PRESENTATION_CONTENT) == theory_numbers
    assert len({content.example for content in PRESENTATION_CONTENT.values()}) == len(theory_numbers)
    assert all(content.relationship and content.self_check for content in PRESENTATION_CONTENT.values())
    assert all("в учебном проекте нужно применить" not in content.example.casefold() for content in PRESENTATION_CONTENT.values())
