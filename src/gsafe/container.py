from __future__ import annotations

import json
import tempfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from gsafe.crypto import decrypt_bytes, encrypt_bytes
from gsafe.errors import GSafeError
from gsafe.files import (
    atomic_write_bytes,
    copy_path_strict,
    create_tar_gz_from_dir,
    ensure_parent_dir,
    extract_tar_gz_bytes,
    is_path_within,
    resolve_path,
    safe_rmtree,
)
from gsafe.git import (
    ensure_fast_forwardable,
    has_remotes,
    is_git_lfs_available,
    run_git,
    validate_bare_repo,
    validate_git_repo,
)
from gsafe.paths import default_origin_path, known_local_machine_id, local_machine_id
from gsafe.payload import (
    DEFAULT_BRANCH,
    UNLOCK_TOKEN_NAME,
    load_manifest,
    restore_repo_from_payload,
    snapshot_repo_to_payload,
    write_manifest,
)

COMMIT_ENVIRONMENT = {
    "GIT_AUTHOR_NAME": "GSafe",
    "GIT_AUTHOR_EMAIL": "gsafe@example.invalid",
    "GIT_COMMITTER_NAME": "GSafe",
    "GIT_COMMITTER_EMAIL": "gsafe@example.invalid",
}
DEFAULT_CONTAINER_EXTENSION = ".gsf"
VALID_CONTAINER_EXTENSIONS = frozenset({".gsf", ".gsafe"})


@dataclass(frozen=True)
class ContainerStatus:
    container_path: Path
    state: str
    version: int
    default_origin_path: Path
    origin_path: str | None
    is_origin_present: bool | None
    is_unlocked_by_this_machine: bool | None
    is_bundle_present: bool
    is_repo_metadata_present: bool
    head_contents: str | None


def read_container_payload(container_path: Path, password: str, parent_dir: Path) -> Path:
    if not container_path.is_file():
        raise GSafeError(f"Container does not exist: {container_path}")
    payload_dir = parent_dir / "payload"
    extract_tar_gz_bytes(decrypt_bytes(container_path.read_bytes(), password), payload_dir)
    return payload_dir


def write_container_payload(container_path: Path, payload_dir: Path, password: str) -> None:
    atomic_write_bytes(container_path, encrypt_bytes(create_tar_gz_from_dir(payload_dir), password))


def source_git_dir(repo_path: Path) -> Path:
    result = run_git(["-C", str(repo_path), "rev-parse", "--path-format=absolute", "--git-dir"])
    return Path(result.stdout.strip())


def import_lfs_objects(source_repo_path: Path, bare_repo_path: Path) -> None:
    git_dir = source_git_dir(source_repo_path)
    source_lfs_dir = git_dir / "lfs"
    if not source_lfs_dir.exists():
        return
    if not is_git_lfs_available():
        raise GSafeError("git-lfs is required to import LFS objects from this repository.")
    if has_remotes(source_repo_path):
        run_git(["-C", str(source_repo_path), "lfs", "fetch", "--all"])
    destination_lfs_dir = bare_repo_path / "lfs"
    if destination_lfs_dir.exists():
        safe_rmtree(destination_lfs_dir)
    copy_path_strict(source_lfs_dir, destination_lfs_dir)


def create_empty_bare_repo(bare_repo_path: Path, workspace_path: Path) -> None:
    worktree_path = workspace_path / "empty-worktree"
    run_git(["init", str(worktree_path)])
    (worktree_path / ".gitignore").write_text("*\n", encoding="utf-8")
    run_git(["-C", str(worktree_path), "add", "-f", ".gitignore"])
    run_git(["-C", str(worktree_path), "commit", "-m", "Initial commit"], environment=COMMIT_ENVIRONMENT)
    run_git(["-C", str(worktree_path), "branch", "-M", DEFAULT_BRANCH])
    run_git(["init", "--bare", str(bare_repo_path)])
    run_git(["-C", str(worktree_path), "remote", "add", "origin", str(bare_repo_path)])
    run_git(["-C", str(worktree_path), "push", "origin", DEFAULT_BRANCH])
    run_git(["-C", str(bare_repo_path), "symbolic-ref", "HEAD", f"refs/heads/{DEFAULT_BRANCH}"])


def create_source_bare_repo(source_repo_path: Path, bare_repo_path: Path) -> None:
    validate_git_repo(source_repo_path)
    run_git(["clone", "--mirror", str(source_repo_path), str(bare_repo_path)])
    import_lfs_objects(source_repo_path, bare_repo_path)


def default_repo_path() -> Path:
    try:
        result = run_git(["-C", str(Path.cwd()), "rev-parse", "--show-toplevel"])
    except GSafeError as error:
        raise GSafeError("Use --path-repo, run inside a Git repository, or use --empty.") from error
    return Path(result.stdout.strip())


def resolve_output_container(path_repo: Path | None, path_output: Path | None, name: str | None) -> Path:
    if path_output and path_output.suffix and name is None:
        return resolve_path(path_output)
    if name is None:
        if path_repo is None:
            name = f"empty{DEFAULT_CONTAINER_EXTENSION}"
        else:
            name = f"{resolve_path(path_repo).name}{DEFAULT_CONTAINER_EXTENSION}"
    if not Path(name).suffix:
        name = f"{name}{DEFAULT_CONTAINER_EXTENSION}"
    if path_output is None:
        output_dir = Path.cwd() if path_repo is None else resolve_path(path_repo).parent
    else:
        output_dir = resolve_path(path_output)
    return output_dir / name


def resolve_init_request(
    path_repo: Path | None,
    path_output: Path | None,
    name: str | None,
    is_empty: bool,
) -> tuple[Path | None, Path]:
    if is_empty and path_repo is not None:
        raise GSafeError("--empty cannot be used with --path-repo.")
    effective_path_repo = None if is_empty else path_repo or default_repo_path()
    return effective_path_repo, resolve_output_container(effective_path_repo, path_output, name)


def init_container(
    path_repo: Path | None,
    path_output: Path | None,
    name: str | None,
    password: str,
    is_empty: bool,
) -> Path:
    effective_path_repo, container_path = resolve_init_request(path_repo, path_output, name, is_empty)
    if container_path.exists():
        raise GSafeError(f"Container already exists: {container_path}")
    ensure_parent_dir(container_path)
    with tempfile.TemporaryDirectory(prefix="gsafe-init-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        bare_repo_path = temp_dir / "repo.git"
        payload_dir = temp_dir / "payload"
        payload_dir.mkdir(parents=True, exist_ok=True)
        if is_empty:
            create_empty_bare_repo(bare_repo_path, temp_dir)
        else:
            create_source_bare_repo(resolve_path(effective_path_repo), bare_repo_path)
        snapshot_repo_to_payload(bare_repo_path, payload_dir, "locked")
        write_container_payload(container_path, payload_dir, password)
    return container_path


def unlock_container(container_path: Path, origin_path: Path | None, password: str, is_force: bool) -> Path:
    resolved_container_path = resolve_path(container_path)
    resolved_origin_path = resolve_path(origin_path) if origin_path else default_origin_path(resolved_container_path)
    if is_path_within(resolved_container_path, resolved_origin_path):
        raise GSafeError(f"Origin path must not contain the container file: {resolved_origin_path}")
    resolved_origin_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gsafe-unlock-", dir=str(resolved_origin_path.parent)) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        payload_dir = read_container_payload(resolved_container_path, password, temp_dir)
        manifest = load_manifest(payload_dir)
        if manifest.state == "unlocked" and not is_force:
            raise GSafeError(f"Container is already unlocked at: {manifest.origin_path}")
        remote_repo_path = temp_dir / "remote.git"
        restore_repo_from_payload(payload_dir, remote_repo_path)
        token = uuid.uuid4().hex
        token_payload = {
            "container_path": str(resolved_container_path),
            "machine_id": local_machine_id(),
            "origin_path": str(resolved_origin_path),
            "unlock_token": token,
        }
        (remote_repo_path / UNLOCK_TOKEN_NAME).write_text(json.dumps(token_payload, sort_keys=True), encoding="utf-8")
        if resolved_origin_path.exists():
            safe_rmtree(resolved_origin_path)
        ensure_parent_dir(resolved_origin_path)
        remote_repo_path.replace(resolved_origin_path)
        write_manifest(
            payload_dir,
            replace(
                manifest,
                state="unlocked",
                origin_path=str(resolved_origin_path),
                machine_id=token_payload["machine_id"],
                unlock_token=token,
            ),
        )
        write_container_payload(resolved_container_path, payload_dir, password)
    return resolved_origin_path


def read_unlock_token(remote_repo_path: Path) -> dict[str, object]:
    token_path = remote_repo_path / UNLOCK_TOKEN_NAME
    if not token_path.exists():
        raise GSafeError(f"Unlocked remote is missing {UNLOCK_TOKEN_NAME}: {remote_repo_path}")
    try:
        data = json.loads(token_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise GSafeError(f"Unlocked remote token is invalid: {token_path}") from error
    if not isinstance(data, dict):
        raise GSafeError(f"Unlocked remote token is invalid: {token_path}")
    return data


def lock_container(container_path: Path, origin_path: Path | None, password: str, is_force: bool) -> Path:
    resolved_container_path = resolve_path(container_path)
    with tempfile.TemporaryDirectory(prefix="gsafe-lock-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        payload_dir = read_container_payload(resolved_container_path, password, temp_dir)
        manifest = load_manifest(payload_dir)
        if manifest.state == "locked" and not is_force:
            raise GSafeError("Container is already locked.")
        resolved_origin_path = resolve_path(origin_path) if origin_path else (
            Path(manifest.origin_path) if manifest.origin_path else default_origin_path(resolved_container_path)
        )
        validate_bare_repo(resolved_origin_path)
        if is_path_within(resolved_container_path, resolved_origin_path):
            raise GSafeError(f"Container path must not be inside the bare repo being locked: {resolved_container_path}")
        if manifest.state == "unlocked":
            token_payload = read_unlock_token(resolved_origin_path)
            if token_payload.get("machine_id") != local_machine_id():
                raise GSafeError("Only the machine that unlocked this container can lock it.")
            if token_payload.get("unlock_token") != manifest.unlock_token:
                raise GSafeError("Unlocked remote token does not match this container.")
            if str(resolve_path(Path(str(token_payload.get("origin_path"))))) != str(resolved_origin_path):
                raise GSafeError("Unlocked remote path does not match this container.")
        previous_repo_path = temp_dir / "previous.git"
        restore_repo_from_payload(payload_dir, previous_repo_path)
        if not is_force:
            ensure_fast_forwardable(previous_repo_path, resolved_origin_path)
        fresh_payload_dir = temp_dir / "fresh-payload"
        fresh_payload_dir.mkdir(parents=True, exist_ok=True)
        snapshot_repo_to_payload(resolved_origin_path, fresh_payload_dir, "locked")
        write_container_payload(resolved_container_path, fresh_payload_dir, password)
    safe_rmtree(resolved_origin_path)
    return resolved_container_path


def recover_container(container_path: Path, password: str) -> Path:
    resolved_container_path = resolve_path(container_path)
    with tempfile.TemporaryDirectory(prefix="gsafe-recover-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        payload_dir = read_container_payload(resolved_container_path, password, temp_dir)
        manifest = load_manifest(payload_dir)
        if manifest.state == "locked":
            raise GSafeError("Container is already locked.")
        write_manifest(
            payload_dir,
            replace(
                manifest,
                state="locked",
                origin_path=None,
                machine_id=None,
                unlock_token=None,
            ),
        )
        write_container_payload(resolved_container_path, payload_dir, password)
    return resolved_container_path


def change_container_password(container_path: Path, current_password: str, new_password: str) -> Path:
    if current_password == new_password:
        raise GSafeError("New password must be different from the current password.")
    resolved_container_path = resolve_path(container_path)
    with tempfile.TemporaryDirectory(prefix="gsafe-password-") as temp_dir_name:
        payload_dir = read_container_payload(resolved_container_path, current_password, Path(temp_dir_name))
        write_container_payload(resolved_container_path, payload_dir, new_password)
    return resolved_container_path


def get_container_status(container_path: Path, password: str) -> ContainerStatus:
    resolved_container_path = resolve_path(container_path)
    with tempfile.TemporaryDirectory(prefix="gsafe-status-") as temp_dir_name:
        payload_dir = read_container_payload(resolved_container_path, password, Path(temp_dir_name))
        manifest = load_manifest(payload_dir)
    local_machine = known_local_machine_id()
    is_unlocked_by_this_machine = None
    if manifest.machine_id and local_machine:
        is_unlocked_by_this_machine = manifest.machine_id == local_machine
    is_origin_present = None
    if manifest.origin_path and is_unlocked_by_this_machine is not False:
        is_origin_present = Path(manifest.origin_path).exists()
    return ContainerStatus(
        container_path=resolved_container_path,
        state=manifest.state,
        version=manifest.version,
        default_origin_path=default_origin_path(resolved_container_path),
        origin_path=manifest.origin_path,
        is_origin_present=is_origin_present,
        is_unlocked_by_this_machine=is_unlocked_by_this_machine,
        is_bundle_present=manifest.is_bundle_present,
        is_repo_metadata_present=manifest.is_repo_metadata_present,
        head_contents=manifest.head_contents,
    )
