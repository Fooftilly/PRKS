"""Folders, created and changed with no server.

The Folder Library is PRKS's home route, so this is the milestone where an
offline launch stops being a read-only view of it: a folder created here is a
valid destination immediately, moving one is an ordinary field edit, and filing
a file is a scalar on the file rather than membership of a set.

Every durable-queue gate here allows a generous timeout. A save offline first
reads the base it measures the edit against, and offline that read has to let a
doomed request fail before the cache answers.
"""
import os
import unittest

from backend.db_manager import PRKSDatabase
from backend.storage.config import StorageConfig
from tests.e2e import test_offline as o
from tests.e2e.fixtures import WORK_A_TITLE, seed_library
from tests.e2e.harness import AppServer, open_app_page, require_chromium, wait_for_async


def load_tests(loader, standard_tests, pattern):
    return standard_tests if os.environ.get('PRKS_E2E') == '1' else unittest.TestSuite()


def setUpModule():
    global _PW, _BROWSER
    _PW, _BROWSER = require_chromium()


def tearDownModule():
    _BROWSER.close(); _PW.stop()


def seed(root):
    ids = seed_library(root)
    db = PRKSDatabase(storage=StorageConfig.for_testing(root))
    ids['folder'] = db.add_folder('Durable Folder', 'Seeded description')
    db.move_work_to_folder(ids['work_a'], ids['folder'])
    return ids


class DurableFolderTests(unittest.TestCase):

    # ---- harness ------------------------------------------------------------

    def start(self):
        server = AppServer(seed_fn=seed)
        self.addCleanup(server.stop); server.start()
        page, context, collector = open_app_page(_BROWSER, server.origin,
                                                 service_workers='allow')
        self.addCleanup(context.close)
        self.addCleanup(lambda: self.assertEqual(collector.pageerrors, []))
        o._wait_sw_active(page)
        self.index(page)
        o._wait_list_cached(page, 'folders:index')
        return server, page, context

    def index(self, page):
        page.evaluate("() => prksNavigate('#/folders')")

    def detail(self, page, folder_id):
        page.evaluate("id => prksNavigate('#/folders/' + id)", folder_id)

    def prepare_folder(self, page, folder_id):
        """Open the folder once while connected, so its revisions are cached."""
        self.detail(page, folder_id)
        o._wait_entity_cached(page, 'folder', folder_id)
        page.evaluate("id => { void Promise.resolve(prksReadFolderState(id)).catch(() => {}); }",
                      folder_id)
        o._wait_entity_cached(page, 'folder-state', folder_id)

    def prepare_work(self, page, work_id):
        o._open_work_from_home(page, WORK_A_TITLE)
        o._wait_entity_cached(page, 'work', work_id)
        page.evaluate(
            "id => { void Promise.resolve(prksReadWorkFolderState(id)).catch(() => {}); }",
            work_id)
        o._wait_entity_cached(page, 'work-folder-state', work_id)
        # And the browse catalogue: a folder page renders a file CARD, and the
        # row for a file moved in has to come from somewhere this device holds.
        # Nothing is invented for a file whose row it does not have.
        page.evaluate("() => prksNavigate('#/types')")
        o._wait_list_cached(page, 'works-browse:index')

    def offline(self, page, context):
        context.set_offline(True)
        page.evaluate('() => prksOfflineNoteRequestFailure()')
        page.wait_for_function("() => prksOfflineRuntimeState() !== 'online'")

    def reconnect(self, page, context):
        context.set_offline(False)
        page.evaluate("() => window.dispatchEvent(new Event('online'))")
        page.wait_for_function("() => prksOfflineRuntimeState() === 'online'")

    def settled(self, page):
        page.evaluate("""async () => {
            const deadline = Date.now() + 30000;
            while (Date.now() < deadline) {
                const rows = await prksSync.store.listOperations();
                if (!rows.some(op => op.status !== 'conflict')) return;
                await new Promise(r => setTimeout(r, 120));
            }
        }""")

    def wait_for_family(self, page, operation):
        wait_for_async(
            page,
            "op => prksSync.store.listOperations().then(rows => rows.some("
            "  o => o.operation === op))",
            arg=operation, timeout=60000,
            message='%s was never enqueued' % operation)

    def db_for(self, server):
        existing = getattr(self, '_db', None)
        if existing is None:
            existing = PRKSDatabase(storage=StorageConfig.for_testing(server.storage_root))
            self._db = existing
        return existing

    def folders_in_db(self, server):
        return {row['title']: row for row in self.db_for(server).get_all_folders()}

    # ---- creation -----------------------------------------------------------

    def test_a_folder_created_offline_is_real_and_survives_reload(self):
        server, page, context = self.start()
        self.offline(page, context)

        folder = page.evaluate(
            "() => createFolder('Offline Folder', 'Made with no server')")
        self.assertRegex(folder, r'^F-[0-9A-F]{32}$',
                         'a permanent id minted here, never remapped later')
        self.index(page)
        o._wait_content_contains(page, 'Offline Folder')

        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('#sidebar')
        self.offline(page, context)
        self.index(page)
        o._wait_content_contains(page, 'Offline Folder')
        self.detail(page, folder)
        o._wait_content_contains(page, 'Made with no server')

        self.reconnect(page, context)
        self.settled(page)
        rows = self.folders_in_db(server)
        self.assertIn('Offline Folder', rows)
        self.assertEqual(rows['Offline Folder']['id'], folder,
                         'the id the client minted is the id SQLite stores')

    def test_a_title_the_server_already_has_comes_back_as_a_refusal(self):
        server, page, context = self.start()
        self.offline(page, context)
        page.evaluate("() => createFolder('Durable Folder', '')")
        self.wait_for_family(page, 'CREATE_FOLDER')

        self.reconnect(page, context)
        self.settled(page)
        state = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.operation === 'CREATE_FOLDER') || {}).server_result)")
        self.assertEqual(state['code'], 'TITLE_TAKEN')

    # ---- fields -------------------------------------------------------------

    def test_renaming_a_folder_offline_reaches_the_files_it_holds(self):
        server, page, context = self.start()
        folder = server.ids['folder']
        self.prepare_folder(page, folder)
        self.offline(page, context)

        page.evaluate("id => patchFolder(id, { title: 'Renamed Offline' })", folder)
        self.wait_for_family(page, 'SET_FOLDER_FIELD')
        self.index(page)
        o._wait_content_contains(page, 'Renamed Offline')

        self.reconnect(page, context)
        self.settled(page)
        self.assertIn('Renamed Offline', self.folders_in_db(server))

    def test_a_rename_taken_back_before_it_is_sent_leaves_no_intent(self):
        server, page, context = self.start()
        folder = server.ids['folder']
        self.prepare_folder(page, folder)
        self.offline(page, context)

        page.evaluate("id => patchFolder(id, { title: 'Temporarily Renamed' })", folder)
        self.wait_for_family(page, 'SET_FOLDER_FIELD')
        page.evaluate("id => patchFolder(id, { title: 'Durable Folder' })", folder)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=60000,
            message='reverting an unsent rename must leave no operation behind')

    # ---- filing -------------------------------------------------------------

    def test_filing_a_file_offline_shows_on_both_the_file_and_the_folder(self):
        server, page, context = self.start()
        destination = page.evaluate("() => createFolder('Destination', '')")
        self.settled(page)
        self.prepare_work(page, server.ids['work_a'])
        self.prepare_folder(page, destination)
        self.offline(page, context)

        page.evaluate("([wid, fid]) => patchWorkFolder(wid, fid)",
                      [server.ids['work_a'], destination])
        self.wait_for_family(page, 'SET_WORK_FOLDER')

        # The folder's own page lists it ...
        self.detail(page, destination)
        o._wait_content_contains(page, WORK_A_TITLE, timeout=30000)
        # ... and the file's own page names the folder. The folder card lives
        # in the right panel rather than the route's own root.
        o._open_work_from_home(page, WORK_A_TITLE)
        o._open_details_drawer_if_tiled(page)
        page.wait_for_function(
            "() => { const p = document.getElementById('panel-content');"
            "        return !!p && p.innerText.indexOf('Destination') !== -1; }",
            timeout=30000)

        self.reconnect(page, context)
        self.settled(page)
        rows = self.db_for(server).execute_query(
            'SELECT folder_id FROM folder_files WHERE work_id = ?', (server.ids['work_a'],))
        self.assertEqual([r['folder_id'] for r in rows], [destination])

    def test_filing_a_file_back_where_it_started_leaves_no_intent(self):
        server, page, context = self.start()
        folder = server.ids['folder']
        self.prepare_work(page, server.ids['work_a'])
        destination = page.evaluate("() => createFolder('Destination', '')")
        self.settled(page)
        self.offline(page, context)

        page.evaluate("([wid, fid]) => patchWorkFolder(wid, fid)",
                      [server.ids['work_a'], destination])
        self.wait_for_family(page, 'SET_WORK_FOLDER')
        page.evaluate("([wid, fid]) => patchWorkFolder(wid, fid)",
                      [server.ids['work_a'], folder])
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=60000,
            message='filed back where it started is not two changes, it is none')

    # ---- deletion -----------------------------------------------------------

    def test_deleting_an_empty_folder_offline_is_a_tombstone(self):
        server, page, context = self.start()
        empty = page.evaluate("() => createFolder('Empty Folder', '')")
        self.settled(page)
        self.prepare_folder(page, empty)
        self.offline(page, context)

        page.evaluate("id => deleteFolderCanonical(id)", empty)
        self.wait_for_family(page, 'DELETE_FOLDER')
        self.index(page)
        page.wait_for_function(
            """needle => {
                const ctx = window.prksGetFocusedTabContext && window.prksGetFocusedTabContext();
                const root = ctx && ctx.root;
                return !!root && root.innerText.indexOf(needle) === -1;
            }""",
            arg='Empty Folder', timeout=30000)

        self.reconnect(page, context)
        self.settled(page)
        self.assertNotIn('Empty Folder', self.folders_in_db(server))

    def test_a_folder_holding_files_comes_back_as_a_named_refusal(self):
        """The empty-only rule is canonical and unchanged: a folder holding
        files is refused rather than cascading what the ordinary endpoint would
        reject."""
        server, page, context = self.start()
        folder = server.ids['folder']
        self.prepare_folder(page, folder)
        self.offline(page, context)
        page.evaluate("id => deleteFolderCanonical(id)", folder)
        self.wait_for_family(page, 'DELETE_FOLDER')

        self.reconnect(page, context)
        self.settled(page)
        state = page.evaluate(
            "() => prksSync.store.listOperations().then(rows => (rows.find("
            "  o => o.operation === 'DELETE_FOLDER') || {}).server_result)")
        self.assertEqual(state['code'], 'FOLDER_NOT_EMPTY')
        self.assertIn('Durable Folder', self.folders_in_db(server))

    def test_deleting_a_folder_created_offline_folds_the_whole_case_away(self):
        server, page, context = self.start()
        self.offline(page, context)
        folder = page.evaluate("() => createFolder('Offline Mistake', 'Typed by accident')")
        self.wait_for_family(page, 'CREATE_FOLDER')
        page.evaluate("id => deleteFolderCanonical(id)", folder)
        wait_for_async(
            page,
            "() => prksSync.store.listOperations().then(rows => rows.length === 0)",
            timeout=60000,
            message='nothing about this folder should ever reach the server')

        self.reconnect(page, context)
        self.settled(page)
        self.assertNotIn('Offline Mistake', self.folders_in_db(server))


if __name__ == '__main__':
    unittest.main()
