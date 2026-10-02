from __future__ import annotations

import getpass
from pathlib import Path

import pytest

from conftest import PASSWORD
from gsafe.cli import main, normalize_argv


@pytest.fixture
def password_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": PASSWORD)


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], []),
        (["repo.gsf"], ["status", "repo.gsf"]),
        (["repo.GSAFE"], ["status", "repo.GSAFE"]),
        (["lock", "repo.gsf"], ["lock", "repo.gsf"]),
        (["--version"], ["--version"]),
        (["missing.txt"], ["missing.txt"]),
    ],
)
def test_normalize_argv(argv: list[str], expected: list[str]) -> None:
    assert normalize_argv(argv) == expected


def test_cli_workflow(
    tmp_path: Path,
    source_repo: Path,
    password_prompt: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["init", "--path-repo", str(source_repo)]) == 0
    container_path = tmp_path / "project.gsf"
    assert container_path.is_file()
    assert main([str(container_path)]) == 0
    assert "State: locked" in capsys.readouterr().out
    assert main(["unlock", str(container_path)]) == 0
    assert "Unlocked bare remote at:" in capsys.readouterr().out
    assert main(["lock", "--path-gsafe", str(container_path)]) == 0
    assert "Locked container:" in capsys.readouterr().out


def test_cli_rejects_nonstandard_extension(
    source_repo: Path,
    password_prompt: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["init", "--path-repo", str(source_repo), "--name", "repo.zip"]) == 1
    assert "Container extension must be one of" in capsys.readouterr().err


def test_cli_unlock_checks_origin_before_password(
    tmp_path: Path,
    source_repo: Path,
    password_prompt: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["init", "--path-repo", str(source_repo)]) == 0
    unrelated_dir = tmp_path / "documents"
    unrelated_dir.mkdir()
    (unrelated_dir / "keep.txt").write_text("keep", encoding="utf-8")

    def unexpected_prompt(prompt: str = "") -> str:
        raise AssertionError("password should not be requested")

    monkeypatch.setattr(getpass, "getpass", unexpected_prompt)
    assert main(["unlock", "project.gsf", "--path-origin", str(unrelated_dir), "--force"]) == 1
    assert "not an unlocked GSafe remote" in capsys.readouterr().err
    assert (unrelated_dir / "keep.txt").exists()


@pytest.mark.parametrize(("exception", "exit_code"), [(KeyboardInterrupt, 130), (EOFError, 1)])
def test_cli_handles_interrupted_prompt(
    source_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exception: type[BaseException],
    exit_code: int,
) -> None:
    def interrupted_prompt(prompt: str = "") -> str:
        raise exception

    monkeypatch.setattr(getpass, "getpass", interrupted_prompt)
    assert main(["init", "--path-repo", str(source_repo)]) == exit_code
    assert "Traceback" not in capsys.readouterr().err
