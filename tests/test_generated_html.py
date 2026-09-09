from pathlib import Path

from tools.course_manifest import SESSIONS
from tools.generate_course import generate


THEORY_FILES = {
    "metodicheskij-material.html",
    "prakticheskie-zadaniya.html",
    "domashnee-zadanie.html",
}
PRACTICE_FILES = {
    "prakticheskaya-rabota.html",
    "prakticheskie-zadaniya.html",
    "domashnee-zadanie.html",
}


def test_each_session_has_exact_html_contract(tmp_path: Path) -> None:
    generate(tmp_path)

    folders = sorted(path for path in tmp_path.iterdir() if path.is_dir())
    assert len(folders) == 49
    assert len(list(tmp_path.rglob("*.html"))) == 147

    for session, folder in zip(SESSIONS, folders, strict=True):
        expected = THEORY_FILES if session.kind == "theory" else PRACTICE_FILES
        assert {path.name for path in folder.glob("*.html")} == expected
        assert folder.name.startswith(f"{session.number:02d}-")


def test_generated_pages_are_student_facing_and_topic_specific(tmp_path: Path) -> None:
    generate(tmp_path)

    homework_pages: list[str] = []
    for session in SESSIONS:
        folder = next(tmp_path.glob(f"{session.number:02d}-*"))
        for page in folder.glob("*.html"):
            text = page.read_text(encoding="utf-8")
            assert '<html lang="ru">' in text
            assert "Источники" in text
            assert session.title in text
            assert session.outcome in text
            assert "колледж" not in text.casefold()
            assert "академическ" not in text.casefold()
            assert "преподавател" not in text.casefold()
        homework_pages.append(
            (folder / "domashnee-zadanie.html").read_text(encoding="utf-8")
        )

    assert len(set(homework_pages)) == len(SESSIONS)


def test_topic_sequences_are_present_in_generated_practices(tmp_path: Path) -> None:
    generate(tmp_path)

    folders = {folder.name[:2]: folder for folder in tmp_path.iterdir() if folder.is_dir()}
    assert [folders[f"{number:02d}"].name[:2] for number in range(13, 25)] == [
        f"{number:02d}" for number in range(13, 25)
    ]
    assert [folders[f"{number:02d}"].name[:2] for number in range(40, 50)] == [
        f"{number:02d}" for number in range(40, 50)
    ]

    assert "граф потока управления" in (
        folders["13"] / "prakticheskaya-rabota.html"
    ).read_text(encoding="utf-8").casefold()
    assert "покрытие ветвей" in (
        folders["15"] / "prakticheskaya-rabota.html"
    ).read_text(encoding="utf-8").casefold()
    assert "техническое задание" in (
        folders["42"] / "prakticheskaya-rabota.html"
    ).read_text(encoding="utf-8").casefold()
    assert "защита комплекта" in (
        folders["49"] / "prakticheskaya-rabota.html"
    ).read_text(encoding="utf-8").casefold()
