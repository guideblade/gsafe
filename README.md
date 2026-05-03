# GSafe

[![PyPI version](https://img.shields.io/pypi/v/gsafe.svg)](https://pypi.org/project/gsafe/)
[![Python versions](https://img.shields.io/pypi/pyversions/gsafe.svg)](https://pypi.org/project/gsafe/)

GSafe stores a Git bare remote as an encrypted `.gsf` container. It allows you to safely store your repository on third party services like Google Drive, and work with it almost as effortlessly as if you were hosting on GitHub. The storage provider cannot read your data, as the unencrypted remote exists only on your device while the container is unlocked.
This tool is not really meant for collaboration. Unlock your containers only on one machine at a time and don't forget to wait until the sync finishes before unlocking it on a second machine.

## Project Status

GSafe is alpha software. The container format and command behavior may still change before a stable `1.0.0` release. Keep independent backups of important repositories.

## Requirements

- Python 3.10 or newer
- Git available in `PATH`
- Git LFS available in `PATH` if the repository uses LFS

The `gsafe` command is a Python console script. It becomes available after the package is installed with a Python package installer. If the Python scripts directory is not in `PATH`, use `python -m gsafe` instead.

## Install

### From PyPI

For CLI usage, `pipx` is recommended because it installs `gsafe` into an isolated environment and exposes the command on your `PATH`:

```bash
pipx install gsafe
```

If you do not use `pipx`, install with `pip`:

```bash
python3 -m pip install gsafe
```

Check that it works:

```bash
gsafe --version
```

### macOS (recommended)

Python on macOS, especially when installed with Homebrew, may block global `pip` installs with an `externally-managed-environment` error.

Install with `pipx`:

```bash
brew install pipx
pipx install gsafe
```

Check that it works:

```bash
gsafe --version
```

### From this repository

```bash
python3 -m pip install git+https://github.com/guideblade/gsafe.git
```

### For local development

Use a virtual environment so the editable install points to this repository without touching the system Python installation:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

Check that it works:

```bash
gsafe --version
```

If `gsafe` is not found:

```bash
python3 -m gsafe --version
```

## Platform Notes

macOS and Linux usually expose `python3` and install command-line scripts into a user or virtual environment scripts directory. If `gsafe` is not found after install, either activate the virtual environment you installed it into or use `python3 -m gsafe`.

Windows requires Python 3.10 or newer and Git for Windows. In Git Bash, use `python` or `py -m pip` depending on how Python was installed. If `gsafe.exe` is not in `PATH`, use `python -m gsafe` or add Python's `Scripts` directory to `PATH`.

## Use

```bash
cd ~/path/repo
gsafe init
gsafe status ~/path/repo.gsf
gsafe unlock ~/path/repo.gsf
gsafe lock ~/path/repo.gsf
gsafe change-password ~/path/repo.gsf
```

`gsafe init` uses the current Git repository. You can also pass `--path-repo ~/path/repo` from another directory.
GSafe uses `.gsf` for new containers. It also accepts `.gsafe`. Other extensions require `--force`.
Passing a container path without a command is shorthand for `gsafe status ~/path/repo.gsf`.
`gsafe unlock` and `gsafe lock` also accept `--path-gsafe ~/path/repo.gsf` if you prefer named options.
`gsafe status` asks for the password and prints encrypted container metadata.
`gsafe change-password` asks for the current password once and the new password twice.

## Recovery

If a machine unlocks a container and then becomes unavailable before `gsafe lock`, the container remains marked as unlocked. Recover it from the last encrypted state:

```bash
gsafe recover ~/path/repo.gsf
```

Recovery asks you to type `RECOVER`. It clears the stale unlock marker, but it cannot save commits that existed only in the old unlocked remote.
