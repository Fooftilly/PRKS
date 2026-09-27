"""Storybook CI must give every Fast Static Analysis event a real outcome.

pull_request and push stay fail-closed. workflow_dispatch builds with no
diff. A declared event must not fall through to the unknown-event failure.
"""
import subprocess
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _ROOT / ".github" / "workflows" / "static-analysis.yml"
_DECLARED = ("push", "pull_request", "workflow_dispatch")


def _workflow_text():
    return _WORKFLOW.read_text(encoding="utf-8")


def _declared_events(text):
    lines = text.splitlines()
    start = lines.index("on:") + 1
    events = []
    for line in lines[start:]:
        if line and not line.startswith(" "):
            break
        if line.startswith("  ") and not line.startswith("   ") and line.rstrip().endswith(":"):
            events.append(line.strip()[:-1])
    return tuple(events)


def _storybook_script(text):
    start = text.index("          pattern='^(frontend-app/")
    end = text.index("          npm run build-storybook --prefix frontend-app")
    lines = []
    for line in text[start:end].splitlines():
        if line.startswith("          "):
            lines.append(line[10:])
        else:
            lines.append(line)
    script = "\n".join(lines)
    script = script.replace("${{ github.event_name }}", "$EVENT_NAME")
    return "set -euo pipefail\n" + script + "\necho WOULD_BUILD\nexit 0\n"


def _branch_body(script, event):
    needle = '[[ "$event_name" == "%s" ]]' % event
    lines = script.splitlines()
    start = next(i for i, line in enumerate(lines) if needle in line)
    body = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("elif ") or stripped.startswith("else") or stripped == "fi":
            break
        body.append(line)
    return "\n".join(body)


def _run_gate(script, event, *, base="", before=""):
    completed = subprocess.run(
        ["bash", "-c", script],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        env={
            "EVENT_NAME": event,
            "PRKS_PR_BASE_SHA": base,
            "PRKS_PUSH_BEFORE": before,
            "PATH": "/usr/bin:/bin",
        },
    )
    output = completed.stdout + completed.stderr
    return completed.returncode, output


class StorybookEventGateTests(unittest.TestCase):
    def test_every_declared_event_has_a_branch(self):
        text = _workflow_text()
        events = _declared_events(text)
        self.assertEqual(events, _DECLARED)
        script = _storybook_script(text)
        for event in events:
            self.assertIn('[[ "$event_name" == "%s" ]]' % event, script)

    def test_workflow_dispatch_builds_without_a_diff(self):
        script = _storybook_script(_workflow_text())
        body = _branch_body(script, "workflow_dispatch")
        self.assertIn("run_storybook=true", body)
        self.assertNotIn("git ", body)
        self.assertNotIn("fetch_baseline", body)
        self.assertNotIn("apply_successful_diff", body)
        status, output = _run_gate(script, "workflow_dispatch")
        self.assertEqual(status, 0, output)
        self.assertIn("WOULD_BUILD", output)
        self.assertNotIn("Skipping Storybook", output)
        self.assertNotIn("No Storybook diff baseline", output)

    def test_pull_request_and_push_stay_fail_closed(self):
        script = _storybook_script(_workflow_text())
        self.assertIn("git diff -z --name-only", script)
        self.assertIn("grep -zEq", script)
        missing_base, missing_out = _run_gate(script, "pull_request")
        self.assertNotEqual(missing_base, 0, missing_out)
        self.assertIn("no base SHA", missing_out)
        self.assertNotIn("WOULD_BUILD", missing_out)
        zero = "0000000000000000000000000000000000000000"
        zero_status, zero_out = _run_gate(script, "push", before=zero)
        self.assertNotEqual(zero_status, 0, zero_out)
        self.assertIn("missing or zero", zero_out)
        self.assertNotIn("WOULD_BUILD", zero_out)
        self.assertNotIn("Skipping Storybook", zero_out)

    def test_undeclared_event_fails(self):
        script = _storybook_script(_workflow_text())
        status, output = _run_gate(script, "schedule")
        self.assertNotEqual(status, 0, output)
        self.assertIn("No Storybook diff baseline", output)
        self.assertNotIn("WOULD_BUILD", output)
        self.assertNotIn("Skipping Storybook", output)


if __name__ == "__main__":
    unittest.main()
