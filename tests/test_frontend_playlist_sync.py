"""Playlists: the client half, and its parity with the server."""
import pathlib
import re
import subprocess
import unittest

from backend import playlist_sync, sync_protocol

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'js'


def js_string_list(source, name):
    body = source[source.index(name):]
    body = body[: body.index(']')]
    return re.findall(r"'([a-z_]+)'", body)


class PlaylistSyncFrontendTests(unittest.TestCase):
    def setUp(self):
        self.store = (FRONTEND / 'local-store.js').read_text()
        self.state = (FRONTEND / 'playlist-state.js').read_text()
        self.backend = (ROOT / 'backend' / 'playlist_sync.py').read_text()

    def test_runtime_selftests(self):
        proc = subprocess.run(
            ['node', str(ROOT / 'tests' / 'browser' / 'run_playlist_sync_selftest.js')],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('checks passed', proc.stdout)

    def test_the_two_sides_synchronize_the_same_fields(self):
        client = js_string_list(self.store, 'const PLAYLIST_FIELDS =')
        self.assertEqual(sorted(client), sorted(playlist_sync.FIELDS))
        labels = self.state[self.state.index('const LABELS = Object.freeze({'):]
        labels = labels[: labels.index('});')]
        for field in playlist_sync.FIELDS:
            with self.subTest(field=field):
                self.assertRegex(labels, r'\b%s:' % field)

    def test_every_family_is_registered_on_both_sides(self):
        families = {'CREATE_PLAYLIST', 'SET_PLAYLIST_FIELD', 'REORDER_PLAYLIST_ITEMS',
                    'DELETE_PLAYLIST', 'SET_WORK_PLAYLIST'}
        self.assertTrue(families <= set(sync_protocol.supported_operations()))
        runtime = (FRONTEND / 'sync-runtime.js').read_text()
        diagnostics = (FRONTEND / 'sync-diagnostics.js').read_text()
        for family in families:
            with self.subTest(family=family):
                self.assertIn("'%s'" % family, self.store)
                self.assertIn('%s:' % family, runtime)
                self.assertIn(family, diagnostics)

    def test_construction_mints_a_permanent_distributed_id(self):
        at = self.store.index('function createPlaylist(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn("generateEntityId('PL', uuid)", body)
        self.assertIn('base_revision: null', body)
        self.assertIn('is_distributed(op["entity_id"], "PL")', self.backend)

    def test_a_videos_playlist_is_a_scalar_on_the_work(self):
        """A video is in at most one playlist, so adding, moving and removing
        are one operation with different values -- not membership of a set.
        Modelling it from the playlist's end would have made moving one video
        between two playlists a change neither playlist's revision
        described."""
        self.assertEqual(playlist_sync.WORK_SCOPE_TYPE, 'work-playlist')
        at = self.store.index('function setWorkPlaylist(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn('base_revision: observed.revision', body)
        self.assertIn("if (desired === observed.playlist_id) { setResult(null); return; }", body)
        # And both directions advance the ORDER of the playlists involved, or a
        # reorder made before the move would still look current.
        at = self.backend.index('def set_work_playlist_on_conn(')
        server = self.backend[at: self.backend.index('\ndef ', at + 5)]
        self.assertEqual(server.count('_advance(conn, ORDER_SCOPE_TYPE'), 2)
        # Both wrapper names reach the one family.
        pl = (FRONTEND / 'components' / 'playlists.js').read_text()
        for fn in ('async function addWorkToPlaylist(',
                   'async function removeWorkFromPlaylist('):
            at = pl.index(fn)
            self.assertIn('prksSetWorkPlaylist(', pl[at: pl.index('\n}', at)])

    def test_the_order_is_an_aggregate_not_racing_positions(self):
        """Two devices that each dragged one video produced two whole orders.
        Merging them index by index would invent a third neither chose."""
        self.assertEqual(playlist_sync.ORDER_SCOPE_TYPE, 'playlist-order')
        self.assertNotIn('order_index', self.store)
        self.assertNotIn('order_index', self.state)
        at = self.store.index('function reorderPlaylistItems(')
        body = self.store[at: self.store.index('\n        /**', at)]
        # ONE payload carrying the whole order, replacing any earlier drag.
        self.assertIn('payload: { work_ids: desired }', body)
        self.assertIn("r.operation === 'REORDER_PLAYLIST_ITEMS'", body)
        self.assertIn('if (same(observed.work_ids)) { setResult(null); return; }', body)

    def test_the_order_payload_is_bounded_on_both_sides(self):
        """An order travels as one payload -- that is what makes it an
        aggregate -- so what it can name is bounded by what an envelope
        carries."""
        at = self.store.index('const PLAYLIST_MAX_ITEMS =')
        value = int(re.search(r'= (\d+);', self.store[at: at + 60]).group(1))
        self.assertEqual(value, playlist_sync.MAX_ITEMS)

    def test_a_video_title_keeps_using_the_work_metadata_family(self):
        """Renaming a video from a playlist page changes a Work, not playlist
        state -- a Playlist-specific title mutation would be a second,
        non-revision-aware path to the same column."""
        # The Playlist family owns only the playlist's own columns; a video's
        # title is not among the fields it will accept.
        at = self.store.index('function savePlaylistFields(')
        body = self.store[at: self.store.index('\n        /**', at)]
        self.assertIn("PLAYLIST_FIELDS.indexOf(field) === -1", body)
        self.assertIn('Not an editable playlist field: ', body)
        pl = (FRONTEND / 'components' / 'playlists.js').read_text()
        at = pl.index("if (renSave) {")
        body = pl[at: pl.index('return;\n        }', at)]
        self.assertIn("prksSaveWorkFieldDurably(wid, 'title', nextTitle", body)

    def test_deletion_cancels_only_what_was_never_sent(self):
        at = self.store.index('function deletePlaylist(')
        body = self.store[at: self.store.index('\n        /*', at)]
        self.assertIn('base_revision: null', body)
        self.assertIn('const neverSent =', body)
        self.assertIn('depends_on: waitFor', body)
        # A video added TO the playlist counts as naming it.
        at = self.store.index('function operationsNamingPlaylist(')
        self.assertIn("row.operation === 'SET_WORK_PLAYLIST'",
                      self.store[at: self.store.index('\n    }', at)])

    def test_deletion_advances_every_members_membership_revision(self):
        """A device holding "this video is in that playlist" has to be able to
        discover it was overtaken."""
        at = self.backend.index('def delete_playlist_on_conn(')
        body = self.backend[at: self.backend.index('\ndef ', at + 5)]
        self.assertIn('_advance(conn, WORK_SCOPE_TYPE, work_id)', body)

    def test_the_base_is_acknowledged_and_unknown_is_not_empty(self):
        at = self.state.index('async function acknowledgedPlaylistBase(')
        body = self.state[at: self.state.index('\n    }', at)]
        self.assertIn('if (!state) return null', body)
        self.assertIn('catalogRowFromOp(creating)', body)
        self.assertIn('newPlaylistState(playlistId)', body)
        at = self.state.index('async function acknowledgedPlaylistOrder(')
        order = self.state[at: self.state.index('\n    }', at)]
        self.assertIn("return { work_ids: [], revision: 0 }", order)
        self.assertIn('if (!state) return null', order)

    def test_a_pending_deletion_is_a_tombstone(self):
        at = self.state.index('function effectivePlaylists(')
        body = self.state[at: self.state.index('\n    /**', at)]
        self.assertIn('pendingDeletions(operations).forEach', body)
        # The count moves between TWO playlists and this projection sees only
        # the catalogue, so it is deliberately left as the server stated it.
        self.assertIn('item_count', body)
        self.assertNotIn('item_count: Math.max', body)

    def test_an_order_conflict_carries_counts_rather_than_the_ids(self):
        """A long playlist's ids would not fit the client's durable result
        bound, and the client re-reads the playlist to see what the server
        has."""
        at = self.backend.index('def apply_order(')
        body = self.backend[at: self.backend.index('\ndef ', at + 5)]
        self.assertIn('current_count=len(present)', body)
        self.assertNotIn('current_value=', body)
        at = self.state.index('const orderHandler = {')
        handler = self.state[at: self.state.index('\n    const deleteHandler', at)]
        self.assertNotIn('current_value', handler)

    def test_an_order_conflict_is_named_rather_than_left_bare(self):
        """An order carries no "current value" to report, so Diagnostics would
        otherwise show "Needs a decision" with no reason -- which is exactly the
        stranded state every named refusal exists to prevent."""
        diagnostics = (FRONTEND / 'sync-diagnostics.js').read_text()
        at = diagnostics.index('function conflictDetail(')
        body = diagnostics[at: diagnostics.index('\n    }', at)]
        self.assertIn("op.operation === 'REORDER_PLAYLIST_ITEMS'", body)
        self.assertIn('PLAYLIST_NOT_FOUND', diagnostics)

    def test_a_pending_membership_is_hydrated_with_the_other_overlays(self):
        """A card renders synchronously. An overlay that had to await the store
        could only correct itself after the first paint."""
        at = self.state.index('async function setWorkPlaylistDurably(')
        body = self.state[at: self.state.index('\n    }', at)]
        self.assertIn('await refreshPendingWorkPlaylists();', body)


if __name__ == '__main__':
    unittest.main()
