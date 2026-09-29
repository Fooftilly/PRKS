"""Work role controls stay on the durable API and refuse a replaced owner."""
import subprocess
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
_UI = (_PROJECT / "frontend" / "js" / "ui.js").read_text(encoding="utf-8")
_APP = (_PROJECT / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
_ROLES = (_PROJECT / "frontend" / "js" / "work-role-editor.js").read_text(encoding="utf-8")


class WorkRoleEditorContractTests(unittest.TestCase):
    def test_confirmations_recheck_the_owner_before_the_durable_call(self):
        still = "prksWorkRoleIntentStill("
        unlink = _UI[_UI.index("async function prksRemoveWorkRoleLink"):_UI.index("async function prksRefreshUiAfterWorkRoleRemoved")]
        credit = _UI[_UI.index("async function prksEditRoleCreditOnWork"):_UI.index("function prksSplitTypedPersonName")]
        add = _UI[_UI.index("async function addRoleToWorkFromMetaEditor"):_UI.index("async function prepareRoleModal")]
        modal = _APP[_APP.index("saveRoleBtn.onclick"):_APP.index("closeModals();", _APP.index("saveRoleBtn.onclick"))]
        for body, name in ((unlink, "unlink"), (credit, "credit"), (add, "add"), (modal, "modal")):
            write = body.index("prksSaveWorkPersonRoleDurably")
            self.assertLess(body.index(still), write, name)
        self.assertIn("prksTabContextOwnsEntityRoute", unlink)
        self.assertNotIn("listOperations", _UI[_UI.index("function prksRefreshOwnedWorkPanelRead"):_UI.index("function prksWorkRoleIntentStill")])

    def test_save_rechecks_the_mounted_owner_before_the_store(self):
        save = _ROLES[_ROLES.index("async function save("):_ROLES.index("root.prksMountWorkRoleEditor")]
        write = save.index("store.saveWorkPersonRole")
        self.assertLess(save.index("if (mounted && !live(ctx, state)) return"), write)
        self.assertLess(save.index("prksOwnerTabId"), write)
        self.assertLess(save.index("typeof still === 'function'"), write)
        self.assertNotIn("prksVueRefreshWorkPanelRead", _ROLES)
        self.assertIn("prksRefreshOwnedWorkPanelRead", _ROLES)
        self.assertIn("prksEffectiveWorkDetailRoles", _ROLES)

    def test_runtime_owner_and_paint(self):
        proc = subprocess.run(
            ["node", str(_PROJECT / "tests" / "browser" / "run_work_role_editor_selftest.js")],
            cwd=_PROJECT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("checks passed", proc.stdout)


if __name__ == "__main__":
    unittest.main()
