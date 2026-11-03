from __future__ import annotations

import os
import shutil
import unicodedata
from collections.abc import Collection
from pathlib import Path
from typing import BinaryIO

DEFAULT_ALLOWED_UPLOAD_EXTENSIONS: frozenset[str] = frozenset({
    ".txt", ".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
    ".csv", ".json", ".sql", ".py", ".zip", ".7z", ".rar", ".tar", ".gz", ".tar.gz"
})


def normalize_upload_extensions(value: str | Collection[str]) -> frozenset[str]:
    if value is None or isinstance(value, (bytes, bytearray)):
        raise RuntimeError("Invalid upload extension configuration")
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, Collection):
        items = list(value)
    else:
        raise RuntimeError("Invalid upload extension configuration")

    if not items:
        raise RuntimeError("Invalid upload extension configuration")

    result: set[str] = set()
    for raw_item in items:
        if not isinstance(raw_item, str):
            raise RuntimeError("Invalid upload extension configuration")
        if not raw_item or not raw_item.strip():
            raise RuntimeError("Invalid upload extension configuration")
        if any(unicodedata.category(c).startswith("C") for c in raw_item):
            raise RuntimeError("Invalid upload extension configuration")
        if "*" in raw_item or "/" in raw_item or "\\" in raw_item:
            raise RuntimeError("Invalid upload extension configuration")

        norm = raw_item.strip().lower()
        if any(c.isspace() for c in norm):
            raise RuntimeError("Invalid upload extension configuration")
        if not norm.startswith(".") or norm == "." or norm.endswith("."):
            raise RuntimeError("Invalid upload extension configuration")

        result.add(norm)

    if not result:
        raise RuntimeError("Invalid upload extension configuration")
    return frozenset(result)


def validated_upload_suffix(
    filename: str,
    allowed_extensions: Collection[str] = DEFAULT_ALLOWED_UPLOAD_EXTENSIONS,
) -> str:
    if not isinstance(filename, str):
        raise ValueError("file extension is not allowed")

    cleaned = filename.replace("\\", "/").rstrip("/")
    basename = cleaned.rsplit("/", 1)[-1] if "/" in cleaned else cleaned

    if (
        not basename
        or basename.startswith(".")
        or "." not in basename
        or basename.endswith(".")
        or basename[-1].isspace()
        or any(unicodedata.category(c).startswith("C") for c in basename)
    ):
        raise ValueError("file extension is not allowed")

    lower_basename = basename.lower()
    matches: list[str] = []
    for ext in allowed_extensions:
        if not isinstance(ext, str):
            continue
        norm_ext = ext.strip().lower()
        if not norm_ext.startswith("."):
            norm_ext = f".{norm_ext}"
        if lower_basename.endswith(norm_ext):
            matches.append(norm_ext)

    if not matches:
        raise ValueError("file extension is not allowed")

    return max(matches, key=len)


def clean(value: str) -> str:
    value = unicodedata.normalize("NFC", value).replace("/", "／").replace("\\", "／")
    value = "".join("" if unicodedata.category(char).startswith("C") else char for char in value)
    value = " ".join(value.split())
    if not value or value in {".", ".."}:
        raise ValueError("invalid path component")
    return value


def destination_directory(root: Path, group: str, discipline: str, work: str) -> Path:
    result = root / clean(group) / clean(discipline) / clean(work)
    result.mkdir(parents=True, exist_ok=True)
    return result


def write_upload(
    source: BinaryIO,
    original_filename: str,
    student_name: str,
    directory: Path,
    *,
    allowed_extensions: Collection[str] = DEFAULT_ALLOWED_UPLOAD_EXTENSIONS,
) -> Path:
    ext = validated_upload_suffix(original_filename, allowed_extensions)
    name = clean(student_name)
    directory.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, 1_000_000):
        if (
            any(directory.glob(f"{name} {attempt}.*"))
            or any(directory.glob(f"+{name} {attempt}.*"))
            or (directory / f"{name} {attempt}").exists()
            or (directory / f"+{name} {attempt}").exists()
        ):
            continue
        path = directory / f"{name} {attempt}{ext}"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        except FileExistsError:
            continue
        try:
            with os.fdopen(fd, "wb") as output:
                shutil.copyfileobj(source, output, 64 * 1024)
            return path
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            path.unlink(missing_ok=True)
            raise
    raise RuntimeError("attempt limit exceeded")
