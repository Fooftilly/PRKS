"""Operation dependencies: ordering, outcome and retention."""
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'js'


class OperationDependencyTests(unittest.TestCase):
    def test_runtime_selftests(self):
        proc = subprocess.run(
            ['node', str(ROOT / 'tests' / 'browser' / 'run_operation_dependency_selftest.js')],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('checks passed', proc.stdout)

    def test_success_and_consumption_are_distinguished_in_both_places(self):
        """Two components decide whether a prerequisite is satisfied: the store,
        when it attaches a dependency, and the coordinator, when it decides what
        may be sent. Both must judge the OUTCOME rather than the status -- a
        terminally refused operation is marked `acknowledged` too, because
        nothing further is owed on it.
        """
        store = (FRONTEND / 'local-store.js').read_text()
        runtime = (FRONTEND / 'sync-runtime.js').read_text()
        for source, name in ((store, 'local-store.js'), (runtime, 'sync-runtime.js')):
            with self.subTest(module=name):
                at = source.index('function dependencySucceeded(')
                body = source[at: source.index('\n', source.index('return', at))]
                self.assertIn('server_result', body,
                              'the recorded result is what separates applied from refused')

    def test_the_server_refusal_is_terminal(self):
        runtime = (FRONTEND / 'sync-runtime.js').read_text()
        listed = runtime[runtime.index('const PROTOCOL_ERRORS'):]
        listed = listed[: listed.index(';')]
        self.assertIn('UNSATISFIED_DEPENDENCY', listed)

    def test_the_server_and_client_agree_on_the_refusal_code(self):
        from backend import sync_protocol  # noqa: F401
        backend = (ROOT / 'backend' / 'sync_protocol.py').read_text()
        codes = set(re.findall(r'"code": "([A-Z_]+)"', backend))
        runtime = (FRONTEND / 'sync-runtime.js').read_text()
        listed = runtime[runtime.index('const PROTOCOL_ERRORS'):]
        listed = set(re.findall(r"'([A-Z_]+)'", listed[: listed.index(';')]))
        # Every dependency-related refusal the protocol layer can emit must be
        # one the coordinator treats as terminal; otherwise it retries forever.
        for code in codes:
            if 'DEPENDENC' in code:
                with self.subTest(code=code):
                    self.assertIn(code, listed)
