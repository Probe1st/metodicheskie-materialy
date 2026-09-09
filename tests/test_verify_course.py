from pathlib import Path

from tools.verify_course import verify


def test_verifier_rejects_missing_homework(tmp_path: Path) -> None:
    (tmp_path / "01-Тест").mkdir()

    errors = verify(tmp_path)

    assert any("domashnee-zadanie.html" in error for error in errors)
