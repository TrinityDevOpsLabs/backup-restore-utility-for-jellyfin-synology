# Release process

This project uses Semantic Versioning and tags releases as `vMAJOR.MINOR.PATCH`.

## Version selection

- Increment **MAJOR** for incompatible configuration, archive, or CLI changes.
- Increment **MINOR** for backwards-compatible features.
- Increment **PATCH** for backwards-compatible fixes and documentation updates.

## Publish a release

1. Confirm the working tree contains only the intended release changes.
2. Run the validation checks:

   ```sh
   python3 -m py_compile jellyfin_backup_restore.py
   ./jellyfin_backup_restore.py --help
   ```

3. Move the completed entries in `CHANGELOG.md` from `Unreleased` into a
   dated version section.
4. Commit the release changes on the default branch.
5. Create an annotated tag. Use a signed tag when Git signing is configured:

   ```sh
   git tag -s v1.0.0 -m "Release v1.0.0"
   ```

   Otherwise, create an annotated tag:

   ```sh
   git tag -a v1.0.0 -m "Release v1.0.0"
   ```

6. Push the branch and the specific tag:

   ```sh
   git push origin main
   git push origin v1.0.0
   ```

7. On GitHub, draft a release from the tag, generate release notes, review them,
   attach any intended assets, and publish it.
8. Mark unstable versions such as `v1.1.0-rc.1` as prereleases.

Never move or reuse a published version tag. Enable immutable releases in the
repository settings after confirming the release workflow.
