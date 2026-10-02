from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import gsafe.crypto
import gsafe.paths

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_dir = tmp_path / "user-cache"
    data_dir = tmp_path / "user-data"
    monkeypatch.setattr(gsafe.paths, "user_cache_path", lambda *args, **kwargs: cache_dir)
    monkeypatch.setattr(gsafe.paths, "user_data_path", lambda *args, **kwargs: data_dir)
    git_config_path = tmp_path / "gitconfig"
    git_config_path.write_text(
        "[user]\n\tname = GSafe Tests\n\temail = tests@example.invalid\n"
        "[init]\n\tdefaultBranch = main\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(git_config_path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr(gsafe.crypto, "ARGON2_TIME_COST", 1)
    monkeypatch.setattr(gsafe.crypto, "ARGON2_MEMORY_COST", 8)
    monkeypatch.setattr(gsafe.crypto, "ARGON2_PARALLELISM", 1)
    monkeypatch.chdir(tmp_path)


def git(*arguments: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=str(cwd) if cwd else None,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def commit_file(repo_path: Path, name: str, contents: str) -> str:
    (repo_path / name).write_text(contents, encoding="utf-8")
    git("add", name, cwd=repo_path)
    git("commit", "-m", f"Add {name}", cwd=repo_path)
    return git("rev-parse", "HEAD", cwd=repo_path)


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "project"
    git("init", str(repo_path))
    commit_file(repo_path, "README.md", "hello\n")
    return repo_path
