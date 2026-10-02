from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from gsafe import __version__
from gsafe.container import (
    VALID_CONTAINER_EXTENSIONS,
    ContainerStatus,
    change_container_password,
    ensure_origin_path_available,
    get_container_status,
    init_container,
    lock_container,
    recover_container,
    resolve_init_request,
    unlock_container,
)
from gsafe.errors import GSafeError
from gsafe.files import is_path_within, resolve_path
from gsafe.git import validate_bare_repo, validate_git_repo
from gsafe.paths import default_origin_path

COMMAND_NAMES = frozenset(
    {
        "init",
        "unlock",
        "lock",
        "recover",
        "change-password",
        "password",
        "status",
    }
)


def prompt_password(is_confirmation_required: bool) -> str:
    password = getpass.getpass("Password: ")
    if not password:
        raise GSafeError("Password must not be empty.")
    if is_confirmation_required:
        confirmed_password = getpass.getpass("Confirm password: ")
        if password != confirmed_password:
            raise GSafeError("Passwords do not match.")
    return password


def prompt_password_change() -> tuple[str, str]:
    current_password = getpass.getpass("Current password: ")
    if not current_password:
        raise GSafeError("Current password must not be empty.")
    new_password = getpass.getpass("New password: ")
    if not new_password:
        raise GSafeError("New password must not be empty.")
    confirmed_password = getpass.getpass("Confirm new password: ")
    if new_password != confirmed_password:
        raise GSafeError("Passwords do not match.")
    return current_password, new_password


def confirm_recovery() -> None:
    print("This container is marked as unlocked by another session.")
    print("Recovery will discard that stale unlock marker.")
    print("Any commits that existed only in the old unlocked remote will not be saved.")
    confirmation = input("Type RECOVER to continue: ")
    if confirmation != "RECOVER":
        raise GSafeError("Recovery cancelled.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gsafe",
        description="Encrypt a Git bare remote into a portable GSafe container.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a GSafe container.")
    init_parser.add_argument("--path-repo", type=Path, help="Git repository to import.")
    init_parser.add_argument("--path-output", type=Path, help="Directory or container file to write.")
    init_parser.add_argument("--name", help="Container file name.")
    init_parser.add_argument("--empty", action="store_true", help="Create an empty repository container.")
    init_parser.add_argument("--force", action="store_true", help="Allow a non-standard container extension.")

    unlock_parser = subparsers.add_parser("unlock", help="Unlock a container into a local bare remote.")
    unlock_parser.add_argument("container", nargs="?", type=Path, help="Container to unlock.")
    unlock_parser.add_argument("--path-gsafe", type=Path, help="Container to unlock.")
    unlock_parser.add_argument("--path-origin", type=Path, help="Bare remote output path.")
    unlock_parser.add_argument(
        "--force",
        action="store_true",
        help="Unlock even when marked as unlocked, replacing a leftover unlocked remote of this container.",
    )
    unlock_parser.set_defaults(command_parser=unlock_parser)

    lock_parser = subparsers.add_parser("lock", help="Lock an unlocked bare remote back into the container.")
    lock_parser.add_argument("container", nargs="?", type=Path, help="Container to lock.")
    lock_parser.add_argument("--path-gsafe", type=Path, help="Container to lock.")
    lock_parser.add_argument("--path-origin", type=Path, help="Bare remote path.")
    lock_parser.add_argument(
        "--force",
        action="store_true",
        help="Lock even when marked as locked, or when refs were deleted or rewritten.",
    )
    lock_parser.set_defaults(command_parser=lock_parser)

    recover_parser = subparsers.add_parser("recover", help="Recover a stale unlocked container.")
    recover_parser.add_argument("container", nargs="?", type=Path, help="Container to recover.")
    recover_parser.add_argument("--path-gsafe", type=Path, help="Container to recover.")
    recover_parser.add_argument("--force", action="store_true", help="Allow a non-standard container extension.")
    recover_parser.set_defaults(command_parser=recover_parser)

    password_parser = subparsers.add_parser(
        "change-password",
        aliases=["password"],
        help="Change a container password.",
    )
    password_parser.add_argument("container", nargs="?", type=Path, help="Container to update.")
    password_parser.add_argument("--path-gsafe", type=Path, help="Container to update.")
    password_parser.add_argument("--force", action="store_true", help="Allow a non-standard container extension.")
    password_parser.set_defaults(command_parser=password_parser)

    status_parser = subparsers.add_parser("status", help="Show container status.")
    status_parser.add_argument("container", nargs="?", type=Path, help="Container to inspect.")
    status_parser.add_argument("--path-gsafe", type=Path, help="Container to inspect.")
    status_parser.add_argument("--force", action="store_true", help="Allow a non-standard container extension.")
    status_parser.set_defaults(command_parser=status_parser)

    return parser


def normalize_argv(argv: list[str] | None) -> list[str] | None:
    if argv is None:
        return None
    if not argv:
        return argv
    first_argument = argv[0]
    if first_argument.startswith("-") or first_argument in COMMAND_NAMES:
        return argv
    first_path = Path(first_argument)
    if first_path.suffix.lower() in VALID_CONTAINER_EXTENSIONS or first_path.is_file():
        return ["status", *argv]
    return argv


def resolve_container_argument(args: argparse.Namespace) -> Path:
    parser = args.command_parser
    if args.container and args.path_gsafe:
        parser.error("pass the container either as an argument or as --path-gsafe, not both")
    container_path = args.container or args.path_gsafe
    if container_path is None:
        parser.error("container path is required")
    return container_path


def resolve_existing_container_argument(args: argparse.Namespace) -> Path:
    container_path = resolve_path(resolve_container_argument(args))
    if not container_path.is_file():
        raise GSafeError(f"Container does not exist: {container_path}")
    validate_container_extension(container_path, args.force)
    return container_path


def validate_container_extension(container_path: Path, is_force: bool) -> None:
    if container_path.suffix.lower() in VALID_CONTAINER_EXTENSIONS:
        return
    if is_force:
        return
    valid_extensions = ", ".join(sorted(VALID_CONTAINER_EXTENSIONS))
    raise GSafeError(
        f"Container extension must be one of: {valid_extensions}. "
        "Use --force to try this file anyway."
    )


def validate_init_before_password(args: argparse.Namespace) -> Path:
    effective_path_repo, container_path = resolve_init_request(
        args.path_repo,
        args.path_output,
        args.name,
        args.empty,
    )
    if container_path.exists():
        raise GSafeError(f"Container already exists: {container_path}")
    validate_container_extension(container_path, args.force)
    if effective_path_repo is not None:
        validate_git_repo(resolve_path(effective_path_repo))
    return container_path


def validate_unlock_before_password(container_path: Path, origin_path: Path | None) -> None:
    resolved_origin_path = resolve_path(origin_path) if origin_path else default_origin_path(container_path)
    if is_path_within(container_path, resolved_origin_path):
        raise GSafeError(f"Origin path must not contain the container file: {resolved_origin_path}")
    ensure_origin_path_available(resolved_origin_path)


def validate_lock_before_password(origin_path: Path | None) -> None:
    if origin_path is not None:
        validate_bare_repo(resolve_path(origin_path))


def print_unlock_commands(origin_path: Path) -> None:
    print(f"Unlocked bare remote at: {origin_path}")
    print(f"Git remote command: git remote add gsafe \"{origin_path}\"")
    print(f"Git clone command: git clone \"{origin_path}\"")


def format_optional_bool(value: bool | None) -> str:
    if value is None:
        return "unknown"
    if value:
        return "yes"
    return "no"


def print_status(status: ContainerStatus) -> None:
    print(f"Container: {status.container_path}")
    print(f"State: {status.state}")
    print(f"Payload version: {status.version}")
    print(f"Default origin: {status.default_origin_path}")
    if status.origin_path:
        print(f"Unlocked origin: {status.origin_path}")
        print(f"Unlocked origin exists: {format_optional_bool(status.is_origin_present)}")
        print(f"Unlocked by this machine: {format_optional_bool(status.is_unlocked_by_this_machine)}")
    print(f"Contains Git refs: {format_optional_bool(status.is_bundle_present)}")
    print(f"Contains Git metadata: {format_optional_bool(status.is_repo_metadata_present)}")
    if status.head_contents:
        print(f"HEAD: {status.head_contents.strip()}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(normalize_argv(raw_argv))
    try:
        if args.command == "init":
            validate_init_before_password(args)
            password = prompt_password(is_confirmation_required=True)
            container_path = init_container(args.path_repo, args.path_output, args.name, password, args.empty)
            print(f"Initialized container: {container_path}")
            print(f"Default unlock path: {default_origin_path(container_path)}")
            return 0
        if args.command == "unlock":
            container_path = resolve_existing_container_argument(args)
            validate_unlock_before_password(container_path, args.path_origin)
            password = prompt_password(is_confirmation_required=False)
            origin_path = unlock_container(container_path, args.path_origin, password, args.force)
            print_unlock_commands(origin_path)
            return 0
        if args.command == "lock":
            container_path = resolve_existing_container_argument(args)
            validate_lock_before_password(args.path_origin)
            password = prompt_password(is_confirmation_required=False)
            locked_container_path = lock_container(container_path, args.path_origin, password, args.force)
            print(f"Locked container: {locked_container_path}")
            return 0
        if args.command == "recover":
            container_path = resolve_existing_container_argument(args)
            password = prompt_password(is_confirmation_required=False)
            status = get_container_status(container_path, password)
            if status.state == "locked":
                raise GSafeError("Container is already locked.")
            confirm_recovery()
            recovered_container_path = recover_container(container_path, password)
            print(f"Recovered container: {recovered_container_path}")
            return 0
        if args.command in {"change-password", "password"}:
            container_path = resolve_existing_container_argument(args)
            current_password, new_password = prompt_password_change()
            changed_container_path = change_container_password(
                container_path,
                current_password,
                new_password,
            )
            print(f"Changed password: {changed_container_path}")
            return 0
        if args.command == "status":
            container_path = resolve_existing_container_argument(args)
            password = prompt_password(is_confirmation_required=False)
            print_status(get_container_status(container_path, password))
            return 0
        parser.error(f"Unknown command: {args.command}")
    except (GSafeError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except EOFError:
        print("\nError: Input ended before a required answer was given.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
