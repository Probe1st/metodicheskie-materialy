import hashlib
import json
import os
import stat
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pytest

from app.database import connect, initialize
from app.storage import (
    DEFAULT_ALLOWED_UPLOAD_EXTENSIONS,
    clean_extension,
    destination_directory,
    normalize_upload_extensions,
    resolve_safe_path,
    validated_upload_suffix,
    write_upload,
)
from tools.reconcile_storage import reconcile_storage


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
    with pytest.raises((RuntimeError, TypeError), match="Invalid upload extension configuration"):
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


def test_resolve_safe_path_normal(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    target = resolve_safe_path(storage, "group-1/topic-1/sub.pdf")
    assert target == (storage / "group-1" / "topic-1" / "sub.pdf").resolve()


def test_resolve_safe_path_traversal_rejected(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()

    with pytest.raises(ValueError):
        resolve_safe_path(storage, "../../etc/passwd")

    with pytest.raises(ValueError):
        resolve_safe_path(storage, "../outside.txt")

    with pytest.raises(ValueError):
        resolve_safe_path(storage, "/etc/passwd")

    with pytest.raises(ValueError):
        resolve_safe_path(storage, ".")


def test_resolve_safe_path_symlink_escape_rejected(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("classified", encoding="utf-8")

    symlink_dir = storage / "sym_link"
    os.symlink(outside, symlink_dir)

    with pytest.raises(ValueError):
        resolve_safe_path(storage, "sym_link/secret.txt")


def test_clean_extension() -> None:
    assert clean_extension(".pdf") == ".pdf"
    assert clean_extension(".tar.gz") == ".gz"
    assert clean_extension(".weird$$") == ""
    assert clean_extension("report.final.pdf") == ".pdf"
    assert clean_extension("archive.tar.gz") == ".gz"
    assert clean_extension("IMAGE.PNG") == ".png"
    assert clean_extension("no_ext") == ""
    assert clean_extension("trailing_dot.") == ""
    assert clean_extension("too_long." + "a" * 17) == ""
    assert clean_extension("max_len." + "a" * 16) == "." + "a" * 16


def _setup_test_db(db_path: Path, storage_path: Path) -> tuple[str, str, str, str, str, str]:
    initialize(db_path)
    storage_path.mkdir(parents=True, exist_ok=True)

    with connect(db_path) as conn:
        group_id = "11111111-1111-1111-1111-111111111111"
        conn.execute(
            "INSERT INTO groups (id, name, name_key) VALUES (?, 'Group A', 'group a')",
            (group_id,),
        )
        conn.execute(
            "INSERT INTO group_access (group_id, is_open, version) VALUES (?, 1, 1)",
            (group_id,),
        )
        student_id = "22222222-2222-2222-2222-222222222222"
        conn.execute(
            "INSERT INTO students (id, group_id, display_name) VALUES (?, ?, 'Student A')",
            (student_id, group_id),
        )
        subj_row = conn.execute("SELECT id FROM subjects WHERE is_system = 1").fetchone()
        subject_id = subj_row["id"]
        topic_id = "33333333-3333-3333-3333-333333333333"
        conn.execute(
            "INSERT INTO topics (id, subject_id, name, name_key) VALUES (?, ?, 'Topic A', 'topic a')",
            (topic_id, subject_id),
        )
        assignment_id = "44444444-4444-4444-4444-444444444444"
        conn.execute(
            """
            INSERT INTO class_assignments (id, group_id, topic_id, lesson_date, pair_number, created_at, updated_at)
            VALUES (?, ?, ?, '2026-11-03', 1, '2026-11-03T10:00:00+00:00', '2026-11-03T10:00:00+00:00')
            """,
            (assignment_id, group_id, topic_id),
        )
        submission_id = "55555555-5555-5555-5555-555555555555"
        conn.execute(
            """
            INSERT INTO submissions (id, student_id, group_id, topic_id, assignment_id, attempt_number, created_at)
            VALUES (?, ?, ?, ?, ?, 1, '2026-11-03T10:00:00+00:00')
            """,
            (submission_id, student_id, group_id, topic_id, assignment_id),
        )

        content = b"sample submission file content"
        content_sha256 = hashlib.sha256(content).hexdigest()
        rel_path = f"{group_id}/{topic_id}/{submission_id}.pdf"
        file_id = "66666666-6666-6666-6666-666666666666"
        conn.execute(
            """
            INSERT INTO submission_files (id, submission_id, original_filename, stored_path, size_bytes, sha256)
            VALUES (?, ?, 'sample.pdf', ?, ?, ?)
            """,
            (file_id, submission_id, rel_path, len(content), content_sha256),
        )

        file_full_path = storage_path / group_id / topic_id / f"{submission_id}.pdf"
        file_full_path.parent.mkdir(parents=True, exist_ok=True)
        file_full_path.write_bytes(content)

    return group_id, student_id, topic_id, assignment_id, submission_id, rel_path


def test_reconcile_storage_clean(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    storage_path = tmp_path / "storage"
    _setup_test_db(db_path, storage_path)

    report = reconcile_storage(db_path, storage_path)
    assert report["status"] == "ok"
    assert report["missing_files"] == []
    assert report["checksum_mismatches"] == []
    assert report["size_mismatches"] == []
    assert report["orphaned_files"] == []
    assert report["pending_files"] == []

    cmd = [
        sys.executable,
        "-m",
        "tools.reconcile_storage",
        "--database",
        str(db_path),
        "--storage",
        str(storage_path),
    ]
    res = subprocess.run(
        cmd,
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    cli_report = json.loads(res.stdout)
    assert cli_report["status"] == "ok"


def test_reconcile_storage_missing_file(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    storage_path = tmp_path / "storage"
    *_, rel_path = _setup_test_db(db_path, storage_path)

    target_file = storage_path / rel_path
    target_file.unlink()

    report = reconcile_storage(db_path, storage_path)
    assert report["status"] == "discrepancies_found"
    assert len(report["missing_files"]) == 1
    assert report["missing_files"][0]["stored_path"] == rel_path

    cmd = [
        sys.executable,
        "-m",
        "tools.reconcile_storage",
        "--database",
        str(db_path),
        "--storage",
        str(storage_path),
    ]
    res = subprocess.run(
        cmd,
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 1


def test_reconcile_storage_size_mismatch(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    storage_path = tmp_path / "storage"
    *_, rel_path = _setup_test_db(db_path, storage_path)

    target_file = storage_path / rel_path
    target_file.write_bytes(b"shorter")

    report = reconcile_storage(db_path, storage_path)
    assert report["status"] == "discrepancies_found"
    assert len(report["size_mismatches"]) == 1
    assert report["size_mismatches"][0]["stored_path"] == rel_path

    cmd = [
        sys.executable,
        "-m",
        "tools.reconcile_storage",
        "--database",
        str(db_path),
        "--storage",
        str(storage_path),
    ]
    res = subprocess.run(
        cmd,
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 1


def test_reconcile_storage_sha_mismatch(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    storage_path = tmp_path / "storage"
    *_, rel_path = _setup_test_db(db_path, storage_path)

    target_file = storage_path / rel_path
    orig_size = target_file.stat().st_size
    target_file.write_bytes(b"X" * orig_size)

    report = reconcile_storage(db_path, storage_path)
    assert report["status"] == "discrepancies_found"
    assert len(report["checksum_mismatches"]) == 1
    assert report["checksum_mismatches"][0]["stored_path"] == rel_path

    cmd = [
        sys.executable,
        "-m",
        "tools.reconcile_storage",
        "--database",
        str(db_path),
        "--storage",
        str(storage_path),
    ]
    res = subprocess.run(
        cmd,
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 1


def test_reconcile_storage_orphan_file(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    storage_path = tmp_path / "storage"
    _setup_test_db(db_path, storage_path)

    orphan = storage_path / "unexpected_file.txt"
    orphan.write_text("orphan", encoding="utf-8")

    report = reconcile_storage(db_path, storage_path)
    assert report["status"] == "discrepancies_found"
    assert len(report["orphaned_files"]) == 1
    assert "unexpected_file.txt" in report["orphaned_files"]

    cmd = [
        sys.executable,
        "-m",
        "tools.reconcile_storage",
        "--database",
        str(db_path),
        "--storage",
        str(storage_path),
    ]
    res = subprocess.run(
        cmd,
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 1


def test_reconcile_storage_pending_file(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    storage_path = tmp_path / "storage"
    _setup_test_db(db_path, storage_path)

    pending_dir = storage_path / ".pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    (pending_dir / "leftover.tmp").write_bytes(b"temp")

    report = reconcile_storage(db_path, storage_path)
    assert report["status"] == "discrepancies_found"
    assert len(report["pending_files"]) == 1
    assert ".pending/leftover.tmp" in report["pending_files"]

    cmd = [
        sys.executable,
        "-m",
        "tools.reconcile_storage",
        "--database",
        str(db_path),
        "--storage",
        str(storage_path),
    ]
    res = subprocess.run(
        cmd,
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 1
