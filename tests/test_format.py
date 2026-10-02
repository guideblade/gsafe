from __future__ import annotations

import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from gsafe.crypto import HEADER, decrypt_bytes, encrypt_bytes
from gsafe.errors import GSafeError
from gsafe.files import atomic_write_bytes, extract_tar_gz_bytes
from gsafe.paths import read_machine_id
from gsafe.payload import MANIFEST_VERSION, Manifest


def test_encrypt_decrypt_round_trip() -> None:
    blob = encrypt_bytes(b"secret payload", "password")
    assert b"secret payload" not in blob
    assert decrypt_bytes(blob, "password") == b"secret payload"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda blob: blob[:10], "too small"),
        (lambda blob: b"NOTGSAFE" + blob[8:], "magic is invalid"),
        (lambda blob: blob[:8] + bytes([99]) + blob[9:], "Unsupported container version"),
        (lambda blob: blob[:-1] + bytes([blob[-1] ^ 1]), "Wrong password or corrupted"),
    ],
)
def test_decrypt_rejects_invalid_containers(mutate, message: str) -> None:
    blob = encrypt_bytes(b"secret payload", "password")
    with pytest.raises(GSafeError, match=message):
        decrypt_bytes(mutate(blob), "password")


def test_encrypt_uses_fresh_salt_and_nonce() -> None:
    first = encrypt_bytes(b"same", "password")
    second = encrypt_bytes(b"same", "password")
    assert first[: HEADER.size] != second[: HEADER.size]


def test_manifest_round_trip() -> None:
    manifest = Manifest(
        version=MANIFEST_VERSION,
        state="unlocked",
        is_bundle_present=True,
        head_contents="ref: refs/heads/main\n",
        is_repo_metadata_present=True,
        origin_path="/tmp/origin.git",
        machine_id="machine",
        unlock_token="token",
    )
    assert Manifest.from_bytes(manifest.to_bytes()) == manifest


def test_manifest_accepts_legacy_keys() -> None:
    manifest = Manifest.from_bytes(json.dumps({"version": 1, "has_bundle": True, "has_repo_meta": True}).encode())
    assert manifest.is_bundle_present is True
    assert manifest.is_repo_metadata_present is True
    assert manifest.state == "locked"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"not json", "not valid JSON"),
        (b"[]", "must be a JSON object"),
        (b'{"version": true, "is_bundle_present": true}', "version metadata is invalid"),
        (b'{"version": 2, "is_bundle_present": true}', "Unsupported payload version: 2"),
        (b'{"version": 1, "state": "open", "is_bundle_present": true}', "state metadata is invalid"),
        (b'{"version": 1}', "bundle metadata is invalid"),
        (b'{"version": 1, "is_bundle_present": true, "head": 5}', "head metadata is invalid"),
    ],
)
def test_manifest_rejects_invalid_metadata(payload: bytes, message: str) -> None:
    with pytest.raises(GSafeError, match=message):
        Manifest.from_bytes(payload)


def tar_gz_with_member(name: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        data = b"payload"
        info = tarfile.TarInfo(name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.fixture(params=["filter", "fallback"])
def extraction_mode(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "fallback":
        original_extractall = tarfile.TarFile.extractall

        def extractall_without_filter(self, *args, **kwargs):
            if "filter" in kwargs:
                raise TypeError("extractall() got an unexpected keyword argument 'filter'")
            return original_extractall(self, *args, **kwargs)

        monkeypatch.setattr(tarfile.TarFile, "extractall", extractall_without_filter)
    return request.param


def test_extract_rejects_parent_traversal(tmp_path: Path, extraction_mode: str) -> None:
    destination = tmp_path / "destination"
    with pytest.raises(GSafeError, match="Payload (archive is invalid|contains an invalid tar path)"):
        extract_tar_gz_bytes(tar_gz_with_member("../escape.txt"), destination)
    assert not (tmp_path / "escape.txt").exists()


def test_extract_keeps_absolute_paths_inside_destination(tmp_path: Path, extraction_mode: str) -> None:
    destination = tmp_path / "destination"
    name = f"{tmp_path.as_posix()}/absolute.txt"
    try:
        extract_tar_gz_bytes(tar_gz_with_member(name), destination)
    except GSafeError:
        pass
    assert not (tmp_path / "absolute.txt").exists()


def test_atomic_write_removes_temporary_file_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "out" / "container.gsf"

    def failing_replace(source, destination) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        atomic_write_bytes(target, b"data")
    assert list(target.parent.iterdir()) == []


@pytest.mark.parametrize("contents", ['["not", "a", "dict"]', "{}", '{"machine_id": ""}', "not json"])
def test_read_machine_id_ignores_invalid_files(tmp_path: Path, contents: str) -> None:
    path = tmp_path / "machine-id.json"
    path.write_text(contents, encoding="utf-8")
    assert read_machine_id(path) is None
