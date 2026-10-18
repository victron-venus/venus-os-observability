#!/usr/bin/env python3
"""Stage the existing, evidence-verified package distributions for PyPI OIDC."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Callable
from email.parser import BytesParser
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING or __package__:
    from .publish_verified import pypi_commands, verified_assets
    from .release_control import GitHub, JSONObject, ReleaseError, digest, require
else:
    from publish_verified import pypi_commands, verified_assets
    from release_control import GitHub, JSONObject, ReleaseError, digest, require

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "victron-venus/venus-os-observability"
PROJECT = "venus-os-observability"
METADATA_LIMIT = 1024 * 1024


def check_metadata(path: Path, version: str) -> None:
    """Read metadata without extracting or executing anything from the package."""
    stem = f"venus_os_observability-{version}"
    if path.suffix == ".whl":
        require(
            path.name.startswith(stem + "-"),
            "Wheel filename has the wrong project/version",
        )
        with zipfile.ZipFile(path) as wheel:
            entries = [
                entry
                for entry in wheel.infolist()
                if entry.filename.endswith("/METADATA")
            ]
            require(len(entries) == 1, "Wheel must contain one METADATA file")
            wheel_entry = entries[0]
            require(
                wheel_entry.filename == stem + ".dist-info/METADATA",
                "Wrong wheel metadata path",
            )
            require(
                wheel_entry.file_size <= METADATA_LIMIT, "Wheel metadata is too large"
            )
            raw = wheel.read(wheel_entry)
    else:
        require(
            path.name == stem + ".tar.gz",
            "Sdist filename has the wrong project/version",
        )
        with tarfile.open(path, "r:gz") as sdist:
            tar_entries = [entry for entry in sdist if entry.name == stem + "/PKG-INFO"]
            require(len(tar_entries) == 1, "Sdist must contain one root PKG-INFO file")
            tar_entry = tar_entries[0]
            require(
                tar_entry.isfile() and tar_entry.size <= METADATA_LIMIT,
                "Invalid sdist metadata",
            )
            stream = sdist.extractfile(tar_entry)
            require(stream is not None, "Missing sdist metadata")
            assert stream is not None
            with stream:
                raw = stream.read(METADATA_LIMIT + 1)
    metadata = BytesParser().parsebytes(raw)
    names = metadata.get_all("Name", [])
    versions = metadata.get_all("Version", [])
    require(
        len(names) == 1 and re.sub(r"[-_.]+", "-", names[0]).lower() == PROJECT,
        "Distribution metadata has the wrong project name",
    )
    require(versions == [version], "Distribution metadata has the wrong version")


def stage(tag: str, output: Path) -> str:
    """Export only policy-selected distributions after all original checks pass."""
    require(
        not output.exists() and not output.is_symlink(),
        "Staging output must not already exist",
    )
    policy = json.loads((ROOT / ".release-policy.json").read_text())
    require(policy["repository"] == REPOSITORY, "Unexpected publication repository")
    with tempfile.TemporaryDirectory(prefix="verified-pypi-source-") as temp:
        source = Path(temp)
        manifest = cast(Callable[[GitHub, str, Path], JSONObject], verified_assets)(
            GitHub(REPOSITORY), tag, source
        )
        # Reuse the vendored adapter's immutable-policy selection, without executing
        # its twine commands. The final uploader is the official OIDC action.
        check_command = cast(
            Callable[[JSONObject, JSONObject, Path], list[list[str]]],
            pypi_commands,
        )(policy, manifest, source)[0]
        require(
            check_command[:4] == ["python3", "-m", "twine", "check"],
            "Unexpected adapter",
        )
        selected = [Path(name) for name in check_command[4:]]
        require(
            len(selected) == 2
            and sum(path.suffix == ".whl" for path in selected) == 1
            and sum(path.name.endswith(".tar.gz") for path in selected) == 1,
            "Expected exactly one verified wheel and one sdist",
        )
        expected = {
            cast(str, asset["name"]): asset
            for asset in cast(list[JSONObject], manifest["assets"])
        }
        files = []
        payloads = {}
        for path in selected:
            require(
                path.parent == source and path.is_file() and not path.is_symlink(),
                "Invalid asset",
            )
            check_metadata(path, cast(str, manifest["version"]))
            raw = path.read_bytes()
            asset = expected[path.name]
            require(
                len(raw) == asset["size"] and digest(raw) == asset["sha256"],
                "Selected distribution differs from verified evidence",
            )
            files.append({"name": path.name, "size": len(raw), "sha256": digest(raw)})
            payloads[path.name] = raw
        receipt = {
            "schema": 1,
            "repository": REPOSITORY,
            "project": PROJECT,
            "stable_tag": tag,
            "version": manifest["version"],
            "candidate_tag": manifest["tag"],
            "source_sha": manifest["source_sha"],
            "files": files,
        }
        receipt_bytes = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
        # Prepare atomically so a failed check never leaves publishable output.
        with tempfile.TemporaryDirectory(
            prefix="verified-pypi-stage-", dir=output.parent
        ) as temp:
            staging = Path(temp) / "payload"
            (staging / "dist").mkdir(parents=True)
            for name, content in payloads.items():
                (staging / "dist" / name).write_bytes(content)
            (staging / "receipt.json").write_bytes(receipt_bytes)
            staging.rename(output)
    return digest(receipt_bytes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, default=Path("verified-pypi"))
    args = parser.parse_args()
    try:
        print(stage(args.tag, args.output))
        return 0
    except (
        ReleaseError,
        OSError,
        ValueError,
        KeyError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"stage-verified-pypi: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
