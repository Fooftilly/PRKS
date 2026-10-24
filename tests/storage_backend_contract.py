"""Shared ``StorageBackend`` contract tests (storage-architecture §10.2).

Every backend runs this same suite. A backend test module subclasses
:class:`StorageBackendContract` together with ``unittest.TestCase`` and
implements ``make_backend()``; nothing here may assume a filesystem. The module
name deliberately does not start with ``test_`` so the mixin is only collected
through a concrete backend.

Local-only behavior (links, directory layout, durability calls, local paths)
belongs in the local backend's own tests, not here.
"""

from __future__ import annotations

import hashlib
import io
import threading

from backend.storage.objects import (
    NAMESPACE_ASSET_OBJECTS,
    NAMESPACE_PORTRAITS,
    InvalidStorageKey,
    ObjectExists,
    ObjectInfo,
    ObjectNotFound,
    StorageBackend,
    StorageKey,
    mint_name,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _writer(data: bytes, *, chunk: int = 7):
    def write(stream):
        for i in range(0, len(data), chunk):
            stream.write(data[i : i + chunk])

    return write


class _Boom(RuntimeError):
    pass


class StorageBackendContract:
    """Mixin: ``self.backend`` is a fresh, empty backend for each test.

    List it **first** among the bases and provide ``make_backend()`` (returning
    a new, empty ``StorageBackend``) on the ``TestCase`` side, so this
    ``setUp`` runs the test case's own ``setUp`` before building the backend.
    """

    def setUp(self):  # noqa: D401 - unittest hook
        super().setUp()
        self.backend: StorageBackend = self.make_backend()

    def key(self, name="1700000000_abcdef12_paper.pdf", namespace=NAMESPACE_ASSET_OBJECTS):
        return StorageKey(namespace, name)

    def read_all(self, key):
        with self.backend.open_read(key) as handle:
            return handle.read()

    # -- shape --------------------------------------------------------------------

    def test_backend_satisfies_the_protocol(self):
        self.assertIsInstance(self.backend, StorageBackend)
        self.assertIsInstance(self.backend.backend_type, str)
        self.assertTrue(self.backend.backend_type)

    # -- put_new --------------------------------------------------------------------

    def test_put_new_stores_bytes_and_hashes_the_stream(self):
        data = b"%PDF-1.4\n" + bytes(range(256)) * 50
        info = self.backend.put_new(self.key(), _writer(data))
        self.assertIsInstance(info, ObjectInfo)
        self.assertEqual(info.key, self.key())
        self.assertEqual(info.size, len(data))
        self.assertEqual(info.sha256, _sha(data))
        self.assertTrue(info.version)
        self.assertEqual(self.read_all(self.key()), data)

    def test_put_new_without_hash(self):
        info = self.backend.put_new(self.key(), _writer(b"abc"), hash=False)
        self.assertIsNone(info.sha256)
        self.assertEqual(info.size, 3)

    def test_put_new_empty_object(self):
        info = self.backend.put_new(self.key(), lambda _stream: None)
        self.assertEqual(info.size, 0)
        self.assertEqual(info.sha256, _sha(b""))
        self.assertEqual(self.read_all(self.key()), b"")

    def test_put_new_is_exclusive_and_never_overwrites(self):
        self.backend.put_new(self.key(), _writer(b"first"))
        with self.assertRaises(ObjectExists):
            self.backend.put_new(self.key(), _writer(b"second"))
        self.assertEqual(self.read_all(self.key()), b"first")

    def test_put_new_failure_leaves_nothing_under_the_key(self):
        def failing(stream):
            stream.write(b"partial bytes")
            raise _Boom()

        with self.assertRaises(_Boom):
            self.backend.put_new(self.key(), failing)
        self.assertIsNone(self.backend.stat(self.key()))
        self.assertEqual(list(self.backend.iter_keys(NAMESPACE_ASSET_OBJECTS)), [])
        # The key is still free.
        self.backend.put_new(self.key(), _writer(b"ok"))

    def test_concurrent_put_new_has_exactly_one_winner(self):
        results = []
        barrier = threading.Barrier(6)

        def attempt(i):
            barrier.wait()
            try:
                self.backend.put_new(self.key(), _writer(f"writer-{i}".encode()))
                results.append(("ok", i))
            except ObjectExists:
                results.append(("exists", i))

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        winners = [i for kind, i in results if kind == "ok"]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(self.read_all(self.key()), f"writer-{winners[0]}".encode())

    def test_readers_never_observe_a_partial_new_object(self):
        seen = []

        def slow(stream):
            stream.write(b"half-")
            seen.append(self.backend.stat(self.key()))
            stream.write(b"done")

        self.backend.put_new(self.key(), slow)
        self.assertEqual(seen, [None])
        self.assertEqual(self.read_all(self.key()), b"half-done")

    # -- replace --------------------------------------------------------------------

    def test_replace_swaps_the_whole_object(self):
        first = self.backend.put_new(self.key(), _writer(b"old contents"))
        info = self.backend.replace(self.key(), _writer(b"new"))
        self.assertEqual(info.size, 3)
        self.assertEqual(info.sha256, _sha(b"new"))
        self.assertEqual(self.read_all(self.key()), b"new")
        self.assertNotEqual(info.version, first.version)

    def test_replace_requires_the_key_to_exist(self):
        with self.assertRaises(ObjectNotFound):
            self.backend.replace(self.key(), _writer(b"x"))
        self.assertIsNone(self.backend.stat(self.key()))

    def test_failed_replace_keeps_the_old_object(self):
        self.backend.put_new(self.key(), _writer(b"keep me"))

        def failing(stream):
            stream.write(b"never visible")
            raise _Boom()

        with self.assertRaises(_Boom):
            self.backend.replace(self.key(), failing)
        self.assertEqual(self.read_all(self.key()), b"keep me")

    def test_reader_during_replace_sees_old_or_new_never_mixed(self):
        self.backend.put_new(self.key(), _writer(b"A" * 4096))
        observed = []

        def interleaved(stream):
            stream.write(b"B" * 2048)
            observed.append(self.read_all(self.key()))
            stream.write(b"B" * 2048)

        self.backend.replace(self.key(), interleaved)
        observed.append(self.read_all(self.key()))
        self.assertEqual(observed, [b"A" * 4096, b"B" * 4096])

    # -- open_read / stat -------------------------------------------------------------

    def test_open_read_is_seekable_for_range_requests(self):
        self.backend.put_new(self.key(), _writer(b"0123456789"))
        with self.backend.open_read(self.key()) as handle:
            handle.seek(4)
            self.assertEqual(handle.read(3), b"456")
            handle.seek(-2, io.SEEK_END)
            self.assertEqual(handle.read(), b"89")

    def test_open_read_of_absent_key(self):
        with self.assertRaises(ObjectNotFound):
            self.backend.open_read(self.key())

    def test_stat(self):
        self.assertIsNone(self.backend.stat(self.key()))
        put = self.backend.put_new(self.key(), _writer(b"12345"))
        info = self.backend.stat(self.key())
        self.assertEqual(info.size, 5)
        self.assertIsNone(info.sha256)
        self.assertEqual(info.version, put.version)

    # -- delete ---------------------------------------------------------------------

    def test_delete_is_idempotent(self):
        self.assertTrue(self.backend.delete(self.key()))
        self.backend.put_new(self.key(), _writer(b"bye"))
        self.assertTrue(self.backend.delete(self.key()))
        self.assertIsNone(self.backend.stat(self.key()))
        self.assertTrue(self.backend.delete(self.key()))
        with self.assertRaises(ObjectNotFound):
            self.backend.open_read(self.key())

    # -- verify ---------------------------------------------------------------------

    def test_verify_is_three_valued(self):
        data = b"integrity"
        self.assertIsNone(self.backend.verify(self.key(), _sha(data)))
        self.backend.put_new(self.key(), _writer(data))
        self.assertTrue(self.backend.verify(self.key(), _sha(data)))
        self.assertTrue(self.backend.verify(self.key(), _sha(data).upper()))
        self.assertFalse(self.backend.verify(self.key(), _sha(b"other")))
        with self.assertRaises(ValueError):
            self.backend.verify(self.key(), "not-a-hash")

    # -- iter_keys ------------------------------------------------------------------

    def test_iter_keys_lists_one_namespace_only(self):
        names = ["b_second.pdf", "a_first.pdf", "c_third.pdf"]
        for name in names:
            self.backend.put_new(self.key(name), _writer(name.encode()))
        self.backend.put_new(self.key("p1_hash.webp", NAMESPACE_PORTRAITS), _writer(b"img"))
        listed = list(self.backend.iter_keys(NAMESPACE_ASSET_OBJECTS))
        self.assertEqual(sorted(k.name for k in listed), sorted(names))
        self.assertTrue(all(k.namespace == NAMESPACE_ASSET_OBJECTS for k in listed))
        self.assertEqual(
            [k.name for k in self.backend.iter_keys(NAMESPACE_PORTRAITS)], ["p1_hash.webp"]
        )

    def test_iter_keys_of_empty_or_unknown_namespace(self):
        self.assertEqual(list(self.backend.iter_keys(NAMESPACE_PORTRAITS)), [])
        with self.assertRaises(InvalidStorageKey):
            list(self.backend.iter_keys("../pdfs"))

    def test_namespaces_are_independent(self):
        self.backend.put_new(self.key("same.bin"), _writer(b"asset"))
        self.backend.put_new(self.key("same.bin", NAMESPACE_PORTRAITS), _writer(b"portrait"))
        self.assertEqual(self.read_all(self.key("same.bin")), b"asset")
        self.assertEqual(self.read_all(self.key("same.bin", NAMESPACE_PORTRAITS)), b"portrait")

    # -- keys -------------------------------------------------------------------------

    def test_invalid_keys_are_refused_before_storage_is_touched(self):
        bad_names = ["", " a.pdf", "a.pdf ", ".", "..", "a/b.pdf", "a\\b.pdf", "a\x00.pdf",
                     "x" * 256, ".prks-write-1.tmp", ".linearized_x.pdf"]
        for name in bad_names:
            with self.subTest(name=name):
                with self.assertRaises(InvalidStorageKey):
                    StorageKey(NAMESPACE_ASSET_OBJECTS, name)
        with self.assertRaises(InvalidStorageKey):
            StorageKey("pdfs", "a.pdf")
        with self.assertRaises(InvalidStorageKey):
            self.backend.stat(("asset-objects", "a.pdf"))
        self.assertEqual(list(self.backend.iter_keys(NAMESPACE_ASSET_OBJECTS)), [])

    def test_legacy_names_stay_valid(self):
        for name in ("1700000000_My Paper (v2).pdf", "résumé.pdf", "a%20b.pdf", "C:odd.pdf", "x" * 255):
            with self.subTest(name=name):
                StorageKey(NAMESPACE_ASSET_OBJECTS, name)
        multibyte = "é" * 127 + ".p"  # 256 bytes
        with self.assertRaises(InvalidStorageKey):
            StorageKey(NAMESPACE_ASSET_OBJECTS, multibyte)

    def test_key_text_form(self):
        self.assertEqual(str(self.key("a.pdf")), "asset-objects/a.pdf")

    def test_minted_names_are_valid_fresh_keys(self):
        first = mint_name(NAMESPACE_ASSET_OBJECTS, "../../etc/My Paper.pdf")
        second = mint_name(NAMESPACE_ASSET_OBJECTS, "../../etc/My Paper.pdf")
        self.assertNotEqual(first, second)
        for name in (first, second):
            self.assertTrue(name.endswith(".pdf"))
            self.assertNotIn("/", name)
            self.backend.put_new(StorageKey(NAMESPACE_ASSET_OBJECTS, name), _writer(b"x"))
        with self.assertRaises(InvalidStorageKey):
            mint_name(NAMESPACE_PORTRAITS, "x")
        with self.assertRaises(InvalidStorageKey):
            mint_name("nope", "x")
