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
4. Merge the release changes into the default branch.
5. On GitHub, draft a release. Choose the next version as its tag
   (for example, `v1.0.2`) and target the default branch. If you use a
   signed tag, create it on the default branch first and select it in
   the release draft. A tag alone does not publish a version.
6. Attach any intended assets before publishing. Mark unstable versions such as
   `v1.1.0-rc.1` as prereleases.
7. Publish the GitHub Release. Its tag is the source of truth for the
   version. The **Release notes** workflow fills in an empty description
   using GitHub's generated notes and the existing `.github/release.yml`
   categories. It leaves any notes you already wrote, the release title,
   and assets unchanged.

If note generation fails, run **Release notes** from the Actions tab with
the published tag. Releases with existing notes are left alone. To review
the notes before publishing instead, use GitHub's **Generate release notes**
button in the draft.

Never move or reuse a published version tag. Enable immutable releases in the
repository settings after confirming the release workflow.
