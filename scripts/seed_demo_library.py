#!/usr/bin/env python3
"""Fill testing storage with public-domain books for README screenshots.

Talks to a running `python prks_app.py --testing` server (default port 8070).
Downloads scans from the Internet Archive, keeps the first pages, and uploads
those PDFs. Idempotent: skips create if Public domain library already exists.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import fitz

UA = "PRKS-demo-seed/1.0 (https://github.com; README screenshots)"
DEMO_LIBRARY = "Public domain library"
DEMO_NOTES = "Commonplace book"
DEMO_GROUP = "Nineteenth century"
CACHE = Path(__file__).resolve().parents[1] / "data_screenshots" / ".seed-cache"

# Internet Archive scans of works in the public domain (US).
WORKS = [
    {
        "title": "On the Origin of Species",
        "year": "1859",
        "author": "Charles Darwin",
        "person": "Darwin",
        "role": "Author",
        "status": "In Progress",
        "tag": "biology",
        "doc_type": "book",
        "abstract": "Darwin's account of natural selection. Public-domain scan.",
        "url": "https://archive.org/download/onoriginofspecie00darw/onoriginofspecie00darw.pdf",
        "file_name": "origin-of-species.pdf",
        "folder": "library",
        "start_page": 6,
        "hero": True,
    },
    {
        "title": "Pride and Prejudice",
        "year": "1813",
        "author": "Jane Austen",
        "person": "Austen",
        "role": "Author",
        "status": "Completed",
        "tag": "fiction",
        "doc_type": "book",
        "abstract": "Austen's novel. Public-domain scan.",
        "url": "https://archive.org/download/prideprejudice00aust/prideprejudice00aust.pdf",
        "file_name": "pride-and-prejudice.pdf",
        "folder": "library",
        "start_page": 0,
    },
    {
        "title": "A Vindication of the Rights of Woman",
        "year": "1792",
        "author": "Mary Wollstonecraft",
        "person": "Wollstonecraft",
        "role": "Author",
        "status": "Planned",
        "tag": "politics",
        "doc_type": "book",
        "abstract": "Wollstonecraft on education and the rights of women. Public-domain scan.",
        "url": "https://archive.org/download/vindicationofrig00wolliala/vindicationofrig00wolliala.pdf",
        "file_name": "vindication-rights-of-woman.pdf",
        "folder": "library",
        "start_page": 8,
    },
    {
        "title": "The Republic",
        "year": "c. 375 BCE",
        "author": "Plato",
        "person": "Plato",
        "role": "Author",
        "status": "Paused",
        "tag": "philosophy",
        "doc_type": "book",
        "abstract": "Jowett-era public-domain scan of Plato's Republic.",
        "url": "https://archive.org/download/therepublicofpla00platuoft/therepublicofpla00platuoft.pdf",
        "file_name": "plato-republic.pdf",
        "folder": "library",
        "start_page": 4,
    },
]

PEOPLE = [
    (
        "Charles",
        "Darwin",
        "Naturalist. On the Origin of Species (1859).",
        "https://en.wikipedia.org/wiki/Charles_Darwin",
        "1809-02-12",
        "1882-04-19",
    ),
    (
        "Jane",
        "Austen",
        "Novelist. Pride and Prejudice (1813).",
        "https://en.wikipedia.org/wiki/Jane_Austen",
        "1775-12-16",
        "1817-07-18",
    ),
    (
        "Mary",
        "Wollstonecraft",
        "Writer and philosopher. A Vindication of the Rights of Woman (1792).",
        "https://en.wikipedia.org/wiki/Mary_Wollstonecraft",
        "1759-04-27",
        "1797-09-10",
    ),
    (
        "Plato",
        "",
        "Athenian philosopher. The Republic.",
        "https://en.wikipedia.org/wiki/Plato",
        "",
        "",
    ),
    (
        "Frederick",
        "Douglass",
        "Writer and abolitionist. Narrative of the Life of Frederick Douglass (1845).",
        "https://en.wikipedia.org/wiki/Frederick_Douglass",
        "1818-02-14",
        "1895-02-20",
    ),
]


def _download(url: str) -> bytes:
    CACHE.mkdir(parents=True, exist_ok=True)
    name = url.rstrip("/").split("/")[-1]
    dest = CACHE / name
    if dest.is_file() and dest.stat().st_size > 10_000:
        return dest.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as res:
        data = res.read()
    if not data.startswith(b"%PDF"):
        raise RuntimeError(f"not a PDF: {url} ({len(data)} bytes)")
    dest.write_bytes(data)
    return data


def _clip_pdf(data: bytes, pages: int = 8, start_page: int | None = None) -> bytes:
    src = fitz.open(stream=data, filetype="pdf")
    if start_page is not None:
        start = max(0, min(int(start_page), src.page_count - 1))
    else:
        start = 0
        for i in range(src.page_count):
            text = (src[i].get_text("text") or "").strip()
            if len(text) > 80:
                start = i
                break
    out = fitz.open()
    end = min(start + pages, src.page_count) - 1
    out.insert_pdf(src, from_page=start, to_page=end)
    clipped = out.tobytes()
    out.close()
    src.close()
    return clipped


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def json(self, method: str, path: str, body=None):
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                raw = res.read().decode()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode()
            raise RuntimeError(f"{method} {path} -> {e.code}: {detail}") from e

    def get(self, path: str):
        return self.json("GET", path)

    def get_bytes(self, path: str) -> bytes:
        req = urllib.request.Request(self.base + path)
        with urllib.request.urlopen(req, timeout=60) as res:
            return res.read()

    def post(self, path: str, body):
        return self.json("POST", path, body)

    def delete(self, path: str):
        return self.json("DELETE", path)


def _folder_by_title(folders, title: str):
    for f in folders or []:
        if (f.get("title") or "") == title:
            return f
        found = _folder_by_title(f.get("children") or [], title)
        if found:
            return found
    return None


DEMO_GROUP_MEMBERS = ("Darwin", "Austen", "Wollstonecraft", "Douglass")
NOTE_TITLE = "Commonplace: Darwin on variation"
NOTE_EXCERPT = (
    "Until recently the causes of variation were profoundly hidden. "
    "Our ignorance is so profound that we cannot even say why one species "
    "is green and another blue—why one fowl has a tufted head and another "
    "not. Any variation which is not inherited is unimportant for us. "
    "But the number and diversity of inheritable deviations of structure, "
    "both those of slight and those of considerable physiological importance, "
    "is endless.\n\n"
    "I have hitherto sometimes spoken as if the variations—so common and "
    "multiform in organic beings under domestication, and in a lesser degree "
    "in those in a state of nature—had been due to chance. This, of course, "
    "is a wholly incorrect expression, but it serves to acknowledge plainly "
    "our ignorance of the cause of each particular variation."
)


def _excerpt_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=396, height=612)
    page.insert_text((48, 64), "COMMONPLACE BOOK", fontsize=9, fontname="helv")
    page.insert_text((48, 88), "On variation", fontsize=16, fontname="times-bold")
    page.insert_text(
        (48, 108),
        "Charles Darwin, On the Origin of Species, 1859",
        fontsize=10,
        fontname="times-italic",
    )
    page.insert_textbox(
        fitz.Rect(48, 132, 348, 560),
        NOTE_EXCERPT,
        fontsize=11,
        fontname="times-roman",
        align=fitz.TEXT_ALIGN_LEFT,
    )
    data = doc.tobytes()
    doc.close()
    return data


def _work_by_title(works, title: str):
    for w in works or []:
        if (w.get("title") or "") == title:
            return w
    return None


def ensure_demo_note(api: Client) -> None:
    works = api.get("/api/works")
    if not isinstance(works, list):
        works = []
    note = _work_by_title(works, NOTE_TITLE)
    file_path = (note.get("file_path") or "").strip() if note else ""
    if note and file_path.startswith("/api/pdfs/"):
        return
    if note:
        api.delete(f"/api/works/{note['id']}")

    folders = api.get("/api/folders")
    notes_folder = _folder_by_title(folders if isinstance(folders, list) else [], DEMO_NOTES)
    persons = api.get("/api/persons")
    if not isinstance(persons, list):
        persons = []
    darwin = next((p for p in persons if (p.get("last_name") or "") == "Darwin"), None)
    tags = api.get("/api/tags")
    if not isinstance(tags, list):
        tags = []
    biology = next((t for t in tags if (t.get("name") or "") == "biology"), None)
    if not notes_folder or not darwin or not biology:
        print("Cannot attach commonplace PDF: missing folder, Darwin, or biology tag.", file=sys.stderr)
        return

    pdf = _excerpt_pdf()
    created = api.post(
        "/api/works",
        {
            "title": NOTE_TITLE,
            "status": "In Progress",
            "abstract": "Excerpt from Origin of Species (public domain).",
            "text_content": NOTE_EXCERPT,
            "author_text": "Charles Darwin",
            "year": "1859",
            "doc_type": "misc",
            "folder_id": notes_folder["id"],
            "file_b64": base64.b64encode(pdf).decode(),
            "file_name": "darwin-variation-excerpt.pdf",
            "roles": [{"person_id": darwin["id"], "role_type": "Author"}],
        },
    )
    api.post(f"/api/works/{created['id']}/tags", {"tag_id": biology["id"]})
    try:
        api.get_bytes(f"/api/works/{created['id']}/thumbnail?page=1")
    except Exception:
        pass
    print("Attached commonplace PDF.")


def ensure_demo_group(api: Client) -> None:
    groups = api.get("/api/person-groups")
    if not isinstance(groups, list):
        groups = []
    if any((g.get("name") or "") == DEMO_GROUP for g in groups):
        return
    persons = api.get("/api/persons")
    if not isinstance(persons, list):
        persons = []
    by_last = {(p.get("last_name") or p.get("first_name") or ""): p.get("id") for p in persons}
    created = api.post(
        "/api/person-groups",
        {"name": DEMO_GROUP, "description": "Writers and thinkers from the long nineteenth century."},
    )
    gid = created["id"]
    for key in DEMO_GROUP_MEMBERS:
        pid = by_last.get(key)
        if pid:
            api.post(f"/api/person-groups/{gid}/members", {"person_id": pid})
    print("Added people group:", DEMO_GROUP)


def seed(base_url: str) -> dict:
    api = Client(base_url)
    folders = api.get("/api/folders")
    if not isinstance(folders, list):
        folders = []
    existing = _folder_by_title(folders, DEMO_LIBRARY)
    if existing:
        print("Public domain library already present. Skipping create.")
        ensure_demo_group(api)
        ensure_demo_note(api)
        works = api.get("/api/works")
        return {
            "folder_id": existing.get("id"),
            "works": works if isinstance(works, list) else [],
        }

    library = api.post(
        "/api/folders",
        {
            "title": DEMO_LIBRARY,
            "description": "Scans of works in the public domain, for screenshots.",
        },
    )
    notes = api.post(
        "/api/folders",
        {"title": DEMO_NOTES, "description": "Excerpts and reading notes."},
    )
    library_id = library["id"]
    notes_id = notes["id"]

    tags = {
        "biology": api.post("/api/tags", {"name": "biology", "color": "#3d8b6e"})["id"],
        "fiction": api.post("/api/tags", {"name": "fiction", "color": "#6d6cf7"})["id"],
        "politics": api.post("/api/tags", {"name": "politics", "color": "#c45c26"})["id"],
        "philosophy": api.post("/api/tags", {"name": "philosophy", "color": "#5a6a8a"})["id"],
    }

    person_ids = {}
    for first, last, about, wiki, born, died in PEOPLE:
        body = {
            "first_name": first,
            "last_name": last,
            "about": about,
            "link_wikipedia": wiki,
            "birth_date": born,
            "death_date": died,
        }
        created = api.post("/api/persons", body)
        key = last or first
        person_ids[key] = created["id"]

    hero_id = None
    for spec in WORKS:
        print("fetch", spec["title"], file=sys.stderr)
        raw = _download(spec["url"])
        pdf = _clip_pdf(raw, pages=8, start_page=spec.get("start_page"))
        folder_id = library_id if spec["folder"] == "library" else notes_id
        payload = {
            "title": spec["title"],
            "status": spec["status"],
            "abstract": spec["abstract"],
            "year": spec["year"],
            "author_text": spec["author"],
            "doc_type": spec["doc_type"],
            "folder_id": folder_id,
            "file_b64": base64.b64encode(pdf).decode(),
            "file_name": spec["file_name"],
            "roles": [{"person_id": person_ids[spec["person"]], "role_type": spec["role"]}],
        }
        created = api.post("/api/works", payload)
        wid = created["id"]
        api.post(f"/api/works/{wid}/tags", {"tag_id": tags[spec["tag"]]})
        if spec.get("hero"):
            hero_id = wid
        try:
            api.get_bytes(f"/api/works/{wid}/thumbnail?page=1")
        except Exception:
            pass

    ensure_demo_note(api)
    ensure_demo_group(api)

    print("Seeded public-domain library.")
    return {"folder_id": library_id, "work_id": hero_id}


def main():
    parser = argparse.ArgumentParser(description="Seed public-domain books for screenshots.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8070")
    args = parser.parse_args()
    seed(args.base_url)


if __name__ == "__main__":
    main()
