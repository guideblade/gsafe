from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from platformdirs import user_cache_path, user_data_path

from gsafe.files import atomic_write_bytes, resolve_path


def container_fingerprint(container_path: Path) -> str:
    resolved_path = str(resolve_path(container_path)).casefold()
    return hashlib.sha256(resolved_path.encode("utf-8")).hexdigest()[:8]


def default_origin_path(container_path: Path) -> Path:
    resolved_container_path = resolve_path(container_path)
    container_name = resolved_container_path.stem
    return (
        user_cache_path("gsafe", appauthor=False)
        / "remotes"
        / container_fingerprint(resolved_container_path)
        / f"{container_name}.git"
    )


def machine_id_path() -> Path:
    return user_data_path("gsafe", appauthor=False) / "machine-id.json"


def read_machine_id(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    machine_id = data.get("machine_id")
    if isinstance(machine_id, str) and machine_id:
        return machine_id
    return None


def local_machine_id() -> str:
    path = machine_id_path()
    machine_id = read_machine_id(path)
    if machine_id:
        return machine_id
    machine_id = uuid.uuid4().hex
    atomic_write_bytes(path, json.dumps({"machine_id": machine_id}, sort_keys=True).encode("utf-8"))
    return machine_id


def known_local_machine_id() -> str | None:
    return read_machine_id(machine_id_path())
