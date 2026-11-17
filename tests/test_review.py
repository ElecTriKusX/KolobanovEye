import tempfile
import unittest
from pathlib import Path

from tools.ke_pixel_debug import load_png
from tools.ke_review import draw_box, parse_vector, write_rgb_png


class ReviewTest(unittest.TestCase):
    def test_overlay_and_png_roundtrip(self):
        rgb = bytearray([40, 50, 60] * 100)
        draw_box(10, 10, rgb, [1, 2, 7, 6])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'review.png'
            write_rgb_png(path, 10, 10, rgb)
            width, height, decoded = load_png(path)
            self.assertEqual((width, height), (10, 10))
            self.assertEqual(decoded, rgb)
            self.assertEqual(decoded[(2 * 10 + 1) * 3:(2 * 10 + 1) * 3 + 3], b'\x00\xff\x00')
            with self.assertRaises(FileExistsError):
                write_rgb_png(path, 10, 10, rgb)

    def test_invalid_box_and_vector(self):
        with self.assertRaises(ValueError):
            draw_box(10, 10, bytearray(300), [9, 0, 3, 3])
        self.assertEqual(parse_vector('eye:p3=1, 2, 3\n', 'eye'), [1, 2, 3])
        with self.assertRaises(ValueError):
            parse_vector('eye:p3=nan,2,3\n', 'eye')
