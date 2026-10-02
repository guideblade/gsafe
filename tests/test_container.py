from __future__ import annotations

from pathlib import Path

import pytest

import gsafe.container
from conftest import PASSWORD, commit_file, git
from gsafe.container import (
    change_container_password,
    get_container_status,
    init_container,
    lock_container,
    recover_container,
    unlock_container,
)
from gsafe.errors import GSafeError
from gsafe.paths import default_origin_path


def clone_and_push(origin_path: Path, work_dir: Path, name: str, contents: str) -> str:
    git("clone", str(origin_path), str(work_dir))
    commit_id = commit_file(work_dir, name, contents)
    git("push", "origin", "HEAD", cwd=work_dir)
    return commit_id


def test_init_unlock_push_lock_round_trip(tmp_path: Path, source_repo: Path) -> None:
    container_path = init_container(source_repo, None, None, PASSWORD, False)
    assert container_path == tmp_path / "project.gsf"
    assert get_container_status(container_path, PASSWORD).state == "locked"

    origin_path = unlock_container(container_path, None, PASSWORD, False)
    assert origin_path == default_origin_path(container_path)
    status = get_container_status(container_path, PASSWORD)
    assert status.state == "unlocked"
    assert status.is_unlocked_by_this_machine is True
    assert status.is_origin_present is True

    pushed_commit = clone_and_push(origin_path, tmp_path / "work", "notes.txt", "pushed\n")
    lock_container(container_path, None, PASSWORD, False)
    assert not origin_path.exists()
    assert get_container_status(container_path, PASSWORD).state == "locked"

    origin_path = unlock_container(container_path, None, PASSWORD, False)
    assert git("-C", str(origin_path), "rev-parse", "refs/heads/main") == pushed_commit


def test_init_empty_container(tmp_path: Path) -> None:
    container_path = init_container(None, None, None, PASSWORD, True)
    assert container_path == tmp_path / "empty.gsf"
    origin_path = unlock_container(container_path, None, PASSWORD, False)
    assert git("-C", str(origin_path), "symbolic-ref", "HEAD") == "refs/heads/main"


def test_init_refuses_existing_container(source_repo: Path) -> None:
    init_container(source_repo, None, None, PASSWORD, False)
    with pytest.raises(GSafeError, match="already exists"):
        init_container(source_repo, None, None, PASSWORD, False)


def test_wrong_password_is_rejected(source_repo: Path) -> None:
    container_path = init_container(source_repo, None, None, PASSWORD, False)
    with pytest.raises(GSafeError, match="Wrong password"):
        get_container_status(container_path, "wrong")


def test_unlock_requires_force_when_already_unlocked(source_repo: Path) -> None:
    container_path = init_container(source_repo, None, None, PASSWORD, False)
    unlock_container(container_path, None, PASSWORD, False)
    with pytest.raises(GSafeError, match="already unlocked"):
        unlock_container(container_path, None, PASSWORD, False)
    unlock_container(container_path, None, PASSWORD, True)
    lock_container(container_path, None, PASSWORD, False)


def test_unlock_never_replaces_unrelated_directory(tmp_path: Path, source_repo: Path) -> None:
    container_path = init_container(source_repo, None, None, PASSWORD, False)
    unrelated_dir = tmp_path / "documents"
    unrelated_dir.mkdir()
    (unrelated_dir / "thesis.txt").write_text("precious", encoding="utf-8")
    for is_force in (False, True):
        with pytest.raises(GSafeError, match="not an unlocked remote of this container"):
            unlock_container(container_path, unrelated_dir, PASSWORD, is_force)
    assert (unrelated_dir / "thesis.txt").read_text(encoding="utf-8") == "precious"
    assert get_container_status(container_path, PASSWORD).state == "locked"


def test_unlock_never_replaces_another_containers_remote(tmp_path: Path, source_repo: Path) -> None:
    first_container = init_container(source_repo, None, "first", PASSWORD, False)
    second_container = init_container(source_repo, None, "second", PASSWORD, False)
    shared_origin = tmp_path / "shared.git"
    unlock_container(first_container, shared_origin, PASSWORD, False)
    with pytest.raises(GSafeError, match="not an unlocked remote of this container"):
        unlock_container(second_container, shared_origin, PASSWORD, True)


def test_unlock_into_empty_directory(tmp_path: Path, source_repo: Path) -> None:
    container_path = init_container(source_repo, None, None, PASSWORD, False)
    origin_path = tmp_path / "origin.git"
    origin_path.mkdir()
    assert unlock_container(container_path, origin_path, PASSWORD, False) == origin_path
    assert (origin_path / "HEAD").exists()


def test_recover_then_unlock_requires_force_to_replace_leftover(tmp_path: Path, source_repo: Path) -> None:
    container_path = init_container(source_repo, None, None, PASSWORD, False)
    origin_path = unlock_container(container_path, None, PASSWORD, False)
    clone_and_push(origin_path, tmp_path / "work", "unsaved.txt", "unsaved\n")
    recover_container(container_path, PASSWORD)
    assert get_container_status(container_path, PASSWORD).state == "locked"
    with pytest.raises(GSafeError, match="never locked into the container"):
        unlock_container(container_path, None, PASSWORD, False)
    unlock_container(container_path, None, PASSWORD, True)


def test_recover_refuses_locked_container(source_repo: Path) -> None:
    container_path = init_container(source_repo, None, None, PASSWORD, False)
    with pytest.raises(GSafeError, match="already locked"):
        recover_container(container_path, PASSWORD)


def test_lock_rejects_rewritten_history_without_force(tmp_path: Path, source_repo: Path) -> None:
    container_path = init_container(source_repo, None, None, PASSWORD, False)
    origin_path = unlock_container(container_path, None, PASSWORD, False)
    work_dir = tmp_path / "work"
    git("clone", str(origin_path), str(work_dir))
    git("commit", "--amend", "-m", "Rewritten", cwd=work_dir)
    git("push", "--force", "origin", "HEAD", cwd=work_dir)
    with pytest.raises(GSafeError, match="would not fast-forward"):
        lock_container(container_path, None, PASSWORD, False)
    assert origin_path.exists()
    lock_container(container_path, None, PASSWORD, True)
    assert not origin_path.exists()


def test_lock_rejects_deleted_ref_without_force(tmp_path: Path, source_repo: Path) -> None:
    git("branch", "feature", cwd=source_repo)
    container_path = init_container(source_repo, None, None, PASSWORD, False)
    origin_path = unlock_container(container_path, None, PASSWORD, False)
    git("-C", str(origin_path), "branch", "-D", "feature")
    with pytest.raises(GSafeError, match="would delete Git ref: refs/heads/feature"):
        lock_container(container_path, None, PASSWORD, False)


def test_lock_rejects_other_machine(monkeypatch: pytest.MonkeyPatch, source_repo: Path) -> None:
    container_path = init_container(source_repo, None, None, PASSWORD, False)
    unlock_container(container_path, None, PASSWORD, False)
    monkeypatch.setattr(gsafe.container, "local_machine_id", lambda: "another-machine")
    with pytest.raises(GSafeError, match="Only the machine that unlocked"):
        lock_container(container_path, None, PASSWORD, False)


def test_change_password(source_repo: Path) -> None:
    container_path = init_container(source_repo, None, None, PASSWORD, False)
    with pytest.raises(GSafeError, match="must be different"):
        change_container_password(container_path, PASSWORD, PASSWORD)
    change_container_password(container_path, PASSWORD, "new password")
    with pytest.raises(GSafeError, match="Wrong password"):
        get_container_status(container_path, PASSWORD)
    assert get_container_status(container_path, "new password").state == "locked"
