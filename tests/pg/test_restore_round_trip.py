"""A round trip over every content table and object kind (L-B-1 #639; §11.4).

The fixture writes at least one row of every table on
``tables.BACKUP_CONTENT_TABLES`` (the test fails when a new content table has
none) and stores an object of every namespace: Work PDFs, a Processing upload
and a Person portrait. After a backup, the Library is changed in every family;
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
from backend.domain.objects import portraits
from backend.domain.processing import commands as processing
from backend.storage.objects import NAMESPACE_ASSET_OBJECTS, NAMESPACE_PORTRAITS, NAMESPACES, StorageKey
from tests.pg.test_annotations import HIGHLIGHT
from tests.pg.test_objects_portraits import image
from tests.pg.test_restore_commit import RestoreTestCase

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
        lib.delete_work_record(gone)  # its object is claimed for cleanup
        self.sql("INSERT INTO prks.sync_work_lifecycle (library_id, work_id, state) VALUES (%s, %s, 'deleted')",
                 (self.actor.library_id, gone))
        # People.
        ada = lib.add_person("Ada", "Lovelace", image_url="https://example.org/ada.png")
        self.sql("INSERT INTO prks.person_aliases (library_id, person_id, position, alias) "
                 "VALUES (%s, %s, 0, 'A. Lovelace')", (self.actor.library_id, ada))
        lib.add_role(ada, first, "Author")
        group = lib.add_person_group_with_parent_options("Analysts")
        lib.add_person_to_group(ada, group)
        publisher = lib.add_publisher("Penguin")["id"]
        lib.add_publisher_alias(publisher, "Penguin Books")
        portrait = portraits.store(self.objects, image())
        self.edit_command(lambda scope: portraits.commit(scope, self.objects, ada, "https://example.org/ada.png",
                                                    portrait))
        # Organization.
        tag = lib.add_tag("Philosophy")["id"]
        lib.add_tag_alias(tag, "Phil")
        lib.add_tag_to_work(first, tag)
        lib.delete_tag(lib.add_tag("Retired")["id"])
        folder = lib.add_folder("Reading")
        lib.add_work_to_folder(folder, first)
        lib.add_tag_to_folder(folder, tag)
        playlist = lib.add_playlist("Queue")
        lib.add_work_to_playlist(playlist, first)
        lib.create_saved_view("Culture", {"mode": "all", "q": "culture", "tag": "", "author": "",
                                          "publisher": ""})
        lib.patch_app_settings({"annotation_author": "Ada"})
        # Research.
        engines = lib.create_concept("Engines")["id"]
        lib.save_work_notes(first, "On [[concept:Engines]]")
        lib.replace_concept_aliases(engines, ["Machines"])
        lib.replace_concept_parents(engines, [lib.create_concept("Mechanism")["id"]])
        claim = lib.create_position("Engines compute")["id"]
        base = lib.create_argument(name="Base", kind="argument", sources=[{"work_id": first, "manifestation_id": revision, "pages": "1"}],
                                   targets=[{"type": "position", "id": claim, "verdict_id": "supports"}])["id"]
        lib.create_argument(name="Reply", kind="argument",
                            targets=[{"type": "argument", "id": base, "verdict_id": "opposes"}])
        lib.save_work_annotations(first, json.dumps([HIGHLIGHT]), base_set_revision=0)
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
        self.library.delete_work_record(ids["second"])
        self.library.update_work_metadata(ids["first"], {"title": "Changed"})
        self.library.delete_tag(ids["tag"])
        self.library.add_person("Later", "Person")
        self.sql("DELETE FROM prks.library_settings")
        self.sql("DELETE FROM prks.sync_operations")
        self.assertNotEqual(self.snapshot(), before)

        self.restore(result.path)
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
