"""Imported citation styles over HTTP on the PostgreSQL Library (Citation V1, #41).

``/api/citation-styles`` lists, reads, imports and deletes a Library's own
CSL styles; a refused file names its reason; only the owner imports or
deletes; the Library setting and a Work may name an imported style, and a
deleted one reads as the Library default.
"""

from __future__ import annotations

from pathlib import Path

from backend.domain.works import custom_citation_styles
from tests.pg.server_api_fixtures import ServerApiTestCase

_ROOT = Path(__file__).resolve().parents[2]
_IEEE = (_ROOT / "frontend" / "vendor" / "csl" / "ieee.csl").read_text(encoding="utf-8")

_NS = 'xmlns="http://purl.org/net/xbiblio/csl"'


def style(title: str = "My House Style", *, body: str = "", info: str = "") -> str:
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<style {_NS} class="in-text" version="1.0">'
            f"<info><title>{title}</title>{info}</info>"
            f"{body or '<citation><layout><text variable=\"title\"/></layout></citation>'}"
            '<bibliography><layout><text variable="title"/></layout></bibliography></style>')


class CitationStyleRouteTests(ServerApiTestCase):
    def imported(self, csl: str) -> dict:
        return self.ok(self.send("POST", "/api/citation-styles", {"csl": csl}), 201)

    def test_import_list_read_and_delete(self) -> None:
        self.assertEqual(self.ok(self.get("/api/citation-styles")), {"styles": []})
        house = self.imported(style("Zeta House"))
        ieee = self.imported(_IEEE)
        self.assertRegex(house["id"], r"^custom-[0-9a-f]{32}$")
        self.assertEqual((ieee["title"], house["title"]), ("IEEE Reference Guide version 11.29.2023", "Zeta House"))
        listed = self.ok(self.get("/api/citation-styles"))["styles"]
        self.assertEqual([s["title"] for s in listed], ["IEEE Reference Guide version 11.29.2023", "Zeta House"])
        self.assertEqual(set(listed[0]), {"id", "title", "created_at"})
        self.assertEqual(self.ok(self.get(f"/api/citation-styles/{ieee['id']}")),
                         {"id": ieee["id"], "title": ieee["title"], "csl": _IEEE})
        self.assertEqual(self.ok(self.send("DELETE", f"/api/citation-styles/{ieee['id']}")), {"deleted": ieee["id"]})
        self.refused(self.get(f"/api/citation-styles/{ieee['id']}"), 404)
        self.refused(self.send("DELETE", f"/api/citation-styles/{ieee['id']}"), 404)
        self.refused(self.get("/api/citation-styles/apa"), 404)
        self.assertEqual([s["title"] for s in self.ok(self.get("/api/citation-styles"))["styles"]], ["Zeta House"])

    def test_a_file_that_is_not_an_independent_csl_style_is_refused_with_its_reason(self) -> None:
        dependent = style(info='<link href="http://www.zotero.org/styles/apa" rel="independent-parent"/>')
        cases = {
            "": "Choose a .csl style file.",
            "not xml at all": "The file is not valid XML, so it is not a CSL style.",
            "<html><body/></html>": "The file is not a CSL style.",
            '<!DOCTYPE style [<!ENTITY a "aaaa">]>' + style(): "The file is not a CSL style.",
            style().replace('in-text" version="1.0"', 'in-text" version="0.8"'): "PRKS reads CSL 1.0 styles; this file is another version.",
            dependent: "This is a dependent style: it only points to another style. Import that style instead.",
            style().replace("<bibliography><layout><text variable=\"title\"/></layout></bibliography>", ""):
                "The style has no bibliography, so PRKS cannot format references with it.",
            style(body="<macro name=\"m\"/>"): "The style has no citation layout, so it is not a complete CSL style.",
            style(title=" "): "The style has no title.",
            style(info="<!-- " + "x" * custom_citation_styles.MAX_CSL_BYTES + " -->"): "The style file is larger than 1 MB.",
        }
        for csl, reason in cases.items():
            with self.subTest(reason=reason, start=csl[:40]):
                self.assertEqual(self.refused(self.send("POST", "/api/citation-styles", {"csl": csl}), 400)["error"],
                                 reason)
        self.assertEqual(self.refused(self.send("POST", "/api/citation-styles", {"csl": 5}), 400)["error"],
                         "Choose a .csl style file.")
        self.assertEqual(self.ok(self.get("/api/citation-styles")), {"styles": []})

    def test_a_style_citeproc_would_misread_is_stored_as_it_reads_it(self) -> None:
        prefixed = (f'<cs:style xmlns:cs="http://purl.org/net/xbiblio/csl" class="in-text" version="1.0">'
                    "<cs:info><cs:title>Prefixed</cs:title></cs:info>"
                    "<cs:citation><cs:layout><cs:text value=\"it&apos;s\"/></cs:layout></cs:citation>"
                    '<cs:bibliography><cs:layout><cs:text variable="title"/></cs:layout></cs:bibliography></cs:style>')
        stored = self.ok(self.get(f"/api/citation-styles/{self.imported(prefixed)['id']}"))["csl"]
        self.assertIn(f"<style {_NS}", stored)
        self.assertNotIn("cs:", stored)
        self.assertIn('value="it\'s"', stored)
        self.assertNotIn("&apos;", stored)
        # Another style is stored byte for byte.
        plain = style("Plain")
        self.assertEqual(self.ok(self.get(f"/api/citation-styles/{self.imported(plain)['id']}"))["csl"], plain)

    def test_a_library_holds_a_bounded_number_of_styles(self) -> None:
        with self.subTest("the limit is checked inside the import"):
            original = custom_citation_styles.MAX_STYLES
            custom_citation_styles.MAX_STYLES = 2
            self.addCleanup(setattr, custom_citation_styles, "MAX_STYLES", original)
            self.imported(style("One"))
            self.imported(style("Two"))
            body = self.refused(self.send("POST", "/api/citation-styles", {"csl": style("Three")}), 400)
            self.assertEqual(body["error"], "A library can hold 2 imported styles. Delete one first.")

    def test_only_the_owner_imports_or_deletes_and_a_reader_may_read(self) -> None:
        house = self.imported(style())
        for role in ("editor", "reader"):
            with self.subTest(role=role):
                name = role.title()
                self.user(name, library=self.lib, role=role)
                cookies = self.session_cookie(name)
                epoch = self.library_epoch()
                self.assertEqual(self.call("POST", "/api/citation-styles", body={"csl": style()}, cookies=cookies,
                                           epoch=epoch).status, 403)
                self.assertEqual(self.call("DELETE", f"/api/citation-styles/{house['id']}", cookies=cookies,
                                           epoch=epoch).status, 403)
                self.assertEqual(self.call("GET", f"/api/citation-styles/{house['id']}", cookies=cookies).status, 200)
        self.assertEqual(len(self.ok(self.get("/api/citation-styles"))["styles"]), 1)

    def test_the_library_and_a_work_may_use_an_imported_style_until_it_is_deleted(self) -> None:
        house = self.imported(style())
        self.assertEqual(self.ok(self.send("PATCH", "/api/settings", {"citation_style": house["id"]}))["citation_style"],
                         house["id"])
        missing = "custom-" + "0" * 32
        self.assertEqual(self.refused(self.send("PATCH", "/api/settings", {"citation_style": missing}), 400)["error"],
                         "Unknown citation style.")
        work = self.create_work("Styled By House")
        self.ok(self.send("PATCH", f"/api/works/{work['id']}", {"citation_style": house["id"]}))
        self.refused(self.send("PATCH", f"/api/works/{work['id']}", {"citation_style": "custom-nothex"}), 400)
        self.ok(self.send("DELETE", f"/api/citation-styles/{house['id']}"))
        # The setting falls back to the default; the Work keeps the id and is cited in the default.
        self.assertEqual(self.ok(self.get("/api/settings"))["citation_style"], "apa")
        self.assertEqual(self.sql("SELECT key FROM prks.library_settings WHERE key = 'citation_style'"), [])
        self.assertEqual(self.ok(self.get(f"/api/works/{work['id']}"))["citation_style"], house["id"])

    def test_a_setting_naming_a_style_that_is_gone_reads_as_the_default(self) -> None:
        self.ok(self.send("PATCH", "/api/settings", {"citation_style": "ieee"}))
        self.sql("UPDATE prks.library_settings SET value = %s WHERE key = 'citation_style'", ("custom-" + "1" * 32,))
        self.assertEqual(self.ok(self.get("/api/settings"))["citation_style"], "apa")
