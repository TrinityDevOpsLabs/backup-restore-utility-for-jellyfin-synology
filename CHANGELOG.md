# Changelog

Notable changes to this project are documented here.

The project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-08-06

### Added

- Atomic, timestamped `tar.gz` backups of essential Jellyfin application state
- Full archive verification before a completed backup is published
- Backup manifests recording the archive format, creation time, and installed
  Jellyfin package version
- Configurable retention for regular backups without deleting rollback archives
  or unrelated files
- Safe restore with path and member-type validation, full gzip verification, and
  a pre-restore rollback backup
- Restoration of package ownership using the installed Jellyfin account's UID
  and GID
- Removal of stale rebuildable cache, metadata, and transcode data after restore
- Synology package stop, start, and real process-state verification
- Failure handling that leaves Jellyfin stopped after a partial restore
- Timestamped standard-output and error logging
- Dependency-free `.env` configuration with process-environment overrides
- A shareable `.env.example` and repository rules that exclude local
  configuration, logs, Python artifacts, and generated backup archives
- DSM Task Scheduler setup and post-run verification instructions
- Factory-reset disaster-recovery guidance and same-NAS backup warnings
- Semantic-versioning, changelog, and GitHub release-maintenance documentation
- Generated GitHub release-note configuration
- Apache License 2.0 licensing, Trinity DevOps LLC copyright attribution, and
  Jellyfin and Synology trademark notices
