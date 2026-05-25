# Release & Rollback Procedures

This document covers the release workflow for **guidewire** on PyPI, including
how to yank or roll back a problematic release.

> **See also:** [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and
> [SECURITY.md](SECURITY.md) for vulnerability reporting.

---

## Table of Contents

- [Release Workflow](#release-workflow)
- [Publishing to PyPI](#publishing-to-pypi)
- [Yanking a Release](#yanking-a-release)
- [Rolling Back a Release](#rolling-back-a-release)
- [Emergency Hotfix Process](#emergency-hotfix-process)
- [Troubleshooting](#troubleshooting)

---

## Release Workflow

Guidewire uses a **tag-triggered release pipeline** (`.github/workflows/release.yml`)
with OIDC trusted publishing. No API tokens or manual uploads are required.

### Steps

1. **Ensure CI is green** on the `main` branch for the commit you want to release.
2. **Create and push a version tag**:
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
3. The release pipeline runs automatically:
   - **Security scan** — `pip-audit --strict`
   - **CI gate** — polls for a successful CI run on the tagged commit
   - **Build** — `python -m build` produces sdist + wheel
   - **SLSA provenance** — signed build provenance for supply-chain trust
   - **Publish to PyPI** — OIDC trusted publishing (no stored secrets)
   - **Verify** — installs from PyPI and confirms the version matches the tag
   - **Release** — generates `CHANGELOG.md` via git-cliff and creates a GitHub Release
4. **Confirm** the new version appears at [pypi.org/project/guidewire](https://pypi.org/project/guidewire/).

### Version Numbering

Guidewire follows [Semantic Versioning](https://semver.org/):

- **Patch** (`X.Y.Z+1`): bug fixes, no new features or breaking changes
- **Minor** (`X.Y+1.0`): new features, backward-compatible
- **Major** (`X+1.0.0`): breaking changes

The version is managed dynamically via `setuptools-scm` — it is derived from
git tags, not hardcoded in source.

---

## Publishing to PyPI

### Production (automatic)

Releases to [PyPI](https://pypi.org/project/guidewire/) are **fully automated**.
Push a `vX.Y.Z` tag to `main` and the pipeline handles everything. See
[Release Workflow](#release-workflow) above.

### TestPyPI (manual dry-run)

A `workflow_dispatch` workflow exists for testing the publish process without
affecting the production package:

1. Go to **Actions → publish-testpypi** in GitHub.
2. Click **Run workflow**.
3. Verify the package installs from TestPyPI.

This uses OIDC trusted publishing against
[test.pypi.org](https://test.pypi.org/project/guidewire/) and does **not**
require any manual token.

---

## Yanking a Release

Yanking removes a version from the PyPI install index while keeping it available
for anyone who pinned it. Users running `pip install guidewire` will skip the
yanked version, but `pip install guidewire==X.Y.Z` still works.

### When to yank

- A critical bug was discovered shortly after release
- A dependency vulnerability was not caught by `pip-audit`
- The wrong commit was tagged and released

### Steps

1. **Go to [pypi.org/manage/project/guidewire/releases](https://pypi.org/manage/project/guidewire/releases/)**.
2. Find the version you want to yank.
3. Click **Options → Yank**.
4. Confirm the yank.

Alternatively, use the command line:

```bash
pip install twine
twine upload --verbose --repository pypi dist/guidewire-X.Y.Z.tar.gz guidewire-X.Y.Z-py3-none-any.whl
# Or use the PyPI API directly:
pip install pypiserver  # if needed
```

The simplest CLI method:

```bash
# Yank via PyPI's web interface is preferred.
# For automation, use the PyPI JSON API or a tool like `pypi-command`.
```

> **Note:** Yanking requires PyPI maintainer or owner access for the
> `guidewire` project. Contact a project owner if you lack permissions.

### After yanking

1. **Communicate**: Open a GitHub issue or discussion explaining the yank reason.
2. **Fix**: Address the issue on a new branch.
3. **Release**: Tag and push a new patch version (e.g., `X.Y.Z+1`).
4. **Announce**: Update the GitHub Release notes for the yanked version to explain
   the issue and link to the fix.

---

## Rolling Back a Release

A full rollback means removing the version from PyPI entirely. This is rarely
needed — **yanking is preferred** in most cases.

### When to roll back

- Sensitive data or secrets were accidentally included in the distribution
- A supply-chain compromise is suspected
- Legal or licensing issues require immediate removal

### Steps

1. **Delete the distribution files from PyPI**:
   - Go to [pypi.org/manage/project/guidewire/releases](https://pypi.org/manage/project/guidewire/releases/).
   - Select the affected version.
   - Delete each file (sdist and wheel).

2. **Delete the Git tag** (if the release was erroneous):
   ```bash
   git push origin :refs/tags/vX.Y.Z
   git tag -d vX.Y.Z
   ```

3. **Delete the GitHub Release** (if created):
   - Go to the [Releases page](https://github.com/HarmenBakhuis/Guidewire/releases).
   - Find the release for the yanked version.
   - Click **Delete**.

4. **Notify users**:
   - Open a GitHub issue with details.
   - Update `SECURITY.md` or `CHANGELOG.md` if appropriate.
   - If the issue is security-related, follow the process in `SECURITY.md`.

> **Important:** PyPI does not allow re-uploading the same version number once
> files are deleted. You **must** release under a new version number (e.g.,
> `X.Y.Z+1` or `X.Y+1.0`).

### Rollback checklist

- [ ] Distribution files deleted from PyPI
- [ ] Git tag removed (if appropriate)
- [ ] GitHub Release removed (if appropriate)
- [ ] GitHub issue or discussion created explaining the rollback
- [ ] Security advisory filed if the issue is vulnerability-related
- [ ] New fix version released and verified

---

## Emergency Hotfix Process

For critical issues that need the fastest possible turnaround:

1. **Yank** the broken version on PyPI immediately.
2. **Branch** from the tagged commit:
   ```bash
   git checkout vX.Y.Z
   git checkout -b hotfix/X.Y.Z+1
   ```
3. **Fix** the issue with the minimal necessary change.
4. **Test** locally:
   ```bash
   pip install -e ".[dev]"
   pytest
   ruff check .
   ```
5. **Commit and push**:
   ```bash
   git add -A
   git commit -m "Hotfix: <description>"
   git push origin hotfix/X.Y.Z+1
   ```
6. **Merge** the hotfix branch into `main` (via PR for traceability).
7. **Tag and release**:
   ```bash
   git tag vX.Y.Z+1
   git push origin vX.Y.Z+1
   ```
8. **Verify** the new version on PyPI.

---

## Troubleshooting

### Release pipeline failed

1. Check the **Actions** tab in GitHub for the failed workflow run.
2. Common failures:
   - `pip-audit` found a vulnerability — update the vulnerable dependency.
   - CI gate timed out — ensure CI passed on the tagged commit.
   - OIDC publishing failed — verify the PyPI project is configured for
     trusted publishing (publisher: GitHub, repository: `HarmenBakhuis/Guidewire`,
     environment: `pypi`).

### Version already exists on PyPI

PyPI rejects duplicate version uploads. You must:
- Bump the version number.
- Tag and push a new release.

### `setuptools-scm` version is wrong

The version is derived from git tags. If it looks wrong:
```bash
python -m setuptools_scm
```
Ensure the correct tag is checked out and there are no uncommitted changes.

### `pip install` still installs the yanked version

PyPI's CDN may cache for a short period. Wait a few minutes and try:
```bash
pip install --no-cache-dir guidewire
```
