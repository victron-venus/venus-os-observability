"""Keep the hash-pinned legacy CI installer aligned with the canonical uv lock."""

import re
import tomllib
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "filename",
    [
        "requirements-ci.txt",
        "requirements-runtime.txt",
        "requirements-release-build.txt",
        "requirements-release-security.txt",
    ],
)
def test_ci_requirements_use_locked_versions_and_hashes(filename: str) -> None:
    """Legacy lint/test jobs must not silently install older dependency releases."""
    root = Path(__file__).resolve().parents[1]
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    packages = {
        (package["name"].replace("_", "-"), package["version"]): package
        for package in lock["package"]
    }
    exported = (root / ".github" / filename).read_text(encoding="utf-8")
    blocks = re.split(r"(?m)^(?=[A-Za-z0-9_.-]+==)", exported)[1:]
    assert blocks, "CI requirements must contain hash-pinned dependencies"
    for block in blocks:
        requirement = re.match(r"([A-Za-z0-9_.-]+)==([^\s;\\]+)", block)
        assert requirement is not None
        name, version = requirement.groups()
        key = (name.replace("_", "-"), version)
        assert key in packages, f"CI export is stale: {name}=={version}"
        package = packages[key]
        artifacts = package.get("wheels", []) + ([package["sdist"]] if "sdist" in package else [])
        expected = {artifact["hash"] for artifact in artifacts}
        actual = set(re.findall(r"--hash=(sha256:[0-9a-f]{64})", block))
        assert actual and actual == expected, f"CI hashes differ for {name}=={version}"
