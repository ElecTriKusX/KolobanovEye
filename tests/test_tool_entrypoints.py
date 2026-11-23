"""Verify relocated secondary tools still run as Python module entry points."""
import subprocess
import sys
import unittest
from pathlib import Path


class ToolsEntryPointTests(unittest.TestCase):
    def test_relocated_cli_tools_show_help(self):
        root = Path(__file__).resolve().parent.parent
        for name in ("ke_maps", "ke_validate", "ke_coco", "ke_review",
                     
                     "ke_pilot", "vendor_ooz"):
            with self.subTest(tool=name):
                process = subprocess.run(
                    [sys.executable, "-m", f"tools.{name}", "--help"],
                    cwd=root, text=True, capture_output=True, timeout=10)
                self.assertEqual(process.returncode, 0,
                                 f"{name}: {process.stdout}\n{process.stderr}")
                self.assertIn("usage:", process.stdout.lower())


if __name__ == "__main__":
    unittest.main()
