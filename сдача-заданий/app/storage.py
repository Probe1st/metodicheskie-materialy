from __future__ import annotations

import os
import shutil
import unicodedata
from pathlib import Path
from typing import BinaryIO


def clean(value: str) -> str:
    value = unicodedata.normalize("NFC", value).replace("/", "／").replace("\\", "／")
    value = "".join("" if unicodedata.category(char).startswith("C") else char for char in value)
    value = " ".join(value.split())
    if not value or value in {".", ".."}:
        raise ValueError("invalid path component")
    return value


def destination_directory(root: Path, group: str, discipline: str, work: str) -> Path:
    result = root / "-".join(map(clean, (group, discipline, work)))
    result.mkdir(parents=True, exist_ok=True)
    return result


def write_upload(source: BinaryIO, original_filename: str, student_name: str, directory: Path) -> Path:
    suffix = "".join(Path(original_filename).suffixes)
    name = clean(student_name)
    for attempt in range(1, 1_000_000):
        if any(directory.glob(f"{name} {attempt}.*")) or (directory / f"{name} {attempt}").exists():
            continue
        path = directory / f"{name} {attempt}{suffix}"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        except FileExistsError:
            continue
        try:
            with os.fdopen(fd, "wb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            return path
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    raise RuntimeError("attempt limit exceeded")
