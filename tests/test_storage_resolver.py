"""Storage-root resolver: §5.2 precedence, §5.3 testing isolation, §6 defaults, V1.

Every case passes an explicit environment mapping, so nothing here depends on
(or reads) the developer's real configuration.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from backend.storage import bootstrap_config, distribution, paths, resolver
from backend.storage.config import StorageConfig
from backend.storage.errors import BootstrapConfigError, InvalidStorageRoot

REPO_DATA = os.path.join(_PROJECT_DIR, "data")
REPO_DATA_TESTING = os.path.join(_PROJECT_DIR, "data_testing")


def _write_config(path, local_root, **storage):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = {"format": 1, "storage": {"backend": "local", "local_root": local_root, "relocation": None}}
    body["storage"].update(storage)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(body, handle)


class ResolverTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="prks-resolver-")
        self.tmp = os.path.realpath(self._tmp.name)
        self.config_file = os.path.join(self.tmp, "cfg", "config.json")

    def tearDown(self):
        self._tmp.cleanup()

    def env(self, **extra):
        base = {"PRKS_CONFIG_FILE": self.config_file, "HOME": self.tmp}
        base.update({k: v for k, v in extra.items() if v is not None})
        return base

    def resolve(self, *, cli=None, testing=False, dist=None, **env):
        return resolver.resolve_storage_root(
            cli_root=cli, environ=self.env(**env), testing=testing, distribution=dist
        )


class TestPrecedence(ResolverTestCase):
    def test_cli_wins_over_every_other_source(self):
        _write_config(self.config_file, os.path.join(self.tmp, "from-config"))
        got = self.resolve(
            cli=os.path.join(self.tmp, "from-cli"),
            PRKS_STORAGE=os.path.join(self.tmp, "from-env"),
        )
        self.assertEqual(got, (os.path.join(self.tmp, "from-cli"), "cli"))

    def test_env_wins_over_config_and_default(self):
        _write_config(self.config_file, os.path.join(self.tmp, "from-config"))
        got = self.resolve(PRKS_STORAGE=os.path.join(self.tmp, "from-env"))
        self.assertEqual(got, (os.path.join(self.tmp, "from-env"), "env"))

    def test_config_file_wins_over_default(self):
        _write_config(self.config_file, os.path.join(self.tmp, "from-config"))
        got = self.resolve()
        self.assertEqual(got, (os.path.join(self.tmp, "from-config"), "config_file"))

    def test_source_checkout_default_is_repo_data(self):
        got = self.resolve()
        self.assertEqual(got, (REPO_DATA, "development_default"))
        self.assertEqual(distribution.DISTRIBUTION, distribution.SOURCE)

    def test_packaged_default_is_platform_location(self):
        got = self.resolve(dist=distribution.PACKAGED, XDG_DATA_HOME=os.path.join(self.tmp, "xdg-data"))
        if resolver.platform_family() == resolver.PLATFORM_XDG:
            self.assertEqual(
                got, (os.path.join(self.tmp, "xdg-data", "prks", "library"), "platform_default")
            )
        else:  # pragma: no cover - other hosts
            self.assertEqual(got.source, "platform_default")

    def test_higher_source_never_reads_lower_sources(self):
        # A broken config file must not matter when the environment decides:
        # the first set source wins completely.
        os.makedirs(os.path.dirname(self.config_file))
        with open(self.config_file, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        got = self.resolve(PRKS_STORAGE=os.path.join(self.tmp, "env"))
        self.assertEqual(got.source, "env")
        got = self.resolve(cli=os.path.join(self.tmp, "cli"))
        self.assertEqual(got.source, "cli")

    def test_blank_env_is_unset_but_blank_cli_is_an_error(self):
        self.assertEqual(self.resolve(PRKS_STORAGE="   ").source, "development_default")
        with self.assertRaises(InvalidStorageRoot) as ctx:
            self.resolve(cli="  ")
        self.assertEqual(ctx.exception.reason, "root_empty")

    def test_set_but_invalid_config_is_an_error_not_a_fallthrough(self):
        for body, reason in (
            ("{not json", "malformed"),
            (json.dumps({"format": 2, "storage": {}}), "format_newer"),
            (json.dumps({"format": 1, "storage": {"local_root": "relative/lib"}}), "local_root_not_absolute"),
            (json.dumps({"format": 1, "storage": {"local_root": ""}}), "local_root_invalid"),
            (json.dumps({"format": 1, "storage": {"backend": "s3", "local_root": "/x"}}), "backend_unsupported"),
            (json.dumps({"format": 1, "storage": {"local_root": "/x", "relocation": {"id": "r", "phase": "?"}}}),
             "relocation_phase_unknown"),
        ):
            with self.subTest(reason=reason):
                os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
                with open(self.config_file, "w", encoding="utf-8") as handle:
                    handle.write(body)
                with self.assertRaises(BootstrapConfigError) as ctx:
                    self.resolve()
                self.assertEqual(ctx.exception.reason, reason)

    def test_config_without_local_root_falls_to_default(self):
        _write_config(self.config_file, None)
        self.assertEqual(self.resolve().source, "development_default")

    def test_known_relocation_record_is_parsed_not_acted_on(self):
        _write_config(
            self.config_file,
            os.path.join(self.tmp, "lib"),
            relocation={"id": "rel-1", "phase": "retained", "from": "/old", "to": "/new"},
        )
        self.assertEqual(self.resolve(), (os.path.join(self.tmp, "lib"), "config_file"))


class TestTestingIsolation(ResolverTestCase):
    """§5.3: testing never reads the bootstrap file or the platform default."""

    def test_testing_default_is_data_testing(self):
        got = self.resolve(testing=True)
        self.assertEqual(got, (REPO_DATA_TESTING, "development_default"))

    def test_testing_never_reads_bootstrap_config_or_platform_default(self):
        _write_config(self.config_file, os.path.join(self.tmp, "real-library"))

        def boom(*_a, **_k):
            raise AssertionError("testing mode consulted the bootstrap/platform default")

        with (
            patch.object(bootstrap_config, "read_bootstrap_config", boom),
            patch.object(resolver, "bootstrap_config_path", boom),
            patch.object(resolver, "platform_default_root", boom),
        ):
            for dist in (distribution.SOURCE, distribution.PACKAGED):
                got = self.resolve(testing=True, dist=dist)
                self.assertEqual(got, (REPO_DATA_TESTING, "development_default"))
            got = resolver.resolve_storage_root(
                cli_root=None, environ=self.env(), testing=True, bootstrap_reader=boom
            )
            self.assertEqual(got.source, "development_default")

    def test_testing_from_env_never_opens_the_config_file(self):
        _write_config(self.config_file, os.path.join(self.tmp, "real-library"))
        real_open = open

        def guarded(file, *args, **kwargs):
            if os.fspath(file) == self.config_file:
                raise AssertionError("testing mode opened the bootstrap config file")
            return real_open(file, *args, **kwargs)

        with patch("builtins.open", guarded):
            cfg = StorageConfig.from_env(environ=self.env(PRKS_TESTING="1"))
        self.assertEqual(cfg.root, REPO_DATA_TESTING)

    def test_testing_still_honors_cli_and_env_under_safety_guard(self):
        root = os.path.join(self.tmp, "t-root")
        self.assertEqual(self.resolve(testing=True, PRKS_STORAGE=root), (root, "env"))
        self.assertEqual(self.resolve(testing=True, cli=root), (root, "cli"))
        with self.assertRaises(RuntimeError) as ctx:
            StorageConfig.from_env(cli_root=REPO_DATA, environ=self.env(PRKS_TESTING="1"))
        self.assertIn("refusing to use --storage-root", str(ctx.exception))
        with self.assertRaises(RuntimeError) as ctx:
            StorageConfig.from_env(environ=self.env(PRKS_TESTING="1", PRKS_STORAGE="/data"))
        self.assertIn("refusing to use PRKS_STORAGE under /data", str(ctx.exception))


class TestNormalization(ResolverTestCase):
    """V1: expand ~ and env references once, absolute, lexically collapsed."""

    def test_relative_is_made_absolute_against_cwd(self):
        got = resolver.normalize_root_spelling("lib/../lib2", environ={}, what="x", cwd="/srv/a")
        self.assertEqual(got, os.path.normpath("/srv/a/lib2"))

    def test_tilde_and_env_references_are_expanded_from_the_given_environment(self):
        env = {"HOME": "/home/u", "LIBS": "/mnt/libs"}
        self.assertEqual(resolver.normalize_root_spelling("~/prks", environ=env, what="x"), "/home/u/prks")
        self.assertEqual(resolver.normalize_root_spelling("$LIBS/one", environ=env, what="x"), "/mnt/libs/one")
        self.assertEqual(resolver.normalize_root_spelling("${LIBS}/two/.", environ=env, what="x"), "/mnt/libs/two")

    def test_symlinks_are_not_resolved_by_normalization(self):
        target = os.path.join(self.tmp, "target")
        link = os.path.join(self.tmp, "link")
        os.mkdir(target)
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError) as exc:  # pragma: no cover
            self.skipTest(f"symlinks unavailable: {exc}")
        self.assertEqual(resolver.normalize_root_spelling(link, environ={}, what="x"), link)

    def test_empty_and_unparseable_are_errors(self):
        for raw, reason in (("", "root_empty"), (" ", "root_empty"), ("a\x00b", "root_unparseable")):
            with self.subTest(raw=raw):
                with self.assertRaises(InvalidStorageRoot) as ctx:
                    resolver.normalize_root_spelling(raw, environ={}, what="x")
                self.assertEqual(ctx.exception.reason, reason)

    def test_env_root_identical_after_restart_from_another_cwd(self):
        old = os.getcwd()
        try:
            os.chdir(self.tmp)
            first = StorageConfig.from_env(environ=self.env(PRKS_TESTING="1", PRKS_STORAGE="lib"))
            os.chdir(os.path.join(_PROJECT_DIR))
            snapshot = first.root
        finally:
            os.chdir(old)
        self.assertEqual(snapshot, os.path.join(self.tmp, "lib"))


class TestPlatformTable(unittest.TestCase):
    """§6: one table, keyed by platform family, standard library only."""

    def test_xdg(self):
        env = {"HOME": "/home/u"}
        self.assertEqual(resolver.bootstrap_config_path(env, family="xdg"), "/home/u/.config/prks/config.json")
        self.assertEqual(resolver.platform_default_root(env, family="xdg"), "/home/u/.local/share/prks/library")
        env = {"HOME": "/home/u", "XDG_CONFIG_HOME": "/c", "XDG_DATA_HOME": "/d"}
        self.assertEqual(resolver.bootstrap_config_path(env, family="xdg"), "/c/prks/config.json")
        self.assertEqual(resolver.platform_default_root(env, family="xdg"), "/d/prks/library")

    def test_xdg_ignores_relative_base_dirs(self):
        env = {"HOME": "/home/u", "XDG_CONFIG_HOME": "rel", "XDG_DATA_HOME": "rel"}
        self.assertEqual(resolver.bootstrap_config_path(env, family="xdg"), "/home/u/.config/prks/config.json")
        self.assertEqual(resolver.platform_default_root(env, family="xdg"), "/home/u/.local/share/prks/library")

    def test_macos(self):
        env = {"HOME": "/Users/u"}
        self.assertEqual(
            resolver.bootstrap_config_path(env, family="macos"),
            "/Users/u/Library/Application Support/PRKS/config.json",
        )
        self.assertEqual(
            resolver.platform_default_root(env, family="macos"),
            "/Users/u/Library/Application Support/PRKS/Library",
        )

    def test_windows_uses_local_not_roaming(self):
        env = {"USERPROFILE": "C:\\Users\\u", "LOCALAPPDATA": "C:\\Users\\u\\AppData\\Local", "APPDATA": "R"}
        self.assertEqual(
            resolver.bootstrap_config_path(env, family="windows"),
            "C:\\Users\\u\\AppData\\Local\\PRKS\\config.json",
        )
        self.assertEqual(
            resolver.platform_default_root(env, family="windows"),
            "C:\\Users\\u\\AppData\\Local\\PRKS\\Library",
        )
        env = {"USERPROFILE": "C:\\Users\\u"}
        self.assertEqual(
            resolver.platform_default_root(env, family="windows"),
            "C:\\Users\\u\\AppData\\Local\\PRKS\\Library",
        )

    def test_config_file_override(self):
        env = {"PRKS_CONFIG_FILE": "/etc/prks/config.json", "HOME": "/home/u"}
        self.assertEqual(resolver.bootstrap_config_path(env, family="xdg"), "/etc/prks/config.json")

    def test_data_root_may_sit_beside_config_on_macos_and_windows(self):
        for family in ("macos", "windows"):
            env = {"HOME": "/Users/u", "USERPROFILE": "C:\\Users\\u"}
            mod = resolver._pathmod(family)
            config_dir = mod.dirname(resolver.bootstrap_config_path(env, family=family))
            self.assertEqual(mod.dirname(resolver.platform_default_root(env, family=family)), config_dir)


class TestExistingDefaultsUnchanged(unittest.TestCase):
    """Phase A must derive exactly the paths every existing deployment uses today."""

    def _cfg(self, **env):
        base = {"PRKS_CONFIG_FILE": os.path.join(REPO_DATA_TESTING, ".no-such-dir", "c.json")}
        base.update(env)
        return StorageConfig.from_env(environ=base)

    def test_production_source_checkout_default(self):
        cfg = self._cfg()
        self.assertEqual(cfg.root_source, "development_default")
        self.assertIsNone(cfg.configured_root)
        self.assertEqual(cfg.root, REPO_DATA)
        self.assertEqual(cfg.db_path, os.path.join(REPO_DATA, "prks_data.db"))
        self.assertEqual(cfg.pdfs_dir, os.path.join(REPO_DATA, "pdfs"))
        self.assertEqual(cfg.thumbs_dir, os.path.join(REPO_DATA, "thumbs"))
        self.assertEqual(cfg.people_dir, os.path.join(REPO_DATA, "people"))
        self.assertEqual(cfg.index_db_path, os.path.join(REPO_DATA, "prks_text_index.db"))
        self.assertEqual(cfg.research_index_db_path, os.path.join(REPO_DATA, "prks_research_index.db"))
        self.assertEqual(cfg.log_file, os.path.join(REPO_DATA, "prks-errors.log"))
        # The Docker-shaped inbox special case is kept unchanged in Phase A.
        self.assertEqual(cfg.processing_dir, "/data/for_processing")
        self.assertEqual(cfg.processing_dir, paths.PROCESSING_PROD_PREFERRED)
        self.assertTrue(cfg.processing_fallback_allowed)
        self.assertEqual(paths.processing_prod_fallback(), os.path.join(REPO_DATA, "for_processing"))

    def test_testing_default(self):
        cfg = self._cfg(PRKS_TESTING="1")
        self.assertEqual(cfg.root, REPO_DATA_TESTING)
        self.assertEqual(cfg.db_path, os.path.join(REPO_DATA_TESTING, "prks_data_testing.db"))
        self.assertEqual(cfg.pdfs_dir, os.path.join(REPO_DATA_TESTING, "pdfs"))
        self.assertEqual(cfg.processing_dir, os.path.join(REPO_DATA_TESTING, "for_processing"))
        self.assertFalse(cfg.processing_fallback_allowed)

    def test_container_and_explicit_env_root(self):
        # docker-compose.yml: PRKS_STORAGE=/data
        cfg = self._cfg(PRKS_STORAGE="/data")
        self.assertEqual(cfg.root_source, "env")
        self.assertEqual(cfg.root, "/data")
        self.assertEqual(cfg.db_path, "/data/prks_data.db")
        self.assertEqual(cfg.pdfs_dir, "/data/pdfs")
        self.assertEqual(cfg.thumbs_dir, "/data/thumbs")
        self.assertEqual(cfg.people_dir, "/data/people")
        self.assertEqual(cfg.processing_dir, "/data/for_processing")
        self.assertEqual(cfg.index_db_path, "/data/prks_text_index.db")
        self.assertEqual(cfg.research_index_db_path, "/data/prks_research_index.db")
        self.assertEqual(cfg.log_file, "/data/prks-errors.log")
        self.assertFalse(cfg.processing_fallback_allowed)

    def test_component_overrides_still_apply(self):
        cfg = self._cfg(PRKS_STORAGE="/srv/prks", PRKS_FOR_PROCESSING_DIR="/inbox", PRKS_LOG_FILE="/var/log/p.log")
        self.assertEqual(cfg.processing_dir, "/inbox")
        self.assertEqual(cfg.log_file, "/var/log/p.log")
        cfg = self._cfg(PRKS_FOR_PROCESSING_DIR="/inbox")
        self.assertEqual(cfg.processing_dir, "/inbox")
        self.assertFalse(cfg.processing_fallback_allowed)

    def test_cli_root_derives_like_an_explicit_root(self):
        cfg = StorageConfig.from_env(cli_root="/srv/cli", environ={"PRKS_STORAGE": "/srv/env"})
        self.assertEqual(cfg.root_source, "cli")
        self.assertEqual(cfg.root, "/srv/cli")
        self.assertEqual(cfg.db_path, "/srv/cli/prks_data.db")
        self.assertEqual(cfg.processing_dir, "/srv/cli/for_processing")

    def test_config_file_root_uses_root_relative_inbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = os.path.join(tmp, "config.json")
            _write_config(config_file, "/srv/lib")
            cfg = StorageConfig.from_env(environ={"PRKS_CONFIG_FILE": config_file})
        self.assertEqual(cfg.root_source, "config_file")
        self.assertEqual(cfg.processing_dir, "/srv/lib/for_processing")
        self.assertFalse(cfg.processing_fallback_allowed)


if __name__ == "__main__":
    unittest.main()
