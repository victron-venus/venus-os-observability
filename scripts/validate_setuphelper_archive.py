"""Validate a native source release without extracting or importing its code."""

import ast
import re
import sys
import tarfile
import tomllib
from pathlib import PurePosixPath

PREFIX = "venus-os-observability"
REQUIRED = {
    "version",
    "setup",
    "update.sh",
    "gitHubInfo",
    "pyproject.toml",
    "uv.lock",
    "setup.py",
    "config.example.yaml",
    "README.md",
    "LICENSE",
    "src/venus_observability/__init__.py",
    "src/venus_observability/__main__.py",
    "src/venus_observability/dbus_listener.py",
    "src/venus_observability/metrics.py",
    "src/venus_observability/correlation.py",
    "src/venus_observability/py.typed",
    "services/venus-os-observability/run",
    "services/venus-os-observability/log/run",
}
EXECUTABLES = {
    "setup",
    "update.sh",
    "services/venus-os-observability/run",
    "services/venus-os-observability/log/run",
}


def validate_archive(path: str) -> None:
    """Reject incomplete, unsafe or version-inconsistent source archives."""
    with tarfile.open(path, "r:gz") as archive:
        contents: dict[str, bytes] = {}
        for member in archive.getmembers():
            parts = PurePosixPath(member.name).parts
            if not parts or parts[0] != PREFIX or ".." in parts:
                raise ValueError(f"Unsafe archive path: {member.name}")
            if member.isdir():
                continue
            name = "/".join(parts[1:])
            if not member.isfile() or name not in REQUIRED or name in contents:
                raise ValueError(f"Unexpected archive member: {member.name}")
            if name in EXECUTABLES and member.mode & 0o111 != 0o111:
                raise ValueError(f"Missing executable mode: {name}")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"Unreadable archive member: {name}")
            contents[name] = stream.read()
        if missing := REQUIRED - contents.keys():
            raise ValueError(f"Missing runtime files: {sorted(missing)}")
        version = contents["version"].decode().strip()
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise ValueError("Expected a numeric patch release version")
        metadata = tomllib.loads(contents["pyproject.toml"].decode())
        module = ast.parse(contents["src/venus_observability/__init__.py"])
        versions = [
            ast.literal_eval(node.value)
            for node in module.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets
            )
        ]
        if metadata["project"]["version"] != version or versions != [version]:
            raise ValueError("Package, Python and SetupHelper versions differ")


if __name__ == "__main__":
    validate_archive(sys.argv[1])
