"""Keep mandatory packaged documentation present without running a package build."""

import ast
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "scripts/validate_setuphelper_archive.py"
CONSTANT = "REQUIRED"


class PackageInputTests(unittest.TestCase):
    """A documentation-only change must not remove a required package input."""

    def test_mandatory_package_inputs_are_tracked_regular_files(self):
        """Read the authoritative literal file list without executing the installer."""
        tree = ast.parse((ROOT / SOURCE).read_text())
        matches = [
            node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == CONSTANT for target in node.targets
            )
        ]
        self.assertEqual(len(matches), 1, "Review changed package input declaration")
        paths = ast.literal_eval(matches[0])
        self.assertIsInstance(paths, (tuple, set))
        self.assertTrue(paths)
        result = subprocess.run(  # nosec B603, B607 - fixed read-only Git index query.
            ["git", "ls-files", "--stage", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        tracked = {}
        for entry in result.stdout.split("\0"):
            if not entry:
                continue
            metadata, name = entry.split("\t", 1)
            mode, _, stage = metadata.split()
            if stage == "0":
                tracked[name] = mode
        for name in paths:
            with self.subTest(path=name):
                self.assertIsInstance(name, str)
                path = Path(name)
                self.assertFalse(path.is_absolute())
                self.assertNotIn("..", path.parts)
                self.assertIn(tracked.get(name), {"100644", "100755"})
                self.assertFalse((ROOT / path).is_symlink())
                self.assertTrue((ROOT / path).is_file(), f"Missing mandatory package input: {name}")
