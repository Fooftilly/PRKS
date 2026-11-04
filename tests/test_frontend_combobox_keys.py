"""Work, Folder, and Processing tag pickers share one combobox keyboard model."""
from __future__ import annotations

import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class ComboboxKeysTests(unittest.TestCase):
    def test_tag_editors_commit_through_the_shared_keyboard_model(self):
        for name in ("work-tag-editor.js", "folder-tag-editor.js"):
            src = _read(os.path.join(_ROOT, "frontend", "js", name))
            self.assertIn("prksBindComboboxKeys", src)
            self.assertIn("enterCreates: true", src)
            # Pointer and keyboard share the row action, including durable create.
            self.assertIn("div.onmousedown", src)
            self.assertIn("prksSubmitNewTag", src)

    def test_script_loads_before_the_new_file_binder(self):
        html = _read(os.path.join(_ROOT, "frontend", "index.html"))
        sw = _read(os.path.join(_ROOT, "frontend", "sw.js"))
        self.assertLess(html.index("/js/combobox-keys.js"), html.index("/js/ui.js"))
        self.assertIn("'/js/combobox-keys.js'", sw)
        card = _read(os.path.join(
            _ROOT, "frontend-app", "src", "features", "processing", "ProcessingFileCard.vue",
        ))
        self.assertIn("prksBindComboboxKeys", card)
        self.assertIn("enterCreates: true", card)


if __name__ == "__main__":
    unittest.main()
