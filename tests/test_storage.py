import stat
from io import BytesIO
from pathlib import Path

import pytest

from app.storage import (
    DEFAULT_ALLOWED_UPLOAD_EXTENSIONS,
    destination_directory,
    normalize_upload_extensions,
    validated_upload_suffix,
    write_upload,
)


def test_storage_uses_safe_folder_and_attempt_numbers(tmp_path: Path) -> None:
    directory = destination_directory(tmp_path, "ИС 25/9-1П", "Архитектура", "Работа")
    first = write_upload(BytesIO(b"one"), "answer.tar.gz", "Иванов Иван Иванович", directory)
    second = write_upload(BytesIO(b"two"), "answer.pdf", "Иванов Иван Иванович", directory)
    assert directory == tmp_path / "ИС 25／9-1П" / "Архитектура" / "Работа"
    assert directory.is_dir()
    assert (tmp_path / "ИС 25／9-1П").is_dir()
    assert (tmp_path / "ИС 25／9-1П" / "Архитектура").is_dir()
    assert first.name == "Иванов Иван Иванович 1.tar.gz"
    assert second.name == "Иванов Иван Иванович 2.pdf"
    assert first.exists()
    assert second.exists()


def test_storage_recognizes_teacher_marked_attempt_numbers(tmp_path: Path) -> None:
    directory = destination_directory(tmp_path, "ИС 25/9-1П", "Архитектура", "Работа")
    (directory / "+Иванов Иван Иванович 1.py").write_text("ok", encoding="utf-8")
    second = write_upload(BytesIO(b"two"), "answer.py", "Иванов Иван Иванович", directory)
    assert second.name == "Иванов Иван Иванович 2.py"
    assert second.exists()


# --- normalize_upload_extensions tests ---


def test_normalize_upload_extensions_valid_strings() -> None:
    assert normalize_upload_extensions(".txt, .pdf, .docx") == frozenset({".txt", ".pdf", ".docx"})
    assert normalize_upload_extensions(" .PDF, .Txt , .PY ") == frozenset({".pdf", ".txt", ".py"})
    assert normalize_upload_extensions(".tar.gz, .zip") == frozenset({".tar.gz", ".zip"})
    # Deduplication
    assert normalize_upload_extensions(".pdf, .PDF, .pdf") == frozenset({".pdf"})


def test_normalize_upload_extensions_valid_collections() -> None:
    assert normalize_upload_extensions([".PDF", " .docx ", ".tar.gz"]) == frozenset({".pdf", ".docx", ".tar.gz"})
    assert normalize_upload_extensions({".zip", ".7z", ".ZIP"}) == frozenset({".zip", ".7z"})
    assert normalize_upload_extensions((".txt", ".py")) == frozenset({".txt", ".py"})
    assert normalize_upload_extensions(DEFAULT_ALLOWED_UPLOAD_EXTENSIONS) == DEFAULT_ALLOWED_UPLOAD_EXTENSIONS


@pytest.mark.parametrize(
    "invalid_input",
    [
        None,
        "",
        "   ",
        [],
        set(),
        (),
        b".pdf",
        ".pdf,,.txt",
        ".pdf,  ,.txt",
        "*",
        ".*",
        ".pdf, *",
        "*.txt",
        "pdf",
        ".pdf, txt",
        [".pdf", "docx"],
        ".tar/gz",
        ".tar\\gz",
        [".pdf", ".tar/gz"],
        [".pdf", ".tar\\gz"],
        ".\x00pdf",
        ".pdf\n",
        ".pdf\r",
        ".pdf\t",
        ".",
        "..",
        ".pdf.",
        [".pdf", 123],
        [None],
    ],
)
def test_normalize_upload_extensions_invalid(invalid_input) -> None:
    with pytest.raises(RuntimeError, match="Invalid upload extension configuration"):
        normalize_upload_extensions(invalid_input)


# --- validated_upload_suffix tests ---


def test_validated_upload_suffix_all_default_extensions() -> None:
    for ext in DEFAULT_ALLOWED_UPLOAD_EXTENSIONS:
        assert validated_upload_suffix(f"homework{ext}") == ext
        assert validated_upload_suffix(f"folder/sub/homework{ext}") == ext
        assert validated_upload_suffix(f"folder\\sub\\homework{ext}") == ext


def test_validated_upload_suffix_compound_and_longest_match() -> None:
    assert validated_upload_suffix("submission.tar.gz") == ".tar.gz"
    assert validated_upload_suffix("submission.gz") == ".gz"
    assert validated_upload_suffix("submission.tar") == ".tar"
    assert validated_upload_suffix("archive.tar.gz", allowed_extensions=[".gz", ".tar.gz"]) == ".tar.gz"
    assert validated_upload_suffix("archive.tar.gz", allowed_extensions=[".tar.gz", ".gz"]) == ".tar.gz"
    assert validated_upload_suffix("my.backup.tar.gz") == ".tar.gz"


def test_validated_upload_suffix_case_insensitive() -> None:
    assert validated_upload_suffix("report.PDF") == ".pdf"
    assert validated_upload_suffix("archive.TAR.GZ") == ".tar.gz"
    assert validated_upload_suffix("archive.Tar.Gz") == ".tar.gz"
    assert validated_upload_suffix("SCRIPT.PY") == ".py"
    assert validated_upload_suffix("test.pY") == ".py"


@pytest.mark.parametrize(
    "filename",
    [
        ".bashrc",
        ".env",
        ".gitignore",
        ".pdf",
        ".tar.gz",
        "dir/.bashrc",
        "dir/.env",
        "dir/.pdf",
        "..",
        ".",
    ],
)
def test_validated_upload_suffix_hidden_files(filename: str) -> None:
    with pytest.raises(ValueError, match="file extension is not allowed"):
        validated_upload_suffix(filename)


@pytest.mark.parametrize(
    "filename",
    [
        "filename",
        "README",
        "folder/noext",
        "",
        "/",
        "///",
        "dir/",
        "dir/subdir/",
        "\\",
        "dir\\",
    ],
)
def test_validated_upload_suffix_extensionless(filename: str) -> None:
    with pytest.raises(ValueError, match="file extension is not allowed"):
        validated_upload_suffix(filename)


@pytest.mark.parametrize(
    "filename",
    [
        "report.pdf.",
        "report.pdf ",
        "report.pdf  ",
        "report.",
        "report.tar.gz ",
        "report.tar.gz.",
    ],
)
def test_validated_upload_suffix_trailing_dot_or_space(filename: str) -> None:
    with pytest.raises(ValueError, match="file extension is not allowed"):
        validated_upload_suffix(filename)


@pytest.mark.parametrize(
    "filename",
    [
        "rep\x00ort.pdf",
        "report\n.pdf",
        "report\r.pdf",
        "report\t.pdf",
        "rep\x1fort.pdf",
    ],
)
def test_validated_upload_suffix_control_characters(filename: str) -> None:
    with pytest.raises(ValueError, match="file extension is not allowed"):
        validated_upload_suffix(filename)


@pytest.mark.parametrize(
    "filename",
    [
        "malware.exe",
        "setup.msi",
        "script.sh",
        "script.bat",
        "script.cmd",
        "document.doc",
        "lib.so",
        "payload.bin",
    ],
)
def test_validated_upload_suffix_disallowed_extensions(filename: str) -> None:
    with pytest.raises(ValueError, match="file extension is not allowed"):
        validated_upload_suffix(filename)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("../../etc/passwd.txt", ".txt"),
        ("..\\..\\Windows\\System32\\notes.txt", ".txt"),
        ("dir/../../file.py", ".py"),
        ("dir/../sub/file.tar.gz", ".tar.gz"),
        ("C:\\Users\\Student\\report.PDF", ".pdf"),
    ],
)
def test_validated_upload_suffix_path_traversal_valid(filename: str, expected: str) -> None:
    assert validated_upload_suffix(filename) == expected


@pytest.mark.parametrize(
    "filename",
    [
        "../../etc/passwd",
        "..\\..\\Windows\\System32\\calc.exe",
        "../../../.bashrc",
        "../../..",
        "dir/..",
        "dir/.",
    ],
)
def test_validated_upload_suffix_path_traversal_invalid(filename: str) -> None:
    with pytest.raises(ValueError, match="file extension is not allowed"):
        validated_upload_suffix(filename)


# --- write_upload tests ---


def test_write_upload_allowed_extensions_normalized_suffix(tmp_path: Path) -> None:
    directory = tmp_path / "uploads"
    # report.v2.PDF -> "Иванов Иван Иванович 1.pdf"
    f1 = write_upload(BytesIO(b"content 1"), "report.v2.PDF", "Иванов Иван Иванович", directory)
    assert f1.name == "Иванов Иван Иванович 1.pdf"
    assert f1.read_bytes() == b"content 1"
    assert stat.S_IMODE(f1.stat().st_mode) == 0o640

    # report.exe.pdf -> "Иванов Иван Иванович 2.pdf" (not retaining .exe)
    f2 = write_upload(BytesIO(b"content 2"), "report.exe.pdf", "Иванов Иван Иванович", directory)
    assert f2.name == "Иванов Иван Иванович 2.pdf"
    assert f2.read_bytes() == b"content 2"

    # archive.tar.gz -> "Иванов Иван Иванович 3.tar.gz"
    f3 = write_upload(BytesIO(b"content 3"), "archive.tar.gz", "Иванов Иван Иванович", directory)
    assert f3.name == "Иванов Иван Иванович 3.tar.gz"
    assert f3.read_bytes() == b"content 3"


def test_write_upload_disallowed_extension_fails_before_reading_or_creating(tmp_path: Path) -> None:
    directory = tmp_path / "target_dir"

    class GuardedStream(BytesIO):
        def read(self, *args, **kwargs):
            raise AssertionError("Stream read should not have been called!")

    stream = GuardedStream(b"malicious payload")
    with pytest.raises(ValueError, match="file extension is not allowed"):
        write_upload(stream, "trojan.exe", "Студент", directory)

    assert not directory.exists()


def test_write_upload_allowed_extensions_override(tmp_path: Path) -> None:
    directory = tmp_path / "uploads"
    pdf_only = frozenset({".pdf"})

    with pytest.raises(ValueError, match="file extension is not allowed"):
        write_upload(BytesIO(b"code"), "solution.py", "Студент", directory, allowed_extensions=pdf_only)

    saved = write_upload(BytesIO(b"document"), "document.pdf", "Студент", directory, allowed_extensions=pdf_only)
    assert saved.name == "Студент 1.pdf"
    assert saved.read_bytes() == b"document"


def test_write_upload_attempt_number_not_consumed_by_failed_validation(tmp_path: Path) -> None:
    directory = tmp_path / "uploads"

    # Failed validation before any attempts
    with pytest.raises(ValueError, match="file extension is not allowed"):
        write_upload(BytesIO(b"bad"), "bad.exe", "Студент", directory)

    # First valid upload gets attempt 1
    f1 = write_upload(BytesIO(b"first"), "first.pdf", "Студент", directory)
    assert f1.name == "Студент 1.pdf"

    # Failed validation between attempts
    with pytest.raises(ValueError, match="file extension is not allowed"):
        write_upload(BytesIO(b"bad"), "bad.sh", "Студент", directory)

    # Next valid upload gets attempt 2 (not 3)
    f2 = write_upload(BytesIO(b"second"), "second.pdf", "Студент", directory)
    assert f2.name == "Студент 2.pdf"

    # Failed validation again
    with pytest.raises(ValueError, match="file extension is not allowed"):
        write_upload(BytesIO(b"bad"), "bad.bat", "Студент", directory)

    # Next valid upload gets attempt 3
    f3 = write_upload(BytesIO(b"third"), "third.py", "Студент", directory)
    assert f3.name == "Студент 3.py"


def test_write_upload_partial_file_cleanup_on_copy_failure(tmp_path: Path) -> None:
    directory = tmp_path / "uploads"

    class FailingStream:
        def __init__(self, data: bytes) -> None:
            self._data = data
            self._read_any = False

        def read(self, size: int = -1) -> bytes:
            if not self._read_any:
                self._read_any = True
                return self._data[:10]
            raise IOError("Simulated network/disk error during copy")

    stream = FailingStream(b"A" * 1024)
    with pytest.raises(IOError, match="Simulated network/disk error during copy"):
        write_upload(stream, "report.pdf", "Студент", directory)

    # Verify no partial file remains
    created_files = list(directory.glob("Студент 1.*"))
    assert created_files == []

    # Subsequent upload succeeds as attempt 1
    clean_file = write_upload(BytesIO(b"clean data"), "report.pdf", "Студент", directory)
    assert clean_file.name == "Студент 1.pdf"
    assert clean_file.read_bytes() == b"clean data"
