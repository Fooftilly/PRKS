"""Editor sessions are non-suspendable owner resources with captured tickets."""
import subprocess
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
_KINDS = (
    ("workRoleEditor", _PROJECT / "frontend" / "js" / "work-role-editor.js", "function mount(", "async function"),
    ("workTagEditor", _PROJECT / "frontend" / "js" / "work-tag-editor.js", "function mount(", "async function"),
    ("workSourceEditor", _PROJECT / "frontend" / "js" / "work-source-editor.js", "function mount(", "async function"),
    ("workMetadataEditor", _PROJECT / "frontend" / "js" / "work-metadata-editor.js", "function mount(", "async function"),
    ("folderTagEditor", _PROJECT / "frontend" / "js" / "folder-tag-editor.js", "function mount(", "async function"),
)


def _slice(text: str, start: str, end: str) -> str:
    at = text.index(start)
    stop = text.find(end, at + len(start))
    return text[at:] if stop == -1 else text[at:stop]


class EditorSessionLifetimeContractTests(unittest.TestCase):
    def test_production_mounts_capture_tickets_on_the_registry(self):
        registry = (_PROJECT / "frontend-app" / "src" / "lifecycle" / "owner-resource.ts").read_text(encoding="utf-8")
        built = (_PROJECT / "frontend" / "js" / "owner-resource.js").read_text(encoding="utf-8")
        self.assertIn("src/lifecycle/owner-resource.ts", built)
        for kind, path, start, end in _KINDS:
            text = path.read_text(encoding="utf-8")
            self.assertIn(f"'{kind}'", registry)
            self.assertIn(f'"{kind}"', built)
            self.assertNotIn("setResource(", text)
            mount = _slice(text, start, end)
            ticket_at = mount.index("ctx.resourceTicket()")
            self.assertLess(ticket_at, mount.index("ctx.registerResource(ticket"))
            self.assertLess(mount.index("attached === 'rejected'"), mount.index("prksSync.subscribe"))
            live = _slice(text, "function live(", "function ")
            self.assertIn(f"getResource('{kind}') === state", live)

    def test_runtime_editor_session_lifetime(self):
        proc = subprocess.run(
            ["node", str(_PROJECT / "tests" / "browser" / "run_editor_session_lifetime_selftest.js")],
            cwd=_PROJECT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("checks passed", proc.stdout)
        self.assertIn("0 failed", proc.stdout)


if __name__ == "__main__":
    unittest.main()
