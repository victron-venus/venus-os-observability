# Quality, builds and evidence

## Reproducing the checks

Use Python 3.12.13 and the locked tools, with `uv` and `openssl` available:

```sh
bash scripts/ci.sh --install
bash scripts/ci.sh
.venv-ci/bin/python -m unittest discover -s .github/release-tests -p 'test_*.py' -v
.venv-ci/bin/python scripts/workflow_contracts.py
.venv-ci/bin/python scripts/version_plan.py check-base
.venv-ci/bin/python -m build --no-isolation
.venv-ci/bin/python -m twine check dist/*
```

The CI script runs Ruff, strict mypy and pytest. Its `--cov=.` report omits the test directory but discovers only importable or executed Python files; the separate test workflow measures only `src/venus_observability`. Neither report alone establishes 80% statement coverage of the complete first-party project. The relay, installer, release scripts and workflow-embedded code must be accounted for before asserting that Silver requirement. Native dependencies and branches require their own evidence.

Security validation uses `bash scripts/ci.sh security` with the declared Bandit and Trivy tools. The pre-release workflow blocks high/critical findings and retains its output. Review medium-severity findings separately; a successful high-only gate does not establish that no medium issue exists. CodeQL analyzes both Python and GitHub Actions. The existing Paho callback API warning in tests is a known compatibility limit: the standalone relay container pins Paho 1.6.1, while the main Python environment has Paho 2.x. Callback migration must retain the supported relay API; warnings are not globally suppressed and a maximally strict warning policy is not claimed.

GitHub rulesets require CI, CodeQL and pull-request review; automated review is not a second human maintainer.

TLS tests use synthetic local SMTP/HTTPS peers and disposable certificates, including incorrect hostnames, untrusted issuers and undersized leaf/intermediate/root keys. Installer tests operate under temporary roots. They send no real alerts and do not deploy to devices. The public [Actions history](https://github.com/victron-venus/venus-os-observability/actions) records source-specific results.

## Build and installation scope

Python wheels and source distributions use standard `build`/setuptools and pip. A virtual environment or pip staging options isolate a Python install; `pip uninstall venus-os-observability` removes it. The native SetupHelper installer supports the documented fixed device path and does not implement general DESTDIR/PREFIX semantics. It must not be invoked on a workstation as a test.

Source and `.py` files are preserved in distributions. The project does not maintain its own native compiler or recursive Make build. Containers include platform-provided native libraries; their flags and debug information are not verified by this statement. The Dockerfile pins its base digest and Python locks, but apt repositories and build outputs have not been proven byte-repeatable. Build tools and platform prerequisites are documented in [release packaging](release-packaging.md).

## Current evidence and open requirements

The [2026-10-13 inventory](evidence/openssf-inventory-2026-10-13.json) records the inspected base, GitHub security state, recent substantive changes and tests. It is a dated observation, not an evergreen claim. Recent OTLP and alert TLS additions each include extensive regression tests; see PRs [67](https://github.com/victron-venus/venus-os-observability/pull/67) and [68](https://github.com/victron-venus/venus-os-observability/pull/68).

Current stable [v0.1.7](https://github.com/victron-venus/venus-os-observability/releases/tag/v0.1.7) has a SHA-256 inventory delivered over HTTPS. Artifact signing, full reproducibility, a six-month regression percentage and complete coverage remain unverified. No stable release is triggered by this documentation change; candidate acceptance remains required under [RELEASING.md](../RELEASING.md).
