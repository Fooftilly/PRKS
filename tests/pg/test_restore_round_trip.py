"""A round trip over every content table and object kind (L-B-1 #639; §11.4).

The fixture writes at least one row of every table on
``tables.BACKUP_CONTENT_TABLES`` (the test fails when a new content table has
none) and stores an object of every namespace: Work PDFs, a Processing upload,
a Person portrait and a saved note figure. After a backup, the Library is changed in every family;
the restore must bring back each table exactly, generated columns included,
and every archived object must be stored with its bytes.
"""

from __future__ import annotations

import io
import json
import uuid
from typing import Any

from psycopg import sql

from backend.domain.identity.library_scope import Capability, Isolation, library_command
from backend.domain.maintenance import tables
from backend.domain.objects import portraits, remote_images
from backend.domain.processing import commands as processing
from backend.storage.objects import NAMESPACE_ASSET_OBJECTS, NAMESPACE_PORTRAITS, NAMESPACES, StorageKey
from tests.pg.test_annotations import HIGHLIGHT
from tests.pg.test_objects_portraits import image
from tests.pg.test_restore_commit import RestoreTestCase
from backend.domain.organization import folder_commands
from backend.domain.organization import playlist_commands
from backend.domain.organization import tag_commands
from backend.domain.saved_views import commands as saved_view_commands
from backend.domain.settings import commands as settings_commands
from tests.acting_context import current_context
from backend.domain.people import group_commands
from backend.domain.people import publisher_commands
from backend.domain.people import role_commands
from tests import people_fixtures
from backend.domain.research import argument_commands
from backend.domain.research import concept_commands
from backend.domain.research import note_commands
from backend.domain.research import position_commands
from backend.domain.annotations import annotation_commands
from backend.domain.works import work_commands

# Identity columns the restore renumbers from the live sequence (see the staging tests).
_RENUMBERED = {"seq"}


def _hex_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex.upper()}"


class EveryTableRoundTripTests(RestoreTestCase):
    def edit_command(self, fn: Any) -> Any:
        return library_command(self.db, self.actor.context, Capability.EDIT_LIBRARY, fn,
                               isolation=Isolation.READ_COMMITTED)

    def primary(self, work_id: str) -> str:
        return self.sql("SELECT primary_manifestation_id AS id FROM prks.works WHERE id = %s",
                        (work_id,))[0]["id"]

    def populate(self) -> dict[str, str]:
        lib = self.library
        first = self.create(self.put(b"%PDF first"))
        second = self.create(self.put(b"%PDF second"))
        # Works family: a second Manifestation with a relation, an identifier, a decision, a note.
        revision = _hex_id("MF")
        self.sql("INSERT INTO prks.manifestations (library_id, id, work_id) VALUES (%s, %s, %s)",
                 (self.actor.library_id, revision, first))
        self.sql("INSERT INTO prks.manifestation_relations (library_id, work_id, from_id, to_id, relation) "
                 "VALUES (%s, %s, %s, %s, 'revision_of')", (self.actor.library_id, first, revision,
                                                             self.primary(first)))
        self.sql("INSERT INTO prks.manifestation_identifiers (library_id, manifestation_id, scheme, value, "
                 "normalized) VALUES (%s, %s, 'doi', '10.1000/X', '10.1000/x')",
                 (self.actor.library_id, self.primary(first)))
        low, high = sorted((first, second))
        self.sql("INSERT INTO prks.duplicate_decisions (library_id, entity_type, low_id, high_id, decision) "
                 "VALUES (%s, 'work', %s, %s, 'not_duplicate')", (self.actor.library_id, low, high))
        gone = self.create(self.put(b"%PDF deleted"))
        work_commands.delete(lib.database, current_context(), gone)  # its object is claimed for cleanup
        self.sql("INSERT INTO prks.sync_work_lifecycle (library_id, work_id, state) VALUES (%s, %s, 'deleted')",
                 (self.actor.library_id, gone))
        # People.
        ada = people_fixtures.add_person(lib.database, current_context(), "Ada", "Lovelace", image_url="https://example.org/ada.png")
        self.sql("INSERT INTO prks.person_aliases (library_id, person_id, position, alias) "
                 "VALUES (%s, %s, 0, 'A. Lovelace')", (self.actor.library_id, ada))
        role_commands.add(lib.database, current_context(), ada, first, "Author")
        group = group_commands.create(lib.database, current_context(), "Analysts")
        group_commands.add_member(lib.database, current_context(), group, ada)
        publisher = publisher_commands.create(lib.database, current_context(), "Penguin")["id"]
        publisher_commands.add_alias(lib.database, current_context(), publisher, "Penguin Books")
        portrait = portraits.store(self.objects, image())
        self.edit_command(lambda scope: portraits.commit(scope, self.objects, ada, "https://example.org/ada.png",
                                                    portrait))
        # Organization.
        tag = tag_commands.create(lib.database, current_context(), "Philosophy")["id"]
        tag_commands.add_alias(lib.database, current_context(), tag, "Phil")
        tag_commands.tag_work(lib.database, current_context(), first, tag)
        tag_commands.delete(lib.database, current_context(), tag_commands.create(lib.database, current_context(), "Retired")["id"])
        folder = folder_commands.create(lib.database, current_context(), "Reading")
        folder_commands.file_work(lib.database, current_context(), folder, first)
        tag_commands.tag_folder(lib.database, current_context(), folder, tag)
        playlist = playlist_commands.create(lib.database, current_context(), "Queue")
        playlist_commands.add_item(lib.database, current_context(), playlist, first)
        saved_view_commands.create(lib.database, current_context(), "Culture", {"mode": "all", "q": "culture", "tag": "", "author": "",
                                          "publisher": ""})
        settings_commands.patch(lib.database, current_context(), {"annotation_author": "Ada"})
        self.sql("INSERT INTO prks.citation_styles (library_id, id, title, csl) VALUES (%s, %s, 'House', '<style/>')",
                 (self.actor.library_id, "custom-" + "a" * 32))
        # Research.
        engines = concept_commands.create(lib.database, current_context(), "Engines")["id"]
        note_commands.save(lib.database, current_context(), first, "On [[concept:Engines]] ![figure](https://example.org/figure.png)")
        figure = remote_images.store(self.objects, image())
        self.edit_command(lambda scope: remote_images.commit(scope, self.objects, "https://example.org/figure.png",
                                                             figure))
        concept_commands.replace_aliases(lib.database, current_context(), engines, ["Machines"])
        concept_commands.replace_parents(lib.database, current_context(), engines, [concept_commands.create(lib.database, current_context(), "Mechanism")["id"]])
        claim = position_commands.create(lib.database, current_context(), "Engines compute")["id"]
        base = argument_commands.create(lib.database, current_context(), name="Base", kind="argument", sources=[{"work_id": first, "manifestation_id": revision, "pages": "1"}], targets=[{"type": "position", "id": claim, "verdict_id": "supports"}])["id"]
        argument_commands.create(lib.database, current_context(), name="Reply", kind="argument", targets=[{"type": "argument", "id": base, "verdict_id": "opposes"}])
        annotation_commands.save(lib.database, current_context(), first, json.dumps([HIGHLIGHT]), base_set_revision=0)
        # Processing: a ready item whose draft has roles and tags.
        data = b"%PDF-1.4\n% queued\n%%EOF\n"
        item = processing.upload(self.db, self.actor.context, self.objects, upload_key=uuid.uuid4().hex,
                                 filename="queued.pdf", body=io.BytesIO(data), content_length=len(data),
                                 max_bytes=1024 * 1024).item
        processing.edit(self.db, self.actor.context, item["id"], 0, {
            "title": "Queued", "roles": [{"person_id": ada, "role_type": "Author"}], "tags": [{"id": tag}]})
        self.ledger_row(self.actor, first)
        return {"first": first, "second": second, "tag": tag, "ada": ada}

    def columns(self, table: str) -> list[str]:
        schema, name = table.split(".")
        rows = self.sql("SELECT column_name FROM information_schema.columns WHERE table_schema = %s "
                        "AND table_name = %s ORDER BY ordinal_position", (schema, name))
        return [row["column_name"] for row in rows if row["column_name"] not in _RENUMBERED]

    def table_rows(self, table: str) -> list[tuple[Any, ...]]:
        columns = self.columns(table)
        schema, name = table.split(".")
        query = sql.SQL("SELECT {cols} FROM {table} WHERE library_id = %s ORDER BY {cols}").format(
            cols=sql.SQL(", ").join(sql.SQL("{}::text").format(sql.Identifier(c)) for c in columns),
            table=sql.Identifier(schema, name))
        return [tuple(row.values()) for row in self.sql(query, (self.actor.library_id,))]

    def snapshot(self) -> dict[str, list[tuple[Any, ...]]]:
        return {table: self.table_rows(table) for table in tables.BACKUP_CONTENT_TABLES}

    def test_every_content_table_and_object_kind_round_trips(self) -> None:
        ids = self.populate()
        before = self.snapshot()
        empty = sorted(table for table, rows in before.items() if not rows)
        self.assertEqual(empty, [], "every content table needs a fixture row")
        result = self.backup()
        namespaces = {key.split("/", 1)[0] for key in result.manifest["objects"]}
        self.assertEqual(namespaces, set(NAMESPACES))

        # Change every family after the backup.
        work_commands.delete(self.db, self.ctx, ids["second"])
        work_commands.update_metadata(self.db, self.ctx, ids["first"], {"title": "Changed"})
        tag_commands.delete(self.db, self.ctx, ids["tag"])
        people_fixtures.add_person(self.db, self.ctx, "Later", "Person")
        self.sql("DELETE FROM prks.library_settings")
        self.sql("DELETE FROM prks.citation_styles")
        self.sql("DELETE FROM prks.sync_operations")
        self.assertNotEqual(self.snapshot(), before)
        # Lose every archived object, so the restore must write each one back from the archive.
        for key in result.manifest["objects"]:
            namespace, name = key.split("/", 1)
            self.objects.delete(StorageKey(namespace, name))
            self.assertIsNone(self.objects.stat(StorageKey(namespace, name)))

        restored = self.restore(result.path)
        self.assertEqual((restored.objects_written, restored.objects_reused), (len(result.manifest["objects"]), 0))
        after = self.snapshot()
        for table in tables.BACKUP_CONTENT_TABLES:
            with self.subTest(table=table):
                self.assertEqual(after[table], before[table])
        for key, entry in result.manifest["objects"].items():
            namespace, name = key.split("/", 1)
            with self.subTest(object=key):
                self.assertTrue(self.objects.verify(StorageKey(namespace, name), entry["sha256"]))
        self.assertIn(NAMESPACE_ASSET_OBJECTS, namespaces)
        self.assertIn(NAMESPACE_PORTRAITS, namespaces)
