"""Safe file storage helpers and path safety for homework-submissions."""

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
        raise TypeError("Invalid upload extension configuration")

    if not items:
        raise RuntimeError("Invalid upload extension configuration")

    result: set[str] = set()
    for raw_item in items:
        if not isinstance(raw_item, str):
            raise TypeError("Invalid upload extension configuration")
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
        raise TypeError("file extension is not allowed")

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


def resolve_safe_path(storage_root: Path, relative_path: str | Path) -> Path:
    """Resolve and validate that relative_path is strictly within storage_root.

    Raises ValueError if target path escapes storage_root via '..', absolute paths,
    or symlinks, or if target resolves to the storage root itself.
    """
    resolved_root = storage_root.resolve()
    target = (resolved_root / relative_path).resolve()
    if not target.is_relative_to(resolved_root) or target == resolved_root:
        raise ValueError(f"Path escapes storage root: {relative_path!r}")
    return target


def clean_extension(filename: str) -> str:
    """Extract and validate the last file extension from filename.

    Extracts last extension from basename (e.g. 'report.final.pdf' -> '.pdf',
    'archive.tar.gz' -> '.gz').
    If the extension (after the dot) consists only of ASCII letters and digits
    and is <= 16 chars: return it in lowercase (e.g. '.pdf').
    Otherwise return ''.
    """
    name = Path(filename).name
    dot_index = name.rfind(".")
    if dot_index == -1:
        return ""
    ext_part = name[dot_index + 1 :]
    if ext_part and len(ext_part) <= 16 and ext_part.isalnum() and ext_part.isascii():
        return f".{ext_part.lower()}"
    return ""


def get_pending_dir(storage_root: Path) -> Path:
    """Get the path to the .pending directory under storage_root."""
    return storage_root / ".pending"


def ensure_pending_dir(storage_root: Path) -> Path:
    """Ensure the .pending directory exists under storage_root with 0700 permissions."""
    pending = get_pending_dir(storage_root)
    pending.mkdir(parents=True, exist_ok=True)
    try:
        pending.chmod(0o700)
    except OSError:
        pass
    return pending


def cleanup_pending_file(path: Path) -> None:
    """Safely unlink a temporary pending file."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def list_pending_files(storage_root: Path) -> list[Path]:
    """List all files in storage_root / '.pending'."""
    pending = get_pending_dir(storage_root)
    if not pending.is_dir():
        return []
    return [p for p in pending.iterdir() if p.is_file()]
