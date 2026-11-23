"""Corruption and containment checks for exported binary visibility masks."""
import tempfile
import unittest
from pathlib import Path

from tools.ke_review import write_rgb_png
from tools.ke_visibility import read_capture_masks


class CaptureMaskValidationTests(unittest.TestCase):
    def test_rejects_corrupt_counts_nonbinary_and_outside_amodal(self):
        for case in ("counts", "nonbinary", "outside"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for kind in ("amodal", "visible"):
                    (root / "masks" / kind).mkdir(parents=True)
                write_rgb_png(root / "masks/amodal/1.png", 2, 2, bytes([255,255,255,0,0,0,0,0,0,0,0,0]))
                pix = [255,255,255,0,0,0,0,0,0,0,0,0]
                if case == "nonbinary":
                    pix[0] = 127
                if case == "outside":
                    pix = [0,0,0,255,255,255,0,0,0,0,0,0]
                write_rgb_png(root / "masks/visible/1.png", 2, 2, bytes(pix))
                meta = ('visibility_method:t="reverse_z_opaque_depth_compare_v1"\n'
                        f'amodal_pixels:i={2 if case == "counts" else 1}\n'
                        'visible_pixels:i=1\nunmatched_depth_pixels:i=0\nvisible_fraction:r=1\n')
                with self.assertRaises(ValueError):
                    read_capture_masks(root, "1.png", meta, 2, 2)


if __name__ == "__main__":
    unittest.main()
