# Release packaging

Official releases are built from the published tag by `.github/workflows/release.yaml`.
The workflow runs `scripts/build_release.py` and attaches `remeha_home.zip` to the
GitHub release. The tag and integration manifest version must agree.

```sh
python scripts/build_release.py --tag vX.Y.Z --output remeha_home.zip
```

The archive stores integration files at its root, as required by `zip_release`
in `hacs.json`. It includes Python, JSON, and YAML sources, translations, generic
dashboard templates, and synthetic example bindings. It excludes caches,
`private/`, `dashboard-private/`, private-named files, and non-example bindings.
Keep actual household profiles and generated configurations outside the source
tree; exclusion rules supplement that separation rather than detecting arbitrary
personal information in a file's contents.

For manual installation, extract this asset into `custom_components/remeha_home`.
GitHub's automatic source archive is not the installer and has a different layout.
Publishing a release does not update or restart an HA instance. Python runtime
changes require an HA restart after installation; the offline generator does not
edit a running dashboard.

The README download badges target `remeha_home.zip`. They count installer requests,
including repeated downloads and updates, not unique users or installations.
Default-branch/source archives are excluded and GitHub may cache the counts.

Before release, run tests, Ruff, hassfest and HACS validation; inspect the archive
for unexpected paths. Public generator/template changes must include their docs
and JSON/YAML equivalence checks. Historical release titles and notes may be
translated without changing their immutable source tags or installer archives.
