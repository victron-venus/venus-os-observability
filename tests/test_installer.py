"""Exercise the actual shell installer against a private fake Venus filesystem."""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_in_place_update_preserves_supervisors_and_can_run_twice(tmp_path: Path) -> None:
    """Installing from cwd must preserve runtime inodes and boot ordering."""
    data = tmp_path / "data"
    services = tmp_path / "service"
    package = data / "venus-os-observability"
    package.mkdir(parents=True)
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
    (package / "update.sh").write_text(script)
    for item in ("run", "log/run"):
        source = package / "services/venus-os-observability" / item
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("#!/bin/sh\nexit 0\n")
    supervisor = package / "service/venus-os-observability/supervise"
    supervisor.mkdir(parents=True)
    sentinel = supervisor / "status"
    sentinel.write_text("live supervisor state")
    inode = supervisor.stat().st_ino
    (services / "venus-os-observability").symlink_to(supervisor.parent)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    for _ in range(2):
        subprocess.run(["sh", "update.sh", str(package)], cwd=package, env=env, check=True)
        assert supervisor.stat().st_ino == inode
        assert sentinel.read_text() == "live supervisor state"
        assert (package / "service/venus-os-observability/run").is_file()
    hook = (data / "rc.local").read_text()
    assert hook.count("# === venus-os-observability service persistence ===") == 1
    assert hook.index("# === end venus-os-observability ===") < hook.index("exit 0")
