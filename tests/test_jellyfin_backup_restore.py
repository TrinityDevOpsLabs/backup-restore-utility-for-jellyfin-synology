import contextlib
import io
import json
import os
import runpy
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch


SCRIPT = Path(__file__).resolve().parents[1] / "jellyfin_backup_restore.py"


class UtilityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.app = self.root / "app"
        self.backups = self.root / "backups"
        config = self.root / ".env"
        config.write_text(
            f"APP_DATA_DIR={self.app}\nBACKUP_DIR={self.backups}\n"
            f"LOG_FILE={self.root}/logs/output\nERROR_FILE={self.root}/logs/error\n"
            "PACKAGE_NAME=jellyfin\nBACKUP_RETENTION=2\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, clear=True), patch.object(Path, "with_name", return_value=config):
            self.utility = runpy.run_path(str(SCRIPT))["backup"].__globals__
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(contextlib.redirect_stderr(self.output))

    def create_layout(self):
        for relative in self.utility["BACKUP_PATHS"]:
            path = self.app / relative
            if relative == Path("fonts/fonts.conf"):
                path.parent.mkdir(parents=True)
                path.write_text("old", encoding="utf-8")
            else:
                path.mkdir(parents=True)

    def service(self, *pids):
        real_run = self.utility["run"]
        commands = Mock()

        def run(*args):
            if args[0] == "synopkg":
                commands(*args)
            else:
                real_run(*args)

        self.enterContext(patch.dict(self.utility, {
            "ensure_package_installed": Mock(return_value="12.4-44"),
            "jellyfin_pid": Mock(side_effect=pids),
            "run": run,
        }))
        return commands

    def test_env_parsing_and_override(self):
        config = self.root / "settings"
        config.write_text('# comment\n\nexport NAME="two words" # comment\nEMPTY=\n')
        values = self.utility["load_env"](config)
        self.assertEqual(values, {"NAME": "two words", "EMPTY": ""})
        with patch.dict(os.environ, {"NAME": "override"}):
            self.assertEqual(self.utility["required_setting"](values, "NAME"), "override")
        with self.assertRaisesRegex(RuntimeError, "empty"):
            self.utility["required_setting"](values, "EMPTY")

    def test_invalid_env_lines(self):
        config = self.root / "settings"
        cases = {
            "MISSING": "expected KEY=VALUE",
            "BAD-KEY=value": "Invalid .env key",
            'NAME="unterminated': "Invalid .env line",
            "NAME=two words": "quote values",
        }
        for line, message in cases.items():
            with self.subTest(line=line):
                config.write_text(line)
                with self.assertRaisesRegex(RuntimeError, message):
                    self.utility["load_env"](config)
        with self.assertRaisesRegex(RuntimeError, "Configuration file not found"):
            self.utility["load_env"](self.root / "missing")

    def test_backup_excludes_generated_data_and_records_version(self):
        self.create_layout()
        (self.app / "data/data/trickplay").mkdir()
        (self.app / "data/data/jellyfin.db").write_bytes(b"database")
        commands = self.service(123, None, 456)
        destination = self.utility["default_archive"]()
        self.utility["backup"](destination)
        with tarfile.open(destination, "r:gz") as archive:
            self.assertIn("jellyfin/data/data/jellyfin.db", archive.getnames())
            self.assertNotIn("jellyfin/data/data/trickplay", archive.getnames())
            with archive.extractfile(str(self.utility["MANIFEST_NAME"])) as manifest:
                self.assertEqual(json.load(manifest)["jellyfin_version"], "12.4-44")
        self.assertEqual(commands.call_args_list, [
            call("synopkg", "stop", "jellyfin"), call("synopkg", "start", "jellyfin"),
        ])

    def test_backup_failure_restarts_service(self):
        commands = self.service(123, None, 456)
        destination = self.utility["default_archive"]()
        with self.assertRaisesRegex(RuntimeError, "Required backup path"):
            self.utility["backup"](destination)
        self.assertFalse(destination.exists())
        self.assertEqual(commands.call_args_list[-1], call("synopkg", "start", "jellyfin"))

    def test_backup_of_stopped_service_leaves_it_stopped(self):
        self.create_layout()
        commands = self.service(None)
        self.utility["backup"](self.utility["default_archive"]())
        commands.assert_not_called()

    def test_existing_archive_is_preserved(self):
        self.create_layout()
        destination = self.root / "existing.tar.gz"
        destination.write_bytes(b"keep")
        with self.assertRaisesRegex(RuntimeError, "Refusing to overwrite"):
            self.utility["create_archive"](destination, "12.4-44")
        self.assertEqual(destination.read_bytes(), b"keep")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_retention_preserves_rollback_and_unrelated_files(self):
        self.backups.mkdir()
        regular = [
            self.backups / f"jellyfin-data_2026-01-0{day}_000000_000000.tar.gz"
            for day in (1, 2, 3)
        ]
        rollback = self.backups / "jellyfin-data_before-restore_2026-01-01_000000_000000.tar.gz"
        unrelated = self.backups / "other.tar.gz"
        for path in [*regular, rollback, unrelated]:
            path.touch()
        self.utility["prune_old_backups"]()
        self.assertEqual(set(self.backups.iterdir()), {*regular[1:], rollback, unrelated})
        with patch.dict(self.utility, BACKUP_RETENTION=0):
            with self.assertRaisesRegex(RuntimeError, "at least 1"):
                self.utility["prune_old_backups"]()

    def test_archive_rejects_links_and_unexpected_paths(self):
        self.create_layout()
        for name, member_type, message in (
            ("jellyfin/config/link", tarfile.SYMTYPE, "Unsupported archive member"),
            ("jellyfin/unexpected", tarfile.REGTYPE, "Unsafe or unexpected archive path"),
        ):
            with self.subTest(name=name):
                destination = self.root / "invalid.tar.gz"
                with tarfile.open(destination, "w:gz") as archive:
                    for relative in self.utility["BACKUP_PATHS"]:
                        archive.add(self.app / relative, f"jellyfin/{relative}")
                    member = tarfile.TarInfo(name)
                    member.type = member_type
                    archive.addfile(member)
                with self.assertRaisesRegex(RuntimeError, message):
                    self.utility["validate_archive"](destination)

    @unittest.skipUnless(shutil.which("rsync"), "restore integration test requires rsync")
    def test_restore_replaces_changed_files_with_matching_size_and_mtime(self):
        self.create_layout()
        database = self.app / "data/data/jellyfin.db"
        fonts = self.app / "fonts/fonts.conf"
        with contextlib.closing(sqlite3.connect(database)) as conn, conn:
            conn.execute("CREATE TABLE state (value TEXT)")
            conn.execute("INSERT INTO state VALUES ('old')")
        before = {}
        for path in (database, fonts):
            os.utime(path, (1700000000, 1700000000))
            before[path] = (path.stat().st_size, path.stat().st_mtime)
        source = self.root / "original.tar.gz"
        self.utility["create_archive"](source, "12.4-44")
        with contextlib.closing(sqlite3.connect(database)) as conn, conn:
            conn.execute("UPDATE state SET value = 'new'")
        fonts.write_text("new", encoding="utf-8")
        for path, (size, mtime) in before.items():
            os.utime(path, (mtime, mtime))
            self.assertEqual((path.stat().st_size, path.stat().st_mtime), (size, mtime))
        cache = self.app / "cache"
        cache.mkdir()
        (cache / "stale").touch()
        commands = self.service(123, None, 456)
        self.utility["restore"](source)
        with contextlib.closing(sqlite3.connect(database)) as conn:
            self.assertEqual(conn.execute("SELECT value FROM state").fetchall(), [("old",)])
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
        self.assertEqual(fonts.read_text(encoding="utf-8"), "old")
        self.assertEqual(list(cache.iterdir()), [])
        rollback, = self.backups.glob("*before-restore*")
        self.utility["validate_archive"](rollback, read_contents=True)
        self.assertEqual(commands.call_args_list, [
            call("synopkg", "stop", "jellyfin"), call("synopkg", "start", "jellyfin"),
        ])

    def test_failed_restore_leaves_running_service_stopped(self):
        self.create_layout()
        source = self.root / "original.tar.gz"
        self.utility["create_archive"](source, "12.4-44")
        commands = self.service(123, None)
        with patch.dict(self.utility, restore_path=Mock(side_effect=OSError("copy failed"))):
            with self.assertRaisesRegex(OSError, "copy failed"):
                self.utility["restore"](source)
        commands.assert_called_once_with("synopkg", "stop", "jellyfin")
        self.assertIn("remains stopped", self.output.getvalue())

    def test_cli_dispatch_and_error_status(self):
        for args, action in ((["--backup"], "backup"), (["--restore", "archive"], "restore")):
            with self.subTest(args=args), patch("sys.argv", [str(SCRIPT), *args]):
                handler = Mock()
                with patch.dict(self.utility, {"setup_logging": Mock(), action: handler}):
                    with patch("os.geteuid", return_value=0):
                        self.assertEqual(self.utility["main"](), 0)
                        handler.assert_called_once()
                        handler.side_effect = RuntimeError("failed")
                        self.assertEqual(self.utility["main"](), 1)
                    with patch("os.geteuid", return_value=1000):
                        self.assertEqual(self.utility["main"](), 1)

    def test_package_version_and_command_errors(self):
        package = self.root / "package"
        package.mkdir()
        self.app.mkdir()
        with patch.dict(self.utility, PACKAGE_DIR=package):
            with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "12.4-44\n", "")):
                self.assertEqual(self.utility["ensure_package_installed"](), "12.4-44")
            with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, "out", "err")):
                with self.assertRaises(subprocess.CalledProcessError):
                    self.utility["run"]("synopkg", "stop", "jellyfin")

    def test_log_tee_flushes_partial_line(self):
        console = io.StringIO()
        log = io.StringIO()
        tee = self.utility["TimestampedTee"](console, log)
        with patch.dict(self.utility, log_timestamp=lambda: "timestamp"):
            self.assertEqual(tee.write(""), 0)
            self.assertEqual(tee.write("complete\npartial"), 16)
            self.assertEqual(log.getvalue(), "timestamp complete\n")
            tee.flush()
        self.assertEqual(log.getvalue(), "timestamp complete\ntimestamp partial")
        self.assertEqual(log.getvalue(), console.getvalue())
