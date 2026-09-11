from io import BytesIO
from pathlib import Path

from app.storage import destination_directory, write_upload


def test_storage_uses_safe_folder_and_attempt_numbers(tmp_path: Path) -> None:
    directory = destination_directory(tmp_path, "ИС 25/9-1П", "Архитектура", "Работа")
    first = write_upload(BytesIO(b"one"), "answer.tar.gz", "Иванов Иван Иванович", directory)
    second = write_upload(BytesIO(b"two"), "answer.pdf", "Иванов Иван Иванович", directory)
    assert directory.name == "ИС 25／9-1П-Архитектура-Работа"
    assert first.name == "Иванов Иван Иванович 1.tar.gz"
    assert second.name == "Иванов Иван Иванович 2.pdf"
