import unittest
import threading
import time
import socket
import urllib.request
import urllib.parse
import urllib.error
import json
import os
import sys
import base64
import tempfile

# Add the parent directory to sys.path so we can import 'backend'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db_manager import PRKSDatabase

# Ensure imports/run never write to container storage (/data).
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("PRKS_TESTING", "1")
os.environ.setdefault("PRKS_STORAGE", os.path.join(_PROJECT_DIR, "data_testing"))

import backend.server as server_module

unittest.defaultTestLoader.sortTestMethodsUsing = None


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
        cls.test_db_path = os.path.join(cls._tmpdir, "test_server_prks_data.db")
            
        schema_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend", "db_schema.sql")
        cls.test_db = PRKSDatabase(db_path=cls.test_db_path, schema_path=schema_path)
        
        # Patch the server's db instance
        server_module.db = cls.test_db

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
            data = json.loads(res.read().decode())
            # Depending on test order, it might not be empty, so we just check it's a list
            self.assertIsInstance(data, list)

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
        with urllib.request.urlopen(req) as res:
            w_id = json.loads(res.read().decode())["id"]
        
        # Patch
        patch_payload = {"title": "Patched Status", "status": "Completed"}
        req2 = urllib.request.Request(f"{self._base_url}/api/works/{w_id}", data=json.dumps(patch_payload).encode(), method="PATCH")
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
        with urllib.request.urlopen(req) as res:
            w_id = json.loads(res.read().decode())["id"]

        patch_payload = {"private_notes": "Thesis ch. 3 — check with Mark"}
        req2 = urllib.request.Request(
            f"{self._base_url}/api/works/{w_id}",
            data=json.dumps(patch_payload).encode(),
            method="PATCH",
        )
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
        pdf_bytes = b"%PDF-1.4\n%PRKS\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
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
        self.assertIn(b"%PDF-1.4", got[:32])

    def test_9_pdf_overwrite_endpoint(self):
        original = b"%PDF-1.4\n%ORIG\n%%EOF\n"
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

        updated = b"%PDF-1.4\n%NEW\n%%EOF\n"
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
        self.assertIn(b"%NEW", got)

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
            self.assertEqual(res.status, 200)
            raw = res.read().decode()
        body = json.loads(raw)
        self.assertIn("id", body)
        self.assertIn("status", body)
        self.assertEqual(body["status"], "skipped")

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

    def test_15_post_concepts_creates_mentioned_role(self):
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
            self.assertEqual(rc.status, 200)
            body = json.loads(rc.read().decode())
        self.assertEqual(body.get("status"), "processed")

        rows = self.__class__.test_db.execute_query(
            "SELECT role_type FROM roles WHERE person_id = ? AND work_id = ?",
            (p_id, w_id),
        )
        types = {r["role_type"] for r in rows}
        self.assertIn("Mentioned", types)

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
        self.assertEqual(cm.exception.code, 400)
        err = json.loads(cm.exception.read().decode())
        self.assertIn("error", err)

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

        self.__class__.test_db.add_work_to_folder(f_id, w_id)
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

    def test_21_graph_recent_and_bibtex_smoke(self):
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

        with urllib.request.urlopen(urllib.request.Request(f"{self._base_url}/api/recent")) as rr:
            recent = json.loads(rr.read().decode())
        self.assertIsInstance(recent, list)
        self.assertTrue(any(r.get("id") == w_id for r in recent))

        with urllib.request.urlopen(urllib.request.Request(f"{self._base_url}/api/graph")) as gr:
            graph = json.loads(gr.read().decode())
        self.assertIsInstance(graph, dict)
        self.assertIn("nodes", graph)
        self.assertIn("edges", graph)

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

        ann = [{"id": "a1", "type": "note", "contents": "hello", "pageIndex": 0, "color": "#fff"}]
        req_ann = urllib.request.Request(
            f"{self._base_url}/api/works/{w_id}/annotations",
            data=json.dumps({"annotations_json": json.dumps(ann)}).encode(),
            method="POST",
        )
        req_ann.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req_ann) as ra:
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
        old_storage = os.environ.get("PRKS_STORAGE")
        old_processing = os.environ.get("PRKS_FOR_PROCESSING_DIR")
        with tempfile.TemporaryDirectory(prefix="prks-processing-api-") as root:
            storage_root = os.path.join(root, "storage")
            processing_root = os.path.join(root, "processing")
            nested = os.path.join(processing_root, "alpha", "beta")
            os.makedirs(nested, exist_ok=True)
            source_pdf = os.path.join(nested, "api_inbox.pdf")
            with open(source_pdf, "wb") as f:
                f.write(b"%PDF-1.4\n%API-INBOX\n%%EOF\n")
            with open(os.path.join(nested, "ignore.txt"), "w", encoding="utf-8") as f:
                f.write("not a pdf")
            try:
                os.environ["PRKS_STORAGE"] = storage_root
                os.environ["PRKS_FOR_PROCESSING_DIR"] = processing_root
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

                req_scan_again = urllib.request.Request(f"{self._base_url}/api/processing-files?rescan=1")
                with urllib.request.urlopen(req_scan_again) as rsa:
                    after = json.loads(rsa.read().decode())
                self.assertEqual(after, [])
            finally:
                if old_storage is None:
                    os.environ.pop("PRKS_STORAGE", None)
                else:
                    os.environ["PRKS_STORAGE"] = old_storage
                if old_processing is None:
                    os.environ.pop("PRKS_FOR_PROCESSING_DIR", None)
                else:
                    os.environ["PRKS_FOR_PROCESSING_DIR"] = old_processing

    def test_processing_files_pdf_preview_endpoint(self):
        old_storage = os.environ.get("PRKS_STORAGE")
        old_processing = os.environ.get("PRKS_FOR_PROCESSING_DIR")
        with tempfile.TemporaryDirectory(prefix="prks-processing-preview-") as root:
            processing_root = os.path.join(root, "processing")
            os.makedirs(processing_root, exist_ok=True)
            source_pdf = os.path.join(processing_root, "preview_me.pdf")
            with open(source_pdf, "wb") as f:
                f.write(b"%PDF-1.4\n%PREVIEW\n%%EOF\n")
            try:
                os.environ["PRKS_STORAGE"] = os.path.join(root, "storage")
                os.environ["PRKS_FOR_PROCESSING_DIR"] = processing_root
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
            finally:
                if old_storage is None:
                    os.environ.pop("PRKS_STORAGE", None)
                else:
                    os.environ["PRKS_STORAGE"] = old_storage
                if old_processing is None:
                    os.environ.pop("PRKS_FOR_PROCESSING_DIR", None)
                else:
                    os.environ["PRKS_FOR_PROCESSING_DIR"] = old_processing

if __name__ == '__main__':
    unittest.main()
