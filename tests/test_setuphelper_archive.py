"""Check release archives before publishing native installer inputs."""

import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

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
