# Releasing

One tag publishes everything: a GitHub release with notes from `CHANGELOG.md`, the Python package on PyPI (with the
dashboard built in), a multi-architecture Docker image on GitHub Container Registry, and optionally the TypeScript
SDK on npm.

## One-time setup

1. **PyPI trusted publishing** (no token stored anywhere): sign in at [pypi.org](https://pypi.org), open
   *Your account → Publishing → Add a new pending publisher*, and enter:
   project `ragops-observatory`, owner `ArathiS1`, repository `ragops-observatory`, workflow `release.yml`,
   environment `pypi`.
2. **GitHub environment**: *Settings → Environments → New environment* named `pypi` (optionally require your
   approval before publishing).
3. **Docs website**: *Settings → Pages → Source: GitHub Actions*. The `Docs website` workflow publishes
   <https://arathis1.github.io/ragops-observatory/> on every push to `main` that changes `docs/`.
4. **npm (optional)**: create the `@ragops` organisation on npmjs.com, add an automation token as the secret
   `NPM_TOKEN`, and set the repository variable `PUBLISH_NPM` to `true`.

## Every release

1. Update the version in `pyproject.toml`, `ragops/__init__.py`, `web/package.json`, `sdk-ts/package.json` and
   `CITATION.cff`.
2. Add a `## <version>` section at the top of `CHANGELOG.md`.
3. Check locally: `ruff check ragops tests examples && pytest -q && (cd web && npm run build) && mkdocs build --strict`.
4. Commit, then tag and push:

   ```bash
   git commit -am "Release v0.10.0"
   git tag v0.10.0
   git push && git push --tags
   ```

The workflow refuses to publish if the tag does not match the package version, or if the wheel does not contain the
dashboard.
