#!/usr/bin/env python3
"""Back up or restore Jellyfin's essential persistent data on Synology DSM."""

from __future__ import annotations

import argparse
import atexit
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path, PurePosixPath

ENV_FILE = Path(__file__).resolve().with_name(".env")


def load_env(path: Path) -> dict[str, str]:
    """Load a small dotenv file without requiring a third-party package."""
    if not path.is_file():
        raise RuntimeError(
            f"Configuration file not found: {path}. Copy .env.example to .env."
        )
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise RuntimeError(f"Invalid .env line {line_number}: expected KEY=VALUE")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise RuntimeError(f"Invalid .env key on line {line_number}: {key!r}")
        try:
            parsed = shlex.split(raw_value, comments=True, posix=True)
        except ValueError as error:
            raise RuntimeError(f"Invalid .env line {line_number}: {error}") from error
        if len(parsed) > 1:
            raise RuntimeError(
                f"Invalid .env value on line {line_number}: quote values with spaces"
            )
        values[key] = parsed[0] if parsed else ""
    return values


def required_setting(values: dict[str, str], name: str) -> str:
    value = os.environ.get(name, values.get(name, "")).strip()
    if not value:
        raise RuntimeError(f"Required configuration value is empty: {name}")
    return value


ENV = load_env(ENV_FILE)
APP_DATA_DIR = Path(required_setting(ENV, "APP_DATA_DIR")).expanduser()
BACKUP_DIR = Path(required_setting(ENV, "BACKUP_DIR")).expanduser()
LOG_FILE = Path(required_setting(ENV, "LOG_FILE")).expanduser()
ERROR_FILE = Path(required_setting(ENV, "ERROR_FILE")).expanduser()
PACKAGE_NAME = required_setting(ENV, "PACKAGE_NAME")
try:
    BACKUP_RETENTION = int(required_setting(ENV, "BACKUP_RETENTION"))
except ValueError as error:
    raise RuntimeError("BACKUP_RETENTION must be an integer") from error
PACKAGE_DIR = Path("/var/packages") / PACKAGE_NAME
PID_FILE = APP_DATA_DIR / "jellyfin.pid"

# Persistent server state only. Media files are NOT included and need separate backups.
BACKUP_PATHS = (
    Path("config"),
    Path("data/data"),
    Path("data/plugins"),
    Path("data/root"),
    Path("fonts/fonts.conf"),
)
# Exclude generated or redundant content nested inside an included directory.
EXCLUDED_PATHS = (
    Path("data/data/trickplay"),
    Path("data/data/backups"),
    Path("data/data/SQLiteBackups"),
)
# Report all omitted temporary/rebuildable paths in the backup log.
SKIPPED_PATHS = (
    "cache/", "log/ and jellyfin.log", "data/metadata/",
    "data/data/trickplay/", "data/data/backups/",
    "data/data/SQLiteBackups/", "data/transcodes/", "jellyfin.pid",
)
# Clear these after restore so stale cache/metadata cannot conflict with the database.
REBUILDABLE_DIRS = (Path("cache"), Path("data/metadata"), Path("data/transcodes"))
MANIFEST_NAME = PurePosixPath("jellyfin/backup-manifest.json")
BACKUP_FILENAME_RE = re.compile(
    r"^jellyfin-data_\d{4}-\d{2}-\d{2}_\d{6}_\d{6}\.tar\.gz$"
)


class TimestampedTee:
    """Write timestamped lines to a console stream and a log file."""

    def __init__(self, console, log_file):
        self.console = console
        self.log_file = log_file
        self._buffer = ""

    @property
    def encoding(self):
        return getattr(self.console, "encoding", "utf-8")

    def write(self, text: str) -> int:
        if not text:
            return 0
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._write_line(line, newline=True)
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._write_line(self._buffer, newline=False)
            self._buffer = ""
        self.console.flush()
        self.log_file.flush()

    def _write_line(self, line: str, newline: bool) -> None:
        suffix = "\n" if newline else ""
        stamped = f"{log_timestamp()} {line}{suffix}"
        self.console.write(stamped)
        self.log_file.write(stamped)


def log_timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S%z")


def setup_logging() -> None:
    LOG_FILE.expanduser().parent.mkdir(parents=True, exist_ok=True)
    ERROR_FILE.expanduser().parent.mkdir(parents=True, exist_ok=True)
    stdout_log = LOG_FILE.expanduser().open("a", encoding="utf-8")
    stderr_log = ERROR_FILE.expanduser().open("a", encoding="utf-8")
    sys.stdout = TimestampedTee(sys.stdout, stdout_log)
    sys.stderr = TimestampedTee(sys.stderr, stderr_log)
    atexit.register(sys.stdout.flush)
    atexit.register(sys.stderr.flush)


def run(*command: str) -> None:
    print("+", " ".join(command), flush=True)
    result = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, command, output=result.stdout, stderr=result.stderr,
        )


def package_version() -> str:
    result = subprocess.run(
        ("synopkg", "version", PACKAGE_NAME), check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return result.stdout.strip()


def ensure_package_installed() -> str:
    """Require Jellyfin to be installed before backup or disaster recovery."""
    if not PACKAGE_DIR.exists() or not APP_DATA_DIR.is_dir():
        raise RuntimeError(
            "Jellyfin must be installed before backup or restore; expected "
            f"{PACKAGE_DIR} and {APP_DATA_DIR}"
        )
    version = package_version()
    print(f"Installed Jellyfin package version: {version}")
    return version


def jellyfin_pid():
    """Return the PID only when it belongs to the expected Jellyfin service."""
    try:
        pid = int(PID_FILE.read_text(encoding="ascii").strip())
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
        if b"jellyfin" in command and b"--service" in command:
            return pid
    except (OSError, ValueError):
        pass
    return None


def wait_for_running(expected: bool, timeout: int = 60) -> None:
    """Verify the real process state instead of trusting an unprivileged status query."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (jellyfin_pid() is not None) == expected:
            return
        time.sleep(1)
    state = "start" if expected else "stop"
    raise RuntimeError(f"Jellyfin did not {state} within {timeout} seconds")


def stop_for_maintenance() -> bool:
    """Stop Jellyfin if needed and remember whether it should be restarted."""
    was_running = jellyfin_pid() is not None
    if was_running:
        run("synopkg", "stop", PACKAGE_NAME)
        wait_for_running(False)
        print("Verified that the Jellyfin process stopped.")
    else:
        print("Jellyfin was already stopped.")
    return was_running


def restart_if_needed(was_running: bool) -> None:
    """Restore the original running state and verify the new process."""
    if was_running:
        run("synopkg", "start", PACKAGE_NAME)
        wait_for_running(True)
        print("Verified that the Jellyfin process restarted.")


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")


def default_archive() -> Path:
    return BACKUP_DIR / f"jellyfin-data_{timestamp()}.tar.gz"


def prune_old_backups() -> None:
    """Keep only BACKUP_RETENTION archives created by normal backups."""
    if BACKUP_RETENTION < 1:
        raise RuntimeError("BACKUP_RETENTION must be at least 1")
    backups = sorted(
        (
            path for path in BACKUP_DIR.iterdir()
            if path.is_file()
            and not path.is_symlink()
            and BACKUP_FILENAME_RE.fullmatch(path.name)
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    for old_backup in backups[BACKUP_RETENTION:]:
        print(f"Removing expired backup: {old_backup}")
        old_backup.unlink()


def archive_filter(member: tarfile.TarInfo):
    """Omit nested backups and generated trickplay files from the tar archive."""
    relative = PurePosixPath(member.name).relative_to("jellyfin")
    excluded = tuple(PurePosixPath(path) for path in EXCLUDED_PATHS)
    if any(relative == path or path in relative.parents for path in excluded):
        return None
    return member


def report_backup_contents() -> None:
    print("Persistent paths included in this backup:")
    for relative in BACKUP_PATHS:
        print(f"  INCLUDE {APP_DATA_DIR / relative}")
    print("Rebuildable or temporary paths excluded:")
    for relative in SKIPPED_PATHS:
        print(f"  EXCLUDE {APP_DATA_DIR / relative}")


def add_manifest(archive: tarfile.TarFile, version: str) -> None:
    """Record archive format, creation time, and Jellyfin package version."""
    data = json.dumps({
        "format": 1,
        "created": datetime.now().astimezone().isoformat(),
        "package": PACKAGE_NAME,
        "jellyfin_version": version,
    }, indent=2).encode("utf-8")
    member = tarfile.TarInfo(str(MANIFEST_NAME))
    member.size = len(data)
    member.mode = 0o600
    member.mtime = int(time.time())
    archive.addfile(member, io.BytesIO(data))


def member_is_allowed(name: PurePosixPath) -> bool:
    if name == MANIFEST_NAME:
        return True
    for relative in BACKUP_PATHS:
        root = PurePosixPath("jellyfin") / PurePosixPath(relative)
        if name == root or root in name.parents:
            return True
    return False


def validate_archive(source: Path, read_contents: bool = False) -> None:
    """Validate layout, member types, traversal safety, and optionally gzip data."""
    with tarfile.open(source, "r:gz") as archive:
        members = archive.getmembers()
        if not members:
            raise RuntimeError("Archive is empty")
        names = {PurePosixPath(member.name) for member in members}
        for relative in BACKUP_PATHS:
            required = PurePosixPath("jellyfin") / PurePosixPath(relative)
            if required not in names:
                raise RuntimeError(f"Required path is missing from archive: {relative}")
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or not member_is_allowed(name):
                raise RuntimeError(f"Unsafe or unexpected archive path: {member.name}")
            # Reject links/special files so extraction cannot redirect writes.
            if member.isdev() or member.isfifo() or member.issym() or member.islnk():
                raise RuntimeError(f"Unsupported archive member: {member.name}")
            if read_contents and member.isfile():
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise RuntimeError(f"Cannot read archive member: {member.name}")
                while extracted.read(1024 * 1024):
                    pass


def create_archive(destination: Path, version: str) -> None:
    for relative in BACKUP_PATHS:
        if not (APP_DATA_DIR / relative).exists():
            raise RuntimeError(f"Required backup path is missing: {APP_DATA_DIR / relative}")
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp",
                                dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        report_backup_contents()
        with tarfile.open(temporary, "w:gz") as archive:
            add_manifest(archive, version)
            for relative in BACKUP_PATHS:
                archive.add(APP_DATA_DIR / relative,
                            arcname=str(Path("jellyfin") / relative),
                            recursive=True, filter=archive_filter)
        print("Verifying the completed compressed archive...")
        validate_archive(temporary, read_contents=True)
        if destination.exists():
            raise RuntimeError(f"Refusing to overwrite existing archive: {destination}")
        # Publish only after full gzip verification; interrupted runs leave no partial archive.
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    print(f"Backup created: {destination}")
    print(f"Compressed size: {destination.stat().st_size / (1024 ** 2):.1f} MiB")


def backup(destination: Path) -> None:
    version = ensure_package_installed()
    was_running = stop_for_maintenance()
    try:
        create_archive(destination, version)
        # Prune only after the new archive has been created and verified.
        prune_old_backups()
    finally:
        restart_if_needed(was_running)


def chown_tree(path: Path, uid: int, gid: int) -> None:
    """Use the newly installed package account ownership after a factory reset."""
    os.chown(str(path), uid, gid, follow_symlinks=False)
    if path.is_dir():
        for root, directories, files in os.walk(str(path)):
            for name in directories + files:
                os.chown(os.path.join(root, name), uid, gid, follow_symlinks=False)


def restore_path(extracted: Path, relative: Path, uid: int, gid: int) -> None:
    source = extracted / relative
    destination = APP_DATA_DIR / relative
    if not source.exists():
        raise RuntimeError(f"Required path is missing from archive: {relative}")
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        # Make persistent directories exactly match the archive; remove stale files.
        run("rsync", "-a", "--delete", f"{source}/", f"{destination}/")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        run("rsync", "-a", str(source), str(destination))
    chown_tree(destination, uid, gid)


def clear_rebuildable_data(empty: Path) -> None:
    print("Clearing stale rebuildable cache and metadata:")
    for relative in REBUILDABLE_DIRS:
        destination = APP_DATA_DIR / relative
        if destination.is_dir():
            print(f"  CLEAR {destination}")
            # Empty contents but retain the package-created directory and permissions.
            run("rsync", "-a", "--delete", f"{empty}/", f"{destination}/")


def restore(source: Path) -> None:
    """Restore onto an installed package while preserving its current UID and GID."""
    version = ensure_package_installed()
    source = source.expanduser().resolve()
    if not source.is_file():
        raise RuntimeError(f"Backup archive does not exist: {source}")
    print(f"Validating archive: {source}")
    validate_archive(source, read_contents=True)
    owner = APP_DATA_DIR.stat()

    with tempfile.TemporaryDirectory(prefix=".jellyfin-restore-",
                                     dir=APP_DATA_DIR.parent) as temporary:
        staging = Path(temporary)
        with tarfile.open(source, "r:gz") as archive:
            archive.extractall(str(staging))
        extracted = staging / "jellyfin"
        # Save the current installation before changing any persistent files.
        rollback = BACKUP_DIR / f"jellyfin-data_before-restore_{timestamp()}.tar.gz"
        was_running = stop_for_maintenance()
        completed = False
        try:
            create_archive(rollback, version)
            for relative in BACKUP_PATHS:
                restore_path(extracted, relative, owner.st_uid, owner.st_gid)
            empty = staging / "empty"
            empty.mkdir()
            clear_rebuildable_data(empty)
            completed = True
            print(f"Restore completed from: {source}")
            print(f"Pre-restore safety backup: {rollback}")
        finally:
            if completed:
                restart_if_needed(was_running)
            elif was_running:
                # Never start Jellyfin against a partially restored database.
                print("Restore failed; Jellyfin remains stopped to protect the data.",
                      file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Back up or restore essential Jellyfin data as a tar.gz archive.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--backup", action="store_true",
                        help="create a timestamped backup in BACKUP_DIR")
    action.add_argument("--restore", metavar="ARCHIVE", type=Path,
                        help="restore an archive (creates a safety backup first)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        setup_logging()
        if os.geteuid() != 0:
            raise RuntimeError("Run this script as root (required by synopkg and app data)")
        if args.restore is not None:
            restore(args.restore)
        else:
            backup(default_archive())
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError,
            tarfile.TarError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
