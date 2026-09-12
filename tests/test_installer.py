"""Exercise the actual shell installer against a private fake Venus filesystem."""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("in_place", [True, False])
def test_update_preserves_supervisors_and_can_run_twice(tmp_path: Path, in_place: bool) -> None:
    """In-place and archive installs preserve service/logger inodes and local files."""
    # The separate filesystem sentinels make preservation failures identifiable.
    # pylint: disable=too-many-locals
    data = tmp_path / "data"
    services = tmp_path / "service"
    package = data / "venus-os-observability"
    package.mkdir(parents=True)
    source_dir = package if in_place else tmp_path / "release"
    source_dir.mkdir(exist_ok=True)
    services.mkdir()
    (data / "rc.local").write_text("#!/bin/sh\nexit 0\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for command in ("svc", "sleep", "python3"):
        path = bin_dir / command
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
    script = (REPO / "update.sh").read_text()
    script = script.replace("/data/", f"{data}/").replace("/service/", f"{services}/")
    script = script.replace(f"$INSTALL_DIR{services}/", "$INSTALL_DIR/service/")
    script = script.replace("/var/log/", f"{tmp_path}/log/")
    (source_dir / "update.sh").write_text(script)
    (source_dir / "version").write_text("0.1.4\n")
    for item in ("run", "log/run"):
        source = source_dir / "services/venus-os-observability" / item
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("#!/bin/sh\nexit 0\n")
    supervisor = package / "service/venus-os-observability/supervise"
    supervisor.mkdir(parents=True)
    log_supervisor = supervisor.parent / "log/supervise"
    log_supervisor.mkdir(parents=True)
    persistent = [supervisor.parent, supervisor, log_supervisor.parent, log_supervisor]
    inodes = {path: path.stat().st_ino for path in persistent}
    sentinels = [supervisor / "status", log_supervisor / "status", package / "config.yaml"]
    venv = package / ".venv2"
    venv.mkdir()
    sentinels.append(venv / "installed-dependencies")
    for sentinel in sentinels:
        sentinel.write_text("preserve this local state")
    (services / "venus-os-observability").symlink_to(supervisor.parent)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    for _ in range(2):
        subprocess.run(["sh", "update.sh", str(package)], cwd=source_dir, env=env, check=True)
        assert all(path.stat().st_ino == inode for path, inode in inodes.items())
        assert all(sentinel.read_text() == "preserve this local state" for sentinel in sentinels)
        assert (package / "service/venus-os-observability/run").is_file()
        assert (package / "version").read_text() == "0.1.4\n"
    hook = (data / "rc.local").read_text()
    assert hook.count("# === venus-os-observability service persistence ===") == 1
    assert hook.index("# === end venus-os-observability ===") < hook.index("exit 0")
