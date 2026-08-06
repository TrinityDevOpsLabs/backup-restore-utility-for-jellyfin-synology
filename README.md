# Backup and Restore for Jellyfin on Synology DSM

A compact backup and disaster-recovery tool for the SynoCommunity Jellyfin
package on Synology DSM.

`jellyfin_backup_restore.py` backs up Jellyfin's essential application state, verifies
the completed archive, and can safely restore it after a package reinstall or
NAS rebuild. Media files are not included.

## Features

- Stops Jellyfin before copying live application data and restores its original running state afterward
- Creates compressed, timestamped archives and verifies every file in them
- Publishes archives atomically, so interrupted backups are not mistaken for completed ones
- Retains a configurable number of regular backups
- Validates archives and creates a rollback archive before restoring
- Preserves the newly installed Jellyfin package ownership
- Logs standard output and errors separately

## Requirements

- Synology DSM
- The SynoCommunity Jellyfin package
- Python 3
- `rsync` (used during restore)
- Root access for `synopkg` and Jellyfin's application-data directory

The defaults assume Jellyfin is installed on Volume 2. Other layouts can be
used by changing the configuration constants below.

## Installation

Copy `jellyfin_backup_restore.py` and `.env.example` to your NAS, create your local
configuration, and make the script executable:

```sh
cp .env.example .env
chmod +x jellyfin_backup_restore.py
```

## Configuration

Copy `.env.example` to `.env`, then edit `.env` for your NAS:

```dotenv
APP_DATA_DIR=<path to Jellyfin application data>
BACKUP_DIR=<directory where backup archives will be stored>
BACKUP_RETENTION=<number of regular backups to retain>
LOG_FILE=<path to the standard output log>
ERROR_FILE=<path to the error log>
PACKAGE_NAME=<internal Synology Jellyfin package name>
```

The script loads `.env` from the same directory as `jellyfin_backup_restore.py`, so it
works regardless of the current working directory. Existing process environment
variables override matching values from the file. Change the paths to match your
NAS. The internal Synology package name is lowercase `jellyfin`, although
Package Center displays `Jellyfin`. `BACKUP_RETENTION` must be at least `1`.

## Usage

Run the script as `root`.

```sh
sudo ./jellyfin_backup_restore.py --backup
sudo ./jellyfin_backup_restore.py --restore /path/to/jellyfin-data_2026-01-01_020000_000000.tar.gz
```

Normal output is appended to `LOG_FILE`, errors are appended to `ERROR_FILE`, and both are also shown in the terminal.

For unattended backups, invoke the same `--backup` command from DSM Task Scheduler as a user-defined script running as `root`. Test a manual backup and restore procedure before relying on a schedule.

## Backup contents

The archive includes:

- Jellyfin configuration
- The main database, users, collections, and downloaded subtitles
- Installed plugins and plugin configuration
- Library definitions and library artwork under `data/root`
- Font configuration
- A manifest with the archive format, creation time, and installed Jellyfin version

The following rebuildable, temporary, or redundant data is excluded:

- Cache, logs, generated metadata, trickplay images, and transcodes
- Jellyfin's own backup and SQLite backup directories
- PID files

Media files referenced by Jellyfin are **not backed up**. Generated metadata is rebuilt after restoration. Custom artwork or manual changes stored only in the excluded metadata directory cannot be recovered from these archives.

## Retention

After a new archive has been created and verified, the script retains the newest `BACKUP_RETENTION` regular backups. It manages only regular, non-symlink files in `BACKUP_DIR` whose names match:

```text
jellyfin-data_YYYY-MM-DD_HHMMSS_microseconds.tar.gz
```

Files with other names and `jellyfin-data_before-restore_*` rollback archives are not removed automatically.

## Safety behavior

During backup, the script:

1. Confirms that Jellyfin and its application-data directory exist.
2. Detects the real Jellyfin process, stops it if running, and verifies it exited.
3. Creates the archive under a temporary filename.
4. Reads the complete archive to verify its gzip data and expected layout.
5. Atomically publishes the verified archive.
6. Restarts Jellyfin only if it was running before the backup.

Before restoring, the script validates the entire archive and rejects unexpected paths, links, and special files. It creates a pre-restore rollback archive, makes persistent directories match the selected backup, and clears stale rebuildable data.

If restoration fails after changes begin, Jellyfin remains stopped to avoid starting against partially restored data. Inspect the error log and use the pre-restore archive for recovery.

## Disaster recovery

After rebuilding the NAS:

1. Recreate or reconnect media shares and restore their permissions.
2. Install the SynoCommunity Jellyfin package at the version stored in the manifest or a compatible newer version. Do not restore onto an older version.
3. Install required dependencies such as FFmpeg.
4. Install Jellyfin on the configured volume so `APP_DATA_DIR` exists.
5. Copy the script and backup archive to the NAS.
6. Review the configuration and run the restore command as `root`.
7. Review both logs and confirm Jellyfin starts and can access its media.

Jellyfin must be installed before restoration. This tool restores application state; it does not install Jellyfin, FFmpeg, or media files.

## Important backup warning

A backup stored only on the same NAS does not protect against disk, volume, NAS, theft, or site failure. Copy verified archives to another NAS, an external disk, or trusted off-site storage, and periodically test restoration.

## Package status

An unprivileged `synopkg status jellyfin` can incorrectly report Jellyfin as stopped. Check it with elevated privileges:

```sh
sudo synopkg status jellyfin
```

The script independently verifies the real process before and after package operations.

## Trademark notice

This independent project is not affiliated with, endorsed by, or sponsored by
Jellyfin, Inc. or Synology Inc. Jellyfin is a trademark of Jellyfin, Inc.
Synology and DiskStation Manager are trademarks of Synology Inc.

## License

Licensed under the [Apache License 2.0](LICENSE). Commercial use, modification,
distribution, and resale are permitted subject to the license terms. Redistributed
copies must preserve the required license and attribution notices.

See [NOTICE](NOTICE) for the project copyright notice.
