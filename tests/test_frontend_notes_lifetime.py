"""Notes sessions are owner resources; no production ctx.resources entry has a disposer."""
import re
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
_JS = _PROJECT / "frontend" / "js"
_WORKS = _JS / "components" / "works.js"
_UI = _JS / "ui.js"
_NOTES_STATE = _JS / "work-notes-state.js"
_REGISTRY = _PROJECT / "frontend-app" / "src" / "lifecycle" / "owner-resource.ts"
_BUILT = _JS / "owner-resource.js"


def _slice(text: str, start: str, end: str) -> str:
    at = text.index(start)
    stop = text.find(end, at + len(start))
    return text[at:] if stop == -1 else text[at:stop]


def _production_sources():
    for path in sorted(_JS.rglob("*.js")):
        if path.name in ("tab-context.js", "owner-resource.js", "tab-leave.js"):
            continue
        yield path, path.read_text(encoding="utf-8")


class NotesLifetimeContractTests(unittest.TestCase):
    def test_notes_kinds_are_registered(self):
        registry = _REGISTRY.read_text(encoding="utf-8")
        built = _BUILT.read_text(encoding="utf-8")
        for kind in ("workNotes", "privateNotesEditor"):
            self.assertIn(f"'{kind}'", registry)
            self.assertIn(f'"{kind}"', built)
        editor_kinds = _slice(registry, "const EDITOR_SESSION_KINDS", "] as const")
        self.assertIn("'privateNotesEditor'", editor_kinds)
        self.assertNotIn("'workNotes'", editor_kinds)

    def test_no_production_resource_has_a_disposer(self):
        retired = ("workNotes", "privateNotesEditor", "workNotesSyncBound", "workNotesSideRo")
        for path, text in _production_sources():
            for kind in retired:
                self.assertNotIn(f"setResource('{kind}'", text, f"{path.name}: {kind}")
            for call in re.finditer(r"\.setResource\(", text):
                depth = 0
                commas = 0
                for ch in text[call.end():]:
                    if ch in "([{":
                        depth += 1
                    elif ch in ")]}":
                        if depth == 0:
                            break
                        depth -= 1
                    elif ch == "," and depth == 0:
                        commas += 1
                self.assertLessEqual(commas, 1, f"{path.name}: setResource with a disposer")

    def test_research_notes_ticket_is_captured_when_the_paint_begins(self):
        works = _WORKS.read_text(encoding="utf-8")
        render = _slice(works, "async function renderWorkDetails(", "\nfunction ")
        ticket_at = render.index("ctx.resourceTicket(generation)")
        self.assertLess(ticket_at, render.index("await "))
        self.assertLess(ticket_at, render.index("setTimeout("))
        self.assertIn("initEasyMDE(ctx, work, notesTicket)", render)
        init = _slice(works, "function initEasyMDE(", "\nwindow.initEasyMDE")
        self.assertLess(init.index("resourceRegistry.accepts(ticket)"), init.index("new EasyMDE("))
        register = _slice(init, "ctx.registerResource(ticket", "});")
        self.assertIn("kind: 'workNotes'", register)
        self.assertIn("suspendable: true", register)
        self.assertLess(init.index("attached === 'rejected'"), init.index("prksSync.subscribe"))
        self.assertIn("ctx.getResource('workNotes') === notes", _slice(works, "function prksWorkNotesSessionLive(", "\n}"))
        self.assertNotIn("workNotesSideRo", works)

    def test_private_notes_register_before_listening(self):
        ui = _UI.read_text(encoding="utf-8")
        bind = _slice(ui, "function prksBindPrivateNotesField(", "\nfunction ")
        ticket_at = bind.index("ctx.resourceTicket()")
        register_at = bind.index("ctx.registerResource(ticket")
        self.assertLess(ticket_at, register_at)
        self.assertLess(register_at, bind.index("attached === 'rejected'"))
        self.assertLess(bind.index("attached === 'rejected'"), bind.index("ta.dataset.prksNotesBound = '1'"))
        self.assertLess(bind.index("attached === 'rejected'"), bind.index("ta.addEventListener('input'"))
        self.assertIn("suspendable: false", bind)
        live = _slice(ui, "function prksPrivateNotesOwnerCurrent(", "\n}")
        self.assertIn("getResource('privateNotesEditor') === editor", live)

    def test_busy_retry_listener_is_owner_scoped(self):
        ui = _UI.read_text(encoding="utf-8")
        retry = _slice(ui, "function prksSchedulePrivateNoteBusyRetry(", "\nfunction ")
        self.assertIn("editor.ctx.destroyed", retry)
        self.assertLess(retry.index("prksSync.subscribe("), retry.index("editor.ctx.registerCleanup(stopRetry)"))
        timer = _slice(retry, "window.setTimeout(", "}, 400)")
        self.assertLess(timer.index("stopRetry()"), timer.index("tryAgain()"))

    def test_notes_acknowledgement_subscription_is_owner_scoped(self):
        state = _NOTES_STATE.read_text(encoding="utf-8")
        bind = _slice(state, "function bindSync(", "\n    }\n")
        self.assertIn("ctx.registerCleanup(", bind)
        self.assertNotIn("setResource", bind)


    def test_notes_viewport_listener_is_an_app_singleton(self):
        # The one global resize listener is installed once per page, holds no
        # owner, and walks the mounted owners when it fires; per-route
        # lifetimes (drag, split observer) go through registerCleanup.
        works = _WORKS.read_text(encoding="utf-8")
        guard = _slice(works, "if (!window.__prksWorkNotesViewportBound)", "\n    }\n")
        self.assertIn("window.__prksWorkNotesViewportBound = true", guard)
        self.assertIn("prksForEachMountedTabContext(", guard)
        self.assertNotIn("ctx", guard.replace("prksForEachMountedTabContext", ""))
        split = _slice(works, "new ResizeObserver(function () {", "\n    }\n")
        self.assertIn("ctx.registerCleanup(", split)
        drag = _slice(works, "unregisterCleanup = ctx.registerCleanup(endDrag)", "});")
        self.assertIn("document.addEventListener('pointermove', onMove, true)", drag)

if __name__ == "__main__":
    unittest.main()
