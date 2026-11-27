"""The backup progress line contract (#1431)."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from backend.api_contract.maintenance import BackupProgressLine

_READY = {"phase": "ready", "percent": 100, "token": "t", "filename": "prks.backup", "warnings": []}
_FAILED = {"phase": "failed", "percent": 0, "error": "Backup could not be created."}


class BackupProgressLineTests(unittest.TestCase):
    def test_every_sent_line_keeps_its_wire_shape(self) -> None:
        for line in ({"phase": "snapshot", "percent": 1}, {"phase": "objects", "percent": 40}, _READY, _FAILED):
            with self.subTest(phase=line["phase"]):
                self.assertEqual(BackupProgressLine.model_validate(line).model_dump(), line)

    def test_an_incomplete_terminal_line_is_refused(self) -> None:
        incomplete = [{k: v for k, v in _READY.items() if k != field} for field in ("token", "filename", "warnings")]
        incomplete.append({"phase": "failed", "percent": 0})
        for line in incomplete:
            with self.subTest(line=line), self.assertRaises(ValidationError):
                BackupProgressLine.model_validate(line)

    def test_a_working_line_carries_no_terminal_field(self) -> None:
        for extra in ({"token": "t"}, {"error": "x"}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                BackupProgressLine.model_validate({"phase": "objects", "percent": 5, **extra})

    def test_the_schema_requires_the_terminal_fields_by_phase(self) -> None:
        schema = BackupProgressLine.model_json_schema()
        self.assertEqual(schema["discriminator"]["propertyName"], "phase")
        required = {name: set(body["required"]) for name, body in schema["$defs"].items()}
        self.assertEqual(required["BackupReady"], {"phase", "percent", "token", "filename", "warnings"})
        self.assertEqual(required["BackupFailed"], {"phase", "percent", "error"})


if __name__ == "__main__":
    unittest.main()
