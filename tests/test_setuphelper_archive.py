"""Check release archives before publishing native installer inputs."""

import importlib.util
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts.package_release import build_package

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_setuphelper_archive", REPO / "scripts/validate_setuphelper_archive.py"
)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def make_archive(path: Path, changed: str = "", mode: int = 0o755) -> None:
    """Build an archive from the actual release inputs, with one optional defect."""
    with tarfile.open(path, "w:gz") as archive:
        for name in sorted(VALIDATOR.REQUIRED):
            if name == changed == "src/venus_observability/metrics.py":
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
    assets = build_package(source, version, "rc", tmp_path / "assets")
    assert len(assets) == 1
    VALIDATOR.validate_archive(str(assets[0]))


@pytest.mark.parametrize(
    "changed,mode,error",
    [
        ("src/venus_observability/metrics.py", 0o755, "Missing runtime files"),
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
