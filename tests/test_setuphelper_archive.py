"""Check release archives before publishing native installer inputs."""

import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PACKAGE_SPEC = importlib.util.spec_from_file_location(
    "package_release", REPO / "scripts/package_release.py"
)
assert PACKAGE_SPEC is not None and PACKAGE_SPEC.loader is not None
PACKAGER = importlib.util.module_from_spec(PACKAGE_SPEC)
PACKAGE_SPEC.loader.exec_module(PACKAGER)
SPEC = importlib.util.spec_from_file_location(
    "validate_setuphelper_archive", REPO / "scripts/validate_setuphelper_archive.py"
)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)

PLAN_HELPER = """
import json
import runpy
import sys
from pathlib import Path

version_plan = runpy.run_path(sys.argv[1])
policy = json.loads(Path(sys.argv[2]).read_text())
plan = version_plan["create_plan"](
    sys.argv[3], sys.argv[4], int(sys.argv[5]), sys.argv[6], policy
)
print(json.dumps(plan))
"""


def make_archive(path: Path, changed: str = "", mode: int = 0o755) -> None:
    """Build an archive from the actual release inputs, with one optional defect."""
    with tarfile.open(path, "w:gz") as archive:
        for name in sorted(VALIDATOR.REQUIRED):
            if name == changed and changed in {
                "src/venus_observability/metrics.py",
                "src/venus_observability/dbus_owners.py",
            }:
                continue
            data = (REPO / name).read_bytes()
            member = tarfile.TarInfo(f"{VALIDATOR.PREFIX}/{name}")
            member.mode = mode if name == "setup" else 0o755
            if changed == "version" and name == "version":
                data = b"9.9.9\n"
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        if changed in {"../private", "local_config.py"}:
            member = tarfile.TarInfo(f"{VALIDATOR.PREFIX}/{changed}")
            member.size = 6
            archive.addfile(member, io.BytesIO(b"secret"))


def create_release_plan(
    source: Path, base: str, channel: str, sequence: int, source_sha: str
) -> dict[str, object]:
    """Create a plan with the vendored implementation in an isolated process."""
    output = subprocess.check_output(
        [
            sys.executable,
            "-c",
            PLAN_HELPER,
            str(source / "scripts/version_plan.py"),
            str(source / ".release-policy.json"),
            base,
            channel,
            str(sequence),
            source_sha,
        ],
        text=True,
    )
    plan = json.loads(output)
    assert isinstance(plan, dict)
    return plan


def test_complete_archive(tmp_path: Path) -> None:
    """All actual installer inputs are accepted with consistent versions."""
    path = tmp_path / "source.tar.gz"
    make_archive(path)
    VALIDATOR.validate_archive(str(path))


def test_candidate_adapter_preserves_the_native_contract(tmp_path: Path) -> None:
    """Validate real candidate packaging against the native installer's file contract."""
    source = tmp_path / "source"
    source.mkdir()
    for name in VALIDATOR.REQUIRED:
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, destination)
    (source / ".release-package.json").write_text(
        json.dumps({"name": VALIDATOR.PREFIX, "include": sorted(VALIDATOR.REQUIRED)})
    )
    (source / ".release-policy.json").write_text(json.dumps({"version_file": "version"}))
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    (source / "local_config.py").write_text("DEVICE_LOCAL = True\n")
    version = (source / "version").read_text().strip()
    assets = PACKAGER.build_package(source, version, "rc", tmp_path / "assets")
    assert len(assets) == 1
    VALIDATOR.validate_archive(str(assets[0]))


@pytest.mark.parametrize(("channel", "sequence"), [("beta", 2), ("rc", 3)])
def test_frozen_candidate_package_excludes_internal_receipts(
    tmp_path: Path, channel: str, sequence: int
) -> None:
    """Run the real overlay and native packager for candidate release channels."""
    source = tmp_path / channel
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(REPO), str(source)],
        check=True,
    )
    package = json.loads((source / ".release-package.json").read_text())
    package["wheel"] = False
    (source / ".release-package.json").write_text(json.dumps(package))
    base = (source / "version").read_text().strip()
    source_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source, text=True
    ).strip()
    plan = create_release_plan(source, base, channel, sequence, source_sha)
    plan_path = source / ".release-plan.json"
    plan_path.write_text(json.dumps(plan))
    subprocess.run(
        [
            sys.executable,
            str(source / "scripts/version_plan.py"),
            "sync",
            "--root",
            str(source),
            "--plan",
            str(plan_path),
        ],
        check=True,
    )
    assets = PACKAGER.build_package(source, base, channel, tmp_path / f"assets-{channel}")
    assert len(assets) == 1
    VALIDATOR.validate_archive(str(assets[0]))
    with tarfile.open(assets[0], "r:gz") as archive:
        names = {member.name for member in archive.getmembers()}
    assert f"{VALIDATOR.PREFIX}/.release-plan.json" not in names
    assert f"{VALIDATOR.PREFIX}/.release-inputs.json" not in names


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("1.2.3", "1.2.3"),
        ("1.2.3-beta.7", "1.2.3b7"),
        (
            "1.2.3-nightly.20260913235959.12.3",
            "1.2.3.dev20260913235959000000000000000000120000000003",
        ),
    ],
)
def test_supported_setuphelper_versions_have_exact_python_projection(
    version: str, expected: str
) -> None:
    """Every accepted native identity has one canonical Python version."""
    assert VALIDATOR.expected_python_version(version) == expected


@pytest.mark.parametrize(
    "version",
    [
        "1.2.3-alpha.1",
        "1.2.3-rc.1",
        "1.2.3-nightly.20260230235959.1.1",
        "01.2.3",
    ],
)
def test_unsupported_setuphelper_versions_are_rejected(version: str) -> None:
    """Do not reinterpret arbitrary prerelease strings as native candidates."""
    with pytest.raises(ValueError, match="Unsupported SetupHelper release version"):
        VALIDATOR.expected_python_version(version)


@pytest.mark.parametrize(
    "changed,mode,error",
    [
        ("src/venus_observability/metrics.py", 0o755, "Missing runtime files"),
        ("src/venus_observability/dbus_owners.py", 0o755, "Missing runtime files"),
        ("", 0o644, "Missing executable mode"),
        ("version", 0o755, "versions differ"),
        ("../private", 0o755, "Unsafe archive path"),
        ("local_config.py", 0o755, "Unexpected archive member"),
    ],
)
def test_reject_invalid_archive(tmp_path: Path, changed: str, mode: int, error: str) -> None:
    """Do not publish incomplete packages, local configuration or unsafe paths."""
    path = tmp_path / "source.tar.gz"
    make_archive(path, changed, mode)
    with pytest.raises(ValueError, match=error):
        VALIDATOR.validate_archive(str(path))
