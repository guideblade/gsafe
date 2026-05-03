from __future__ import annotations

import os
import subprocess
from pathlib import Path

from gsafe.errors import GSafeError


def run_git(
    arguments: list[str],
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = ["git", *arguments]
    process_environment = os.environ.copy()
    if environment:
        process_environment.update(environment)
    try:
        return subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            env=process_environment,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as error:
        raise GSafeError("git is not installed or not available in PATH.") from error
    except subprocess.CalledProcessError as error:
        stderr = error.stderr.strip() or error.stdout.strip() or "unknown git error"
        raise GSafeError(f"Git command failed: {' '.join(command)}\n{stderr}") from error


def run_git_check(arguments: list[str], cwd: Path | None = None) -> int:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        ).returncode
    except FileNotFoundError as error:
        raise GSafeError("git is not installed or not available in PATH.") from error


def validate_git_repo(repo_path: Path) -> None:
    if not repo_path.exists() or not repo_path.is_dir():
        raise GSafeError(f"Git repo path is not a directory: {repo_path}")
    run_git(["-C", str(repo_path), "rev-parse", "--git-dir"])


def validate_bare_repo(repo_path: Path) -> None:
    if not repo_path.exists() or not repo_path.is_dir():
        raise GSafeError(f"Remote path is not a directory: {repo_path}")
    result = run_git(["-C", str(repo_path), "rev-parse", "--is-bare-repository"])
    if result.stdout.strip() != "true":
        raise GSafeError(f"Remote path is not a bare Git repo: {repo_path}")


def is_bare_repo(repo_path: Path) -> bool:
    if not repo_path.exists() or not repo_path.is_dir():
        return False
    try:
        result = run_git(["-C", str(repo_path), "rev-parse", "--is-bare-repository"])
    except GSafeError:
        return False
    return result.stdout.strip() == "true"


def is_repo_with_refs(repo_path: Path) -> bool:
    result_code = run_git_check(["-C", str(repo_path), "show-ref", "--head", "--quiet"])
    if result_code == 0:
        return True
    if result_code == 1:
        return False
    raise GSafeError(f"Git command failed: git -C {repo_path} show-ref --head --quiet")


def read_head_contents(repo_path: Path) -> str | None:
    head_path = repo_path / "HEAD"
    if not head_path.exists():
        return None
    return head_path.read_text(encoding="utf-8")


def write_head_contents(repo_path: Path, head_contents: str | None) -> None:
    if head_contents is not None:
        (repo_path / "HEAD").write_text(head_contents, encoding="utf-8")


def parse_head_ref(head_contents: str) -> str | None:
    prefix = "ref: "
    if not head_contents.startswith(prefix):
        return None
    return head_contents[len(prefix) :].strip()


def is_ref_present(repo_path: Path, reference_name: str) -> bool:
    result_code = run_git_check(["-C", str(repo_path), "show-ref", "--verify", "--quiet", reference_name])
    if result_code == 0:
        return True
    if result_code == 1:
        return False
    raise GSafeError(f"Git command failed: git -C {repo_path} show-ref --verify --quiet {reference_name}")


def list_head_refs(repo_path: Path) -> list[str]:
    result = run_git(["-C", str(repo_path), "for-each-ref", "--format=%(refname)", "refs/heads"])
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def manifest_head_contents_for_repo(repo_path: Path) -> str | None:
    head_contents = read_head_contents(repo_path)
    if head_contents is None:
        return None
    head_ref = parse_head_ref(head_contents)
    if head_ref is None or is_ref_present(repo_path, head_ref):
        return head_contents
    head_refs = list_head_refs(repo_path)
    if len(head_refs) == 1:
        return f"ref: {head_refs[0]}\n"
    return head_contents


def list_refs(repo_path: Path) -> dict[str, str]:
    result = run_git(["-C", str(repo_path), "for-each-ref", "--format=%(refname) %(objectname)", "refs"])
    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        reference_name, object_name = line.strip().split(" ", 1)
        refs[reference_name] = object_name
    return refs


def has_remotes(repo_path: Path) -> bool:
    result = run_git(["-C", str(repo_path), "remote"])
    return any(line.strip() for line in result.stdout.splitlines())


def object_type(repo_path: Path, object_name: str) -> str | None:
    result_code = run_git_check(["-C", str(repo_path), "cat-file", "-e", object_name])
    if result_code != 0:
        return None
    result = run_git(["-C", str(repo_path), "cat-file", "-t", object_name])
    return result.stdout.strip()


def is_ancestor(repo_path: Path, ancestor: str, descendant: str) -> bool:
    result_code = run_git_check(["-C", str(repo_path), "merge-base", "--is-ancestor", ancestor, descendant])
    return result_code == 0


def ensure_fast_forwardable(old_repo_path: Path, new_repo_path: Path) -> None:
    old_refs = list_refs(old_repo_path)
    new_refs = list_refs(new_repo_path)
    for reference_name, old_object in old_refs.items():
        new_object = new_refs.get(reference_name)
        if new_object is None:
            raise GSafeError(f"Lock would delete Git ref: {reference_name}")
        if old_object == new_object:
            continue
        if reference_name.startswith("refs/tags/"):
            raise GSafeError(f"Lock would rewrite Git tag: {reference_name}")
        if object_type(new_repo_path, old_object) != "commit" or object_type(new_repo_path, new_object) != "commit":
            raise GSafeError(f"Lock would rewrite non-commit Git ref: {reference_name}")
        if not is_ancestor(new_repo_path, old_object, new_object):
            raise GSafeError(f"Lock would not fast-forward Git ref: {reference_name}")


def is_git_lfs_available() -> bool:
    return run_git_check(["lfs", "version"]) == 0
