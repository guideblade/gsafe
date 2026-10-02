from __future__ import annotations

import io
import os
import shutil
import stat
import tarfile
import tempfile
from pathlib import Path

from gsafe.errors import GSafeError


def resolve_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    ensure_parent_dir(path)
    with tempfile.NamedTemporaryFile(
        prefix=f"{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        delete=False,
    ) as file:
        temporary_path = Path(file.name)
        try:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            file.close()
            temporary_path.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def is_path_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def safe_rmtree(path: Path) -> None:
    def remove_readonly(function, target: str, exception_info: object) -> None:
        exception = exception_info[1] if isinstance(exception_info, tuple) else exception_info
        if not isinstance(exception, PermissionError):
            raise GSafeError(f"Failed to remove path: {path}\n{exception}") from exception
        target_mode = os.stat(target).st_mode
        os.chmod(target, target_mode | stat.S_IWRITE)
        function(target)

    if not path.exists():
        return
    try:
        if path.is_file() or path.is_symlink():
            path.unlink()
            return
        shutil.rmtree(path, onerror=remove_readonly)
    except OSError as error:
        raise GSafeError(f"Failed to remove path: {path}\n{error}") from error


def is_symlink_or_reparse_point(path: Path) -> bool:
    status = os.lstat(path)
    if stat.S_ISLNK(status.st_mode):
        return True
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_attribute and (getattr(status, "st_file_attributes", 0) & reparse_attribute))


def copy_path_strict(source: Path, destination: Path) -> None:
    try:
        if is_symlink_or_reparse_point(source):
            raise GSafeError(f"Refusing to copy symlink or reparse point: {source}")
        status = os.stat(source, follow_symlinks=False)
        if stat.S_ISDIR(status.st_mode):
            if destination.exists() and not destination.is_dir():
                safe_rmtree(destination)
            destination.mkdir(parents=True, exist_ok=True)
            for entry in sorted(source.iterdir(), key=lambda value: value.name):
                copy_path_strict(entry, destination / entry.name)
            return
        if stat.S_ISREG(status.st_mode):
            if destination.exists() and destination.is_dir():
                safe_rmtree(destination)
            ensure_parent_dir(destination)
            shutil.copy2(source, destination)
            return
    except OSError as error:
        raise GSafeError(f"Failed to copy path: {source}\n{error}") from error
    raise GSafeError(f"Unsupported filesystem entry: {source}")


def create_tar_gz_from_dir(source_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for item in sorted(source_dir.iterdir(), key=lambda value: value.name):
            tar.add(item, arcname=item.name)
    return buffer.getvalue()


def extract_tar_gz_bytes(payload: bytes, destination_dir: Path) -> None:
    destination_dir.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
            try:
                tar.extractall(destination_dir, filter="data")
            except TypeError:
                destination_root = resolve_path(destination_dir)
                members = tar.getmembers()
                for member in members:
                    member_path = resolve_path(destination_dir / member.name)
                    if not member.name or not is_path_within(member_path, destination_root):
                        raise GSafeError(f"Payload contains an invalid tar path: {member.name!r}")
                    if member.islnk() or member.issym() or member.isdev():
                        raise GSafeError(f"Payload contains an unsupported tar entry: {member.name!r}")
                tar.extractall(destination_dir, members=members)
    except (OSError, tarfile.TarError) as error:
        raise GSafeError("Payload archive is invalid.") from error
