from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from gsafe.errors import GSafeError
from gsafe.files import copy_path_strict, safe_rmtree
from gsafe.git import (
    is_repo_with_refs,
    manifest_head_contents_for_repo,
    run_git,
    write_head_contents,
)

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1
BUNDLE_NAME = "repo.bundle"
REPO_METADATA_DIR_NAME = "repo-meta"
UNLOCK_TOKEN_NAME = "gsafe-unlock.json"
DEFAULT_BRANCH = "main"
DEFAULT_HEAD_CONTENTS = f"ref: refs/heads/{DEFAULT_BRANCH}\n"
REPO_METADATA_EXCLUDES = frozenset({"objects", "refs", "packed-refs", UNLOCK_TOKEN_NAME})


@dataclass(frozen=True)
class Manifest:
    version: int
    state: str
    is_bundle_present: bool
    head_contents: str | None = None
    is_repo_metadata_present: bool = False
    origin_path: str | None = None
    machine_id: str | None = None
    unlock_token: str | None = None

    def to_bytes(self) -> bytes:
        payload: dict[str, object] = {
            "version": self.version,
            "state": self.state,
            "is_bundle_present": self.is_bundle_present,
            "is_repo_metadata_present": self.is_repo_metadata_present,
        }
        optional_values = {
            "head": self.head_contents,
            "origin_path": self.origin_path,
            "machine_id": self.machine_id,
            "unlock_token": self.unlock_token,
        }
        for key, value in optional_values.items():
            if value is not None:
                payload[key] = value
        return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    @staticmethod
    def from_bytes(data: bytes) -> "Manifest":
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GSafeError("Manifest metadata is not valid JSON.") from error
        if not isinstance(payload, dict):
            raise GSafeError("Manifest metadata must be a JSON object.")
        version = payload.get("version")
        state = payload.get("state", "locked")
        is_bundle_present = payload.get("is_bundle_present", payload.get("has_bundle"))
        is_repo_metadata_present = payload.get("is_repo_metadata_present", payload.get("has_repo_meta", False))
        head_contents = payload.get("head")
        origin_path = payload.get("origin_path")
        machine_id = payload.get("machine_id")
        unlock_token = payload.get("unlock_token")
        if not isinstance(version, int) or isinstance(version, bool):
            raise GSafeError("Manifest version metadata is invalid.")
        if version > MANIFEST_VERSION:
            raise GSafeError(
                f"Unsupported payload version: {version}. Upgrade gsafe to open this container."
            )
        if state not in {"locked", "unlocked"}:
            raise GSafeError("Manifest state metadata is invalid.")
        if not isinstance(is_bundle_present, bool):
            raise GSafeError("Manifest bundle metadata is invalid.")
        if not isinstance(is_repo_metadata_present, bool):
            raise GSafeError("Manifest repo metadata flag is invalid.")
        for field_name, value in {
            "head": head_contents,
            "origin_path": origin_path,
            "machine_id": machine_id,
            "unlock_token": unlock_token,
        }.items():
            if value is not None and not isinstance(value, str):
                raise GSafeError(f"Manifest {field_name} metadata is invalid.")
        return Manifest(
            version=version,
            state=state,
            is_bundle_present=is_bundle_present,
            head_contents=head_contents,
            is_repo_metadata_present=is_repo_metadata_present,
            origin_path=origin_path,
            machine_id=machine_id,
            unlock_token=unlock_token,
        )


def load_manifest(payload_dir: Path) -> Manifest:
    manifest_path = payload_dir / MANIFEST_NAME
    if not manifest_path.exists():
        raise GSafeError(f"Payload is missing {MANIFEST_NAME}.")
    return Manifest.from_bytes(manifest_path.read_bytes())


def write_manifest(payload_dir: Path, manifest: Manifest) -> None:
    (payload_dir / MANIFEST_NAME).write_bytes(manifest.to_bytes())


def snapshot_repo_metadata(source_repo_path: Path, payload_dir: Path) -> None:
    metadata_dir = payload_dir / REPO_METADATA_DIR_NAME
    if metadata_dir.exists():
        safe_rmtree(metadata_dir)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source_repo_path.iterdir(), key=lambda value: value.name):
        if entry.name in REPO_METADATA_EXCLUDES:
            continue
        copy_path_strict(entry, metadata_dir / entry.name)


def restore_repo_metadata(payload_dir: Path, remote_repo_path: Path) -> bool:
    metadata_dir = payload_dir / REPO_METADATA_DIR_NAME
    if not metadata_dir.exists():
        return False
    if not metadata_dir.is_dir():
        raise GSafeError(f"Payload metadata path is invalid: {metadata_dir}")
    for entry in sorted(metadata_dir.iterdir(), key=lambda value: value.name):
        copy_path_strict(entry, remote_repo_path / entry.name)
    return True


def import_bundle_into_bare_repo(bundle_path: Path, remote_repo_path: Path) -> None:
    run_git(["init", "--bare", str(remote_repo_path)])
    run_git(["-C", str(remote_repo_path), "fetch", str(bundle_path), "+refs/*:refs/*"])


def restore_repo_from_payload(payload_dir: Path, remote_repo_path: Path) -> Manifest:
    manifest = load_manifest(payload_dir)
    if manifest.is_bundle_present:
        bundle_path = payload_dir / BUNDLE_NAME
        if not bundle_path.exists():
            raise GSafeError(f"Payload says bundle exists, but {BUNDLE_NAME} is missing.")
        import_bundle_into_bare_repo(bundle_path, remote_repo_path)
    else:
        run_git(["init", "--bare", str(remote_repo_path)])
    is_metadata_restored = restore_repo_metadata(payload_dir, remote_repo_path)
    if manifest.is_repo_metadata_present and not is_metadata_restored:
        raise GSafeError(f"Payload says repo metadata exists, but {REPO_METADATA_DIR_NAME} is missing.")
    head_contents = manifest.head_contents
    if not manifest.is_bundle_present and head_contents is None:
        head_contents = DEFAULT_HEAD_CONTENTS
    write_head_contents(remote_repo_path, head_contents)
    return manifest


def snapshot_repo_to_payload(remote_repo_path: Path, payload_dir: Path, state: str) -> Manifest:
    is_bundle_present = is_repo_with_refs(remote_repo_path)
    if is_bundle_present:
        run_git(["-C", str(remote_repo_path), "bundle", "create", str(payload_dir / BUNDLE_NAME), "--all"])
    snapshot_repo_metadata(remote_repo_path, payload_dir)
    manifest = Manifest(
        version=MANIFEST_VERSION,
        state=state,
        is_bundle_present=is_bundle_present,
        head_contents=manifest_head_contents_for_repo(remote_repo_path),
        is_repo_metadata_present=True,
    )
    write_manifest(payload_dir, manifest)
    return manifest
