"""Keep OIDC isolated and publish exactly the evidence-verified distributions."""

import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
stage = importlib.import_module("stage_verified_pypi")

TAG = "v0.1.4"
STEM = "venus_os_observability-0.1.4"
WHEEL = STEM + "-py3-none-any.whl"
SDIST = STEM + ".tar.gz"


def write_distributions(directory, *, name=stage.PROJECT, version="0.1.4"):
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n".encode()
    with zipfile.ZipFile(directory / WHEEL, "w") as wheel:
        wheel.writestr(STEM + ".dist-info/METADATA", metadata)
    with tarfile.open(directory / SDIST, "w:gz") as sdist:
        entry = tarfile.TarInfo(STEM + "/PKG-INFO")
        entry.size = len(metadata)
        sdist.addfile(entry, io.BytesIO(metadata))
    # A valid release also includes artifacts that must never reach PyPI.
    (directory / "container.oci.tar").write_bytes(b"not a Python distribution")


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        write_distributions(self.source)
        self.output = self.root / "verified-pypi"
        self.policy = {
            "repository": stage.REPOSITORY,
            "pypi_assets": ["*.whl", "venus_os_observability-*.tar.gz"],
        }
        (self.root / ".release-policy.json").write_text(json.dumps(self.policy))
        self.addCleanup(patch.stopall)
        patch.object(stage, "ROOT", self.root).start()
        self.workflow = yaml.load(
            (ROOT / ".github/workflows/publish-pypi.yml").read_text(), Loader=yaml.BaseLoader
        )

    def verified(self, _gh, _tag, directory):
        for path in self.source.iterdir():
            shutil.copyfile(path, directory / path.name)
        return {
            "version": "0.1.4",
            "tag": "v0.1.4-rc.1",
            "source_sha": "a" * 40,
            "source_policy": {"data": self.policy},
            "assets": [
                {"name": p.name, "size": p.stat().st_size, "sha256": stage.digest(p.read_bytes())}
                for p in directory.iterdir()
            ],
        }

    def export(self):
        with patch.object(stage, "verified_assets", side_effect=self.verified) as verify:
            digest = stage.stage(TAG, self.output)
        self.assertEqual(verify.call_args.args[1], TAG)
        return digest

    def test_exports_only_verified_bytes_and_bound_receipt_without_upload(self):
        with patch("publish_verified.subprocess.run") as run:
            receipt_digest = self.export()
        run.assert_not_called()
        self.assertEqual({p.name for p in (self.output / "dist").iterdir()}, {WHEEL, SDIST})
        for name in (WHEEL, SDIST):
            self.assertEqual(
                (self.output / "dist" / name).read_bytes(), (self.source / name).read_bytes()
            )
        raw = (self.output / "receipt.json").read_bytes()
        self.assertEqual(stage.digest(raw), receipt_digest)
        self.assertEqual(json.loads(raw)["stable_tag"], TAG)

    def test_invalid_stable_tag_fails_before_github_and_output(self):
        for tag in ("", "v0.1.4-rc.1", "main", "v0.1.4; echo bad"):
            with self.subTest(tag=tag), patch.object(stage, "GitHub") as github:
                with self.assertRaises(stage.ReleaseError):
                    stage.stage(tag, self.output)
                github.return_value.api.assert_not_called()
                self.assertFalse(self.output.exists())

    def test_verification_failure_cannot_leave_publishable_output(self):
        for message in (
            "Source run identity mismatch",
            "Evidence missing",
            "Stable payload changed",
        ):
            with (
                self.subTest(message=message),
                patch.object(stage, "verified_assets", side_effect=stage.ReleaseError(message)),
                patch.object(stage, "pypi_commands") as select,
            ):
                with self.assertRaises(stage.ReleaseError):
                    stage.stage(TAG, self.output)
                select.assert_not_called()
                self.assertFalse(self.output.exists())

    def test_metadata_project_version_and_duplicate_headers_are_rejected(self):
        for name, version in (
            ("other-project", "0.1.4"),
            (stage.PROJECT, "0.1.5"),
            (stage.PROJECT + "\nName: other", "0.1.4"),
        ):
            with self.subTest(name=name, version=version):
                write_distributions(self.source, name=name, version=version)
                with self.assertRaises(stage.ReleaseError):
                    self.export()
                self.assertFalse(self.output.exists())

    def test_missing_extra_and_renamed_distributions_are_rejected(self):
        for change in ("missing", "extra", "renamed"):
            with self.subTest(change=change):
                write_distributions(self.source)
                if change == "missing":
                    (self.source / SDIST).unlink()
                elif change == "extra":
                    shutil.copyfile(self.source / WHEEL, self.source / "extra.whl")
                else:
                    (self.source / WHEEL).rename(self.source / "wrong.whl")
                with self.assertRaises(stage.ReleaseError):
                    self.export()
                self.assertFalse(self.output.exists())
                for path in self.source.iterdir():
                    path.unlink()

    def test_existing_directory_or_dangling_symlink_is_not_replaced(self):
        for kind in ("directory", "symlink"):
            with self.subTest(kind=kind):
                if kind == "directory":
                    self.output.mkdir()
                else:
                    self.output.symlink_to(self.root / "absent")
                with self.assertRaises(stage.ReleaseError):
                    self.export()
                if kind == "directory":
                    self.output.rmdir()
                else:
                    self.output.unlink()

    def test_publication_is_manual_main_only_and_oidc_is_isolated(self):
        self.assertEqual(set(self.workflow["on"]), {"workflow_dispatch"})
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        jobs = self.workflow["jobs"]
        self.assertEqual(set(jobs), {"verify-pypi", "publish-pypi"})
        for job in jobs.values():
            self.assertIn("github.repository == 'victron-venus/venus-os-observability'", job["if"])
            self.assertIn("github.event_name == 'workflow_dispatch'", job["if"])
            self.assertIn("github.ref == 'refs/heads/main'", job["if"])
        self.assertEqual(
            jobs["verify-pypi"]["permissions"], {"contents": "read", "actions": "read"}
        )
        publisher = jobs["publish-pypi"]
        self.assertEqual(publisher["permissions"], {"contents": "read", "id-token": "write"})
        self.assertEqual(publisher["needs"], "verify-pypi")
        self.assertEqual(publisher["environment"], "pypi")
        self.assertFalse(
            any(step.get("uses", "").startswith("actions/checkout@") for step in publisher["steps"])
        )
        action = publisher["steps"][-1]
        self.assertEqual(
            action["uses"], "pypa/gh-action-pypi-publish@7245906fb61fa33a13554f9d5fb856fe61758086"
        )
        self.assertEqual(action["with"]["skip-existing"], "false")
        self.assertNotIn("password", action["with"])

    def test_existing_validation_workflow_remains_required(self):
        policy = json.loads((ROOT / ".release-policy.json").read_text())
        self.assertIn("ci.yml", policy["validation_workflows"])
        gate = yaml.load(
            (ROOT / ".github/workflows/quality-gate.yml").read_text(), Loader=yaml.BaseLoader
        )
        self.assertEqual(gate["jobs"]["check-0"]["uses"], "./.github/workflows/ci.yml")

    def test_publish_consumer_rejects_transfer_mutation(self):
        digest = self.export()
        step = next(
            step for step in self.workflow["jobs"]["publish-pypi"]["steps"] if "run" in step
        )
        environment = {**os.environ, "RECEIPT_SHA256": digest, "STABLE_TAG": TAG}

        def validate():
            return subprocess.run(
                ["bash", "-e", "-c", step["run"]],
                cwd=self.root,
                env=environment,
                capture_output=True,
                check=False,
            )

        self.assertEqual(validate().returncode, 0)
        wheel = self.output / "dist" / WHEEL
        original = wheel.read_bytes()
        wheel.write_bytes(b"replacement")
        self.assertNotEqual(validate().returncode, 0)
        wheel.write_bytes(original)
        extra = self.output / "dist" / "injected.whl"
        extra.write_bytes(b"extra")
        self.assertNotEqual(validate().returncode, 0)
        extra.unlink()
        wheel.unlink()
        wheel.symlink_to(self.source / WHEEL)
        self.assertNotEqual(validate().returncode, 0)
        wheel.unlink()
        wheel.write_bytes(original)
        receipt = self.output / "receipt.json"
        receipt.write_bytes(receipt.read_bytes() + b" ")
        self.assertNotEqual(validate().returncode, 0)


if __name__ == "__main__":
    unittest.main()
