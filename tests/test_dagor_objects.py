import unittest
from tools.ke_dagor_objects import (upgrade_walk, upgrade_capture, WALK_START, WALK_END, WALK_EDITS,
    LIMIT_OLD, WRAPPER_ANCHOR, INCLUDE_ANCHOR, CAPTURE_ANCHOR)


class NativeInventoryInstallerTests(unittest.TestCase):
    def test_walk_idempotence(self):
        src=WALK_START+"\n"+"\n".join(old for old,new in WALK_EDITS)+"\n"+LIMIT_OLD+"\n"+LIMIT_OLD+"\n"+WALK_END+"\n"+WRAPPER_ANCHOR
        patched=upgrade_walk(src)
        self.assertEqual(upgrade_walk(patched),patched)
        self.assertIn("GatherRiTypeFlag::RiGenOnly, nullptr, ignore, callback, false, true",patched)

    def test_capture_idempotence(self):
        src=INCLUDE_ANCHOR+"\n"+CAPTURE_ANCHOR
        patched=upgrade_capture(src)
        self.assertEqual(upgrade_capture(patched),patched)
        self.assertIn("scene.prepareFixedClip(1024);",patched)
        self.assertIn('return true;\n  }\n'+CAPTURE_ANCHOR,patched)
