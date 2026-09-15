from pathlib import Path
import subprocess


from tools.program_modules_manifest import INSTITUTION_LABEL, SESSIONS


def test_manifest_preserves_2025_course_identity_and_load() -> None:
    assert INSTITUTION_LABEL == 'ММКЦТ «Академия ТОП» · ПМ.01 · МДК.01.01 · набор 2025'
    assert len(SESSIONS) == 105
    assert [session.number for session in SESSIONS] == list(range(1, 106))
    assert {session.kind for session in SESSIONS} == {'theory', 'practice'}
    assert any('Указатели' in session.title for session in SESSIONS)
    assert any('списками' in session.title for session in SESSIONS)



def test_generator_writes_full_2025_course_package(tmp_path: Path) -> None:
    from tools.generate_program_modules_course import generate

    generate(tmp_path)

    curriculum = (tmp_path / '00_Рабочая_программа.html').read_text(encoding='utf-8')
    folders = sorted(path for path in tmp_path.iterdir() if path.is_dir())

    assert INSTITUTION_LABEL in curriculum
    assert len(folders) == 105
    assert len(list(tmp_path.rglob('*.html'))) == 316
    assert (folders[0] / 'metodicheskij-material.html').is_file()
    assert (folders[0] / 'prezentaciya.pdf').read_bytes().startswith(b'%PDF-')

    sample = (folders[-1] / 'domashnee-zadanie.html').read_text(encoding='utf-8')
    assert INSTITUTION_LABEL in sample
    assert 'Сервис бронирования учебных ресурсов' in sample


def test_generator_runs_as_a_script(tmp_path: Path) -> None:
    result = subprocess.run(
        ['python', 'tools/generate_program_modules_course.py', '--output', str(tmp_path / 'course')],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr