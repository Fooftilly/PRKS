import unittest
import threading
import time
import socket
import http.client
import urllib.request
import urllib.parse
import urllib.error
import json
import os
import sys
import base64
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_tests import apply_isolated_test_env

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
apply_isolated_test_env(_PROJECT_DIR)

from backend.db_manager import (
    prks_person_image_cache_path,
    prks_person_image_legacy_bin_path,
    prks_person_image_url_hash,
    PRKS_PERSON_IMAGE_CACHE_REV,
)
from backend.person_image import PortraitImage, decode_and_transcode
from backend.storage.config import StorageConfig

import backend.server as server_module
import backend.db_manager as db_manager_module

unittest.defaultTestLoader.sortTestMethodsUsing = None


def _tiny_test_portrait_png_bytes() -> bytes:
    """Minimal decodable PNG for portrait encode/cache tests."""
    from io import BytesIO

    from PIL import Image

    img = Image.new("RGB", (64, 64), (120, 80, 200))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _pdf_with_text_bytes(text: str) -> bytes:
    import pymupdf as fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text or "")
    out = doc.tobytes()
    doc.close()
    return out


def _find_free_port() -> int:
    """Return a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


class TestServerAPI(unittest.TestCase):
    @classmethod
    def _wait_for_server_ready(cls, timeout_seconds=8.0):
        base_url = cls._base_url
        deadline = time.time() + timeout_seconds
        last_err = None
        while time.time() < deadline:
            try:
                req = urllib.request.Request(f"{base_url}/api/works")
                with urllib.request.urlopen(req, timeout=1.2) as res:
                    if res.status == 200:
                        return
            except Exception as e:
                last_err = e
            time.sleep(0.1)
        raise RuntimeError(f"Server did not become ready in time: {last_err}")

    @classmethod
    def setUpClass(cls):
        # Pick a free port at setup time to avoid collisions with parallel runs.
        cls._test_port = _find_free_port()
        cls._base_url = f"http://localhost:{cls._test_port}"

        # Create a test database
        cls._tmpdir = tempfile.mkdtemp(prefix="prks-server-tests-")
        cls._storage_root = os.path.join(cls._tmpdir, "storage")
        cls._processing_root = os.path.join(cls._tmpdir, "processing")
        os.makedirs(cls._storage_root, exist_ok=True)
        os.makedirs(cls._processing_root, exist_ok=True)
        cfg = replace(
            StorageConfig.for_testing(cls._storage_root),
            processing_dir=cls._processing_root,
        )
        server_module.bind_storage(cfg)
        cls.test_db = server_module.db
        cls.test_db_path = server_module.db.db_path

        # Start server in a background daemon thread
        cls.server_thread = threading.Thread(target=server_module.run_server, args=(cls._test_port,), daemon=True)
        cls.server_thread.start()
        
        cls._wait_for_server_ready()

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_tmpdir", None):
            try:
                import shutil
                shutil.rmtree(cls._tmpdir, ignore_errors=True)
            except Exception:
                pass

    def setUp(self):
        # We can clear the db before each test or just ensure unique data.
        # Since these tests are isolated enough, we'll let them add data.
        pass

    def test_1_get_works_empty(self):
        req = urllib.request.Request(f"{self._base_url}/api/works")
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 200)
            self.assertTrue((res.headers.get("X-Request-ID") or "").strip())
            data = json.loads(res.read().decode())
            # Depending on test order, it might not be empty, so we just check it's a list
            self.assertIsInstance(data, list)

    def test_1a_internal_error_has_request_id(self):
        with patch.object(server_module.db, "get_all_works", side_effect=RuntimeError("boom")):
            req = urllib.request.Request(f"{self._base_url}/api/works")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 500)
        req_id_header = (cm.exception.headers.get("X-Request-ID") or "").strip()
        self.assertTrue(req_id_header)
        body = json.loads(cm.exception.read().decode())
        self.assertEqual(body.get("error"), "internal_error")
        self.assertEqual(body.get("request_id"), req_id_header)

    def test_1aa_client_errors_endpoint_accepts_payload(self):
        payload = {
            "kind": "window_error",
            "error_name": "TypeError",
            "source": "app.js",
            "line": 10,
            "column": 2,
            "request_id": "abc123",
        }
        req = urllib.request.Request(
            f"{self._base_url}/api/client-errors",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 200)
            self.assertTrue((res.headers.get("X-Request-ID") or "").strip())
            body = json.loads(res.read().decode())
        self.assertEqual(body.get("status"), "logged")
        self.assertTrue((body.get("request_id") or "").strip())

    def test_1ab_client_errors_endpoint_rejects_invalid_payload(self):
        req = urllib.request.Request(
            f"{self._base_url}/api/client-errors",
            data=json.dumps(["window_error"]).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400)
        body = json.loads(cm.exception.read().decode())
        self.assertIn("error", body)

    def test_1b_settings_get_and_patch_annotation_author(self):
        req = urllib.request.Request(f"{self._base_url}/api/settings")
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 200)
            data = json.loads(res.read().decode())
            self.assertIn("annotation_author", data)
            self.assertEqual(data["annotation_author"], "")
            self.assertIn("bibtex_export_fields", data)
            self.assertIsInstance(data["bibtex_export_fields"], dict)
            self.assertTrue(data["bibtex_export_fields"].get("abstract"))

        patch = json.dumps({"annotation_author": "Shared Author"}).encode()
        req2 = urllib.request.Request(f"{self._base_url}/api/settings", data=patch, method="PATCH")
        req2.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req2) as res2:
            self.assertEqual(res2.status, 200)
            out = json.loads(res2.read().decode())
            self.assertEqual(out.get("annotation_author"), "Shared Author")
            self.assertTrue(out.get("bibtex_export_fields", {}).get("abstract"))

        bf_patch = json.dumps({"bibtex_export_fields": {"abstract": False, "isbn": False}}).encode()
        req_bf = urllib.request.Request(f"{self._base_url}/api/settings", data=bf_patch, method="PATCH")
        req_bf.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_bf) as res_bf:
            self.assertEqual(res_bf.status, 200)
            out_bf = json.loads(res_bf.read().decode())
            self.assertFalse(out_bf["bibtex_export_fields"]["abstract"])
            self.assertFalse(out_bf["bibtex_export_fields"]["isbn"])
            self.assertTrue(out_bf["bibtex_export_fields"]["year"])

        with urllib.request.urlopen(req) as res3:
            data3 = json.loads(res3.read().decode())
            self.assertEqual(data3.get("annotation_author"), "Shared Author")
            self.assertFalse(data3["bibtex_export_fields"]["abstract"])
            self.assertFalse(data3["bibtex_export_fields"]["isbn"])

        reset_bf = json.dumps({"bibtex_export_fields": {"abstract": True, "isbn": True}}).encode()
        req_reset = urllib.request.Request(f"{self._base_url}/api/settings", data=reset_bf, method="PATCH")
        req_reset.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_reset) as _:
            pass

    def test_2_create_and_get_work(self):
        # Create work
        payload = {"title": "API Test Work", "status": "Not Started"}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(f"{self._base_url}/api/works", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 200)
            resp_data = json.loads(res.read().decode())
            self.assertIn("id", resp_data)
            w_id = resp_data["id"]

        # Get single work
        req2 = urllib.request.Request(f"{self._base_url}/api/works/{w_id}")
        with urllib.request.urlopen(req2) as res2:
            single = json.loads(res2.read().decode())
            self.assertEqual(single["id"], w_id)
            self.assertEqual(single["title"], "API Test Work")

    def test_3_create_folder(self):
        payload = {"title": "API Folder", "description": "Folder desc"}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(f"{self._base_url}/api/folders", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 200)
            resp_data = json.loads(res.read().decode())
            self.assertIn("id", resp_data)

    def test_3b_create_folder_duplicate_name_conflict(self):
        payload = {"title": "Dup Folder API", "description": "first"}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(f"{self._base_url}/api/folders", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 200)

        req2 = urllib.request.Request(
            f"{self._base_url}/api/folders",
            data=json.dumps({"title": "dup folder api", "description": "second"}).encode(),
            method="POST",
        )
        req2.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req2)
        self.assertEqual(cm.exception.code, 409)
        body = json.loads(cm.exception.read().decode())
        self.assertIn("error", body)

    def test_3c_create_subfolder_with_parent(self):
        req_parent = urllib.request.Request(
            f"{self._base_url}/api/folders",
            data=json.dumps({"title": "Parent API Folder", "description": ""}).encode(),
            method="POST",
        )
        req_parent.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_parent) as rp:
            parent_id = json.loads(rp.read().decode())["id"]

        req_child = urllib.request.Request(
            f"{self._base_url}/api/folders",
            data=json.dumps(
                {"title": "Child API Folder", "description": "", "parent_id": parent_id}
            ).encode(),
            method="POST",
        )
        req_child.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_child) as rc:
            child_id = json.loads(rc.read().decode())["id"]

        req_get = urllib.request.Request(f"{self._base_url}/api/folders/{urllib.parse.quote(child_id)}")
        with urllib.request.urlopen(req_get) as rg:
            child = json.loads(rg.read().decode())
        self.assertEqual(child.get("parent_id"), parent_id)

    def test_4_patch_work(self):
        # Create
        payload = {"title": "Patch Work"}
        req = urllib.request.Request(f"{self._base_url}/api/works", data=json.dumps(payload).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            w_id = json.loads(res.read().decode())["id"]
        
        # Patch
        patch_payload = {"title": "Patched Status", "status": "Completed"}
        req2 = urllib.request.Request(f"{self._base_url}/api/works/{w_id}", data=json.dumps(patch_payload).encode(), method="PATCH")
        req2.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req2) as res2:
            self.assertEqual(res2.status, 200)
            
        # Verify
        req3 = urllib.request.Request(f"{self._base_url}/api/works/{w_id}")
        with urllib.request.urlopen(req3) as res3:
            single = json.loads(res3.read().decode())
            self.assertEqual(single["title"], "Patched Status")
            self.assertEqual(single["status"], "Completed")

    def test_4b_patch_work_private_notes(self):
        req = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps({"title": "Notes Work"}).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            w_id = json.loads(res.read().decode())["id"]

        patch_payload = {"private_notes": "Thesis ch. 3 — check with Mark"}
        req2 = urllib.request.Request(
            f"{self._base_url}/api/works/{w_id}",
            data=json.dumps(patch_payload).encode(),
            method="PATCH",
        )
        req2.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req2) as res2:
            self.assertEqual(res2.status, 200)

        req3 = urllib.request.Request(f"{self._base_url}/api/works/{w_id}")
        with urllib.request.urlopen(req3) as res3:
            single = json.loads(res3.read().decode())
        self.assertEqual(single.get("private_notes"), "Thesis ch. 3 — check with Mark")

    def test_4c_patch_folder_private_notes(self):
        req = urllib.request.Request(
            f"{self._base_url}/api/folders",
            data=json.dumps({"title": "Notes Folder", "description": "d"}).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            f_id = json.loads(res.read().decode())["id"]

        req2 = urllib.request.Request(
            f"{self._base_url}/api/folders/{f_id}",
            data=json.dumps({"private_notes": "Literature review bucket"}).encode(),
            method="PATCH",
        )
        req2.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req2) as res2:
            self.assertEqual(res2.status, 200)

        req3 = urllib.request.Request(f"{self._base_url}/api/folders/{f_id}")
        with urllib.request.urlopen(req3) as res3:
            folder = json.loads(res3.read().decode())
        self.assertEqual(folder.get("private_notes"), "Literature review bucket")

    def test_4d_patch_folder_parent_and_cycle_rejection(self):
        req_root = urllib.request.Request(
            f"{self._base_url}/api/folders",
            data=json.dumps({"title": "Hierarchy Root", "description": ""}).encode(),
            method="POST",
        )
        req_root.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_root) as rr:
            root_id = json.loads(rr.read().decode())["id"]

        req_child = urllib.request.Request(
            f"{self._base_url}/api/folders",
            data=json.dumps({"title": "Hierarchy Child", "description": "", "parent_id": root_id}).encode(),
            method="POST",
        )
        req_child.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_child) as rc:
            child_id = json.loads(rc.read().decode())["id"]

        req_grand = urllib.request.Request(
            f"{self._base_url}/api/folders",
            data=json.dumps({"title": "Hierarchy Grand", "description": "", "parent_id": child_id}).encode(),
            method="POST",
        )
        req_grand.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_grand) as rg:
            grand_id = json.loads(rg.read().decode())["id"]

        req_cycle = urllib.request.Request(
            f"{self._base_url}/api/folders/{urllib.parse.quote(root_id)}",
            data=json.dumps({"parent_id": grand_id}).encode(),
            method="PATCH",
        )
        req_cycle.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req_cycle)
        self.assertEqual(cm.exception.code, 400)

        req_top = urllib.request.Request(
            f"{self._base_url}/api/folders/{urllib.parse.quote(child_id)}",
            data=json.dumps({"parent_id": None}).encode(),
            method="PATCH",
        )
        req_top.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_top) as rt:
            self.assertEqual(rt.status, 200)

        req_child_get = urllib.request.Request(f"{self._base_url}/api/folders/{urllib.parse.quote(child_id)}")
        with urllib.request.urlopen(req_child_get) as rcg:
            child = json.loads(rcg.read().decode())
        self.assertIsNone(child.get("parent_id"))

    def test_5_delete_work(self):
        # Create
        payload = {"title": "To Delete"}
        req = urllib.request.Request(f"{self._base_url}/api/works", data=json.dumps(payload).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            w_id = json.loads(res.read().decode())["id"]
            
        # Delete
        req2 = urllib.request.Request(f"{self._base_url}/api/works/{w_id}", method="DELETE")
        with urllib.request.urlopen(req2) as res2:
            self.assertEqual(res2.status, 200)
            
        # Verify not found
        req3 = urllib.request.Request(f"{self._base_url}/api/works/{w_id}")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req3)
        self.assertEqual(cm.exception.code, 404)

    def test_5b_delete_missing_work_returns_200(self):
        req = urllib.request.Request(
            f"{self._base_url}/api/works/W-DOESNOTEXIST",
            method="DELETE",
        )
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 200)
            body = json.loads(res.read().decode())
        self.assertEqual(body.get("status"), "deleted")

    def test_5c_post_work_folder_rollback_removes_partial_work(self):
        term = "RollbackPartialWorkTextZed"
        pdf_bytes = _pdf_with_text_bytes(f"Body contains {term}")
        payload = {
            "title": "Rollback Partial Work",
            "status": "Planned",
            "file_b64": base64.b64encode(pdf_bytes).decode("utf-8"),
            "file_name": "rollback_partial.pdf",
        }
        pdfs_before = set(os.listdir(server_module.pdfs_dir))
        with patch.object(
            server_module.db,
            "add_work_to_folder",
            side_effect=ValueError("This file is already in another folder."),
        ):
            req = urllib.request.Request(
                f"{self._base_url}/api/works",
                data=json.dumps(payload).encode(),
                method="POST",
            )
            req.add_header("Content-Type", "application/json")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 409)
        pdfs_after = set(os.listdir(server_module.pdfs_dir))
        self.assertEqual(pdfs_before, pdfs_after)
        req_list = urllib.request.Request(f"{self._base_url}/api/works")
        with urllib.request.urlopen(req_list) as res:
            works = json.loads(res.read().decode())
        titles = [w.get("title") for w in works]
        self.assertNotIn("Rollback Partial Work", titles)
        leftover_ids = server_module.text_index.search_work_ids(term)
        self.assertEqual(leftover_ids, [])

    def test_6_patch_person(self):
        payload = {"first_name": "Test", "last_name": "Philosopher"}
        req = urllib.request.Request(f"{self._base_url}/api/persons", data=json.dumps(payload).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            p_id = json.loads(res.read().decode())["id"]

        patch_payload = {
            "link_wikipedia": "https://en.wikipedia.org/wiki/Test",
            "link_iep": "https://iep.utm.edu/test/",
        }
        req2 = urllib.request.Request(
            f"{self._base_url}/api/persons/{p_id}",
            data=json.dumps(patch_payload).encode(),
            method="PATCH",
        )
        req2.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req2) as res2:
            self.assertEqual(res2.status, 200)

        req3 = urllib.request.Request(f"{self._base_url}/api/persons/{p_id}")
        with urllib.request.urlopen(req3) as res3:
            person = json.loads(res3.read().decode())
        self.assertEqual(person["link_wikipedia"], "https://en.wikipedia.org/wiki/Test")
        self.assertEqual(person["link_iep"], "https://iep.utm.edu/test/")

    def test_6b_delete_unlinked_person(self):
        req_p = urllib.request.Request(
            f"{self._base_url}/api/persons",
            data=json.dumps({"first_name": "Delete", "last_name": "Allowed"}).encode(),
            method="POST",
        )
        req_p.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_p) as rp:
            p_id = json.loads(rp.read().decode())["id"]

        req_del = urllib.request.Request(
            f"{self._base_url}/api/persons/{urllib.parse.quote(p_id)}",
            method="DELETE",
        )
        with urllib.request.urlopen(req_del) as rd:
            self.assertEqual(rd.status, 200)
            body = json.loads(rd.read().decode())
        self.assertEqual(body.get("status"), "deleted")

        req_get = urllib.request.Request(f"{self._base_url}/api/persons/{urllib.parse.quote(p_id)}")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req_get)
        self.assertEqual(cm.exception.code, 404)

    def test_6c_delete_linked_person_conflict(self):
        req_p = urllib.request.Request(
            f"{self._base_url}/api/persons",
            data=json.dumps({"first_name": "Delete", "last_name": "Blocked"}).encode(),
            method="POST",
        )
        req_p.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_p) as rp:
            p_id = json.loads(rp.read().decode())["id"]

        req_w = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps({"title": "Linked Work", "status": "Not Started"}).encode(),
            method="POST",
        )
        req_w.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_w) as rw:
            w_id = json.loads(rw.read().decode())["id"]

        req_role = urllib.request.Request(
            f"{self._base_url}/api/roles",
            data=json.dumps({"person_id": p_id, "work_id": w_id, "role_type": "Author"}).encode(),
            method="POST",
        )
        req_role.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_role) as rr:
            self.assertEqual(rr.status, 200)

        req_del = urllib.request.Request(
            f"{self._base_url}/api/persons/{urllib.parse.quote(p_id)}",
            method="DELETE",
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req_del)
        self.assertEqual(cm.exception.code, 409)
        body = json.loads(cm.exception.read().decode())
        self.assertIn("error", body)

    def test_7_person_groups_api(self):
        req = urllib.request.Request(
            f"{self._base_url}/api/person-groups",
            data=json.dumps({"name": "Root Group"}).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            g_root = json.loads(res.read().decode())["id"]

        req2 = urllib.request.Request(
            f"{self._base_url}/api/person-groups",
            data=json.dumps({"name": "Child Group", "parent_id": g_root}).encode(),
            method="POST",
        )
        req2.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req2) as res2:
            g_child = json.loads(res2.read().decode())["id"]

        req_list = urllib.request.Request(f"{self._base_url}/api/person-groups")
        with urllib.request.urlopen(req_list) as resl:
            groups = json.loads(resl.read().decode())
        self.assertEqual(len(groups), 2)

        req_p = urllib.request.Request(
            f"{self._base_url}/api/persons",
            data=json.dumps({"first_name": "A", "last_name": "Member"}).encode(),
            method="POST",
        )
        req_p.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_p) as resp:
            p_id = json.loads(resp.read().decode())["id"]

        req_m = urllib.request.Request(
            f"{self._base_url}/api/person-groups/{g_child}/members",
            data=json.dumps({"person_id": p_id}).encode(),
            method="POST",
        )
        req_m.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_m) as resm:
            self.assertEqual(resm.status, 200)

        req_g = urllib.request.Request(f"{self._base_url}/api/person-groups/{g_child}")
        with urllib.request.urlopen(req_g) as rg:
            detail = json.loads(rg.read().decode())
        self.assertEqual(len(detail["members"]), 1)

        patch_p = urllib.request.Request(
            f"{self._base_url}/api/persons/{p_id}",
            data=json.dumps({"group_ids": [g_root]}).encode(),
            method="PATCH",
        )
        patch_p.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(patch_p) as rp:
            self.assertEqual(rp.status, 200)

        req_g2 = urllib.request.Request(f"{self._base_url}/api/person-groups/{g_child}")
        with urllib.request.urlopen(req_g2) as rg2:
            detail2 = json.loads(rg2.read().decode())
        self.assertEqual(len(detail2["members"]), 0)

    def test_8_pdf_upload_and_fetch(self):
        pdf_bytes = _pdf_with_text_bytes("PRKS upload fetch smoke")
        payload = {
            "title": "Upload API Work",
            "status": "Planned",
            "file_b64": base64.b64encode(pdf_bytes).decode("utf-8"),
            "file_name": "upload_test.pdf",
        }
        req = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 200)
            w_id = json.loads(res.read().decode())["id"]

        req2 = urllib.request.Request(f"{self._base_url}/api/works/{w_id}")
        with urllib.request.urlopen(req2) as res2:
            work = json.loads(res2.read().decode())
        file_path = (work.get("file_path") or "").strip()
        self.assertTrue(file_path.startswith("/api/pdfs/"))

        req3 = urllib.request.Request(f"{self._base_url}{file_path}")
        with urllib.request.urlopen(req3) as res3:
            self.assertEqual(res3.status, 200)
            got = res3.read()
        self.assertIn(b"%PDF", got[:32])

    def test_8b_search_indexes_pdf_text_all_mode_only(self):
        term = "UniqPdfSearchTermAlpha"
        pdf_bytes = _pdf_with_text_bytes(f"Body contains {term} and extra words")
        payload = {
            "title": "PDF Text Search Work",
            "status": "Planned",
            "file_b64": base64.b64encode(pdf_bytes).decode("utf-8"),
            "file_name": "search_text_test.pdf",
        }
        req = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            w_id = json.loads(res.read().decode())["id"]

        req_keywords = urllib.request.Request(f"{self._base_url}/api/search?q={urllib.parse.quote(term)}")
        with urllib.request.urlopen(req_keywords) as rk:
            rows_keywords = json.loads(rk.read().decode())
        self.assertFalse(any(r.get("id") == w_id for r in rows_keywords))

        req_all = urllib.request.Request(f"{self._base_url}/api/search?any=1&q={urllib.parse.quote(term)}")
        with urllib.request.urlopen(req_all) as ra:
            rows_all = json.loads(ra.read().decode())
        self.assertTrue(any(r.get("id") == w_id for r in rows_all))

    def test_9_pdf_overwrite_endpoint(self):
        original = _pdf_with_text_bytes("oldtermreplacepdf")
        payload = {
            "title": "Overwrite Work",
            "status": "Planned",
            "file_b64": base64.b64encode(original).decode("utf-8"),
            "file_name": "overwrite_test.pdf",
        }
        req = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            w_id = json.loads(res.read().decode())["id"]

        req2 = urllib.request.Request(f"{self._base_url}/api/works/{w_id}")
        with urllib.request.urlopen(req2) as res2:
            work = json.loads(res2.read().decode())
        file_path = (work.get("file_path") or "").strip()
        self.assertTrue(file_path.startswith("/api/pdfs/"))

        updated = _pdf_with_text_bytes("newtermreplacepdf")
        overwrite_req = urllib.request.Request(
            f"{self._base_url}/api/works/{w_id}/pdf",
            data=json.dumps({"file_b64": base64.b64encode(updated).decode("utf-8")}).encode(),
            method="POST",
        )
        overwrite_req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(overwrite_req) as orr:
            self.assertEqual(orr.status, 200)

        fetch_req = urllib.request.Request(f"{self._base_url}{file_path}")
        with urllib.request.urlopen(fetch_req) as fres:
            got = fres.read()
        self.assertIn(b"%PDF", got[:32])

        req_s = urllib.request.Request(f"{self._base_url}/api/search?any=1&q=newtermreplacepdf")
        with urllib.request.urlopen(req_s) as rs:
            rows = json.loads(rs.read().decode())
        self.assertTrue(any(r.get("id") == w_id for r in rows))
        req_old = urllib.request.Request(f"{self._base_url}/api/search?any=1&q=oldtermreplacepdf")
        with urllib.request.urlopen(req_old) as rold:
            old_rows = json.loads(rold.read().decode())
        self.assertFalse(any(r.get("id") == w_id for r in old_rows))

    def test_9b_manual_reindex_pdf_text(self):
        term = "ManualReindexTermBeta"
        pdf_bytes = _pdf_with_text_bytes(f"manual index text {term}")
        filename = "manual_reindex_test.pdf"
        abs_pdf = os.path.join(server_module.pdfs_dir, filename)
        with open(abs_pdf, "wb") as f:
            f.write(pdf_bytes)

        w_id = self.__class__.test_db.add_work(
            title="Manual Reindex Work",
            status="Planned",
            file_path=f"/api/pdfs/{filename}",
        )
        self.__class__.test_db.add_work_to_folder(
            self.__class__.test_db.ensure_default_uncategorized_folder_id(),
            w_id,
        )

        req_before = urllib.request.Request(f"{self._base_url}/api/search?q={urllib.parse.quote(term)}")
        with urllib.request.urlopen(req_before) as rb:
            rows_before = json.loads(rb.read().decode())
        self.assertFalse(any(r.get("id") == w_id for r in rows_before))

        req_reindex = urllib.request.Request(
            f"{self._base_url}/api/works/reindex-pdf-text",
            data=json.dumps({}).encode(),
            method="POST",
        )
        req_reindex.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_reindex) as rr:
            self.assertEqual(rr.status, 200)
            body = json.loads(rr.read().decode())
        self.assertEqual(body.get("status"), "ok")
        self.assertGreaterEqual(int(body.get("processed", 0)), 1)
        self.assertIn("updated", body)
        self.assertIn("unchanged", body)
        self.assertIn("removed_orphans", body)

        req_after = urllib.request.Request(f"{self._base_url}/api/search?any=1&q={urllib.parse.quote(term)}")
        with urllib.request.urlopen(req_after) as ra:
            rows_after = json.loads(ra.read().decode())
        self.assertTrue(any(r.get("id") == w_id for r in rows_after))

        req_force = urllib.request.Request(
            f"{self._base_url}/api/works/reindex-pdf-text",
            data=json.dumps({"force": True}).encode(),
            method="POST",
        )
        req_force.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_force) as rf:
            self.assertEqual(rf.status, 200)
            force_body = json.loads(rf.read().decode())
        self.assertGreaterEqual(int(force_body.get("updated", 0)), 1)

    def test_9b2_patch_file_path_syncs_text_index(self):
        a_name = "patch_fp_a.pdf"
        b_name = "patch_fp_b.pdf"
        a_path = os.path.join(server_module.pdfs_dir, a_name)
        b_path = os.path.join(server_module.pdfs_dir, b_name)
        with open(a_path, "wb") as handle:
            handle.write(_pdf_with_text_bytes("patchfilepathtermA"))
        with open(b_path, "wb") as handle:
            handle.write(_pdf_with_text_bytes("patchfilepathtermB"))
        payload = {
            "title": "Patch File Path Work",
            "status": "Planned",
            "file_b64": base64.b64encode(_pdf_with_text_bytes("unused")).decode("utf-8"),
            "file_name": "patch_fp_upload.pdf",
        }
        req = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            w_id = json.loads(res.read().decode())["id"]
        patch_a = urllib.request.Request(
            f"{self._base_url}/api/works/{w_id}",
            data=json.dumps({"file_path": f"/api/pdfs/{a_name}"}).encode(),
            method="PATCH",
        )
        patch_a.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(patch_a) as pa:
            self.assertEqual(pa.status, 200)
        req_a = urllib.request.Request(f"{self._base_url}/api/search?any=1&q=patchfilepathtermA")
        with urllib.request.urlopen(req_a) as ra:
            self.assertTrue(any(r.get("id") == w_id for r in json.loads(ra.read().decode())))
        patch_b = urllib.request.Request(
            f"{self._base_url}/api/works/{w_id}",
            data=json.dumps({"file_path": f"/api/pdfs/{b_name}"}).encode(),
            method="PATCH",
        )
        patch_b.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(patch_b) as pb:
            self.assertEqual(pb.status, 200)
        req_b = urllib.request.Request(f"{self._base_url}/api/search?any=1&q=patchfilepathtermB")
        with urllib.request.urlopen(req_b) as rb:
            self.assertTrue(any(r.get("id") == w_id for r in json.loads(rb.read().decode())))
        req_old = urllib.request.Request(f"{self._base_url}/api/search?any=1&q=patchfilepathtermA")
        with urllib.request.urlopen(req_old) as rold:
            self.assertFalse(any(r.get("id") == w_id for r in json.loads(rold.read().decode())))
        patch_clear = urllib.request.Request(
            f"{self._base_url}/api/works/{w_id}",
            data=json.dumps({"file_path": ""}).encode(),
            method="PATCH",
        )
        patch_clear.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(patch_clear) as pc:
            self.assertEqual(pc.status, 200)
        req_gone = urllib.request.Request(f"{self._base_url}/api/search?any=1&q=patchfilepathtermB")
        with urllib.request.urlopen(req_gone) as rg:
            self.assertFalse(any(r.get("id") == w_id for r in json.loads(rg.read().decode())))

    def test_9b3_overwrite_extraction_failure_clears_old_text(self):
        original = _pdf_with_text_bytes("overwritefailoldterm")
        payload = {
            "title": "Overwrite Fail Work",
            "status": "Planned",
            "file_b64": base64.b64encode(original).decode("utf-8"),
            "file_name": "overwrite_fail_test.pdf",
        }
        req = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            w_id = json.loads(res.read().decode())["id"]
        req_old = urllib.request.Request(f"{self._base_url}/api/search?any=1&q=overwritefailoldterm")
        with urllib.request.urlopen(req_old) as rold:
            self.assertTrue(any(r.get("id") == w_id for r in json.loads(rold.read().decode())))
        updated = _pdf_with_text_bytes("overwritefailnewterm")
        from backend.text_index import PDFTextExtractionError

        with patch(
            "backend.text_index.extract_pdf",
            side_effect=PDFTextExtractionError("FileDataError"),
        ):
            overwrite_req = urllib.request.Request(
                f"{self._base_url}/api/works/{w_id}/pdf",
                data=json.dumps({"file_b64": base64.b64encode(updated).decode("utf-8")}).encode(),
                method="POST",
            )
            overwrite_req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(overwrite_req) as orr:
                self.assertEqual(orr.status, 200)
        req_old2 = urllib.request.Request(f"{self._base_url}/api/search?any=1&q=overwritefailoldterm")
        with urllib.request.urlopen(req_old2) as rold2:
            self.assertFalse(any(r.get("id") == w_id for r in json.loads(rold2.read().decode())))
        req_new = urllib.request.Request(f"{self._base_url}/api/search?any=1&q=overwritefailnewterm")
        with urllib.request.urlopen(req_new) as rnew:
            self.assertFalse(any(r.get("id") == w_id for r in json.loads(rnew.read().decode())))
        req_work = urllib.request.Request(f"{self._base_url}/api/works/{w_id}")
        with urllib.request.urlopen(req_work) as rw:
            self.assertEqual(rw.status, 200)

    def test_9c_linearize_existing_pdfs_endpoint(self):
        pdf_bytes = _pdf_with_text_bytes("linearize me")
        filename = "manual_linearize_existing.pdf"
        abs_pdf = os.path.join(server_module.pdfs_dir, filename)
        with open(abs_pdf, "wb") as f:
            f.write(pdf_bytes)

        w_id = self.__class__.test_db.add_work(
            title="Manual Linearize Existing Work",
            status="Planned",
            file_path=f"/api/pdfs/{filename}",
        )
        self.__class__.test_db.add_work_to_folder(
            self.__class__.test_db.ensure_default_uncategorized_folder_id(),
            w_id,
        )

        req = urllib.request.Request(
            f"{self._base_url}/api/works/linearize-existing-pdfs",
            data=json.dumps({"unlinearized_only": True}).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 200)
            body = json.loads(res.read().decode())

        self.assertEqual(body.get("status"), "ok")
        self.assertGreaterEqual(int(body.get("processed", 0)), 1)
        changed = int(body.get("changed", 0))
        already_linearized = int(body.get("already_linearized", 0))
        skipped = int(body.get("skipped", 0))
        failed = int(body.get("failed", 0))
        self.assertGreaterEqual(changed + already_linearized + skipped, 1)
        self.assertGreaterEqual(failed, 0)

    def test_10_thumbnail_endpoint_smoke(self):
        pdf_bytes = b"%PDF-1.4\n%THUMB\n%%EOF\n"
        payload = {
            "title": "Thumb Work",
            "status": "Planned",
            "file_b64": base64.b64encode(pdf_bytes).decode("utf-8"),
            "file_name": "thumb_test.pdf",
        }
        req = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            w_id = json.loads(res.read().decode())["id"]

        thumb_req = urllib.request.Request(f"{self._base_url}/api/works/{w_id}/thumbnail?page=1")
        try:
            with urllib.request.urlopen(thumb_req) as tres:
                self.assertEqual(tres.status, 200)
                ctype = (tres.headers.get("Content-Type") or "").lower()
                self.assertTrue(("image/" in ctype) or ("application/octet-stream" in ctype) or (ctype == ""))
                _ = tres.read(32)
        except urllib.error.HTTPError as e:
            # Accept a controlled failure if thumbnail rendering deps are missing.
            self.assertIn(e.code, (404, 500))
            body = e.read().decode("utf-8", errors="replace")
            self.assertTrue(
                ("Could not render thumbnail" in body)
                or ("render thumbnail" in body.lower())
                or ("ghostscript" in body.lower())
                or ("poppler" in body.lower())
                or ("thumbnail unavailable" in body.lower())
            )

    def test_11_post_concepts_single_json_body(self):
        payload = {"name": "API Concept", "description": "from test"}
        req = urllib.request.Request(
            f"{self._base_url}/api/concepts",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 201)
            raw = res.read().decode()
        body = json.loads(raw)
        self.assertIn("id", body)
        self.assertEqual(body.get("name"), "API Concept")

    def test_12_pdf_path_outside_storage_returns_404(self):
        evil = urllib.request.Request(f"{self._base_url}/api/pdfs/..")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(evil)
        self.assertEqual(cm.exception.code, 404)

    def test_13_get_work_not_found_404(self):
        req = urllib.request.Request(f"{self._base_url}/api/works/W-00000000-NOTFOUND")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 404)

    def test_14_search_returns_json_list(self):
        req = urllib.request.Request(f"{self._base_url}/api/search?q=nonexistenttokenxyz")
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 200)
            data = json.loads(res.read().decode())
        self.assertIsInstance(data, list)

    def test_15_post_concepts_does_not_attach_work_metadata(self):
        req_p = urllib.request.Request(
            f"{self._base_url}/api/persons",
            data=json.dumps({"first_name": "Concept", "last_name": "Mention"}).encode(),
            method="POST",
        )
        req_p.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_p) as rp:
            p_id = json.loads(rp.read().decode())["id"]

        req_w = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps({"title": "Concept Work", "status": "Not Started"}).encode(),
            method="POST",
        )
        req_w.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_w) as rw:
            w_id = json.loads(rw.read().decode())["id"]

        payload = {
            "name": "Linked Concept",
            "description": "",
            "work_id": w_id,
            "annotations_text": "See [[Concept Mention]] for details.",
        }
        req_c = urllib.request.Request(
            f"{self._base_url}/api/concepts",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req_c.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_c) as rc:
            self.assertEqual(rc.status, 201)
            body = json.loads(rc.read().decode())
        self.assertEqual(body.get("name"), "Linked Concept")
        self.assertTrue(body.get("id"))

        rows = self.__class__.test_db.execute_query(
            "SELECT role_type FROM roles WHERE person_id = ? AND work_id = ?",
            (p_id, w_id),
        )
        self.assertEqual(rows, [])

    def test_16_playlist_add_item_and_get(self):
        req_pl = urllib.request.Request(
            f"{self._base_url}/api/playlists",
            data=json.dumps({"title": "Test PL", "description": ""}).encode(),
            method="POST",
        )
        req_pl.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_pl) as rpl:
            pl_id = json.loads(rpl.read().decode())["id"]

        req_w = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps({"title": "PL Item Work", "status": "Not Started"}).encode(),
            method="POST",
        )
        req_w.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_w) as rw:
            w_id = json.loads(rw.read().decode())["id"]

        req_item = urllib.request.Request(
            f"{self._base_url}/api/playlists/{pl_id}/items",
            data=json.dumps({"work_id": w_id}).encode(),
            method="POST",
        )
        req_item.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_item) as ri:
            self.assertEqual(ri.status, 200)
            self.assertEqual(json.loads(ri.read().decode()).get("status"), "added")

        req_get = urllib.request.Request(f"{self._base_url}/api/playlists/{pl_id}")
        with urllib.request.urlopen(req_get) as rg:
            pl = json.loads(rg.read().decode())
        ids = [it.get("id") for it in (pl.get("items") or [])]
        self.assertIn(w_id, ids)

    def test_17_post_pdf_rejects_unsafe_stored_file_path(self):
        req_w = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps({"title": "Unsafe path work", "status": "Not Started"}).encode(),
            method="POST",
        )
        req_w.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_w) as rw:
            w_id = json.loads(rw.read().decode())["id"]

        patch = urllib.request.Request(
            f"{self._base_url}/api/works/{w_id}",
            data=json.dumps({"file_path": "/api/pdfs/.."}).encode(),
            method="PATCH",
        )
        patch.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(patch) as rp:
            self.assertEqual(rp.status, 200)

        pdf_bytes = b"%PDF-1.4\n%T\n%%EOF\n"
        post_pdf = urllib.request.Request(
            f"{self._base_url}/api/works/{w_id}/pdf",
            data=json.dumps({"file_b64": base64.b64encode(pdf_bytes).decode("utf-8")}).encode(),
            method="POST",
        )
        post_pdf.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(post_pdf)
        # `/api/pdfs/..` is rejected by managed_pdf_filename (not a managed
        # basename), so replace reports no managed PDF rather than reaching
        # the safe_pdf_path 400 path.
        self.assertEqual(cm.exception.code, 404)
        err = json.loads(cm.exception.read().decode())
        self.assertIn("error", err)
        self.assertIn("managed PDF", err["error"])

    def test_18_delete_work_with_dotdot_file_path_does_not_crash(self):
        req_w = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps(
                {
                    "title": "Dotdot work",
                    "status": "Not Started",
                    "file_path": "/api/pdfs/..",
                }
            ).encode(),
            method="POST",
        )
        req_w.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_w) as rw:
            w_id = json.loads(rw.read().decode())["id"]

        del_req = urllib.request.Request(f"{self._base_url}/api/works/{w_id}", method="DELETE")
        with urllib.request.urlopen(del_req) as rd:
            self.assertEqual(rd.status, 200)

        req_get = urllib.request.Request(f"{self._base_url}/api/works/{w_id}")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req_get)
        self.assertEqual(cm.exception.code, 404)

    def test_19_delete_non_empty_folder_returns_conflict(self):
        req_f = urllib.request.Request(
            f"{self._base_url}/api/folders",
            data=json.dumps({"title": "Conflict Folder", "description": ""}).encode(),
            method="POST",
        )
        req_f.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_f) as rf:
            f_id = json.loads(rf.read().decode())["id"]

        req_w = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps({"title": "Foldered Work", "status": "Planned"}).encode(),
            method="POST",
        )
        req_w.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_w) as rw:
            w_id = json.loads(rw.read().decode())["id"]

        self.__class__.test_db.move_work_to_folder(w_id, f_id)
        req_del = urllib.request.Request(f"{self._base_url}/api/folders/{f_id}", method="DELETE")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req_del)
        self.assertEqual(cm.exception.code, 409)
        body = json.loads(cm.exception.read().decode())
        self.assertIn("error", body)

    def test_20_tags_endpoint_roundtrip(self):
        req_create = urllib.request.Request(
            f"{self._base_url}/api/tags",
            data=json.dumps({"name": "api-tag", "color": "#123abc"}).encode(),
            method="POST",
        )
        req_create.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_create) as rc:
            payload = json.loads(rc.read().decode())
        self.assertIn("id", payload)

        req_list = urllib.request.Request(f"{self._base_url}/api/tags")
        with urllib.request.urlopen(req_list) as rl:
            tags = json.loads(rl.read().decode())
        self.assertTrue(any(t.get("id") == payload["id"] for t in tags))

    def test_20b_tags_merge_endpoint(self):
        db = self.__class__.test_db
        w = db.add_work(title="API Merge Work")
        src = db.add_tag("ApiMergeSrc", "#a00")["id"]
        tgt = db.add_tag("ApiMergeTgt", "#b00")["id"]
        db.add_tag_to_work(w, src)
        req = urllib.request.Request(
            f"{self._base_url}/api/tags/merge",
            data=json.dumps({"source_tag_id": src, "target_tag_id": tgt}).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 200)
            body = json.loads(res.read().decode())
        self.assertEqual(body.get("status"), "merged")
        self.assertEqual(body.get("canonical_tag_id"), tgt)
        self.assertEqual(body.get("canonical_name"), "ApiMergeTgt")
        self.assertFalse(db.execute_query("SELECT id FROM tags WHERE id = ?", (src,)))
        wtags = db.get_work_tags(w)
        self.assertEqual(len(wtags), 1)
        self.assertEqual(wtags[0]["id"], tgt)

        solo = db.add_tag("ApiMergeSolo", "#c00")["id"]
        req_self = urllib.request.Request(
            f"{self._base_url}/api/tags/merge",
            data=json.dumps({"source_tag_id": solo, "target_tag_id": solo}).encode(),
            method="POST",
        )
        req_self.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req_self)
        self.assertEqual(cm.exception.code, 400)

    def test_20c_delete_tag_endpoint(self):
        db = self.__class__.test_db
        w = db.add_work(title="API Delete Tag Work")
        tid = db.add_tag("ApiDeleteCanon", "#d00")["id"]
        db.add_tag_alias(tid, "ApiDeleteAlias")
        db.add_tag_to_work(w, tid)
        req = urllib.request.Request(
            f"{self._base_url}/api/tags/{tid}",
            method="DELETE",
        )
        with urllib.request.urlopen(req) as res:
            body = json.loads(res.read().decode())
        self.assertEqual(body.get("status"), "deleted")
        self.assertNotIn("promoted", body)
        self.assertFalse(db.execute_query("SELECT id FROM tags WHERE id = ?", (tid,)))
        self.assertEqual(len(db.get_work_tags(w)), 0)

    def test_21_recent_and_bibtex_smoke(self):
        req_w = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps({"title": "Graph Bib Work", "status": "Not Started", "author_text": "Ada"}).encode(),
            method="POST",
        )
        req_w.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_w) as rw:
            w_id = json.loads(rw.read().decode())["id"]

        with urllib.request.urlopen(urllib.request.Request(f"{self._base_url}/api/works/{w_id}")) as rg:
            self.assertEqual(rg.status, 200)

        # A GET is a PURE read: it must not put the Work in Recent.
        with urllib.request.urlopen(urllib.request.Request(f"{self._base_url}/api/recent")) as rr:
            recent = json.loads(rr.read().decode())
        self.assertIsInstance(recent, list)
        self.assertFalse(any(r.get("id") == w_id for r in recent),
                         "GET /api/works/:id must not record an open")

        # Only the explicit open event does.
        self.assertEqual(self._sv_json("POST", f"/api/works/{w_id}/opened", {})[0], 200)
        with urllib.request.urlopen(urllib.request.Request(f"{self._base_url}/api/recent")) as rr:
            recent = json.loads(rr.read().decode())
        self.assertTrue(any(r.get("id") == w_id for r in recent))

        with urllib.request.urlopen(urllib.request.Request(f"{self._base_url}/api/recently-added")) as ra:
            recently_added = json.loads(ra.read().decode())
        self.assertIsInstance(recently_added, list)
        self.assertTrue(any(r.get("id") == w_id for r in recently_added))

        with urllib.request.urlopen(urllib.request.Request(f"{self._base_url}/api/bibtex/{w_id}")) as br:
            txt = br.read().decode("utf-8", errors="replace")
        self.assertIn("@", txt)
        self.assertIn("Graph Bib Work", txt)

    def test_21b_bibtex_includes_translator_role(self):
        req_p = urllib.request.Request(
            f"{self._base_url}/api/persons",
            data=json.dumps({"first_name": "Anne", "last_name": "Translator"}).encode(),
            method="POST",
        )
        req_p.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_p) as rp:
            p_id = json.loads(rp.read().decode())["id"]

        req_w = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps({"title": "Translated API Work", "status": "Not Started", "doc_type": "book"}).encode(),
            method="POST",
        )
        req_w.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_w) as rw:
            w_id = json.loads(rw.read().decode())["id"]

        req_role = urllib.request.Request(
            f"{self._base_url}/api/roles",
            data=json.dumps({"person_id": p_id, "work_id": w_id, "role_type": "Translator"}).encode(),
            method="POST",
        )
        req_role.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_role) as rr:
            self.assertEqual(rr.status, 200)

        with urllib.request.urlopen(urllib.request.Request(f"{self._base_url}/api/bibtex/{w_id}")) as br:
            txt = br.read().decode("utf-8", errors="replace")
        self.assertIn("translator = {Translator, Anne}", txt)

    def test_21c_bibtex_includes_book_contributor_roles(self):
        req_w = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps({"title": "Contributors API Work", "status": "Not Started", "doc_type": "book"}).encode(),
            method="POST",
        )
        req_w.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_w) as rw:
            w_id = json.loads(rw.read().decode())["id"]

        contributors = [
            ("Ivy", "Intro", "Introduction", "introduction = {Intro, Ivy}"),
            ("Fiona", "Fore", "Foreword", "foreword = {Fore, Fiona}"),
            ("Aaron", "After", "Afterword", "afterword = {After, Aaron}"),
        ]

        for first, last, role, _expected in contributors:
            req_p = urllib.request.Request(
                f"{self._base_url}/api/persons",
                data=json.dumps({"first_name": first, "last_name": last}).encode(),
                method="POST",
            )
            req_p.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req_p) as rp:
                p_id = json.loads(rp.read().decode())["id"]

            req_role = urllib.request.Request(
                f"{self._base_url}/api/roles",
                data=json.dumps({"person_id": p_id, "work_id": w_id, "role_type": role}).encode(),
                method="POST",
            )
            req_role.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req_role) as rr:
                self.assertEqual(rr.status, 200)

        with urllib.request.urlopen(urllib.request.Request(f"{self._base_url}/api/bibtex/{w_id}")) as br:
            txt = br.read().decode("utf-8", errors="replace")
        for _, _, _, expected in contributors:
            self.assertIn(expected, txt)

    def test_22_roles_and_annotations_endpoints(self):
        req_p = urllib.request.Request(
            f"{self._base_url}/api/persons",
            data=json.dumps({"first_name": "Role", "last_name": "Owner"}).encode(),
            method="POST",
        )
        req_p.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_p) as rp:
            p_id = json.loads(rp.read().decode())["id"]

        req_w = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps({"title": "Role Work", "status": "Planned"}).encode(),
            method="POST",
        )
        req_w.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_w) as rw:
            w_id = json.loads(rw.read().decode())["id"]

        req_role = urllib.request.Request(
            f"{self._base_url}/api/roles",
            data=json.dumps({"person_id": p_id, "work_id": w_id, "role_type": "Author"}).encode(),
            method="POST",
        )
        req_role.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_role) as rr:
            self.assertEqual(rr.status, 200)

        # Unique id: annotations.id is globally unique across Works.
        ann = [
            {
                "id": "role-work-ann-1",
                "type": "note",
                "contents": "hello",
                "pageIndex": 0,
                "color": "#fff",
            }
        ]
        # Legacy annotations-first handshake requires the acknowledged tip.
        with self._post_work_annotations(
            w_id, {"annotations_json": json.dumps(ann)}
        ) as ra:
            self.assertEqual(ra.status, 200)

        req_ann_get = urllib.request.Request(f"{self._base_url}/api/works/{w_id}/annotations")
        with urllib.request.urlopen(req_ann_get) as rag:
            payload = json.loads(rag.read().decode())
        self.assertEqual(payload.get("work_id"), w_id)
        self.assertIn("annotations_json", payload)

        q = urllib.parse.urlencode(
            {"person_id": p_id, "role_type": "Author", "order_index": "0"}
        )
        req_del = urllib.request.Request(
            f"{self._base_url}/api/works/{w_id}/roles?{q}",
            method="DELETE",
        )
        with urllib.request.urlopen(req_del) as rd:
            self.assertEqual(rd.status, 200)
            body = json.loads(rd.read().decode())
        self.assertEqual(body.get("status"), "removed")

        with self.assertRaises(urllib.error.HTTPError) as cm2:
            urllib.request.urlopen(req_del)
        self.assertEqual(cm2.exception.code, 404)

    def _create_work_api(self, title):
        req = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps({"title": title, "status": "Planned"}).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read().decode())["id"]

    def _post_work_annotations(self, work_id, body):
        payload = dict(body)
        if (
            "annotations_json" in payload
            and "canonical_annotation_set_revision" not in payload
        ):
            mat = self.__class__.test_db.get_work_pdf_materialization(work_id) or {}
            payload["canonical_annotation_set_revision"] = int(
                mat.get("canonical_annotation_set_revision") or 0
            )
        req = urllib.request.Request(
            f"{self._base_url}/api/works/{work_id}/annotations",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        return urllib.request.urlopen(req)

    def _get_work_annotations(self, work_id):
        req = urllib.request.Request(f"{self._base_url}/api/works/{work_id}/annotations")
        with urllib.request.urlopen(req) as res:
            payload = json.loads(res.read().decode())
        return json.loads(payload["annotations_json"])

    def _save_confirm(self, work_id, token):
        q = urllib.parse.urlencode({"token": token})
        req = urllib.request.Request(
            f"{self._base_url}/api/works/{work_id}/save-confirm?{q}"
        )
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read().decode())

    def test_22c_annotation_http_validation(self):
        w_id = self._create_work_api("Ann HTTP Valid")
        other = self._create_work_api("Ann HTTP Other")
        good = [{"id": "keep", "type": "note", "contents": "hello", "pageIndex": 0}]
        with self._post_work_annotations(w_id, {"annotations_json": json.dumps(good)}) as res:
            self.assertEqual(res.status, 200)
        self.assertEqual(self._get_work_annotations(w_id)[0]["contents"], "hello")

        cases = [
            ({"save_token": "no-list"}, 400, "malformed_annotation_payload"),
            ({"annotations_json": "{"}, 400, "malformed_annotation_payload"),
            ({"annotations_json": json.dumps({"id": "a"})}, 400, "malformed_annotation_payload"),
            ({"annotations_json": json.dumps("x")}, 400, "malformed_annotation_payload"),
            (
                {"annotations_json": json.dumps([{"type": "note", "pageIndex": 0}])},
                400,
                "malformed_annotation_payload",
            ),
            (
                {
                    "annotations_json": json.dumps(
                        [
                            {"id": "dup", "pageIndex": 0},
                            {"id": "dup", "pageIndex": 1},
                        ]
                    )
                },
                400,
                "malformed_annotation_payload",
            ),
        ]
        for body, status, code in cases:
            with self.subTest(body=body):
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    self._post_work_annotations(w_id, body)
                self.assertEqual(cm.exception.code, status)
                err = json.loads(cm.exception.read().decode())
                self.assertEqual(err.get("code"), code)
                self.assertEqual(self._get_work_annotations(w_id)[0]["id"], "keep")

        with self._post_work_annotations(
            other, {"annotations_json": json.dumps([{"id": "owned-x", "pageIndex": 0}])}
        ) as res:
            self.assertEqual(res.status, 200)
        with self.assertRaises(urllib.error.HTTPError) as cm409:
            self._post_work_annotations(
                w_id,
                {"annotations_json": json.dumps([{"id": "owned-x", "pageIndex": 0}])},
            )
        self.assertEqual(cm409.exception.code, 409)
        err409 = json.loads(cm409.exception.read().decode())
        self.assertEqual(err409.get("code"), "annotation_id_conflict")
        self.assertEqual(self._get_work_annotations(w_id)[0]["id"], "keep")
        self.assertEqual(self._get_work_annotations(other)[0]["id"], "owned-x")

        with self.assertRaises(urllib.error.HTTPError) as cm404:
            self._post_work_annotations(
                "W-MISSING",
                {"annotations_json": "[]"},
            )
        self.assertEqual(cm404.exception.code, 404)
        err404 = json.loads(cm404.exception.read().decode())
        self.assertEqual(err404.get("code"), "work_not_found")

    def test_22c2_annotation_adopt_byte_only(self):
        w_id = self._create_work_api("Ann Adopt")
        meta = [{"id": "meta-only", "type": 9, "contents": "m", "pageIndex": 0,
                 "segmentRects": [{"origin": {"x": 1, "y": 1}, "size": {"width": 2, "height": 2}}],
                 "custom": {"prksComment": "m"}}]
        with self._post_work_annotations(w_id, {"annotations_json": json.dumps(meta)}) as res:
            self.assertEqual(res.status, 200)
            body = json.loads(res.read().decode())
        # Annotations-only POST must NOT claim PDF bytes are materialized.
        self.assertNotIn("materialized_pdf_annotation_revision", body)
        self.assertNotIn("stale", body)
        # Adoption requires current materialization; mark explicitly (no PDF handshake).
        self.__class__.test_db.mark_work_pdf_materialized(w_id)
        viewer = [
            {
                "id": "byte-only",
                "type": 9,
                "contents": "b",
                "pageIndex": 0,
                "segmentRects": [{"origin": {"x": 3, "y": 3}, "size": {"width": 2, "height": 2}}],
                "custom": {"prksComment": "b"},
            },
            {
                "id": "link-skip",
                "type": 2,
                "uri": "https://example.test/x",
                "pageIndex": 0,
            },
            {
                "id": "link-type1",
                "type": 1,
                "pageIndex": 0,
                "rect": {"origin": {"x": 8, "y": 8}, "size": {"width": 20, "height": 8}},
            },
            meta[0],
        ]
        req = urllib.request.Request(
            f"{self._base_url}/api/works/{w_id}/annotations/adopt",
            data=json.dumps({"viewer_annotations": viewer}).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 200)
            body = json.loads(res.read().decode())
        self.assertEqual(body.get("adopted"), ["byte-only"])
        ids = {row["id"] for row in self._get_work_annotations(w_id)}
        self.assertEqual(ids, {"meta-only", "byte-only"})
        self.assertNotIn("link-skip", ids)
        self.assertNotIn("link-type1", ids)

    def _post_work_pdf(self, work_id, body):
        req = urllib.request.Request(
            f"{self._base_url}/api/works/{work_id}/pdf",
            data=json.dumps(body).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        return urllib.request.urlopen(req)

    def _ann_row(self, ann_id, contents):
        return {
            "id": ann_id,
            "type": 9,
            "contents": contents,
            "pageIndex": 0,
            "segmentRects": [{"origin": {"x": 1, "y": 1}, "size": {"width": 2, "height": 2}}],
            "custom": {"prksComment": contents},
        }

    def test_22c3_online_legacy_marks_via_pdf_claim(self):
        """POST /annotations then POST /pdf with that generation marks materialization."""
        pdf_bytes = _pdf_with_text_bytes("legacy mark order")
        payload = {
            "title": "Legacy Mat Order",
            "status": "Planned",
            "file_b64": base64.b64encode(pdf_bytes).decode("utf-8"),
            "file_name": "legacy_mat.pdf",
        }
        req = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            w_id = json.loads(res.read().decode())["id"]

        ann = [self._ann_row("legacy-ann-1", "hi")]
        with self._post_work_annotations(
            w_id,
            {
                "annotations_json": json.dumps(ann),
                "save_token": "legacy-handshake",
            },
        ) as ar:
            self.assertEqual(ar.status, 200)
            ann_body = json.loads(ar.read().decode())
        replace_gen = ann_body.get("canonical_annotation_set_revision")
        self.assertIsInstance(replace_gen, int)
        self.assertGreaterEqual(replace_gen, 1)
        # Annotations never claim bytes are materialized.
        self.assertNotIn("materialized_pdf_annotation_revision", ann_body)
        mat_mid = self.__class__.test_db.get_work_pdf_materialization(w_id)
        self.assertTrue(mat_mid["stale"])
        self.assertEqual(mat_mid["canonical_annotation_set_revision"], replace_gen)

        updated = _pdf_with_text_bytes("legacy mark order v2")
        with self._post_work_pdf(
            w_id,
            {
                "file_b64": base64.b64encode(updated).decode("utf-8"),
                "save_token": "legacy-handshake",
                "materialized_annotation_set_revision": replace_gen,
            },
        ) as pr:
            self.assertEqual(pr.status, 200)
            pdf_body = json.loads(pr.read().decode())
        self.assertEqual(pdf_body.get("materialized_pdf_annotation_revision"), replace_gen)
        self.assertEqual(pdf_body.get("canonical_annotation_set_revision"), replace_gen)
        self.assertFalse(pdf_body.get("stale"))
        mat = self.__class__.test_db.get_work_pdf_materialization(w_id)
        self.assertEqual(
            mat["canonical_annotation_set_revision"],
            mat["materialized_pdf_annotation_revision"],
        )
        self.assertFalse(mat["stale"])

        # Annotations-only (no subsequent PDF claim) advances tip and leaves stale.
        with self._post_work_annotations(
            w_id,
            {
                "annotations_json": json.dumps([self._ann_row("legacy-ann-1", "changed")]),
                "save_token": "another-token",
            },
        ) as bad:
            self.assertEqual(bad.status, 200)
            bad_body = json.loads(bad.read().decode())
        self.assertNotIn("materialized_pdf_annotation_revision", bad_body)
        mat_after = self.__class__.test_db.get_work_pdf_materialization(w_id)
        self.assertTrue(mat_after["stale"])
        self.assertGreater(
            mat_after["canonical_annotation_set_revision"],
            mat_after["materialized_pdf_annotation_revision"],
        )

    def test_22c4_annotations_only_never_marks_materialization(self):
        """POST /annotations never marks bytes — even with a leftover PDF save_token."""
        pdf_bytes = _pdf_with_text_bytes("ann only seed")
        payload = {
            "title": "Ann Only No Mark",
            "status": "Planned",
            "file_b64": base64.b64encode(pdf_bytes).decode("utf-8"),
            "file_name": "ann_only.pdf",
        }
        req = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            w_id = json.loads(res.read().decode())["id"]
        before = self.__class__.test_db.get_work_pdf_materialization(w_id)
        # Plant a PDF save_token as if an older PDF-first client had uploaded.
        leftover = _pdf_with_text_bytes("token plant")
        with self._post_work_pdf(
            w_id,
            {
                "file_b64": base64.b64encode(leftover).decode("utf-8"),
                "save_token": "leftover-pdf-token",
            },
        ) as pr:
            self.assertEqual(pr.status, 200)
            pdf_body = json.loads(pr.read().decode())
        self.assertNotIn("materialized_pdf_annotation_revision", pdf_body)

        ann = [self._ann_row("only-ann", "x")]
        with self._post_work_annotations(
            w_id,
            {
                "annotations_json": json.dumps(ann),
                "save_token": "leftover-pdf-token",
            },
        ) as res:
            self.assertEqual(res.status, 200)
            body = json.loads(res.read().decode())
        self.assertIn("canonical_annotation_set_revision", body)
        self.assertNotIn("materialized_pdf_annotation_revision", body)
        self.assertNotIn("stale", body)
        after = self.__class__.test_db.get_work_pdf_materialization(w_id)
        self.assertEqual(
            after["materialized_pdf_annotation_revision"],
            before["materialized_pdf_annotation_revision"],
        )
        self.assertGreater(
            after["canonical_annotation_set_revision"],
            after["materialized_pdf_annotation_revision"],
        )
        self.assertTrue(after["stale"])

    def test_22c5_same_token_replay_does_not_mark_new_generation(self):
        """Reusing a save_token on a later annotations list must not mark that tip."""
        pdf_bytes = _pdf_with_text_bytes("replay token A")
        payload = {
            "title": "Legacy Token Replay",
            "status": "Planned",
            "file_b64": base64.b64encode(pdf_bytes).decode("utf-8"),
            "file_name": "replay.pdf",
        }
        req = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            w_id = json.loads(res.read().decode())["id"]

        token = "replay-token-A"
        with self._post_work_annotations(
            w_id,
            {
                "annotations_json": json.dumps([self._ann_row("a1", "first")]),
                "save_token": token,
            },
        ) as ar:
            gen_a = json.loads(ar.read().decode())["canonical_annotation_set_revision"]
        pdf_a = _pdf_with_text_bytes("replay token A bytes")
        with self._post_work_pdf(
            w_id,
            {
                "file_b64": base64.b64encode(pdf_a).decode("utf-8"),
                "save_token": token,
                "materialized_annotation_set_revision": gen_a,
            },
        ) as pr:
            self.assertEqual(pr.status, 200)

        # Same token, different annotations B — must advance tip without marking.
        with self._post_work_annotations(
            w_id,
            {
                "annotations_json": json.dumps([self._ann_row("a1", "second-B")]),
                "save_token": token,
            },
        ) as ar2:
            body_b = json.loads(ar2.read().decode())
        gen_b = body_b["canonical_annotation_set_revision"]
        self.assertGreater(gen_b, gen_a)
        self.assertNotIn("materialized_pdf_annotation_revision", body_b)
        mat = self.__class__.test_db.get_work_pdf_materialization(w_id)
        self.assertEqual(mat["materialized_pdf_annotation_revision"], gen_a)
        self.assertEqual(mat["canonical_annotation_set_revision"], gen_b)
        self.assertTrue(mat["stale"])
        # Stale claim for A must not clear lag after tip moved to B.
        pdf_stale = _pdf_with_text_bytes("stale claim A")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._post_work_pdf(
                w_id,
                {
                    "file_b64": base64.b64encode(pdf_stale).decode("utf-8"),
                    "save_token": token,
                    "materialized_annotation_set_revision": gen_a,
                },
            )
        self.assertEqual(cm.exception.code, 409)
        err = json.loads(cm.exception.read().decode())
        self.assertEqual(err.get("code"), "ANNOTATION_MATERIALIZATION_STALE")
        mat2 = self.__class__.test_db.get_work_pdf_materialization(w_id)
        self.assertEqual(mat2["materialized_pdf_annotation_revision"], gen_a)
        self.assertEqual(mat2["canonical_annotation_set_revision"], gen_b)

    def test_22c6_intervening_annotations_reject_stale_pdf_claim(self):
        """Cross-tab: PDF claiming gen A fails after annotations advanced to B."""
        pdf_bytes = _pdf_with_text_bytes("race base")
        payload = {
            "title": "Legacy Race Claim",
            "status": "Planned",
            "file_b64": base64.b64encode(pdf_bytes).decode("utf-8"),
            "file_name": "race.pdf",
        }
        req = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            w_id = json.loads(res.read().decode())["id"]

        with self._post_work_annotations(
            w_id,
            {"annotations_json": json.dumps([self._ann_row("r1", "A")])},
        ) as ar:
            gen_a = json.loads(ar.read().decode())["canonical_annotation_set_revision"]

        # Tab B advances canonical before Tab A's PDF claim lands.
        with self._post_work_annotations(
            w_id,
            {"annotations_json": json.dumps([self._ann_row("r1", "B")])},
        ) as ar2:
            gen_b = json.loads(ar2.read().decode())["canonical_annotation_set_revision"]
        self.assertGreater(gen_b, gen_a)

        pdf_a = _pdf_with_text_bytes("bytes for A")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._post_work_pdf(
                w_id,
                {
                    "file_b64": base64.b64encode(pdf_a).decode("utf-8"),
                    "save_token": "tab-a",
                    "materialized_annotation_set_revision": gen_a,
                },
            )
        self.assertEqual(cm.exception.code, 409)
        mat = self.__class__.test_db.get_work_pdf_materialization(w_id)
        self.assertTrue(mat["stale"])
        self.assertEqual(mat["canonical_annotation_set_revision"], gen_b)
        self.assertLess(mat["materialized_pdf_annotation_revision"], gen_b)

        # Concurrent PDF claim for A while another PDF claim for B holds the lock:
        # B wins; A's claim stays rejected. Serialize via lock by running B first
        # under the materialization lock path, then A.
        pdf_b = _pdf_with_text_bytes("bytes for B")
        with self._post_work_pdf(
            w_id,
            {
                "file_b64": base64.b64encode(pdf_b).decode("utf-8"),
                "save_token": "tab-b",
                "materialized_annotation_set_revision": gen_b,
            },
        ) as pr:
            self.assertEqual(pr.status, 200)
            body_b = json.loads(pr.read().decode())
        self.assertEqual(body_b.get("materialized_pdf_annotation_revision"), gen_b)
        mat_ok = self.__class__.test_db.get_work_pdf_materialization(w_id)
        self.assertFalse(mat_ok["stale"])
        self.assertEqual(mat_ok["materialized_pdf_annotation_revision"], gen_b)

        # True race: hold materialization lock while another thread tries a claim.
        mat_lock = server_module._pdf_materialization_lock_for(w_id)
        results = {"status": None, "code": None}

        def _claim_while_held():
            try:
                self._post_work_pdf(
                    w_id,
                    {
                        "file_b64": base64.b64encode(
                            _pdf_with_text_bytes("concurrent loser")
                        ).decode("utf-8"),
                        "save_token": "loser",
                        "materialized_annotation_set_revision": gen_b,
                    },
                )
                results["status"] = 200
            except urllib.error.HTTPError as exc:
                results["status"] = exc.code
                try:
                    results["code"] = json.loads(exc.read().decode()).get("code")
                except Exception:
                    results["code"] = None

        with mat_lock:
            # Advance tip under lock so the in-flight claim sees a new tip.
            with self._post_work_annotations(
                w_id,
                {"annotations_json": json.dumps([self._ann_row("r1", "C")])},
            ) as ar3:
                gen_c = json.loads(ar3.read().decode())["canonical_annotation_set_revision"]
            self.assertGreater(gen_c, gen_b)
            t = threading.Thread(target=_claim_while_held)
            t.start()
            time.sleep(0.15)
            # Still holding lock: peer must be blocked, not marking.
            mid = self.__class__.test_db.get_work_pdf_materialization(w_id)
            self.assertEqual(mid["materialized_pdf_annotation_revision"], gen_b)
            self.assertEqual(mid["canonical_annotation_set_revision"], gen_c)
        t.join(timeout=10)
        self.assertFalse(t.is_alive())
        # After unlock, claim for obsolete gen_b is rejected as stale.
        self.assertEqual(results["status"], 409)
        self.assertEqual(results["code"], "ANNOTATION_MATERIALIZATION_STALE")
        final = self.__class__.test_db.get_work_pdf_materialization(w_id)
        self.assertEqual(final["materialized_pdf_annotation_revision"], gen_b)
        self.assertEqual(final["canonical_annotation_set_revision"], gen_c)
        self.assertTrue(final["stale"])

    def test_22c7_pdf_post_no_managed_pdf_404_before_mark(self):
        """Blank/invalid file_path must 404 before save_token or materialization mark."""
        w_id = self._create_work_api("No Managed PDF")
        row = self.__class__.test_db.execute_query(
            "SELECT file_path FROM works WHERE id=?", (w_id,)
        )
        self.assertTrue(row)
        self.assertFalse((row[0].get("file_path") or "").strip())

        before = self.__class__.test_db.get_work_pdf_materialization(w_id) or {
            "canonical_annotation_set_revision": 0,
            "materialized_pdf_annotation_revision": 0,
        }
        pdf_bytes = _pdf_with_text_bytes("orphan bytes")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._post_work_pdf(
                w_id,
                {
                    "file_b64": base64.b64encode(pdf_bytes).decode("utf-8"),
                    "save_token": "no-pdf-token",
                    "materialized_annotation_set_revision": max(
                        1, int(before.get("canonical_annotation_set_revision") or 0)
                    ),
                },
            )
        self.assertEqual(cm.exception.code, 404)
        confirm = self._save_confirm(w_id, "no-pdf-token")
        self.assertFalse(confirm.get("pdf_saved"))
        after = self.__class__.test_db.get_work_pdf_materialization(w_id) or {}
        self.assertEqual(
            after.get("materialized_pdf_annotation_revision"),
            before.get("materialized_pdf_annotation_revision"),
        )
        self.assertEqual(
            after.get("canonical_annotation_set_revision"),
            before.get("canonical_annotation_set_revision"),
        )

    def test_22c8_pdf_post_missing_work_does_not_grow_lock_dict(self):
        """404 for unknown work id must not create a materialization lock entry."""
        missing = "W-missing-pdf-lock-probe"
        self.assertNotIn(missing, server_module._PDF_MATERIALIZATION_LOCKS)
        pdf_bytes = _pdf_with_text_bytes("lock probe")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._post_work_pdf(
                missing,
                {
                    "file_b64": base64.b64encode(pdf_bytes).decode("utf-8"),
                    "save_token": "lock-probe",
                },
            )
        self.assertEqual(cm.exception.code, 404)
        self.assertNotIn(missing, server_module._PDF_MATERIALIZATION_LOCKS)

    def test_22c9_atomic_pdf_replace_keeps_live_bytes_on_write_failure(self):
        """Failed temp write must leave the existing managed PDF intact."""
        from backend.services import work_pdf_replace

        with tempfile.TemporaryDirectory(prefix="prks-atomic-pdf-") as tmp:
            filename = "doc.pdf"
            live = os.path.join(tmp, filename)
            with open(live, "wb") as f:
                f.write(b"%PDF-1.4 live-original")
            with open(live, "rb") as f:
                before = f.read()

            real_fdopen = os.fdopen

            class _BoomWriteProxy:
                """fdopen stand-in: write raises OSError; close on context exit.

                Cannot assign to BufferedWriter.write on some Python builds
                (read-only); wrap instead so the failure path is actually hit.
                """

                def __init__(self, wrapped):
                    self._wrapped = wrapped

                def write(self, _data):
                    raise OSError("simulated write failure")

                def flush(self):
                    return self._wrapped.flush()

                def fileno(self):
                    return self._wrapped.fileno()

                def close(self):
                    return self._wrapped.close()

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    self._wrapped.close()
                    return False

            def boom_fdopen(fd, mode="r", *args, **kwargs):
                fp = real_fdopen(fd, mode, *args, **kwargs)
                if "w" in mode:
                    return _BoomWriteProxy(fp)
                return fp

            with patch.object(work_pdf_replace.os, "fdopen", boom_fdopen):
                with self.assertRaises(OSError):
                    work_pdf_replace.atomic_replace_managed_pdf_bytes(
                        tmp, filename, b"%PDF-1.4 new"
                    )
            with open(live, "rb") as f:
                self.assertEqual(f.read(), before)
            leftovers = [
                name
                for name in os.listdir(tmp)
                if name.startswith(".prks-write-") and name.endswith(".tmp")
            ]
            self.assertEqual(leftovers, [])
            with patch.object(
                work_pdf_replace,
                "fsync_managed_pdf_parent",
                wraps=work_pdf_replace.fsync_managed_pdf_parent,
            ) as dir_sync:
                work_pdf_replace.atomic_replace_managed_pdf_bytes(
                    tmp, filename, b"%PDF-1.4 replaced"
                )
            dir_sync.assert_called()
            with open(live, "rb") as f:
                self.assertEqual(f.read(), b"%PDF-1.4 replaced")
            leftovers = [
                name
                for name in os.listdir(tmp)
                if name.startswith(".prks-write-") and name.endswith(".tmp")
            ]
            self.assertEqual(leftovers, [])

    def test_22c10_atomic_pdf_temps_are_unique_and_path_lock_is_shared(self):
        """Shared managed PDF paths share one path lock; temps are unique."""
        from backend.services import work_pdf_replace

        with tempfile.TemporaryDirectory(prefix="prks-atomic-pdf-uniq-") as tmp:
            filename = "shared.pdf"
            live = os.path.join(tmp, filename)
            with open(live, "wb") as f:
                f.write(b"%PDF-1.4 a")
            lock_a = work_pdf_replace.managed_pdf_path_lock(tmp, filename)
            lock_b = work_pdf_replace.managed_pdf_path_lock(tmp, filename)
            self.assertIsNotNone(lock_a)
            self.assertIs(lock_a, lock_b)
            seen = []
            real_mkstemp = work_pdf_replace.tempfile.mkstemp

            def tracking_mkstemp(*args, **kwargs):
                fd, path = real_mkstemp(*args, **kwargs)
                seen.append(path)
                return fd, path

            with patch.object(work_pdf_replace.tempfile, "mkstemp", tracking_mkstemp):
                work_pdf_replace.atomic_replace_managed_pdf_bytes(
                    tmp, filename, b"%PDF-1.4 b"
                )
                work_pdf_replace.atomic_replace_managed_pdf_bytes(
                    tmp, filename, b"%PDF-1.4 c"
                )
            self.assertEqual(len(seen), 2)
            self.assertNotEqual(seen[0], seen[1])
            self.assertTrue(all(os.path.dirname(p) == tmp for p in seen))
            with open(live, "rb") as f:
                self.assertEqual(f.read(), b"%PDF-1.4 c")

    def test_22c11_shared_managed_pdf_cow_keeps_sibling_materialization_honest(self):
        """Two Works on one path: both materialize → exclusive files; not both non-stale on one blob."""
        from backend.db_manager import managed_pdf_filename
        import uuid as _uuid

        suffix = _uuid.uuid4().hex[:10]
        shared_name = f"shared-cow-{suffix}.pdf"
        seed = _pdf_with_text_bytes("shared seed bytes")
        shared_abs = os.path.join(server_module.pdfs_dir, shared_name)
        with open(shared_abs, "wb") as f:
            f.write(seed)
        a_id = self.__class__.test_db.add_work(
            title="CowShareA", file_path=f"/api/pdfs/{shared_name}"
        )
        b_id = self.__class__.test_db.add_work(
            title="CowShareB", file_path=f"/api/pdfs/{shared_name}"
        )
        ann_a = f"cow-a-{suffix}"
        ann_b = f"cow-b-{suffix}"

        with self._post_work_annotations(
            a_id, {"annotations_json": json.dumps([self._ann_row(ann_a, "A")])}
        ) as ar:
            gen_a = json.loads(ar.read().decode())["canonical_annotation_set_revision"]
        pdf_a = _pdf_with_text_bytes("materialized-A-only")
        with self._post_work_pdf(
            a_id,
            {
                "file_b64": base64.b64encode(pdf_a).decode("utf-8"),
                "materialized_annotation_set_revision": gen_a,
            },
        ) as pr:
            self.assertEqual(pr.status, 200)
            body_a = json.loads(pr.read().decode())
        self.assertIn("file_path", body_a)
        self.assertNotEqual(body_a["file_path"], f"/api/pdfs/{shared_name}")

        with self._post_work_annotations(
            b_id, {"annotations_json": json.dumps([self._ann_row(ann_b, "B")])}
        ) as br:
            gen_b = json.loads(br.read().decode())["canonical_annotation_set_revision"]
        pdf_b = _pdf_with_text_bytes("materialized-B-only")
        with self._post_work_pdf(
            b_id,
            {
                "file_b64": base64.b64encode(pdf_b).decode("utf-8"),
                "materialized_annotation_set_revision": gen_b,
            },
        ) as pr:
            self.assertEqual(pr.status, 200)
            body_b = json.loads(pr.read().decode())

        mat_a = self.__class__.test_db.get_work_pdf_materialization(a_id)
        mat_b = self.__class__.test_db.get_work_pdf_materialization(b_id)
        self.assertFalse(mat_a["stale"])
        self.assertFalse(mat_b["stale"])
        row_a = self.__class__.test_db.execute_query(
            "SELECT file_path FROM works WHERE id=?", (a_id,)
        )[0]
        row_b = self.__class__.test_db.execute_query(
            "SELECT file_path FROM works WHERE id=?", (b_id,)
        )[0]
        self.assertNotEqual(row_a["file_path"], row_b["file_path"])
        name_a = managed_pdf_filename(row_a["file_path"])
        name_b = managed_pdf_filename(row_b["file_path"])
        self.assertIsNotNone(name_a)
        self.assertIsNotNone(name_b)
        path_a = os.path.join(server_module.pdfs_dir, name_a)
        path_b = os.path.join(server_module.pdfs_dir, name_b)
        with open(path_a, "rb") as f:
            bytes_a = f.read()
        with open(path_b, "rb") as f:
            bytes_b = f.read()
        self.assertNotEqual(bytes_a, bytes_b)
        # Extracted page text must match each Work's materialization payload.
        import pymupdf as fitz

        def _page_text(pdf_bytes: bytes) -> str:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            try:
                return doc[0].get_text() or ""
            finally:
                doc.close()

        self.assertIn("materialized-A-only", _page_text(bytes_a))
        self.assertIn("materialized-B-only", _page_text(bytes_b))
        # Impossible for both to be non-stale while sharing one file with only B's bytes.
        self.assertNotEqual(path_a, path_b)

    def test_22c13_cow_stale_final_mark_returns_retargeted_file_path(self):
        """COW retarget that then hits STALE still returns exclusive file_path.

        Race: accept claim → write exclusive + UPDATE works.file_path → final
        mark sees advanced canonical → 409. Client must learn the new path.
        """
        from unittest import mock
        from backend.pdf_materialization import STALE_CODE
        from backend.db_manager import managed_pdf_filename
        import uuid as _uuid

        suffix = _uuid.uuid4().hex[:10]
        shared_name = f"shared-stale-cow-{suffix}.pdf"
        seed = _pdf_with_text_bytes("shared seed for stale cow")
        shared_abs = os.path.join(server_module.pdfs_dir, shared_name)
        with open(shared_abs, "wb") as f:
            f.write(seed)
        a_id = self.__class__.test_db.add_work(
            title="CowStaleA", file_path=f"/api/pdfs/{shared_name}"
        )
        self.__class__.test_db.add_work(
            title="CowStaleB", file_path=f"/api/pdfs/{shared_name}"
        )
        ann_a = f"cow-stale-a-{suffix}"
        with self._post_work_annotations(
            a_id, {"annotations_json": json.dumps([self._ann_row(ann_a, "A")])}
        ) as ar:
            gen_a = json.loads(ar.read().decode())["canonical_annotation_set_revision"]

        def _stale_after_cow(work_id, claimed_revision):
            raise ValueError(STALE_CODE)

        pdf_a = _pdf_with_text_bytes("stale-cow-exclusive-bytes")
        with mock.patch.object(
            self.__class__.test_db,
            "mark_work_pdf_materialized_if_claim_current",
            side_effect=_stale_after_cow,
        ):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._post_work_pdf(
                    a_id,
                    {
                        "file_b64": base64.b64encode(pdf_a).decode("utf-8"),
                        "materialized_annotation_set_revision": gen_a,
                    },
                )
        self.assertEqual(cm.exception.code, 409)
        err = json.loads(cm.exception.read().decode())
        self.assertEqual(err.get("code"), "ANNOTATION_MATERIALIZATION_STALE")
        self.assertIn("file_path", err)
        self.assertNotEqual(err["file_path"], f"/api/pdfs/{shared_name}")
        row_a = self.__class__.test_db.execute_query(
            "SELECT file_path FROM works WHERE id=?", (a_id,)
        )[0]
        self.assertEqual(row_a["file_path"], err["file_path"])
        exclusive = managed_pdf_filename(err["file_path"])
        self.assertIsNotNone(exclusive)
        exclusive_abs = os.path.join(server_module.pdfs_dir, exclusive)
        self.assertTrue(os.path.isfile(exclusive_abs))
        # Shared seed must still exist for the sibling Work.
        self.assertTrue(os.path.isfile(shared_abs))

    def test_22c14_cow_retarget_db_failure_removes_orphan_exclusive(self):
        """Exclusive COW write + failed file_path UPDATE must not leave an orphan.

        Regression for Greptile P2: bytes land under a new managed name before
        works.file_path is retargeted. If that UPDATE fails, delete the exclusive
        file and leave the Work on the shared path.
        """
        from unittest import mock
        from backend.db_manager import managed_pdf_filename
        from backend.services import work_pdf_replace
        import uuid as _uuid

        suffix = _uuid.uuid4().hex[:10]
        shared_name = f"shared-orphan-cow-{suffix}.pdf"
        seed = _pdf_with_text_bytes("shared seed for orphan cow")
        shared_abs = os.path.join(server_module.pdfs_dir, shared_name)
        with open(shared_abs, "wb") as f:
            f.write(seed)
        a_id = self.__class__.test_db.add_work(
            title="CowOrphanA", file_path=f"/api/pdfs/{shared_name}"
        )
        self.__class__.test_db.add_work(
            title="CowOrphanB", file_path=f"/api/pdfs/{shared_name}"
        )
        ann_a = f"cow-orphan-a-{suffix}"
        with self._post_work_annotations(
            a_id, {"annotations_json": json.dumps([self._ann_row(ann_a, "A")])}
        ) as ar:
            gen_a = json.loads(ar.read().decode())["canonical_annotation_set_revision"]

        before = set(
            name
            for name in os.listdir(server_module.pdfs_dir)
            if name.endswith(".pdf")
        )

        pdf_a = _pdf_with_text_bytes("orphan-cow-exclusive-bytes")
        with mock.patch.object(
            work_pdf_replace,
            "retarget_work_managed_file_path",
            side_effect=RuntimeError("forced cow retarget failure"),
        ):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._post_work_pdf(
                    a_id,
                    {
                        "file_b64": base64.b64encode(pdf_a).decode("utf-8"),
                        "materialized_annotation_set_revision": gen_a,
                    },
                )
        self.assertEqual(cm.exception.code, 500)
        row_a = self.__class__.test_db.execute_query(
            "SELECT file_path FROM works WHERE id=?", (a_id,)
        )[0]
        self.assertEqual(row_a["file_path"], f"/api/pdfs/{shared_name}")
        self.assertTrue(os.path.isfile(shared_abs))
        after = set(
            name
            for name in os.listdir(server_module.pdfs_dir)
            if name.endswith(".pdf")
        )
        # No new managed PDF may remain unreferenced after the failed retarget.
        self.assertEqual(after - before, set())
        self.assertEqual(managed_pdf_filename(row_a["file_path"]), shared_name)

    def test_22c15_cow_retarget_zero_row_after_delete_removes_orphan(self):
        """Concurrent Work delete after exclusive write must not orphan the PDF.

        Delete does not take the PDF materialization lock. If the Work disappears
        between exclusive write and retarget UPDATE, the UPDATE affects 0 rows —
        require rowcount==1 before clearing the orphan flag; unlink and 404.
        """
        from unittest import mock
        from backend.services import work_pdf_replace
        import uuid as _uuid

        suffix = _uuid.uuid4().hex[:10]
        shared_name = f"shared-zerorow-cow-{suffix}.pdf"
        seed = _pdf_with_text_bytes("shared seed for zerorow cow")
        shared_abs = os.path.join(server_module.pdfs_dir, shared_name)
        with open(shared_abs, "wb") as f:
            f.write(seed)
        a_id = self.__class__.test_db.add_work(
            title="CowZeroRowA", file_path=f"/api/pdfs/{shared_name}"
        )
        self.__class__.test_db.add_work(
            title="CowZeroRowB", file_path=f"/api/pdfs/{shared_name}"
        )
        ann_a = f"cow-zerorow-a-{suffix}"
        with self._post_work_annotations(
            a_id, {"annotations_json": json.dumps([self._ann_row(ann_a, "A")])}
        ) as ar:
            gen_a = json.loads(ar.read().decode())["canonical_annotation_set_revision"]

        before = set(
            name
            for name in os.listdir(server_module.pdfs_dir)
            if name.endswith(".pdf")
        )
        real_atomic = work_pdf_replace.atomic_replace_managed_pdf_bytes

        def _write_then_delete_work(pdfs_dir, filename, body):
            out = real_atomic(pdfs_dir, filename, body)
            # Simulate concurrent delete after exclusive bytes land.
            self.__class__.test_db.delete_work_record(a_id)
            return out

        pdf_a = _pdf_with_text_bytes("zerorow-cow-exclusive-bytes")
        with mock.patch.object(
            work_pdf_replace,
            "atomic_replace_managed_pdf_bytes",
            side_effect=_write_then_delete_work,
        ):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._post_work_pdf(
                    a_id,
                    {
                        "file_b64": base64.b64encode(pdf_a).decode("utf-8"),
                        "materialized_annotation_set_revision": gen_a,
                    },
                )
        self.assertEqual(cm.exception.code, 404)
        gone = self.__class__.test_db.execute_query(
            "SELECT 1 AS ok FROM works WHERE id=?", (a_id,)
        )
        self.assertFalse(gone)
        self.assertTrue(os.path.isfile(shared_abs))
        after = set(
            name
            for name in os.listdir(server_module.pdfs_dir)
            if name.endswith(".pdf")
        )
        self.assertEqual(after - before, set())

    def test_22c12_legacy_annotations_reject_stale_set_revision(self):
        """Stale full-list replace cannot overwrite newer annotations or delete B."""
        import uuid as _uuid

        suffix = _uuid.uuid4().hex[:10]
        ann_a = f"stale-a-{suffix}"
        ann_b = f"stale-b-{suffix}"
        w_id = self._create_work_api("Ann Stale Set")
        with self._post_work_annotations(
            w_id, {"annotations_json": json.dumps([self._ann_row(ann_a, "first")])}
        ) as res:
            tip = json.loads(res.read().decode())["canonical_annotation_set_revision"]
        with self._post_work_annotations(
            w_id,
            {
                "annotations_json": json.dumps(
                    [
                        self._ann_row(ann_a, "second"),
                        self._ann_row(ann_b, "new-B"),
                    ]
                )
            },
        ) as res2:
            tip2 = json.loads(res2.read().decode())["canonical_annotation_set_revision"]
        self.assertGreater(tip2, tip)

        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._post_work_annotations(
                w_id,
                {
                    "annotations_json": json.dumps([self._ann_row(ann_a, "stale-A")]),
                    "canonical_annotation_set_revision": tip,
                },
            )
        self.assertEqual(cm.exception.code, 409)
        err = json.loads(cm.exception.read().decode())
        self.assertEqual(err.get("code"), "ANNOTATION_SET_STALE")
        ids = {row["id"]: row["contents"] for row in self._get_work_annotations(w_id)}
        self.assertEqual(ids.get(ann_a), "second")
        self.assertEqual(ids.get(ann_b), "new-B")

        # Repeated save against the current tip still succeeds (identical or edit).
        with self._post_work_annotations(
            w_id,
            {
                "annotations_json": json.dumps(
                    [
                        self._ann_row(ann_a, "second"),
                        self._ann_row(ann_b, "new-B"),
                    ]
                ),
                "canonical_annotation_set_revision": tip2,
            },
        ) as ok:
            self.assertEqual(ok.status, 200)
            body = json.loads(ok.read().decode())
        self.assertEqual(body["canonical_annotation_set_revision"], tip2)

        # Missing base is refused on the wire.
        req = urllib.request.Request(
            f"{self._base_url}/api/works/{w_id}/annotations",
            data=json.dumps(
                {"annotations_json": json.dumps([self._ann_row(ann_a, "x")])}
            ).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as cm_miss:
            urllib.request.urlopen(req)
        self.assertEqual(cm_miss.exception.code, 400)

    def test_22d_annotation_save_token_only_after_success(self):
        w_id = self._create_work_api("Ann Token")
        good = [{"id": "tok", "contents": "ok", "pageIndex": 0}]
        with self._post_work_annotations(
            w_id, {"annotations_json": json.dumps(good), "save_token": "token-ok"}
        ) as res:
            self.assertEqual(res.status, 200)
        confirm_ok = self._save_confirm(w_id, "token-ok")
        self.assertTrue(confirm_ok["annotations_saved"])

        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._post_work_annotations(
                w_id,
                {"annotations_json": "{", "save_token": "token-bad"},
            )
        self.assertEqual(cm.exception.code, 400)
        confirm_bad = self._save_confirm(w_id, "token-bad")
        self.assertFalse(confirm_bad["annotations_saved"])
        confirm_ok2 = self._save_confirm(w_id, "token-ok")
        self.assertTrue(confirm_ok2["annotations_saved"])
        self.assertEqual(self._get_work_annotations(w_id)[0]["id"], "tok")

    def test_22b_role_credit_name_post_and_patch(self):
        req_p = urllib.request.Request(
            f"{self._base_url}/api/persons",
            data=json.dumps({"first_name": "Mark", "last_name": "Johnson"}).encode(),
            method="POST",
        )
        req_p.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_p) as rp:
            p_id = json.loads(rp.read().decode())["id"]

        req_w = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps({"title": "Credit API Work", "status": "Planned"}).encode(),
            method="POST",
        )
        req_w.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_w) as rw:
            w_id = json.loads(rw.read().decode())["id"]

        req_role = urllib.request.Request(
            f"{self._base_url}/api/roles",
            data=json.dumps(
                {
                    "person_id": p_id,
                    "work_id": w_id,
                    "role_type": "Author",
                    "credit_name": "Mark S. Johnson",
                }
            ).encode(),
            method="POST",
        )
        req_role.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_role) as rr:
            self.assertEqual(rr.status, 200)

        with urllib.request.urlopen(urllib.request.Request(f"{self._base_url}/api/bibtex/{w_id}")) as br:
            bib = br.read().decode("utf-8", errors="replace")
        self.assertIn("author = {Mark S. Johnson}", bib)

        req_patch = urllib.request.Request(
            f"{self._base_url}/api/works/{w_id}/roles",
            data=json.dumps(
                {
                    "person_id": p_id,
                    "role_type": "Author",
                    "order_index": 0,
                    "credit_name": "Mark Johnson",
                }
            ).encode(),
            method="PATCH",
        )
        req_patch.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_patch) as rpatch:
            self.assertEqual(rpatch.status, 200)
            body = json.loads(rpatch.read().decode())
        self.assertEqual(body.get("status"), "updated")

        with urllib.request.urlopen(urllib.request.Request(f"{self._base_url}/api/bibtex/{w_id}")) as br2:
            bib2 = br2.read().decode("utf-8", errors="replace")
        self.assertIn("author = {Mark Johnson}", bib2)

        req_clear = urllib.request.Request(
            f"{self._base_url}/api/works/{w_id}/roles",
            data=json.dumps(
                {
                    "person_id": p_id,
                    "role_type": "Author",
                    "order_index": 0,
                    "credit_name": "",
                }
            ).encode(),
            method="PATCH",
        )
        req_clear.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_clear):
            pass
        with urllib.request.urlopen(urllib.request.Request(f"{self._base_url}/api/bibtex/{w_id}")) as br3:
            bib3 = br3.read().decode("utf-8", errors="replace")
        self.assertIn("author = {Johnson, Mark}", bib3)

        with urllib.request.urlopen(urllib.request.Request(f"{self._base_url}/api/persons/{p_id}")) as pr:
            person = json.loads(pr.read().decode())
        self.assertIn("Mark S. Johnson", person.get("aliases") or "")

    def test_23_static_path_traversal_blocked(self):
        req = urllib.request.Request(f"{self._base_url}/../../backend/server.py")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 404)

    def test_24_invalid_pdf_base64_returns_400(self):
        req_w = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps({"title": "bad b64", "status": "Planned"}).encode(),
            method="POST",
        )
        req_w.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_w) as rw:
            w_id = json.loads(rw.read().decode())["id"]

        req_bad = urllib.request.Request(
            f"{self._base_url}/api/works/{w_id}/pdf",
            data=json.dumps({"file_b64": "!!not-base64!!"}).encode(),
            method="POST",
        )
        req_bad.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req_bad)
        self.assertEqual(cm.exception.code, 400)
        body = json.loads(cm.exception.read().decode())
        self.assertIn("error", body)

    def test_25_folder_works_post_patch_and_get_folder_fields(self):
        req_f1 = urllib.request.Request(
            f"{self._base_url}/api/folders",
            data=json.dumps({"title": "API Folder Alpha", "description": ""}).encode(),
            method="POST",
        )
        req_f1.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_f1) as rf1:
            f1 = json.loads(rf1.read().decode())["id"]

        req_f2 = urllib.request.Request(
            f"{self._base_url}/api/folders",
            data=json.dumps({"title": "API Folder Beta", "description": ""}).encode(),
            method="POST",
        )
        req_f2.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_f2) as rf2:
            f2 = json.loads(rf2.read().decode())["id"]

        req_w = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps({"title": "Folderable Work", "status": "Planned"}).encode(),
            method="POST",
        )
        req_w.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_w) as rw:
            w_id = json.loads(rw.read().decode())["id"]

        req_clear_unc = urllib.request.Request(
            f"{self._base_url}/api/works/{urllib.parse.quote(w_id)}",
            data=json.dumps({"folder_id": None}).encode(),
            method="PATCH",
        )
        req_clear_unc.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_clear_unc) as rcu:
            self.assertEqual(rcu.status, 200)

        req_add = urllib.request.Request(
            f"{self._base_url}/api/folders/{urllib.parse.quote(f1)}/works",
            data=json.dumps({"work_id": w_id}).encode(),
            method="POST",
        )
        req_add.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_add) as ra:
            self.assertEqual(ra.status, 200)

        req_add2 = urllib.request.Request(
            f"{self._base_url}/api/folders/{urllib.parse.quote(f2)}/works",
            data=json.dumps({"work_id": w_id}).encode(),
            method="POST",
        )
        req_add2.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req_add2)
        self.assertEqual(cm.exception.code, 409)

        req_list = urllib.request.Request(f"{self._base_url}/api/works")
        with urllib.request.urlopen(req_list) as rl:
            works = json.loads(rl.read().decode())
        row = next(x for x in works if x.get("id") == w_id)
        self.assertEqual(row.get("folder_id"), f1)

        req_get = urllib.request.Request(f"{self._base_url}/api/works/{urllib.parse.quote(w_id)}")
        with urllib.request.urlopen(req_get) as rg:
            detail = json.loads(rg.read().decode())
        self.assertEqual(detail.get("folder_id"), f1)
        self.assertEqual(detail.get("folder_title"), "API Folder Alpha")

        req_clear = urllib.request.Request(
            f"{self._base_url}/api/works/{urllib.parse.quote(w_id)}",
            data=json.dumps({"folder_id": None}).encode(),
            method="PATCH",
        )
        req_clear.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_clear) as rc:
            self.assertEqual(rc.status, 200)

        req_get2 = urllib.request.Request(f"{self._base_url}/api/works/{urllib.parse.quote(w_id)}")
        with urllib.request.urlopen(req_get2) as rg2:
            detail2 = json.loads(rg2.read().decode())
        self.assertIsNone(detail2.get("folder_id"))
        self.assertIsNone(detail2.get("folder_title"))

        req_move = urllib.request.Request(
            f"{self._base_url}/api/works/{urllib.parse.quote(w_id)}",
            data=json.dumps({"folder_id": f2}).encode(),
            method="PATCH",
        )
        req_move.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_move) as rm:
            self.assertEqual(rm.status, 200)

        req_get3 = urllib.request.Request(f"{self._base_url}/api/works/{urllib.parse.quote(w_id)}")
        with urllib.request.urlopen(req_get3) as rg3:
            detail3 = json.loads(rg3.read().decode())
        self.assertEqual(detail3.get("folder_id"), f2)
        self.assertEqual(detail3.get("folder_title"), "API Folder Beta")

    def test_search_publisher_query_param(self):
        payload = {
            "title": "PubSearchApiWork",
            "publisher": "MegaPublisher House",
        }
        req = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            w_id = json.loads(res.read().decode())["id"]

        q = urllib.parse.quote("MegaPublisher")
        sreq = urllib.request.Request(f"{self._base_url}/api/search?publisher={q}")
        with urllib.request.urlopen(sreq) as sr:
            data = json.loads(sr.read().decode())
        self.assertTrue(any(x.get("id") == w_id for x in data))

    def test_publishers_api_list_create_alias_delete(self):
        req_list = urllib.request.Request(f"{self._base_url}/api/publishers?used=1")
        with urllib.request.urlopen(req_list) as rl:
            before = json.loads(rl.read().decode())
        self.assertIsInstance(before, list)

        req_p = urllib.request.Request(
            f"{self._base_url}/api/publishers",
            data=json.dumps({"name": "ApiCanonPublisher"}).encode(),
            method="POST",
        )
        req_p.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_p) as rp:
            body = json.loads(rp.read().decode())
        self.assertIn("id", body)
        pid = body["id"]

        req_a = urllib.request.Request(
            f"{self._base_url}/api/publishers/{urllib.parse.quote(pid)}/aliases",
            data=json.dumps({"alias": "ACP Alias"}).encode(),
            method="POST",
        )
        req_a.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_a) as ra:
            self.assertEqual(ra.status, 200)

        req_list2 = urllib.request.Request(f"{self._base_url}/api/publishers?used=1")
        with urllib.request.urlopen(req_list2) as rl2:
            after = json.loads(rl2.read().decode())
        row = next(x for x in after if x.get("id") == pid)
        self.assertIn("ACP Alias", row.get("aliases", []))

        alias_enc = urllib.parse.quote("ACP Alias")
        req_da = urllib.request.Request(
            f"{self._base_url}/api/publishers/{urllib.parse.quote(pid)}/aliases?alias={alias_enc}",
            method="DELETE",
        )
        with urllib.request.urlopen(req_da) as rda:
            self.assertEqual(rda.status, 200)

        req_dp = urllib.request.Request(
            f"{self._base_url}/api/publishers/{urllib.parse.quote(pid)}",
            method="DELETE",
        )
        with urllib.request.urlopen(req_dp) as rdp:
            self.assertEqual(rdp.status, 200)

    def test_processing_files_api_scan_patch_and_import(self):
        processing_root = server_module.processing_dir
        nested = os.path.join(processing_root, "alpha", "beta")
        os.makedirs(nested, exist_ok=True)
        source_pdf = os.path.join(nested, "api_inbox.pdf")
        with open(source_pdf, "wb") as f:
            f.write(_pdf_with_text_bytes("processingimportsearchterm"))
        with open(os.path.join(nested, "ignore.txt"), "w", encoding="utf-8") as f:
            f.write("not a pdf")
        req_person = urllib.request.Request(
            f"{self._base_url}/api/persons",
            data=json.dumps({"first_name": "Api", "last_name": "Author"}).encode(),
            method="POST",
        )
        req_person.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_person) as rperson:
            person_id = json.loads(rperson.read().decode())["id"]

        req_folder = urllib.request.Request(
            f"{self._base_url}/api/folders",
            data=json.dumps({"title": "API Processing Folder", "description": ""}).encode(),
            method="POST",
        )
        req_folder.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_folder) as rf:
            folder_id = json.loads(rf.read().decode())["id"]

        req_scan = urllib.request.Request(f"{self._base_url}/api/processing-files?rescan=1")
        with urllib.request.urlopen(req_scan) as rs:
            self.assertEqual(rs.status, 200)
            rows = json.loads(rs.read().decode())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.get("rel_path"), "alpha/beta/api_inbox.pdf")

        req_patch = urllib.request.Request(
            f"{self._base_url}/api/processing-files/{urllib.parse.quote(row['id'])}",
            data=json.dumps(
                {
                    "title": "API Imported Inbox File",
                    "status_draft": "In Progress",
                    "target_folder_id": folder_id,
                    "roles": [{"person_id": person_id, "role_type": "Author"}],
                }
            ).encode(),
            method="PATCH",
        )
        req_patch.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_patch) as rp:
            self.assertEqual(rp.status, 200)
            patched = json.loads(rp.read().decode())
        self.assertEqual(patched.get("title"), "API Imported Inbox File")
        self.assertEqual(patched.get("status_draft"), "In Progress")

        req_import = urllib.request.Request(
            f"{self._base_url}/api/processing-files/{urllib.parse.quote(row['id'])}/import",
            data=json.dumps({}).encode(),
            method="POST",
        )
        req_import.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_import) as ri:
            self.assertEqual(ri.status, 200)
            imported = json.loads(ri.read().decode())
        self.assertIn("work_id", imported)
        self.assertFalse(os.path.exists(source_pdf))

        req_work = urllib.request.Request(
            f"{self._base_url}/api/works/{urllib.parse.quote(imported['work_id'])}"
        )
        with urllib.request.urlopen(req_work) as rw:
            work = json.loads(rw.read().decode())
        self.assertEqual(work.get("title"), "API Imported Inbox File")
        self.assertEqual(work.get("status"), "In Progress")
        self.assertEqual(work.get("folder_id"), folder_id)
        self.assertTrue(
            any(r.get("id") == person_id and r.get("role_type") == "Author" for r in work.get("roles", []))
        )
        req_search = urllib.request.Request(
            f"{self._base_url}/api/search?any=1&q=processingimportsearchterm"
        )
        with urllib.request.urlopen(req_search) as rsearch:
            search_rows = json.loads(rsearch.read().decode())
        self.assertTrue(any(r.get("id") == imported["work_id"] for r in search_rows))

        req_scan_again = urllib.request.Request(f"{self._base_url}/api/processing-files?rescan=1")
        with urllib.request.urlopen(req_scan_again) as rsa:
            after = json.loads(rsa.read().decode())
        self.assertEqual(after, [])

    def test_processing_files_pdf_preview_endpoint(self):
        processing_root = server_module.processing_dir
        source_pdf = os.path.join(processing_root, "preview_me.pdf")
        with open(source_pdf, "wb") as f:
            f.write(b"%PDF-1.4\n%PREVIEW\n%%EOF\n")
        req_scan = urllib.request.Request(f"{self._base_url}/api/processing-files?rescan=1")
        with urllib.request.urlopen(req_scan) as rs:
            rows = json.loads(rs.read().decode())
        self.assertEqual(len(rows), 1)
        file_id = rows[0]["id"]
        req_preview = urllib.request.Request(
            f"{self._base_url}/api/processing-files/{urllib.parse.quote(file_id)}/pdf"
        )
        with urllib.request.urlopen(req_preview) as rp:
            self.assertEqual(rp.status, 200)
            ctype = (rp.headers.get("Content-Type") or "").lower()
            self.assertIn("application/pdf", ctype)
            head = rp.read(16)
        self.assertIn(b"%PDF-1.4", head)

    def test_server_post_work_without_folder_id_uses_uncategorized(self):
        req = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps({"title": "Default Folder Work", "status": "Planned"}).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            w_id = json.loads(res.read().decode())["id"]
        req_g = urllib.request.Request(f"{self._base_url}/api/works/{urllib.parse.quote(w_id)}")
        with urllib.request.urlopen(req_g) as rg:
            work = json.loads(rg.read().decode())
        self.assertEqual(work.get("folder_title"), "Uncategorized")
        self.assertTrue(work.get("folder_id"))

    @patch("backend.server.fetch_and_prepare")
    def test_server_person_profile_image_serves_cache_when_remote_fails(self, mock_fetch):
        encoded = decode_and_transcode(_tiny_test_portrait_png_bytes())
        if encoded is None:
            self.skipTest("Pillow/WebP encode not available")
        out, subtype = encoded
        mock_fetch.return_value = PortraitImage(body=out, subtype=subtype)
        payload = {
            "first_name": "Cache",
            "last_name": "Portrait",
            "image_url": "https://example.invalid/p.jpg",
        }
        req_p = urllib.request.Request(
            f"{self._base_url}/api/persons",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req_p.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_p) as rp:
            p_id = json.loads(rp.read().decode())["id"]

        img_url = f"{self._base_url}/api/persons/{urllib.parse.quote(p_id)}/profile-image"
        req_img = urllib.request.Request(img_url)
        with urllib.request.urlopen(req_img) as ri:
            self.assertEqual(ri.status, 200)
            body1 = ri.read()
            ctype = ri.headers.get("Content-Type", "")
        self.assertIn("image/webp", ctype)
        self.assertEqual(body1[:4], b"RIFF")
        self.assertEqual(body1[8:12], b"WEBP")

        cache_path = prks_person_image_cache_path(
            p_id, payload["image_url"], server_module._bound_storage.people_dir
        )
        self.assertTrue(os.path.isfile(cache_path))
        self.assertTrue(cache_path.endswith(f"_v{PRKS_PERSON_IMAGE_CACHE_REV}.webp"))

        mock_fetch.return_value = None

        with urllib.request.urlopen(req_img) as ri2:
            self.assertEqual(ri2.status, 200)
            body2 = ri2.read()
        self.assertEqual(body1, body2)

    @patch("backend.server.fetch_and_prepare")
    def test_server_person_profile_image_cache_purged_on_image_url_change(
        self, mock_fetch
    ):
        encoded = decode_and_transcode(_tiny_test_portrait_png_bytes())
        if encoded is None:
            self.skipTest("Pillow/WebP encode not available")
        out, subtype = encoded
        mock_fetch.return_value = PortraitImage(body=out, subtype=subtype)
        url_a = "https://example.invalid/a.jpg"
        url_b = "https://example.invalid/b.jpg"
        payload = {
            "first_name": "Purge",
            "last_name": "Portrait",
            "image_url": url_a,
        }
        req_p = urllib.request.Request(
            f"{self._base_url}/api/persons",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req_p.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_p) as rp:
            p_id = json.loads(rp.read().decode())["id"]

        cache_a = prks_person_image_cache_path(
            p_id, url_a, server_module._bound_storage.people_dir
        )
        img_url = f"{self._base_url}/api/persons/{urllib.parse.quote(p_id)}/profile-image"
        with urllib.request.urlopen(urllib.request.Request(img_url)) as ri:
            ri.read()
        self.assertTrue(os.path.isfile(cache_a))

        req_patch = urllib.request.Request(
            f"{self._base_url}/api/persons/{urllib.parse.quote(p_id)}",
            data=json.dumps({"image_url": url_b}).encode(),
            method="PATCH",
        )
        req_patch.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_patch) as rpatch:
            self.assertEqual(rpatch.status, 200)

        self.assertFalse(os.path.isfile(cache_a))
        cache_b = prks_person_image_cache_path(
            p_id, url_b, server_module._bound_storage.people_dir
        )
        self.assertNotEqual(
            prks_person_image_url_hash(url_a), prks_person_image_url_hash(url_b)
        )

        with urllib.request.urlopen(urllib.request.Request(img_url)) as ri2:
            ri2.read()
        self.assertTrue(os.path.isfile(cache_b))

    def test_server_invalid_image_url_post_rejected(self):
        payload = {
            "first_name": "Bad",
            "last_name": "Url",
            "image_url": "http://127.0.0.1/x.jpg",
        }
        req_p = urllib.request.Request(
            f"{self._base_url}/api/persons",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req_p.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req_p)
        self.assertEqual(cm.exception.code, 400)
        body = json.loads(cm.exception.read().decode())
        self.assertEqual(body.get("error"), "Invalid image_url")
        people = json.loads(
            urllib.request.urlopen(f"{self._base_url}/api/persons").read().decode()
        )
        self.assertFalse(
            any(p.get("first_name") == "Bad" and p.get("last_name") == "Url" for p in people)
        )

    @patch("backend.server.fetch_and_prepare")
    def test_server_invalid_image_url_patch_does_not_purge_cache(self, mock_fetch):
        encoded = decode_and_transcode(_tiny_test_portrait_png_bytes())
        if encoded is None:
            self.skipTest("Pillow/WebP encode not available")
        out, subtype = encoded
        mock_fetch.return_value = PortraitImage(body=out, subtype=subtype)
        url_ok = "https://example.invalid/ok.jpg"
        payload = {
            "first_name": "Keep",
            "last_name": "Cache",
            "image_url": url_ok,
        }
        req_p = urllib.request.Request(
            f"{self._base_url}/api/persons",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req_p.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_p) as rp:
            p_id = json.loads(rp.read().decode())["id"]
        img_url = f"{self._base_url}/api/persons/{urllib.parse.quote(p_id)}/profile-image"
        with urllib.request.urlopen(urllib.request.Request(img_url)) as ri:
            ri.read()
        cache_path = prks_person_image_cache_path(
            p_id, url_ok, server_module._bound_storage.people_dir
        )
        self.assertTrue(os.path.isfile(cache_path))

        req_patch = urllib.request.Request(
            f"{self._base_url}/api/persons/{urllib.parse.quote(p_id)}",
            data=json.dumps({"image_url": "http://192.168.1.10/x.jpg"}).encode(),
            method="PATCH",
        )
        req_patch.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req_patch)
        self.assertEqual(cm.exception.code, 400)
        person = json.loads(
            urllib.request.urlopen(
                f"{self._base_url}/api/persons/{urllib.parse.quote(p_id)}"
            ).read().decode()
        )
        self.assertEqual(person.get("image_url"), url_ok)
        self.assertTrue(os.path.isfile(cache_path))

    def test_person_profile_patch_is_atomic_across_metadata_and_groups(self):
        """A rejected group id must not leave the metadata half-updated.

        Offline coherence rests on "a failed canonical request keeps the
        previous cache eligible", which is only sound if a 4xx really means
        nothing changed.
        """
        group_id = server_module.db.add_person_group("Atomic Test Group")
        p_id = server_module.db.add_person(first_name="Old", last_name="Atomic")
        server_module.db.set_person_group_memberships(p_id, [group_id])

        req = urllib.request.Request(
            f"{self._base_url}/api/persons/{urllib.parse.quote(p_id)}",
            data=json.dumps(
                {"first_name": "New", "group_ids": ["missing-group"]}
            ).encode(),
            method="PATCH",
        )
        req.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400)

        person = server_module.db.get_person(p_id)
        self.assertEqual(person["first_name"], "Old", "metadata was written despite a 400")
        self.assertEqual([g["id"] for g in person["groups"]], [group_id])

    def test_group_http_failure_does_not_create_typed_parent(self):
        a = server_module.db.add_person_group('Atomic A')
        server_module.db.add_person_group('Atomic B')
        before = server_module.db.get_all_person_groups()
        for method, path, name in (
            ('POST', '/api/person-groups', 'Atomic A'),
            ('PATCH', '/api/person-groups/' + a, 'Atomic B'),
        ):
            with self.subTest(method=method):
                req = urllib.request.Request(
                    self._base_url + path, method=method,
                    data=json.dumps({'name': name, 'parent_name': 'Atomic Orphan'}).encode(),
                    headers={'Content-Type': 'application/json'},
                )
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(req)
                self.assertEqual(error.exception.code, 400)
                error.exception.close()
                self.assertEqual(server_module.db.get_all_person_groups(), before)

    def test_person_profile_patch_applies_metadata_and_groups_together(self):
        group_a = server_module.db.add_person_group("Atomic Group A")
        group_b = server_module.db.add_person_group("Atomic Group B")
        p_id = server_module.db.add_person(first_name="Before", last_name="Atomic")
        server_module.db.set_person_group_memberships(p_id, [group_a])

        req = urllib.request.Request(
            f"{self._base_url}/api/persons/{urllib.parse.quote(p_id)}",
            data=json.dumps({"first_name": "After", "group_ids": [group_b]}).encode(),
            method="PATCH",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 200)
        person = server_module.db.get_person(p_id)
        self.assertEqual(person["first_name"], "After")
        self.assertEqual([g["id"] for g in person["groups"]], [group_b])

    def test_person_metadata_only_patch_leaves_memberships_untouched(self):
        """Omitting group_ids must keep the ordinary metadata-only PATCH working."""
        group_id = server_module.db.add_person_group("Metadata Only Group")
        p_id = server_module.db.add_person(first_name="Meta", last_name="Only")
        server_module.db.set_person_group_memberships(p_id, [group_id])

        req = urllib.request.Request(
            f"{self._base_url}/api/persons/{urllib.parse.quote(p_id)}",
            data=json.dumps({"about": "A new biography."}).encode(),
            method="PATCH",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 200)
        person = server_module.db.get_person(p_id)
        self.assertEqual(person["about"], "A new biography.")
        self.assertEqual([g["id"] for g in person["groups"]], [group_id])

    def test_server_stale_loopback_image_url_never_connects(self):
        p_id = server_module.db.add_person(
            first_name="Legacy",
            last_name="Loopback",
            image_url="http://127.0.0.1/image",
        )
        img_url = f"{self._base_url}/api/persons/{urllib.parse.quote(p_id)}/profile-image"
        with patch("backend.person_image._getaddrinfo") as mock_dns:
            with patch("backend.person_image._create_socket") as mock_sock:
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(urllib.request.Request(img_url))
        self.assertEqual(cm.exception.code, 404)
        mock_dns.assert_not_called()
        mock_sock.assert_not_called()

    def test_server_invalid_remote_bytes_not_cached(self):
        payload = {
            "first_name": "Html",
            "last_name": "Remote",
            "image_url": "https://example.invalid/not-image",
        }
        req_p = urllib.request.Request(
            f"{self._base_url}/api/persons",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req_p.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_p) as rp:
            p_id = json.loads(rp.read().decode())["id"]
        with patch(
            "backend.person_image._fetch_remote_response",
            return_value=(b"<html>nope</html>", "text/html"),
        ):
            img_url = f"{self._base_url}/api/persons/{urllib.parse.quote(p_id)}/profile-image"
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(urllib.request.Request(img_url))
        self.assertEqual(cm.exception.code, 404)
        people_dir = server_module._bound_storage.people_dir
        cache_path = prks_person_image_cache_path(p_id, payload["image_url"], people_dir)
        legacy = prks_person_image_legacy_bin_path(p_id, people_dir)
        self.assertFalse(os.path.isfile(cache_path))
        self.assertFalse(os.path.isfile(legacy))

    def test_server_legacy_bin_migrated_and_invalid_not_served(self):
        encoded = decode_and_transcode(_tiny_test_portrait_png_bytes())
        if encoded is None:
            self.skipTest("Pillow/WebP encode not available")
        people_dir = server_module._bound_storage.people_dir
        os.makedirs(people_dir, exist_ok=True)

        p_ok = server_module.db.add_person(
            first_name="Legacy",
            last_name="Good",
            image_url="https://example.invalid/legacy-ok.jpg",
        )
        legacy_ok = prks_person_image_legacy_bin_path(p_ok, people_dir)
        with open(legacy_ok, "wb") as fp:
            fp.write(_tiny_test_portrait_png_bytes())
        img_ok = f"{self._base_url}/api/persons/{urllib.parse.quote(p_ok)}/profile-image"
        with patch("backend.server.fetch_and_prepare") as mock_fetch:
            mock_fetch.side_effect = AssertionError("must not fetch when legacy migrates")
            with urllib.request.urlopen(urllib.request.Request(img_ok)) as ri:
                self.assertEqual(ri.status, 200)
                body = ri.read()
                ctype = ri.headers.get("Content-Type", "")
        self.assertIn("image/webp", ctype)
        self.assertEqual(body[:4], b"RIFF")
        cache_ok = prks_person_image_cache_path(
            p_ok, "https://example.invalid/legacy-ok.jpg", people_dir
        )
        self.assertTrue(os.path.isfile(cache_ok))
        self.assertFalse(os.path.isfile(legacy_ok))

        p_bad = server_module.db.add_person(
            first_name="Legacy",
            last_name="Bad",
            image_url="https://example.invalid/legacy-bad.jpg",
        )
        legacy_bad = prks_person_image_legacy_bin_path(p_bad, people_dir)
        with open(legacy_bad, "wb") as fp:
            fp.write(b"not-an-image")
        img_bad = f"{self._base_url}/api/persons/{urllib.parse.quote(p_bad)}/profile-image"
        with patch("backend.server.fetch_and_prepare", return_value=None):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(urllib.request.Request(img_bad))
        self.assertEqual(cm.exception.code, 404)
        self.assertFalse(os.path.isfile(legacy_bad))

    def test_server_cached_jpeg_served_as_jpeg(self):
        from io import BytesIO

        from PIL import Image

        img = Image.new("RGB", (12, 12), (9, 8, 7))
        buf = BytesIO()
        img.save(buf, format="JPEG")
        jpeg = buf.getvalue()
        payload = {
            "first_name": "Jpeg",
            "last_name": "Cache",
            "image_url": "https://example.invalid/jpeg-cache.jpg",
        }
        req_p = urllib.request.Request(
            f"{self._base_url}/api/persons",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req_p.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_p) as rp:
            p_id = json.loads(rp.read().decode())["id"]
        cache_path = prks_person_image_cache_path(
            p_id, payload["image_url"], server_module._bound_storage.people_dir
        )
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as fp:
            fp.write(jpeg)
        img_url = f"{self._base_url}/api/persons/{urllib.parse.quote(p_id)}/profile-image"
        with patch("backend.server.fetch_and_prepare") as mock_fetch:
            mock_fetch.side_effect = AssertionError("must use cache")
            with urllib.request.urlopen(urllib.request.Request(img_url)) as ri:
                self.assertEqual(ri.status, 200)
                self.assertEqual(ri.headers.get("Content-Type"), "image/jpeg")
                self.assertEqual(ri.read()[:2], b"\xff\xd8")

    def _raw_http(self, method, path, header_pairs, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self._test_port, timeout=5)
        skip_host = any(name.lower() == "host" for name, _ in header_pairs)
        conn.putrequest(method, path, skip_host=skip_host, skip_accept_encoding=True)
        headers = list(header_pairs)
        if body is not None and not any(name.lower() == "content-length" for name, _ in headers):
            headers.append(("Content-Length", str(len(body))))
        for name, value in headers:
            conn.putheader(name, value)
        if body is not None:
            conn.endheaders(body)
        else:
            conn.endheaders()
        response = conn.getresponse()
        payload = response.read()
        status = response.status
        conn.close()
        return status, payload

    def _json_error(self, payload):
        return json.loads(payload.decode("utf-8")).get("error")

    def test_trust_localhost_host_allowed(self):
        status, body = self._raw_http(
            "GET",
            "/api/works",
            [("Host", f"localhost:{self._test_port}")],
        )
        self.assertEqual(status, 200)
        json.loads(body.decode("utf-8"))

    def test_trust_loopback_ip_host_allowed(self):
        status, body = self._raw_http(
            "GET",
            "/api/works",
            [("Host", f"127.0.0.1:{self._test_port}")],
        )
        self.assertEqual(status, 200)
        json.loads(body.decode("utf-8"))

    def test_trust_untrusted_host_rejected(self):
        before = json.loads(
            urllib.request.urlopen(f"{self._base_url}/api/works").read().decode()
        )
        status, payload = self._raw_http(
            "GET",
            "/api/works",
            [("Host", "attacker.example")],
        )
        self.assertEqual(status, 421)
        self.assertEqual(self._json_error(payload), "untrusted_host")
        after = json.loads(
            urllib.request.urlopen(f"{self._base_url}/api/works").read().decode()
        )
        self.assertEqual(len(after), len(before))

    def test_trust_duplicate_host_rejected(self):
        status, payload = self._raw_http(
            "GET",
            "/api/works",
            [
                ("Host", f"localhost:{self._test_port}"),
                ("Host", "evil.example"),
            ],
        )
        self.assertEqual(status, 400)
        self.assertEqual(self._json_error(payload), "invalid_host")

    def test_trust_same_origin_json_post_allowed(self):
        body = json.dumps({"title": "Trust Same Origin"}).encode()
        status, payload = self._raw_http(
            "POST",
            "/api/works",
            [
                ("Host", f"localhost:{self._test_port}"),
                ("Origin", f"http://localhost:{self._test_port}"),
                ("Content-Type", "application/json"),
            ],
            body,
        )
        self.assertEqual(status, 200)
        created = json.loads(payload.decode("utf-8"))
        self.assertTrue(created.get("id"))

    def test_trust_cross_origin_post_forbidden(self):
        before = json.loads(
            urllib.request.urlopen(f"{self._base_url}/api/works").read().decode()
        )
        status, payload = self._raw_http(
            "POST",
            "/api/works",
            [
                ("Host", f"localhost:{self._test_port}"),
                ("Origin", "https://evil.example"),
                ("Content-Type", "application/json"),
            ],
            json.dumps({"title": "Trust Cross Origin"}).encode(),
        )
        self.assertEqual(status, 403)
        self.assertEqual(self._json_error(payload), "origin_not_allowed")
        after = json.loads(
            urllib.request.urlopen(f"{self._base_url}/api/works").read().decode()
        )
        self.assertEqual(len(after), len(before))

    def test_trust_origin_null_forbidden(self):
        before = json.loads(
            urllib.request.urlopen(f"{self._base_url}/api/works").read().decode()
        )
        status, payload = self._raw_http(
            "POST",
            "/api/works",
            [
                ("Host", f"localhost:{self._test_port}"),
                ("Origin", "null"),
                ("Content-Type", "application/json"),
            ],
            json.dumps({"title": "Trust Null Origin"}).encode(),
        )
        self.assertEqual(status, 403)
        self.assertEqual(self._json_error(payload), "origin_not_allowed")
        after = json.loads(
            urllib.request.urlopen(f"{self._base_url}/api/works").read().decode()
        )
        self.assertEqual(len(after), len(before))

    def test_trust_duplicate_origin_forbidden(self):
        before = json.loads(
            urllib.request.urlopen(f"{self._base_url}/api/works").read().decode()
        )
        status, payload = self._raw_http(
            "POST",
            "/api/works",
            [
                ("Host", f"localhost:{self._test_port}"),
                ("Origin", f"http://localhost:{self._test_port}"),
                ("Origin", "https://evil.example"),
                ("Content-Type", "application/json"),
            ],
            json.dumps({"title": "Trust Dup Origin"}).encode(),
        )
        self.assertEqual(status, 403)
        self.assertEqual(self._json_error(payload), "origin_not_allowed")
        after = json.loads(
            urllib.request.urlopen(f"{self._base_url}/api/works").read().decode()
        )
        self.assertEqual(len(after), len(before))

    def test_trust_missing_origin_json_allowed(self):
        status, payload = self._raw_http(
            "POST",
            "/api/works",
            [
                ("Host", f"localhost:{self._test_port}"),
                ("Content-Type", "application/json"),
            ],
            json.dumps({"title": "Trust Missing Origin"}).encode(),
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload.decode("utf-8")).get("id"))

    def test_trust_missing_content_type_rejected(self):
        before = json.loads(
            urllib.request.urlopen(f"{self._base_url}/api/works").read().decode()
        )
        status, payload = self._raw_http(
            "POST",
            "/api/works",
            [("Host", f"localhost:{self._test_port}")],
            json.dumps({"title": "Trust Missing CT"}).encode(),
        )
        self.assertEqual(status, 415)
        self.assertEqual(self._json_error(payload), "unsupported_media_type")
        after = json.loads(
            urllib.request.urlopen(f"{self._base_url}/api/works").read().decode()
        )
        self.assertEqual(len(after), len(before))

    def test_trust_empty_body_missing_content_type_rejected(self):
        status, payload = self._raw_http(
            "POST",
            "/api/works",
            [("Host", f"localhost:{self._test_port}")],
        )
        self.assertEqual(status, 415)
        self.assertEqual(self._json_error(payload), "unsupported_media_type")

    def test_trust_text_plain_rejected(self):
        before = json.loads(
            urllib.request.urlopen(f"{self._base_url}/api/works").read().decode()
        )
        status, payload = self._raw_http(
            "POST",
            "/api/works",
            [
                ("Host", f"localhost:{self._test_port}"),
                ("Content-Type", "text/plain"),
            ],
            json.dumps({"title": "Trust Text Plain"}).encode(),
        )
        self.assertEqual(status, 415)
        self.assertEqual(self._json_error(payload), "unsupported_media_type")
        after = json.loads(
            urllib.request.urlopen(f"{self._base_url}/api/works").read().decode()
        )
        self.assertEqual(len(after), len(before))

    def test_trust_form_urlencoded_rejected(self):
        before = json.loads(
            urllib.request.urlopen(f"{self._base_url}/api/works").read().decode()
        )
        status, payload = self._raw_http(
            "POST",
            "/api/works",
            [
                ("Host", f"localhost:{self._test_port}"),
                ("Content-Type", "application/x-www-form-urlencoded"),
            ],
            b"title=TrustForm",
        )
        self.assertEqual(status, 415)
        self.assertEqual(self._json_error(payload), "unsupported_media_type")
        after = json.loads(
            urllib.request.urlopen(f"{self._base_url}/api/works").read().decode()
        )
        self.assertEqual(len(after), len(before))

    def test_trust_duplicate_content_type_rejected(self):
        before = json.loads(
            urllib.request.urlopen(f"{self._base_url}/api/works").read().decode()
        )
        status, payload = self._raw_http(
            "POST",
            "/api/works",
            [
                ("Host", f"localhost:{self._test_port}"),
                ("Content-Type", "application/json"),
                ("Content-Type", "text/plain"),
            ],
            json.dumps({"title": "Trust Dup CT"}).encode(),
        )
        self.assertEqual(status, 415)
        self.assertEqual(self._json_error(payload), "unsupported_media_type")
        after = json.loads(
            urllib.request.urlopen(f"{self._base_url}/api/works").read().decode()
        )
        self.assertEqual(len(after), len(before))

    def test_trust_empty_json_body_keeps_object_semantics(self):
        status, payload = self._raw_http(
            "PATCH",
            "/api/settings",
            [
                ("Host", f"localhost:{self._test_port}"),
                ("Content-Type", "application/json"),
            ],
        )
        self.assertEqual(status, 200)
        settings = json.loads(payload.decode("utf-8"))
        self.assertIsInstance(settings, dict)

    def test_trust_json_charset_patch_allowed(self):
        req = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps({"title": "Trust Charset"}).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            work_id = json.loads(res.read().decode())["id"]
        status, payload = self._raw_http(
            "PATCH",
            f"/api/works/{work_id}",
            [
                ("Host", f"localhost:{self._test_port}"),
                ("Content-Type", "application/json; charset=utf-8"),
            ],
            json.dumps({"title": "Trust Charset Patched"}).encode(),
        )
        self.assertEqual(status, 200)
        with urllib.request.urlopen(f"{self._base_url}/api/works/{work_id}") as res:
            self.assertEqual(json.loads(res.read().decode())["title"], "Trust Charset Patched")

    def test_trust_cross_origin_delete_leaves_work(self):
        req = urllib.request.Request(
            f"{self._base_url}/api/works",
            data=json.dumps({"title": "Trust Delete Keep"}).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as res:
            work_id = json.loads(res.read().decode())["id"]
        status, payload = self._raw_http(
            "DELETE",
            f"/api/works/{work_id}",
            [
                ("Host", f"localhost:{self._test_port}"),
                ("Origin", "https://evil.example"),
            ],
        )
        self.assertEqual(status, 403)
        self.assertEqual(self._json_error(payload), "origin_not_allowed")
        with urllib.request.urlopen(f"{self._base_url}/api/works/{work_id}") as res:
            self.assertEqual(res.status, 200)

    def test_trust_attacker_host_and_origin_rejected_as_untrusted_host(self):
        status, payload = self._raw_http(
            "POST",
            "/api/works",
            [
                ("Host", "attacker.example"),
                ("Origin", "http://attacker.example"),
                ("Content-Type", "application/json"),
            ],
            json.dumps({"title": "Trust Rebind"}).encode(),
        )
        self.assertEqual(status, 421)
        self.assertEqual(self._json_error(payload), "untrusted_host")

    def test_processing_files_rescan_query_controls_scan_and_avoids_duplicate_fetch(self):
        processing_root = server_module.processing_dir
        for dirpath, _dirnames, filenames in os.walk(processing_root, topdown=False):
            for name in filenames:
                os.remove(os.path.join(dirpath, name))
            if dirpath != processing_root:
                try:
                    os.rmdir(dirpath)
                except OSError:
                    pass
        db = server_module.db
        db.scan_processing_files()
        pdf_path = os.path.join(processing_root, "rescan_flag.pdf")
        with open(pdf_path, "wb") as handle:
            handle.write(b"%PDF-1.4\n%RESCAN\n%%EOF\n")
        with (
            patch.object(db, "scan_processing_files", wraps=db.scan_processing_files) as scan,
            patch.object(db, "get_processing_files", wraps=db.get_processing_files) as get,
        ):
            req = urllib.request.Request(f"{self._base_url}/api/processing-files")
            with urllib.request.urlopen(req) as res:
                self.assertEqual(res.status, 200)
                plain = json.loads(res.read().decode())
            self.assertEqual(scan.call_count, 0)
            self.assertEqual(get.call_count, 1)
            self.assertEqual(plain, [])

            scan.reset_mock()
            get.reset_mock()
            req_scan = urllib.request.Request(f"{self._base_url}/api/processing-files?rescan=1")
            with urllib.request.urlopen(req_scan) as res:
                scanned = json.loads(res.read().decode())
            self.assertEqual(scan.call_count, 1)
            self.assertEqual(get.call_count, 1)
            self.assertEqual(len(scanned), 1)
            self.assertEqual(scanned[0]["rel_path"], "rescan_flag.pdf")

            scan.reset_mock()
            get.reset_mock()
            req_true = urllib.request.Request(f"{self._base_url}/api/processing-files?rescan=true")
            with urllib.request.urlopen(req_true) as res:
                self.assertEqual(res.status, 200)
                _ = res.read()
            self.assertEqual(scan.call_count, 1)
            self.assertEqual(get.call_count, 1)

            os.remove(pdf_path)
            scan.reset_mock()
            get.reset_mock()
            req_stale = urllib.request.Request(f"{self._base_url}/api/processing-files")
            with urllib.request.urlopen(req_stale) as res:
                stale = json.loads(res.read().decode())
            self.assertEqual(scan.call_count, 0)
            self.assertEqual(len(stale), 1)

            scan.reset_mock()
            get.reset_mock()
            req_refresh = urllib.request.Request(f"{self._base_url}/api/processing-files?rescan=yes")
            with urllib.request.urlopen(req_refresh) as res:
                gone = json.loads(res.read().decode())
            self.assertEqual(scan.call_count, 1)
            self.assertEqual(get.call_count, 1)
            self.assertEqual(gone, [])

    def _post_json(self, path, payload):
        req = urllib.request.Request(
            f"{self._base_url}{path}",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        return urllib.request.urlopen(req)

    def test_bulk_works_status_and_counts(self):
        db = self.__class__.test_db
        a = db.add_work(title="Bulk A", status="Paused")
        b = db.add_work(title="Bulk B", status="Paused")
        c = db.add_work(title="Bulk C", status="Paused")
        with self._post_json(
            "/api/works/bulk",
            {"work_ids": [a, a, b], "action": "set_status", "status": "Completed"},
        ) as res:
            self.assertEqual(res.status, 200)
            body = json.loads(res.read().decode())
        self.assertEqual(body["status"], "updated")
        self.assertEqual(body["action"], "set_status")
        self.assertEqual(body["requested"], 2)
        self.assertEqual(body["updated"], 2)
        self.assertNotIn("works", body)
        self.assertEqual(db.get_work(a)["status"], "Completed")
        self.assertEqual(db.get_work(b)["status"], "Completed")
        self.assertEqual(db.get_work(c)["status"], "Paused")

    def test_bulk_works_invalid_status_400(self):
        db = self.__class__.test_db
        w = db.add_work(title="Bulk Bad Status", status="Planned")
        req = urllib.request.Request(
            f"{self._base_url}/api/works/bulk",
            data=json.dumps(
                {"work_ids": [w], "action": "set_status", "status": "Finished"}
            ).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(db.get_work(w)["status"], "Planned")

    def test_bulk_works_unknown_action_400(self):
        db = self.__class__.test_db
        w = db.add_work(title="Bulk Unknown Action", status="Planned")
        req = urllib.request.Request(
            f"{self._base_url}/api/works/bulk",
            data=json.dumps(
                {"work_ids": [w], "action": "delete_everything"}
            ).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(db.get_work(w)["status"], "Planned")

    def test_bulk_works_missing_work_is_atomic(self):
        db = self.__class__.test_db
        w = db.add_work(title="Bulk Atomic Work", status="Planned")
        req = urllib.request.Request(
            f"{self._base_url}/api/works/bulk",
            data=json.dumps(
                {
                    "work_ids": [w, "W-MISSING"],
                    "action": "set_status",
                    "status": "Completed",
                }
            ).encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 404)
        err = json.loads(cm.exception.read().decode())
        self.assertEqual(err.get("error"), "One or more selected files no longer exist.")
        self.assertEqual(db.get_work(w)["status"], "Planned")

    def test_bulk_works_does_not_sync_text_index(self):
        db = self.__class__.test_db
        w = db.add_work(title="Bulk No Index", status="Paused")
        with patch.object(server_module.text_index, "sync_work") as sync:
            with self._post_json(
                "/api/works/bulk",
                {"work_ids": [w], "action": "set_status", "status": "Completed"},
            ) as res:
                self.assertEqual(res.status, 200)
            sync.assert_not_called()

    def test_bulk_works_trust_cross_origin_forbidden(self):
        db = self.__class__.test_db
        w = db.add_work(title="Bulk Trust", status="Planned")
        status, payload = self._raw_http(
            "POST",
            "/api/works/bulk",
            [
                ("Host", f"localhost:{self._test_port}"),
                ("Origin", "https://evil.example"),
                ("Content-Type", "application/json"),
            ],
            json.dumps(
                {"work_ids": [w], "action": "set_status", "status": "Completed"}
            ).encode(),
        )
        self.assertEqual(status, 403)
        self.assertEqual(self._json_error(payload), "origin_not_allowed")
        self.assertEqual(db.get_work(w)["status"], "Planned")

    def test_bulk_works_bad_content_type_rejected(self):
        db = self.__class__.test_db
        w = db.add_work(title="Bulk CType", status="Planned")
        status, _payload = self._raw_http(
            "POST",
            "/api/works/bulk",
            [
                ("Host", f"localhost:{self._test_port}"),
                ("Origin", f"http://localhost:{self._test_port}"),
                ("Content-Type", "text/plain"),
            ],
            json.dumps(
                {"work_ids": [w], "action": "set_status", "status": "Completed"}
            ).encode(),
        )
        self.assertEqual(status, 415)
        self.assertEqual(db.get_work(w)["status"], "Planned")

    def test_bulk_works_privacy_no_title_in_logs(self):
        import logging

        db = self.__class__.test_db
        sentinel = "PRIVATE_BULK_TITLE_X9Q7"
        w = db.add_work(title=sentinel, status="Paused")
        records = []

        class _Handler(logging.Handler):
            def emit(self, record):
                records.append(self.format(record))

        handler = _Handler()
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(message)s"))
        loggers = [logging.getLogger("prks.server"), logging.getLogger("prks.db")]
        prev_levels = []
        for lg in loggers:
            prev_levels.append(lg.level)
            lg.addHandler(handler)
            lg.setLevel(logging.DEBUG)
        try:
            with self._post_json(
                "/api/works/bulk",
                {"work_ids": [w], "action": "set_status", "status": "Completed"},
            ) as res:
                self.assertEqual(res.status, 200)
            snap = server_module.performance_snapshot()
        finally:
            for lg, prev in zip(loggers, prev_levels):
                lg.removeHandler(handler)
                lg.setLevel(prev)
        blob = "\n".join(records) + json.dumps(snap)
        self.assertNotIn(sentinel, blob)
        self.assertTrue(any("bulk_work_update" in line for line in records))

    def _sv_json(self, method, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(f"{self._base_url}{path}", data=data, method=method)
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as res:
                body = res.read().decode()
                parsed = json.loads(body) if body else {}
                return res.status, parsed
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"raw": raw}
            return exc.code, parsed

    def test_saved_views_crud_and_duplicate_name(self):
        status, created = self._sv_json(
            "POST",
            "/api/saved-views",
            {
                "name": "Adorno — culture industry",
                "search": {
                    "mode": "advanced",
                    "q": "culture industry",
                    "tag": "",
                    "author": "Adorno",
                    "publisher": "",
                },
            },
        )
        self.assertEqual(status, 201)
        self.assertTrue(created["id"].startswith("SV-"))
        self.assertEqual(created["search"]["author"], "Adorno")
        self.assertNotIn("search_q", created)
        vid = created["id"]
        status, listed = self._sv_json("GET", "/api/saved-views")
        self.assertEqual(status, 200)
        self.assertTrue(any(v["id"] == vid for v in listed))
        status, one = self._sv_json("GET", f"/api/saved-views/{vid}")
        self.assertEqual(status, 200)
        self.assertEqual(one["name"], "Adorno — culture industry")
        status, dup = self._sv_json(
            "POST",
            "/api/saved-views",
            {
                "name": "adorno — culture industry",
                "search": created["search"],
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(dup["error"], "A Saved View with that name already exists.")
        status, patched = self._sv_json(
            "PATCH",
            f"/api/saved-views/{vid}",
            {"name": "Critical Theory"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(patched["id"], vid)
        self.assertEqual(patched["name"], "Critical Theory")
        status, missing = self._sv_json("GET", "/api/saved-views/SV-missing")
        self.assertEqual(status, 404)
        status, deleted = self._sv_json("DELETE", f"/api/saved-views/{vid}")
        self.assertEqual(status, 200)
        status, gone = self._sv_json("GET", f"/api/saved-views/{vid}")
        self.assertEqual(status, 404)

    def test_saved_views_reject_invalid_and_partial_search(self):
        status, body = self._sv_json(
            "POST",
            "/api/saved-views",
            {"name": "Bad", "search": {"mode": "advanced", "author": "Adorno"}},
        )
        self.assertEqual(status, 400)
        created = self._sv_json(
            "POST",
            "/api/saved-views",
            {
                "name": "Valid View",
                "search": {
                    "mode": "all",
                    "q": "critical theory",
                    "tag": "",
                    "author": "",
                    "publisher": "",
                },
            },
        )[1]
        status, partial = self._sv_json(
            "PATCH",
            f"/api/saved-views/{created['id']}",
            {"search": {"author": "Adorno"}},
        )
        self.assertEqual(status, 400)
        self.assertEqual(
            self._sv_json("GET", f"/api/saved-views/{created['id']}")[1]["search"]["q"],
            "critical theory",
        )

    def test_saved_views_live_results_match_search(self):
        db = self.__class__.test_db
        a = db.add_work(title="Work A", author_text="Adorno")
        view = self._sv_json(
            "POST",
            "/api/saved-views",
            {
                "name": "Adorno files",
                "search": {
                    "mode": "advanced",
                    "q": "",
                    "tag": "",
                    "author": "Adorno",
                    "publisher": "",
                },
            },
        )[1]
        with urllib.request.urlopen(f"{self._base_url}/api/search?author=Adorno") as res:
            search_ids = [w["id"] for w in json.loads(res.read().decode())]
        self.assertIn(a, search_ids)
        stored = self._sv_json("GET", f"/api/saved-views/{view['id']}")[1]
        self.assertEqual(stored["search"]["author"], "Adorno")
        self.assertNotIn("works", stored)
        b = db.add_work(title="Work B", author_text="Adorno")
        with urllib.request.urlopen(f"{self._base_url}/api/search?author=Adorno") as res:
            later = [w["id"] for w in json.loads(res.read().decode())]
        self.assertIn(a, later)
        self.assertIn(b, later)
        still = self._sv_json("GET", f"/api/saved-views/{view['id']}")[1]
        self.assertEqual(still["search"], stored["search"])

    def test_saved_view_tag_results_are_live_after_bulk_remove(self):
        db = self.__class__.test_db
        tag = db.add_tag("Frankfurt School")
        a = db.add_work(title="Tagged A")
        b = db.add_work(title="Tagged B")
        db.add_tag_to_work(a, tag["id"])
        db.add_tag_to_work(b, tag["id"])
        view = self._sv_json(
            "POST",
            "/api/saved-views",
            {
                "name": "Frankfurt tag view",
                "search": {
                    "mode": "tag",
                    "q": "",
                    "tag": "Frankfurt School",
                    "author": "",
                    "publisher": "",
                },
            },
        )[1]
        with urllib.request.urlopen(
            f"{self._base_url}/api/search?tag={urllib.parse.quote('Frankfurt School')}"
        ) as res:
            before = [w["id"] for w in json.loads(res.read().decode())]
        self.assertIn(a, before)
        self.assertIn(b, before)
        with self._post_json(
            "/api/works/bulk",
            {"work_ids": [a, b], "action": "remove_tags", "tag_ids": [tag["id"]]},
        ) as res:
            self.assertEqual(res.status, 200)
        with urllib.request.urlopen(
            f"{self._base_url}/api/search?tag={urllib.parse.quote('Frankfurt School')}"
        ) as res:
            after = [w["id"] for w in json.loads(res.read().decode())]
        self.assertNotIn(a, after)
        self.assertNotIn(b, after)
        still = self._sv_json("GET", f"/api/saved-views/{view['id']}")[1]
        self.assertEqual(still["search"]["tag"], "Frankfurt School")

    def test_saved_views_trust_boundary(self):
        payload = json.dumps(
            {
                "name": "Trust View",
                "search": {
                    "mode": "all",
                    "q": "trust",
                    "tag": "",
                    "author": "",
                    "publisher": "",
                },
            }
        ).encode()
        status, raw = self._raw_http(
            "POST",
            "/api/saved-views",
            [
                ("Host", f"localhost:{self._test_port}"),
                ("Origin", "https://evil.example"),
                ("Content-Type", "application/json"),
            ],
            payload,
        )
        self.assertEqual(status, 403)
        status, _raw = self._raw_http(
            "POST",
            "/api/saved-views",
            [
                ("Host", f"localhost:{self._test_port}"),
                ("Origin", f"http://localhost:{self._test_port}"),
                ("Content-Type", "text/plain"),
            ],
            payload,
        )
        self.assertEqual(status, 415)
        created = self._sv_json(
            "POST",
            "/api/saved-views",
            {
                "name": "Trust Keep",
                "search": {
                    "mode": "all",
                    "q": "keep",
                    "tag": "",
                    "author": "",
                    "publisher": "",
                },
            },
        )[1]
        status, _raw = self._raw_http(
            "PATCH",
            f"/api/saved-views/{created['id']}",
            [
                ("Host", f"localhost:{self._test_port}"),
                ("Origin", "https://evil.example"),
                ("Content-Type", "application/json"),
            ],
            json.dumps({"name": "Hijacked"}).encode(),
        )
        self.assertEqual(status, 403)
        status, _raw = self._raw_http(
            "DELETE",
            f"/api/saved-views/{created['id']}",
            [
                ("Host", f"localhost:{self._test_port}"),
                ("Origin", "https://evil.example"),
            ],
        )
        self.assertEqual(status, 403)
        self.assertEqual(self._sv_json("GET", f"/api/saved-views/{created['id']}")[0], 200)

    def test_saved_views_privacy_sentinel_not_logged(self):
        import logging

        sentinel = "PRIVATE_SAVED_QUERY_X9Q7"
        records = []

        class _H(logging.Handler):
            def emit(self, record):
                records.append(self.format(record))

        handler = _H()
        loggers = [
            logging.getLogger("prks.server"),
            logging.getLogger("prks.db"),
            logging.getLogger("prks.performance"),
        ]
        prev_levels = []
        for lg in loggers:
            prev_levels.append(lg.level)
            lg.addHandler(handler)
            lg.setLevel(logging.DEBUG)
        try:
            status, created = self._sv_json(
                "POST",
                "/api/saved-views",
                {
                    "name": sentinel,
                    "search": {
                        "mode": "all",
                        "q": sentinel,
                        "tag": "",
                        "author": "",
                        "publisher": "",
                    },
                },
            )
            self.assertEqual(status, 201)
            self._sv_json("GET", f"/api/saved-views/{created['id']}")
            self._sv_json(
                "PATCH",
                f"/api/saved-views/{created['id']}",
                {
                    "search": {
                        "mode": "all",
                        "q": sentinel,
                        "tag": "",
                        "author": "",
                        "publisher": "",
                    }
                },
            )
            snap = server_module.performance_snapshot()
        finally:
            for lg, prev in zip(loggers, prev_levels):
                lg.removeHandler(handler)
                lg.setLevel(prev)
        blob = "\n".join(records) + json.dumps(snap)
        self.assertNotIn(sentinel, blob)
        self.assertTrue(any("saved_view_created" in line for line in records))
        self.assertTrue(any("saved_view_updated" in line for line in records))
        row = self.__class__.test_db.get_saved_view(created["id"])
        self.assertEqual(row["search"]["q"], sentinel)

    def test_note_save_creates_concept_and_research_refs(self):
        status, created = self._sv_json("POST", "/api/works", {"title": "Research note work"})
        self.assertEqual(status, 200)
        wid = created["id"]
        status, body = self._sv_json(
            "PATCH",
            f"/api/works/{wid}",
            {"text_content": "See [[concept:Culture Industry]]."},
        )
        self.assertEqual(status, 200)
        status, concepts = self._sv_json("GET", "/api/concepts")
        self.assertEqual(status, 200)
        names = [c["name"] for c in concepts]
        self.assertIn("Culture Industry", names)
        status, work = self._sv_json("GET", f"/api/works/{wid}")
        self.assertEqual(status, 200)
        refs = work.get("research_refs") or {}
        self.assertTrue(refs.get("concepts"))

    def test_index_sync_failure_keeps_note(self):
        status, created = self._sv_json("POST", "/api/works", {"title": "Index fail work"})
        wid = created["id"]
        with patch.object(server_module.research_index, "sync_work", side_effect=RuntimeError("boom")):
            status, body = self._sv_json(
                "PATCH",
                f"/api/works/{wid}",
                {"text_content": "[[concept:Instrumental Reason]]"},
            )
        self.assertEqual(status, 200)
        status, work = self._sv_json("GET", f"/api/works/{wid}")
        self.assertIn("[[concept:Instrumental Reason]]", work["text_content"])

    def test_stale_index_concept_delete_returns_409(self):
        status, created = self._sv_json("POST", "/api/works", {"title": "Stale concept delete"})
        self.assertEqual(status, 200)
        wid = created["id"]
        with patch.object(server_module.research_index, "sync_work", side_effect=RuntimeError("boom")):
            status, _body = self._sv_json(
                "PATCH",
                f"/api/works/{wid}",
                {"text_content": "[[concept:Culture Industry]]"},
            )
        self.assertEqual(status, 200)
        status, concepts = self._sv_json("GET", "/api/concepts")
        cid = next(c["id"] for c in concepts if c["name"] == "Culture Industry")
        status, body = self._sv_json("DELETE", f"/api/concepts/{cid}")
        self.assertEqual(status, 409)
        self.assertEqual(body.get("code"), "concept_in_use")

    def test_stale_index_argument_delete_returns_409(self):
        status, created = self._sv_json("POST", "/api/works", {"title": "Stale argument delete"})
        self.assertEqual(status, 200)
        wid = created["id"]
        status, arg = self._sv_json(
            "POST",
            "/api/arguments",
            {"name": "Linked argument", "kind": "argument"},
        )
        self.assertEqual(status, 201)
        aid = arg["id"]
        with patch.object(server_module.research_index, "sync_work", side_effect=RuntimeError("boom")):
            status, _body = self._sv_json(
                "PATCH",
                f"/api/works/{wid}",
                {"text_content": "[[argument:%s|label]]" % aid},
            )
        self.assertEqual(status, 200)
        status, body = self._sv_json("DELETE", f"/api/arguments/{aid}")
        self.assertEqual(status, 409)
        self.assertEqual(body.get("code"), "argument_in_use")

    def test_missing_argument_ref_omitted_from_research_refs(self):
        status, created = self._sv_json("POST", "/api/works", {"title": "Missing argument ref"})
        self.assertEqual(status, 200)
        wid = created["id"]
        status, arg = self._sv_json(
            "POST",
            "/api/arguments",
            {"name": "Real argument", "kind": "argument"},
        )
        self.assertEqual(status, 201)
        aid = arg["id"]
        status, _body = self._sv_json(
            "PATCH",
            f"/api/works/{wid}",
            {"text_content": "[[argument:A-MISSING]] and [[argument:%s|ok]]" % aid},
        )
        self.assertEqual(status, 200)
        status, work = self._sv_json("GET", f"/api/works/{wid}")
        self.assertEqual(status, 200)
        ids = [a["id"] for a in (work.get("research_refs") or {}).get("arguments") or []]
        self.assertNotIn("A-MISSING", ids)
        self.assertIn(aid, ids)

    # -- Video source (YouTube-only) creation contract ---------------------

    def _work_titles(self):
        status, works = self._sv_json("GET", "/api/works")
        self.assertEqual(status, 200)
        return [w.get("title") for w in works]

    def test_work_creation_with_initial_roles_is_construction(self):
        """A Work born with two Authors has not "changed" twice. Revision 1 for
        each would make every device's first read look like missed changes, and
        routing construction through the mutation boundary also threw away the
        author order the caller stated."""
        pa = self._sv_json("POST", "/api/persons",
                           {"first_name": "Ann", "last_name": "Lee"})[1]["id"]
        pb = self._sv_json("POST", "/api/persons",
                           {"first_name": "Bo", "last_name": "Ng"})[1]["id"]
        # The array IS the author order -- a JSON list is how the request
        # states it -- and creation preserves it rather than appending.
        status, created = self._sv_json("POST", "/api/works", {
            "title": "Co-authored", "roles": [
                {"person_id": pa, "role_type": "Author", "credit_name": "A. Lee"},
                {"person_id": pb, "role_type": "Author"},
            ]})
        self.assertEqual(status, 200, created)
        work = created["id"]

        state = self._sv_json("GET", "/api/works/%s/people-state" % work, None)[1]
        self.assertEqual(sorted(s["revision"] for s in state["scopes"]), [0, 0],
                         "construction creates no revisions")

        detail = self._sv_json("GET", "/api/works/" + work, None)[1]
        self.assertEqual([r["role_type"] for r in detail["roles"]], ["Author", "Author"])
        self.assertEqual([r["id"] for r in detail["roles"]], [pa, pb],
                         "the order the caller stated is preserved")

        # ... and mutating one moves only that element.
        self.assertEqual(self._sv_json(
            "DELETE", "/api/works/%s/roles?person_id=%s&role_type=Author" % (work, pa),
            None)[0], 200)
        state = {s["person_id"]: s for s in self._sv_json(
            "GET", "/api/works/%s/people-state" % work, None)[1]["scopes"]}
        self.assertEqual(state[pa], {"person_id": pa, "role_type": "Author",
                                     "revision": 1, "present": False})
        self.assertEqual(state[pb]["revision"], 0, "the other is untouched")

    def test_the_role_api_refuses_a_role_outside_the_domain(self):
        """`POST /api/roles` took any string while the durable operation
        refused unknown roles -- so `Producer` was impossible offline and fine
        online, and the accepted row matched no filter, icon or BibTeX
        mapping."""
        person = self._sv_json("POST", "/api/persons",
                               {"first_name": "Pro", "last_name": "Ducer"})[1]["id"]
        work = self._sv_json("POST", "/api/works", {"title": "Roleless"})[1]["id"]
        status, body = self._sv_json("POST", "/api/roles", {
            "person_id": person, "work_id": work, "role_type": "Producer"})
        self.assertEqual(status, 400)
        self.assertIn("not a Work role", body.get("error", ""))
        self.assertEqual(self._sv_json("GET", "/api/works/" + work, None)[1]["roles"], [])

    def test_linking_through_the_api_advances_the_relationship_revision(self):
        """The ordinary endpoint and the durable operation are the same
        canonical write; a link made here must be discoverable by an offline
        device."""
        person = self._sv_json("POST", "/api/persons",
                               {"first_name": "Samuel", "last_name": "Clemens"})[1]["id"]
        work = self._sv_json("POST", "/api/works", {"title": "Huck"})[1]["id"]
        self.assertEqual(self._sv_json("POST", "/api/roles", {
            "person_id": person, "work_id": work, "role_type": "Author",
            "credit_name": "Mark Twain"})[0], 200)
        state = self._sv_json("GET", "/api/works/%s/people-state" % work, None)[1]
        self.assertEqual(state["scopes"],
                         [{"person_id": person, "role_type": "Author",
                           "revision": 1, "present": True}])
        detail = self._sv_json("GET", "/api/works/" + work, None)[1]
        self.assertEqual(detail["roles"][0]["credit_name"], "Mark Twain")
        self.assertIn("Mark Twain",
                      self._sv_json("GET", "/api/persons/" + person, None)[1]["aliases"])

        self.assertEqual(self._sv_json("PATCH", "/api/works/%s/roles" % work, {
            "person_id": person, "role_type": "Author", "order_index": 99,
            "credit_name": "M. Twain"})[0], 200)
        state = self._sv_json("GET", "/api/works/%s/people-state" % work, None)[1]
        self.assertEqual(state["scopes"][0]["revision"], 2,
                         "ordinary credit PATCH must advance the revision")
        self.assertEqual(self._sv_json("GET", "/api/works/" + work, None)[1]
                         ["roles"][0]["credit_name"], "M. Twain")
        self.assertEqual(self._sv_json("PATCH", "/api/works/%s/roles" % work, {
            "person_id": person, "role_type": "Author", "credit_name": ""})[0], 200)
        self.assertEqual(self._sv_json("GET", "/api/works/%s/people-state" % work,
                                       None)[1]["scopes"][0]["revision"], 3)

    def test_the_role_api_refuses_an_oversized_credit_name(self):
        """Ordinary add/edit accepted 501 bytes while the durable envelope
        refused them -- a split contract the write boundary now closes."""
        from backend.work_role_sync import MAX_CREDIT_NAME_BYTES
        person = self._sv_json("POST", "/api/persons",
                               {"first_name": "Long", "last_name": "Name"})[1]["id"]
        work = self._sv_json("POST", "/api/works", {"title": "Bound"})[1]["id"]
        too_long = "x" * (MAX_CREDIT_NAME_BYTES + 1)
        status, body = self._sv_json("POST", "/api/roles", {
            "person_id": person, "work_id": work, "role_type": "Author",
            "credit_name": too_long})
        self.assertEqual(status, 400)
        self.assertIn("credit_name", body.get("error", ""))
        self.assertEqual(self._sv_json("GET", "/api/works/" + work, None)[1]["roles"], [])

        self.assertEqual(self._sv_json("POST", "/api/roles", {
            "person_id": person, "work_id": work, "role_type": "Author"})[0], 200)
        status, body = self._sv_json("PATCH", "/api/works/%s/roles" % work, {
            "person_id": person, "role_type": "Author", "credit_name": too_long})
        self.assertEqual(status, 400)
        self.assertIn("credit_name", body.get("error", ""))
        self.assertEqual(self._sv_json("GET", "/api/works/" + work, None)[1]
                         ["roles"][0].get("credit_name"), None)

    def test_an_inferred_video_is_enriched_exactly_like_a_declared_one(self):
        """`source_kind` is not what makes a Work a video -- the inference is.

        Enrichment was gated on the caller having said "video", so two requests
        with identical canonical identity got different titles, authors and
        thumbnails purely because one of them said so twice. Both go down the
        same path now, decided by the same authoritative function `add_work()`
        classifies with.
        """
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        seen = []

        def fake_oembed(requested):
            seen.append(requested)
            return {"title": "Enriched Title", "author_name": "Enriched Channel",
                    "thumbnail_url": "https://img.example/thumb.jpg"}

        with patch.object(server_module, "_fetch_youtube_oembed", fake_oembed):
            status_declared, declared = self._sv_json(
                "POST", "/api/works",
                {"source_kind": "video", "source_url": url})
            status_inferred, inferred = self._sv_json(
                "POST", "/api/works", {"source_url": url})

        self.assertEqual((status_declared, status_inferred), (200, 200))
        self.assertEqual(seen, [url, url], "the same enrichment boundary, twice")

        rows = [self._sv_json("GET", "/api/works/" + w["id"], None)[1]
                for w in (declared, inferred)]
        for row in rows:
            self.assertEqual(row["source_kind"], "video")
            self.assertEqual(row["provider"], "youtube")
            self.assertEqual(row["provider_id"], "dQw4w9WgXcQ")
            self.assertEqual(row["title"], "Enriched Title")
            self.assertEqual(row["author_text"], "Enriched Channel")
            self.assertEqual(row["thumb_url"], "https://img.example/thumb.jpg")

    def test_a_file_backed_work_is_not_enriched_however_its_url_looks(self):
        """A file makes it a PDF; its URL is provenance. Enriching there would
        retitle a paper after the video page it was cited from."""
        seen = []
        with patch.object(server_module, "_fetch_youtube_oembed",
                          lambda u: seen.append(u)):
            status, created = self._sv_json(
                "POST", "/api/works",
                {"title": "Paper", "file_b64": base64.b64encode(b"%PDF-1.4\n%%EOF\n").decode(),
                 "file_name": "paper.pdf",
                 "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
        self.assertEqual(status, 200, created)
        self.assertEqual(seen, [], "no video enrichment for a file-backed Work")
        row = self._sv_json("GET", "/api/works/" + created["id"], None)[1]
        self.assertEqual(row["title"], "Paper")
        self.assertIsNone(row["provider_id"])

    def test_source_kind_is_stored_canonically(self):
        for sent, stored in (("PDF", "pdf"), ("Pdf", "pdf"), ("VIDEO", "video")):
            with self.subTest(sent=sent):
                payload = {"title": "Cased", "source_kind": sent}
                if stored == "video":
                    payload["source_url"] = "https://youtu.be/dQw4w9WgXcQ"
                else:
                    payload["file_b64"] = base64.b64encode(b"%PDF-1.4\n%%EOF\n").decode()
                    payload["file_name"] = "c.pdf"
                status, created = self._sv_json("POST", "/api/works", payload)
                self.assertEqual(status, 200, created)
                row = self._sv_json("GET", "/api/works/" + created["id"], None)[1]
                self.assertEqual(row["source_kind"], stored,
                                 "the column holds the canonical spelling, "
                                 "not one every reader has to normalize")

    def test_an_unknown_source_kind_is_refused(self):
        before = self._work_titles()
        status, body = self._sv_json(
            "POST", "/api/works",
            {"title": "Web Work", "source_kind": "web",
             "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
        self.assertEqual(status, 400)
        self.assertIn("not a source kind", body.get("error", ""))
        self.assertEqual(self._work_titles(), before)

    def test_video_work_malformed_url_rejected_no_work_created(self):
        before = self._work_titles()
        status, body = self._sv_json(
            "POST",
            "/api/works",
            {"title": "Malformed Video Work", "source_kind": "video", "source_url": "not a url"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(body.get("error"), "Invalid YouTube URL")
        self.assertEqual(self._work_titles(), before)

    def test_video_work_non_youtube_url_rejected_no_work_created(self):
        before = self._work_titles()
        status, body = self._sv_json(
            "POST",
            "/api/works",
            {
                "title": "Non-YouTube Video Work",
                "source_kind": "video",
                "source_url": "https://example.com/video",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(body.get("error"), "Invalid YouTube URL")
        self.assertEqual(self._work_titles(), before)

    def test_video_work_lookalike_youtube_hostname_rejected(self):
        before = self._work_titles()
        for bad_url in (
            "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ",
            "javascript://youtube.com/watch?v=dQw4w9WgXcQ",
        ):
            status, body = self._sv_json(
                "POST",
                "/api/works",
                {"title": "Lookalike Host Work", "source_kind": "video", "source_url": bad_url},
            )
            self.assertEqual(status, 400, bad_url)
            self.assertEqual(body.get("error"), "Invalid YouTube URL")
        self.assertEqual(self._work_titles(), before)

    def test_video_work_missing_url_rejected(self):
        before = self._work_titles()
        status, body = self._sv_json(
            "POST",
            "/api/works",
            {"title": "No URL Video Work", "source_kind": "video", "source_url": ""},
        )
        self.assertEqual(status, 400)
        self.assertEqual(body.get("error"), "Invalid YouTube URL")
        self.assertEqual(self._work_titles(), before)

    def test_video_work_valid_watch_url_accepted(self):
        status, created = self._sv_json(
            "POST",
            "/api/works",
            {
                "title": "Valid Watch URL Work",
                "source_kind": "video",
                "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("Valid Watch URL Work", self._work_titles())
        status, work = self._sv_json("GET", f"/api/works/{created['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(work.get("provider"), "youtube")
        self.assertEqual(work.get("provider_id"), "dQw4w9WgXcQ")

    def test_video_work_valid_short_url_accepted(self):
        status, created = self._sv_json(
            "POST",
            "/api/works",
            {
                "title": "Valid Short URL Work",
                "source_kind": "video",
                "source_url": "https://youtu.be/dQw4w9WgXcQ",
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("Valid Short URL Work", self._work_titles())
        status, work = self._sv_json("GET", f"/api/works/{created['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(work.get("provider"), "youtube")
        self.assertEqual(work.get("provider_id"), "dQw4w9WgXcQ")

    # -- Folder destination semantics: UI-visible default vs. stored folder -

    def test_default_folder_destination_is_uncategorized(self):
        status, created = self._sv_json(
            "POST",
            "/api/works",
            {"title": "Default Folder Work", "folder_id": None},
        )
        self.assertEqual(status, 200)
        status, work = self._sv_json("GET", f"/api/works/{created['id']}")
        self.assertEqual(status, 200)
        status, folders = self._sv_json("GET", "/api/folders")
        self.assertEqual(status, 200)
        folder_row = next((f for f in folders if f.get("id") == work.get("folder_id")), None)
        self.assertIsNotNone(folder_row)
        self.assertEqual(folder_row.get("title"), "Uncategorized")


    # -- Catalog freshness: an ETag must change whenever the body can ------

    def _conditional_get(self, path, etag=None):
        """Returns (status, etag, parsed_body). 304 bodies are empty."""
        req = urllib.request.Request(f"{self._base_url}{path}")
        if etag:
            req.add_header("If-None-Match", etag)
        try:
            with urllib.request.urlopen(req) as res:
                raw = res.read().decode()
                return res.status, res.headers.get("ETag"), (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            exc.read()
            return exc.code, exc.headers.get("ETag"), None

    def test_work_tag_sync_http_replay_and_options_etag(self):
        import uuid
        db = server_module.db
        work = db.add_work("HTTP Sync")
        tag = db.add_tag("HTTP Sync")["id"]
        path = f"/api/works/{work}/tag-options"
        status, etag, before = self._conditional_get(path)
        self.assertEqual(status, 200)
        op = dict(op_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()), operation="ADD_WORK_TAG",
                  entity_type="work", entity_id=work, payload={"tag_id": tag}, base_revision=0,
                  occurred_at="2026-09-11T00:00:00Z", created_at="2026-09-11T00:00:00Z", depends_on=[])
        result = self._sv_json("POST", "/api/sync/operations", op)
        self.assertEqual(result[0], 200)
        self.assertEqual(self._sv_json("POST", "/api/sync/operations", op), result)
        self.assertEqual(self._conditional_get(path, etag)[0], 200)
        op.update(op_id=str(uuid.uuid4()), operation="REMOVE_WORK_TAG")
        result = self._sv_json("POST", "/api/sync/operations", op)
        self.assertEqual(result[0], 409)
        self.assertEqual(self._sv_json("POST", "/api/sync/operations", op), result)

    def test_research_note_ack_includes_research_refs_not_body(self):
        """Durable Research Note ACK keeps the body omitted but carries the
        compact research_refs map so the live Work preview can resolve links
        without a second Work GET."""
        import uuid
        db = server_module.db
        work = db.add_work("Note ACK Refs")
        concept = db.add_concept("ACK Ref Concept")
        body = "See [[concept:ACK Ref Concept]]."
        op = dict(
            op_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()),
            operation="SET_WORK_RESEARCH_NOTE", entity_type="work", entity_id=work,
            payload={"text": body}, base_revision=0,
            occurred_at="2026-09-16T00:00:00Z", created_at="2026-09-16T00:00:00Z",
            depends_on=[],
        )
        status, result = self._sv_json("POST", "/api/sync/operations", op)
        self.assertEqual(status, 200)
        self.assertEqual(result.get("code"), "ACKNOWLEDGED")
        self.assertTrue(result.get("value_omitted"))
        self.assertNotIn("text", result)
        self.assertNotIn(body, json.dumps(result))
        refs = result.get("research_refs") or {}
        names = [c.get("name") for c in (refs.get("concepts") or [])]
        self.assertIn("ACK Ref Concept", names)
        self.assertEqual(concept, next(
            c["id"] for c in refs["concepts"] if c.get("name") == "ACK Ref Concept"))

    def test_tags_etag_representation_contract(self):
        db = server_module.db
        tag = db.add_tag("ETag ABCD")["id"]
        target = db.add_tag("ETag Target")["id"]
        work = db.add_work("ETag relationship")
        def check(mutate, changes=True):
            status, before, body = self._conditional_get("/api/tags")
            self.assertEqual(status, 200)
            mutate()
            status, after, result = self._conditional_get("/api/tags", before)
            self.assertEqual(status, 200 if changes else 304)
            if changes:
                self.assertNotEqual(before, after)
                self.assertNotEqual(body, result)
        check(lambda: db.execute_query("UPDATE tags SET name = ? WHERE id = ?", ("ETag WXYZ", tag)))
        check(lambda: db.execute_query("UPDATE tags SET color = ? WHERE id = ?", ("#abcdef", tag)))
        check(lambda: db.add_tag_alias(tag, "ETag FOUR"))
        check(lambda: db.execute_query("UPDATE tag_aliases SET alias = ? WHERE tag_id = ?", ("ETag FIVE", tag)))
        check(lambda: db.delete_tag_alias(tag, "ETag FIVE"))
        check(lambda: db.add_tag("ETag Created"))
        check(lambda: db.add_tag_to_work(work, tag), False)
        check(lambda: db.remove_tag_from_work(work, tag), False)
        check(lambda: db.merge_tags_into(tag, target))
        check(lambda: db.delete_tag(target))

    def test_folders_etag_changes_when_a_work_moves_between_folders(self):
        """Moving a Work changes both folders' work_count, so the cached
        catalog is stale -- a 304 here would republish stale counts into the
        client's folders:index after its offline domain was invalidated.

        The total folder_files row count is IDENTICAL across a move, which is
        exactly why a count-based ETag missed it.
        """
        status, src = self._sv_json("POST", "/api/folders", {"title": "ETag Move Source"})
        self.assertEqual(status, 200)
        status, dst = self._sv_json("POST", "/api/folders", {"title": "ETag Move Dest"})
        self.assertEqual(status, 200)
        status, work = self._sv_json(
            "POST", "/api/works", {"title": "ETag Move Work", "folder_id": src["id"]}
        )
        self.assertEqual(status, 200)

        status, etag1, body1 = self._conditional_get("/api/folders")
        self.assertEqual(status, 200)
        self.assertTrue(etag1)

        def count_for(body, folder_id):
            row = next((f for f in body if f.get("id") == folder_id), None)
            self.assertIsNotNone(row, folder_id)
            return row.get("work_count")

        self.assertEqual(count_for(body1, src["id"]), 1)
        self.assertEqual(count_for(body1, dst["id"]), 0)

        status, _ = self._sv_json(
            "PATCH", f"/api/works/{work['id']}", {"folder_id": dst["id"]}
        )
        self.assertEqual(status, 200)

        status, etag2, body2 = self._conditional_get("/api/folders", etag=etag1)
        self.assertNotEqual(status, 304, "stale folder catalog served as Not Modified")
        self.assertEqual(status, 200)
        self.assertNotEqual(etag2, etag1)
        self.assertEqual(count_for(body2, src["id"]), 0)
        self.assertEqual(count_for(body2, dst["id"]), 1)

    def test_folders_etag_changes_on_bulk_move_folder(self):
        """bulk move_folder writes folder_files on its own path."""
        status, src = self._sv_json("POST", "/api/folders", {"title": "ETag Bulk Source"})
        self.assertEqual(status, 200)
        status, dst = self._sv_json("POST", "/api/folders", {"title": "ETag Bulk Dest"})
        self.assertEqual(status, 200)
        status, work = self._sv_json(
            "POST", "/api/works", {"title": "ETag Bulk Work", "folder_id": src["id"]}
        )
        self.assertEqual(status, 200)

        status, etag1, _ = self._conditional_get("/api/folders")
        self.assertEqual(status, 200)

        status, _ = self._sv_json(
            "POST",
            "/api/works/bulk",
            {"action": "move_folder", "work_ids": [work["id"]], "folder_id": dst["id"]},
        )
        self.assertEqual(status, 200)

        status, etag2, body2 = self._conditional_get("/api/folders", etag=etag1)
        self.assertNotEqual(status, 304, "stale folder catalog served after bulk move")
        self.assertEqual(status, 200)
        self.assertNotEqual(etag2, etag1)
        dst_row = next(f for f in body2 if f.get("id") == dst["id"])
        self.assertEqual(dst_row.get("work_count"), 1)

    def test_folders_etag_follows_every_index_field_within_one_second(self):
        """CURRENT_TIMESTAMP has one-second resolution, so a revision probe
        built on MAX(updated_at) cannot see a change made in the same second
        as the previous one. Each mutation here runs back-to-back."""
        status, parent = self._sv_json("POST", "/api/folders", {"title": "ETag Fields Parent"})
        self.assertEqual(status, 200)
        # A weak ETag identifies the representation, so an operation that
        # restores an earlier body legitimately restores its ETag. What must
        # never happen is the ETag standing still across a body change, so
        # each step is compared against the one immediately before it.
        state = {"etag": None}

        def snapshot(label):
            status, etag, _ = self._conditional_get("/api/folders")
            self.assertEqual(status, 200, label)
            self.assertNotEqual(etag, state["etag"], "ETag did not change after %s" % label)
            state["etag"] = etag
            return etag

        snapshot("baseline")
        # title
        status, _ = self._sv_json("PATCH", f"/api/folders/{parent['id']}", {"title": "ETag Fields Renamed"})
        self.assertEqual(status, 200)
        snapshot("title change")
        # description
        status, _ = self._sv_json("PATCH", f"/api/folders/{parent['id']}", {"description": "desc"})
        self.assertEqual(status, 200)
        snapshot("description change")
        # child_count (a new subfolder changes the parent's rendered count)
        status, child = self._sv_json(
            "POST", "/api/folders", {"title": "ETag Fields Child", "parent_id": parent["id"]}
        )
        self.assertEqual(status, 200)
        snapshot("subfolder created")
        # parent_id / child_count (reparent to root)
        status, _ = self._sv_json("PATCH", f"/api/folders/{child['id']}", {"parent_id": None})
        self.assertEqual(status, 200)
        snapshot("reparent")
        # work_count via creation into the folder
        status, work = self._sv_json(
            "POST", "/api/works", {"title": "ETag Fields Work", "folder_id": parent["id"]}
        )
        self.assertEqual(status, 200)
        snapshot("work filed")
        # work_count via deletion
        status, _ = self._sv_json("DELETE", f"/api/works/{work['id']}")
        self.assertEqual(status, 200)
        snapshot("work deleted")
        # folder delete
        status, _ = self._sv_json("DELETE", f"/api/folders/{child['id']}")
        self.assertEqual(status, 200)
        snapshot("folder deleted")

    # -- Tag mutations report what they staled ----------------------------

    def test_tag_delete_reports_affected_works_and_folders(self):
        status, folder = self._sv_json("POST", "/api/folders", {"title": "Tag Del Folder"})
        self.assertEqual(status, 200)
        status, work = self._sv_json("POST", "/api/works", {"title": "Tag Del Work"})
        self.assertEqual(status, 200)
        status, other = self._sv_json("POST", "/api/works", {"title": "Tag Del Untouched"})
        self.assertEqual(status, 200)
        status, tag = self._sv_json("POST", "/api/tags", {"name": "TagDelSubject"})
        self.assertEqual(status, 200)
        self.assertEqual(
            self._sv_json("POST", f"/api/works/{work['id']}/tags", {"tag_id": tag["id"]})[0], 200
        )
        self.assertEqual(
            self._sv_json("POST", f"/api/folders/{folder['id']}/tags", {"tag_id": tag["id"]})[0], 200
        )

        status, out = self._sv_json("DELETE", f"/api/tags/{tag['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(out.get("affected_work_ids"), [work["id"]])
        self.assertEqual(out.get("affected_folder_ids"), [folder["id"]])
        self.assertNotIn(other["id"], out.get("affected_work_ids") or [])

    def test_tag_merge_reports_source_linked_works_and_folders(self):
        status, folder = self._sv_json("POST", "/api/folders", {"title": "Tag Merge Folder"})
        self.assertEqual(status, 200)
        status, work = self._sv_json("POST", "/api/works", {"title": "Tag Merge Work"})
        self.assertEqual(status, 200)
        status, target_only = self._sv_json("POST", "/api/works", {"title": "Tag Merge TargetOnly"})
        self.assertEqual(status, 200)
        status, source = self._sv_json("POST", "/api/tags", {"name": "MergeSource"})
        self.assertEqual(status, 200)
        status, target = self._sv_json("POST", "/api/tags", {"name": "MergeTarget"})
        self.assertEqual(status, 200)
        for path in (f"/api/works/{work['id']}/tags", f"/api/folders/{folder['id']}/tags"):
            self.assertEqual(self._sv_json("POST", path, {"tag_id": source["id"]})[0], 200)
        # A Work carrying ONLY the target is not affected by the merge.
        self.assertEqual(
            self._sv_json("POST", f"/api/works/{target_only['id']}/tags", {"tag_id": target["id"]})[0], 200
        )

        status, out = self._sv_json(
            "POST", "/api/tags/merge",
            {"source_tag_id": source["id"], "target_tag_id": target["id"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(out.get("affected_work_ids"), [work["id"]])
        self.assertEqual(out.get("affected_folder_ids"), [folder["id"]])
        self.assertNotIn(target_only["id"], out.get("affected_work_ids") or [])

    def test_tag_merge_reports_entities_that_already_carry_both(self):
        """Dedupe case: the source link is dropped, so the rendered tag list
        changes even though the target was already present."""
        status, work = self._sv_json("POST", "/api/works", {"title": "Tag Merge Both"})
        self.assertEqual(status, 200)
        status, source = self._sv_json("POST", "/api/tags", {"name": "BothSource"})
        self.assertEqual(status, 200)
        status, target = self._sv_json("POST", "/api/tags", {"name": "BothTarget"})
        self.assertEqual(status, 200)
        for tid in (source["id"], target["id"]):
            self.assertEqual(
                self._sv_json("POST", f"/api/works/{work['id']}/tags", {"tag_id": tid})[0], 200
            )

        status, out = self._sv_json(
            "POST", "/api/tags/merge",
            {"source_tag_id": source["id"], "target_tag_id": target["id"]},
        )
        self.assertEqual(status, 200)
        self.assertIn(work["id"], out.get("affected_work_ids") or [])
        status, detail = self._sv_json("GET", f"/api/works/{work['id']}")
        self.assertEqual(status, 200)
        names = sorted((t.get("name") or "") for t in (detail.get("tags") or []))
        self.assertEqual(names, ["BothTarget"])

    def test_failed_tag_merge_changes_no_canonical_relationship(self):
        status, work = self._sv_json("POST", "/api/works", {"title": "Tag Merge Fail"})
        self.assertEqual(status, 200)
        status, source = self._sv_json("POST", "/api/tags", {"name": "FailSource"})
        self.assertEqual(status, 200)
        self.assertEqual(
            self._sv_json("POST", f"/api/works/{work['id']}/tags", {"tag_id": source["id"]})[0], 200
        )

        status, out = self._sv_json(
            "POST", "/api/tags/merge",
            {"source_tag_id": source["id"], "target_tag_id": "T-does-not-exist"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(out.get("affected_work_ids"), None)
        # Nothing canonical moved, so the client's caches stay eligible.
        status, detail = self._sv_json("GET", f"/api/works/{work['id']}")
        self.assertEqual(status, 200)
        self.assertEqual([t.get("name") for t in (detail.get("tags") or [])], ["FailSource"])

    # -- Browse projection freshness --------------------------------------

    def _browse_paths(self):
        return {
            "works-browse": "/api/works?projection=browse",
            "recent": "/api/recent",
            "recently-added": "/api/recently-added",
        }

    def _assert_browse_etag_follows(self, label, mutate, *, expect_change=True):
        """Assert the named projection's ETag tracks its own representation."""
        path = self._browse_paths()[label]
        status, etag1, body1 = self._conditional_get(path)
        self.assertEqual(status, 200, label)
        mutate()
        status, etag2, body2 = self._conditional_get(path, etag=etag1)
        if expect_change:
            self.assertNotEqual(status, 304, "%s served stale as Not Modified" % label)
            self.assertEqual(status, 200, label)
            self.assertNotEqual(etag2, etag1, label)
            self.assertNotEqual(body1, body2, label)
        else:
            self.assertEqual(status, 304, "%s changed when it should not have" % label)

    def _make_browse_work(self, title, **extra):
        payload = {"title": title}
        payload.update(extra)
        status, work = self._sv_json("POST", "/api/works", payload)
        self.assertEqual(status, 200)
        return work["id"]

    def test_works_browse_etag_follows_every_rendered_field(self):
        wid = self._make_browse_work("Browse ETag Work", status="Not Started")
        status, person = self._sv_json(
            "POST", "/api/persons", {"first_name": "Bro", "last_name": "Wser"}
        )
        self.assertEqual(status, 200)
        status, folder = self._sv_json("POST", "/api/folders", {"title": "Browse ETag Folder"})
        self.assertEqual(status, 200)

        self._assert_browse_etag_follows(
            "works-browse",
            lambda: self._sv_json("PATCH", f"/api/works/{wid}", {"title": "Browse ETag Retitled"}),
        )
        self._assert_browse_etag_follows(
            "works-browse",
            lambda: self._sv_json("PATCH", f"/api/works/{wid}", {"status": "Completed"}),
        )
        self._assert_browse_etag_follows(
            "works-browse",
            # (the default doc_type is already "article", so use another one)
            lambda: self._sv_json("PATCH", f"/api/works/{wid}", {"doc_type": "book"}),
        )
        self._assert_browse_etag_follows(
            "works-browse",
            lambda: self._sv_json(
                "POST", "/api/roles",
                {"person_id": person["id"], "work_id": wid, "role_type": "Author"},
            ),
        )
        # A Person rename changes the rendered credit line with no Work write.
        self._assert_browse_etag_follows(
            "works-browse",
            lambda: self._sv_json(
                "PATCH", f"/api/persons/{person['id']}",
                {"first_name": "Bro", "last_name": "Renamed"},
            ),
        )
        # The browse catalog deliberately carries no folder_id, so a move is
        # correctly invisible to it -- and must therefore NOT bust its ETag.
        self._assert_browse_etag_follows(
            "works-browse",
            lambda: self._sv_json("PATCH", f"/api/works/{wid}", {"folder_id": folder["id"]}),
            expect_change=False,
        )

    def test_works_browse_etag_follows_managed_pdf_file_size(self):
        import backend.server as _sm

        pdfs_dir = _sm.db.storage.pdfs_dir
        os.makedirs(pdfs_dir, exist_ok=True)
        name = "browse-etag-size.pdf"
        with open(os.path.join(pdfs_dir, name), "wb") as f:
            f.write(b"%PDF-1.4\n" + b"a" * 100)
        self._make_browse_work("Browse Size Work", file_path=f"/api/pdfs/{name}")

        def grow():
            with open(os.path.join(pdfs_dir, name), "ab") as f:
                f.write(b"b" * 5000)

        # file_size_bytes is read from disk at serialization time, so no SQL
        # row changes at all -- a table-revision ETag could never see this.
        self._assert_browse_etag_follows("works-browse", grow)

    def test_recent_etag_follows_opening_a_work(self):
        first = self._make_browse_work("Recent ETag One")
        second = self._make_browse_work("Recent ETag Two")
        # Opening is the explicit event, never the GET.
        self._sv_json("POST", f"/api/works/{first}/opened", {})
        self._assert_browse_etag_follows(
            "recent", lambda: self._sv_json("POST", f"/api/works/{second}/opened", {})
        )
        # Re-opening the one already at the top still reorders nothing but does
        # change its timestamp; the representation decides, not a count.
        status, etag1, body1 = self._conditional_get("/api/recent")
        self.assertEqual(status, 200)
        self.assertTrue(any(r.get("id") == second for r in body1))

    def test_recent_etag_follows_display_change_of_a_listed_work(self):
        wid = self._make_browse_work("Recent Display Work")
        self._sv_json("POST", f"/api/works/{wid}/opened", {})
        self._assert_browse_etag_follows(
            "recent",
            lambda: self._sv_json("PATCH", f"/api/works/{wid}", {"title": "Recent Display Renamed"}),
        )

    def test_recently_added_etag_follows_create_and_member_edit(self):
        self._assert_browse_etag_follows(
            "recently-added", lambda: self._make_browse_work("Recently Added ETag New")
        )
        wid = self._make_browse_work("Recently Added Member")
        self._assert_browse_etag_follows(
            "recently-added",
            lambda: self._sv_json("PATCH", f"/api/works/{wid}", {"status": "Paused"}),
        )
        # It carries folder_id for local search, so a move IS visible here.
        status, folder = self._sv_json("POST", "/api/folders", {"title": "RA ETag Folder"})
        self.assertEqual(status, 200)
        self._assert_browse_etag_follows(
            "recently-added",
            lambda: self._sv_json("PATCH", f"/api/works/{wid}", {"folder_id": folder["id"]}),
        )

    def test_browse_projections_are_deterministically_ordered(self):
        """Recent/Recently-added tie-break on id, so a cached copy and a fresh
        read agree even when several rows share a one-second timestamp."""
        ids = [self._make_browse_work(f"Order Probe {i}") for i in range(5)]
        for wid in ids:
            self._sv_json("POST", f"/api/works/{wid}/opened", {})
        for path in ("/api/recent", "/api/recently-added", "/api/works?projection=browse"):
            first = self._conditional_get(path)[2]
            second = self._conditional_get(path)[2]
            self.assertEqual([r["id"] for r in first], [r["id"] for r in second], path)

    def test_browse_projection_is_compact(self):
        wid = self._make_browse_work("Compact Probe", abstract="x" * 5000)
        status, browse, _ = 200, self._conditional_get("/api/works?projection=browse")[2], None
        row = next(r for r in browse if r["id"] == wid)
        # The full abstract must never ship to a client that renders 100 chars.
        self.assertNotIn("abstract", row)
        self.assertIn("abstract_excerpt", row)
        self.assertLessEqual(len(row["abstract_excerpt"]), 100)
        # The default /api/works contract is untouched for its other callers.
        status, full, _ = 200, self._conditional_get("/api/works")[2], None
        full_row = next(r for r in full if r["id"] == wid)
        self.assertIn("abstract", full_row)
        self.assertEqual(len(full_row["abstract"]), 5000)

    # -- Pure reads / explicit open event -----------------------------------

    def test_get_work_never_mutates_last_opened_at(self):
        """A GET must be pure. It used to stamp last_opened_at, which made
        every internal refresh (tag, folder, playlist, role, metadata, notes)
        silently reorder Recent behind the UI's back."""
        wid = self._make_browse_work("Pure Read Work")
        status, _etag, before = self._conditional_get("/api/recent")
        self.assertEqual(status, 200)

        for _ in range(3):
            self.assertEqual(self._sv_json("GET", f"/api/works/{wid}")[0], 200)

        status, etag, after = self._conditional_get("/api/recent")
        self.assertEqual(status, 200)
        self.assertEqual([r["id"] for r in after], [r["id"] for r in before])
        self.assertFalse(any(r.get("id") == wid for r in after))

    def test_internal_refresh_shaped_mutations_do_not_record_an_open(self):
        """Everything the UI does after a save reads the Work back. None of it
        may make the Work look "recently opened"."""
        wid = self._make_browse_work("Refresh Shape Work")
        status, folder = self._sv_json("POST", "/api/folders", {"title": "Refresh Shape Folder"})
        self.assertEqual(status, 200)
        status, tag = self._sv_json("POST", "/api/tags", {"name": "RefreshShapeTag"})
        self.assertEqual(status, 200)
        status, person = self._sv_json(
            "POST", "/api/persons", {"first_name": "Ref", "last_name": "Shape"}
        )
        self.assertEqual(status, 200)

        # Each pair is "canonical mutation, then the refresh read the UI does".
        for label, mutate in (
            ("tag add", lambda: self._sv_json(
                "POST", f"/api/works/{wid}/tags", {"tag_id": tag["id"]})),
            ("folder move", lambda: self._sv_json(
                "PATCH", f"/api/works/{wid}", {"folder_id": folder["id"]})),
            ("role add", lambda: self._sv_json(
                "POST", "/api/roles",
                {"person_id": person["id"], "work_id": wid, "role_type": "Author"})),
            ("metadata save", lambda: self._sv_json(
                "PATCH", f"/api/works/{wid}", {"title": "Refresh Shape Renamed"})),
            ("tag remove", lambda: self._sv_json(
                "DELETE", f"/api/works/{wid}/tags/{tag['id']}")),
        ):
            with self.subTest(step=label):
                mutate()
                self._sv_json("GET", f"/api/works/{wid}")   # the refresh read
                status, _etag, recent = self._conditional_get("/api/recent")
                self.assertEqual(status, 200)
                self.assertFalse(
                    any(r.get("id") == wid for r in recent),
                    "%s made the Work look recently opened" % label,
                )

    def test_open_event_is_idempotent_in_membership_and_404s_for_unknown(self):
        wid = self._make_browse_work("Open Event Work")
        self.assertEqual(self._sv_json("POST", f"/api/works/{wid}/opened", {})[0], 200)
        status, _etag, first = self._conditional_get("/api/recent")
        self.assertEqual(status, 200)
        self.assertEqual(len([r for r in first if r["id"] == wid]), 1)
        self.assertEqual(self._sv_json("POST", f"/api/works/{wid}/opened", {})[0], 200)
        status, _etag, second = self._conditional_get("/api/recent")
        self.assertEqual(len([r for r in second if r["id"] == wid]), 1)
        self.assertEqual(self._sv_json("POST", "/api/works/W-nope/opened", {})[0], 404)

    def test_folders_etag_is_stable_when_nothing_changed(self):
        """The invariant is one-directional: revalidation must still work."""
        self._sv_json("POST", "/api/folders", {"title": "ETag Stable Folder"})
        status, etag1, _ = self._conditional_get("/api/folders")
        self.assertEqual(status, 200)
        status, _etag2, _ = self._conditional_get("/api/folders", etag=etag1)
        self.assertEqual(status, 304)


    def _response_headers(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self._test_port, timeout=5)
        conn.request("GET", path)
        response = conn.getresponse()
        response.read()
        headers = dict(response.getheaders())
        status = response.status
        conn.close()
        return status, headers

    def test_every_response_forbids_mime_sniffing(self):
        """JSON bodies echo library content back to the page. Without nosniff a
        crafted title can be sniffed as HTML and run in the app's own origin, so
        the header belongs on every response, not on one streaming endpoint."""
        db = server_module.db
        db.add_work("<html><body>sniffable</body></html>")
        for path in ("/api/works", "/", "/index.html", "/api/no-such-endpoint"):
            status, headers = self._response_headers(path)
            self.assertIn(status, (200, 304, 404), path)
            self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff", path)

    def test_pdf_upload_writes_only_inside_the_managed_pdf_directory(self):
        """The upload write resolves through the same containment helper as
        every other managed-PDF path, so a file_name from the request body can
        never name a destination outside pdfs_dir."""
        pdfs_dir = server_module.pdfs_dir
        parent = os.path.dirname(os.path.realpath(pdfs_dir))
        before = set(os.listdir(parent))
        status, created = self._sv_json("POST", "/api/works", {
            "title": "Traversal Upload",
            "file_name": "../../escaped.pdf",
            "file_b64": base64.b64encode(b"%PDF-1.4\n%%EOF\n").decode("utf-8"),
        })
        self.assertEqual(status, 200, created)
        row = self._sv_json("GET", "/api/works/" + created["id"], None)[1]
        stored = row["file_path"]
        self.assertTrue(stored.startswith("/api/pdfs/"), stored)
        name = stored[len("/api/pdfs/"):]
        self.assertNotIn("/", name)
        self.assertTrue(
            os.path.isfile(os.path.join(os.path.realpath(pdfs_dir), name)), stored)
        self.assertEqual(set(os.listdir(parent)) - before, set())

    def _upload_work(self, title, file_name, body=b"%PDF-1.4\n%%EOF\n"):
        status, created = self._sv_json("POST", "/api/works", {
            "title": title,
            "file_name": file_name,
            "file_b64": base64.b64encode(body).decode("utf-8"),
        })
        return status, created

    def test_two_same_second_uploads_do_not_share_one_managed_pdf(self):
        """A managed PDF is never written over. The name used to be
        `<seconds>_<sanitized>`, so two uploads of `paper.pdf` inside one second
        resolved to the same path: the second write replaced the first Work's
        bytes while both rows still pointed at it."""
        first_body = b"%PDF-1.4\n%% FIRST-WORK-CONTENT\n%%EOF\n"
        second_body = b"%PDF-1.4\n%% SECOND-WORK-CONTENT\n%%EOF\n"
        # Freeze the clock the minter reads. Left to real time this crosses a
        # second boundary now and then, and on those runs the timestamp-only
        # implementation also produced two names — so the test would pass
        # against the very bug it exists for.
        frozen = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)

        class _FrozenDatetime:
            @staticmethod
            def now(tz=None):
                return frozen

        with patch.object(db_manager_module, "datetime", _FrozenDatetime):
            minted = [db_manager_module.mint_managed_pdf_filename("paper.pdf") for _ in range(2)]
            stamps = {name.split("_", 1)[0] for name in minted}
            self.assertEqual(len(stamps), 1, "both names carry one timestamp: %s" % minted)
            status_a, work_a = self._upload_work("Collide A", "paper.pdf", first_body)
            status_b, work_b = self._upload_work("Collide B", "paper.pdf", second_body)
        self.assertEqual((status_a, status_b), (200, 200))

        path_a = self._sv_json("GET", "/api/works/" + work_a["id"], None)[1]["file_path"]
        path_b = self._sv_json("GET", "/api/works/" + work_b["id"], None)[1]["file_path"]
        self.assertNotEqual(path_a, path_b, "each Work owns its own managed PDF")

        root = os.path.realpath(server_module.pdfs_dir)
        for stored, expected in ((path_a, b"FIRST-WORK-CONTENT"), (path_b, b"SECOND-WORK-CONTENT")):
            with open(os.path.join(root, stored[len("/api/pdfs/"):]), "rb") as handle:
                self.assertIn(expected, handle.read(), stored)

    def test_a_failed_pdf_write_leaves_no_orphan_in_the_managed_directory(self):
        """`open(..., "xb")` can succeed and the write or fsync still fail — a
        full disk is both the likeliest cause and the likeliest to be retried.
        No Work row would reference the partial file, so it has to go."""
        root = os.path.realpath(server_module.pdfs_dir)
        before = set(os.listdir(root))
        real_open = open

        class _FullDisk:
            """Open succeeds — `O_CREAT|O_EXCL` is atomic, so a create either
            happens or does not. The disk fills on the write that follows."""

            def __init__(self, handle):
                self._handle = handle

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._handle.close()
                return False

            def write(self, _data):
                raise OSError(28, "No space left on device")

            def __getattr__(self, name):
                return getattr(self._handle, name)

        def failing_open(path, mode="r", *args, **kwargs):
            handle = real_open(path, mode, *args, **kwargs)
            return _FullDisk(handle) if "x" in mode else handle

        with patch("builtins.open", failing_open):
            status, body = self._sv_json("POST", "/api/works", {
                "title": "Disk Full Upload",
                "file_name": "paper.pdf",
                "file_b64": base64.b64encode(b"%PDF-1.4\n%%EOF\n").decode("utf-8"),
            })
        self.assertEqual(status, 500, body)
        self.assertEqual(
            set(os.listdir(root)) - before, set(), "a failed write left a managed file behind"
        )

    def test_a_rejected_work_create_leaves_no_stored_pdf_behind(self):
        """The upload is stored before the row exists, so a create refused
        afterwards would leave a managed PDF nothing references — and a client
        retrying an invalid request would accumulate them."""
        root = os.path.realpath(server_module.pdfs_dir)
        before = set(os.listdir(root))
        # A file-backed Work classified as PDF, carrying video-only identity:
        # add_work() refuses this at the creation boundary.
        status, body = self._sv_json("POST", "/api/works", {
            "title": "Rejected upload",
            "file_name": "paper.pdf",
            "file_b64": base64.b64encode(b"%PDF-1.4\n%%EOF\n").decode("utf-8"),
            "provider": "youtube",
        })
        self.assertEqual(status, 400, body)
        self.assertEqual(
            set(os.listdir(root)) - before,
            set(),
            "a refused create left its uploaded PDF behind",
        )

    def test_a_rejected_create_never_removes_a_pdf_it_did_not_upload(self):
        """The rollback is only ever allowed to touch a name this request just
        minted. A caller referencing an existing managed PDF is pointing at
        another Work's bytes, and removing those is the data loss the exclusive
        write exists to prevent."""
        status, owner = self._upload_work("Owns The PDF", "shared.pdf")
        self.assertEqual(status, 200, owner)
        stored = self._sv_json("GET", "/api/works/" + owner["id"], None)[1]["file_path"]
        root = os.path.realpath(server_module.pdfs_dir)
        name = stored[len("/api/pdfs/"):]
        self.assertTrue(os.path.isfile(os.path.join(root, name)))

        # Same refusal, but the PDF came from file_path rather than an upload.
        status, body = self._sv_json("POST", "/api/works", {
            "title": "Rejected reference",
            "file_path": stored,
            "provider": "youtube",
        })
        self.assertEqual(status, 400, body)
        self.assertTrue(
            os.path.isfile(os.path.join(root, name)),
            "the rollback deleted a PDF this request did not upload",
        )

    def test_upload_filenames_are_sanitized_without_being_mangled(self):
        """The old filter deleted disallowed characters, so
        `../../etc/passwd` became the literal name `....etcpasswd`. Taking the
        basename first keeps a readable name and makes containment explicit
        rather than a side effect of which characters happen to be allowed."""
        root = os.path.realpath(server_module.pdfs_dir)
        cases = (
            ("../../../../etc/passwd", "passwd.pdf"),
            ("***", "file.pdf"),
            ("..", "file.pdf"),
            ("report.PDF", "report.pdf"),
            ("a" * 300 + ".pdf", None),
        )
        for raw, expected_suffix in cases:
            status, created = self._upload_work("Upload " + raw[:12], raw)
            self.assertEqual(status, 200, "%r -> %s" % (raw, created))
            stored = self._sv_json("GET", "/api/works/" + created["id"], None)[1]["file_path"]
            name = stored[len("/api/pdfs/"):]
            self.assertNotIn("/", name, raw)
            self.assertTrue(name.endswith(".pdf"), name)
            # Whatever the caller sent, the component fits what a filesystem takes.
            self.assertLessEqual(len(name.encode("utf-8")), 255, raw)
            self.assertTrue(os.path.isfile(os.path.join(root, name)), stored)
            if expected_suffix:
                self.assertTrue(name.endswith(expected_suffix), "%r -> %s" % (raw, name))

    def test_oembed_stays_best_effort_for_malformed_unicode(self):
        """`quote()` raises UnicodeEncodeError on a lone surrogate, and
        `json.loads` happily produces one from `\\ud800`. Percent-encoding the
        lookup URL must not turn an optional metadata fetch into a failed
        request, so the encode belongs inside the helper's own try."""
        lone_surrogate = json.loads('"https://www.youtube.com/watch?v=\\ud800"')
        self.assertIsNone(server_module._fetch_youtube_oembed(lone_surrogate))

        # And the same value through the real creation endpoint. `json.dumps`
        # escapes the surrogate to ASCII, so it survives the wire and the
        # server's own `json.loads` hands it back to the enrichment path.
        status, body = self._sv_json("POST", "/api/works", {
            "title": "Malformed Source URL",
            "source_url": lone_surrogate,
        })
        self.assertNotEqual(status, 500, body)
        self.assertIn(status, (200, 400), body)
        if status == 200:
            self.assertEqual(
                self._sv_json("GET", "/api/works/" + body["id"], None)[0], 200
            )

    def test_oembed_url_is_a_percent_encoded_query_value(self):
        """Appended raw, a YouTube URL's own `&`/`#` both truncate the lookup
        and let the request body append parameters to the outbound query."""
        seen = []

        class _FakeResponse:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def read(self_inner):
                return b'{"title": "T"}'

        def fake_urlopen(req, timeout=None):
            seen.append(req.full_url)
            return _FakeResponse()

        with patch.object(server_module, "urlopen", fake_urlopen):
            meta = server_module._fetch_youtube_oembed(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1&t=30")

        self.assertEqual(meta, {"title": "T"})
        self.assertEqual(len(seen), 1)
        requested = seen[0]
        prefix = "https://www.youtube.com/oembed?format=json&url="
        self.assertTrue(requested.startswith(prefix), requested)
        value = requested[len(prefix):]
        self.assertNotIn("&", value)
        self.assertNotIn("#", value)
        self.assertEqual(
            urllib.parse.unquote(value),
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1&t=30")


if __name__ == '__main__':
    unittest.main()
