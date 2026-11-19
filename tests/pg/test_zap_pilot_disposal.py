"""The ZAP pilot disposes of its server even when seeding fails (#860)."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from scripts.ci import zap_passive_pilot
from tests.e2e import harness as e2e_harness
from tests.pg.harness import get_harness


class SeedFailure(Exception):
    pass


class ZapPilotDisposalTests(unittest.TestCase):
    def test_a_seed_failure_after_provisioning_leaves_no_database_or_storage(self) -> None:
        get_harness().template()
        servers: list[e2e_harness.AppServer] = []
        seen: dict[str, str] = {}

        class RecordingServer(e2e_harness.AppServer):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                servers.append(self)

        def failing_seed(storage_root: str) -> dict:
            server = servers[0]
            assert server.library is not None  # provisioned before the seed runs
            seen["database"] = server.library.database
            seen["tmpdir"] = os.path.dirname(storage_root)
            raise SeedFailure("seed failed after provisioning")

        env = {name: "" for name in zap_passive_pilot.INHERITED_REFUSED}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(zap_passive_pilot.shutil, "which", return_value="/usr/bin/docker"), \
                mock.patch.object(e2e_harness, "AppServer", RecordingServer), \
                mock.patch("tests.ux_tour.fixtures.seed_ux_tour_library", failing_seed), \
                mock.patch.object(zap_passive_pilot.subprocess, "run") as docker:
            with self.assertRaises(SeedFailure):
                zap_passive_pilot.run(None)

        docker.assert_not_called()  # no ZAP container was ever started
        self.assertEqual(len(servers), 1)
        self.assertIn("database", seen, "the seed never ran, so nothing was injected")
        self.assertFalse(get_harness().exists(seen["database"]), "the provisioned database was left behind")
        self.assertFalse(os.path.exists(seen["tmpdir"]), "the temporary storage root was left behind")
        self.assertIsNone(servers[0].library)


if __name__ == "__main__":
    unittest.main()
