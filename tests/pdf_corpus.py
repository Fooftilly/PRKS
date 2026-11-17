"""A synthetic adversarial PDF corpus, generated at test time (#1287).

Every case is built here from PyMuPDF or from hand-written PDF syntax, so the
repository holds only this generator and no third-party document. Each case
is small (well under a megabyte) and carries the word :data:`MARKER` where it
has extractable text, so a test can check that search finds it.

The cases are about how *PRKS* handles a difficult file (an upload that is
refused or kept, text extraction that fails or finds nothing, a render or a
linearization that fails), not about fuzzing the parsers themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

MARKER = "zebracorpus"
PASSWORD = "corpus-secret"
MANY_PAGES = 400
# ``pymupdf.PDF_ENCRYPT_AES_256``, which PyMuPDF's type stubs do not declare;
# the corpus test checks the two agree.
PDF_ENCRYPT_AES_256 = 5


@dataclass(frozen=True)
class Case:
    name: str
    build: Callable[[], bytes]
    pdf_magic: bool = True  # starts with ``%PDF-``, so an upload keeps it


def _document(pages: int = 1, text: str | None = MARKER, rotate: int = 0):
    import pymupdf

    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page(width=200, height=200)
        if text is not None:
            page.insert_text((20, 40), f"{text} {index}" if pages > 1 else text, fontsize=10)
        if rotate:
            page.set_rotation(rotate)
    return doc


# Fixed metadata and no fresh /ID, so every build of an unencrypted case is the
# same bytes (AES-256 draws a random key and IV, so ``encrypted`` differs each time).
_METADATA = {"producer": "PRKS test corpus", "creator": "PRKS test corpus", "title": "", "author": "",
             "subject": "", "keywords": "", "creationDate": "D:20260101000000Z", "modDate": "D:20260101000000Z"}


def _save(doc, **options) -> bytes:
    try:
        doc.set_metadata(_METADATA)
        return doc.tobytes(garbage=0, deflate=False, no_new_id=True, **options)
    finally:
        doc.close()


def valid() -> bytes:
    """The control: one page with the marker."""
    return _save(_document())


def truncated() -> bytes:
    """A valid file cut inside its first stream: no xref table, no trailer, no ``%%EOF``."""
    data = valid()
    return data[: data.index(b"stream") + len(b"stream\n") + 4]


def damaged_xref() -> bytes:
    """Every xref offset and ``startxref`` points nowhere."""
    data = valid()
    data = re.sub(rb"\d{10} 00000 n", b"9999999999 00000 n", data)
    return re.sub(rb"startxref\s+\d+", b"startxref\n999999999", data)


def encrypted() -> bytes:
    """AES-256 with a user password: nothing opens it without the password."""
    return _save(_document(), encryption=PDF_ENCRYPT_AES_256, user_pw=PASSWORD, owner_pw=PASSWORD + "-owner")


def zero_pages() -> bytes:
    """A well-formed file whose page tree is empty."""
    return _hand_written([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [] /Count 0 >>",
    ])


def many_pages() -> bytes:
    """:data:`MANY_PAGES` tiny pages, each with a line of text."""
    return _save(_document(pages=MANY_PAGES))


def rotated() -> bytes:
    """Pages rotated 90 and 270 degrees."""
    import pymupdf

    doc = pymupdf.open()
    for rotation in (90, 270):
        page = doc.new_page(width=300, height=150)
        page.insert_text((20, 40), MARKER, fontsize=10)
        page.set_rotation(rotation)
    return _save(doc)


def no_text() -> bytes:
    """Only a drawn shape: a scan-like page with nothing to extract."""
    doc = _document(text=None)
    doc[0].draw_rect((20, 20, 180, 180), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
    return _save(doc)


def odd_font_encoding() -> bytes:
    """A font whose ToUnicode map sends codes to NUL, a control, a private-use and a non-BMP character.

    The page shows the marker in plain codes too, so extraction finds text.
    """
    cmap = (b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
            b"/CMapName /Odd def 1 begincodespacerange <00> <FF> endcodespacerange\n"
            b"4 beginbfchar <01> <0000> <02> <0007> <03> <E000> <04> <D83DDE00> endbfchar\n"
            b"endcmap CMapName currentdict /CMap defineresource pop end end\n")
    content = (b"BT /F1 12 Tf 20 100 Td (" + MARKER.encode() + b") Tj 0 -20 Td (\\001\\002\\003\\004) Tj ET\n")
    return _hand_written([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        _stream(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica"
        b" /Encoding << /Type /Encoding /Differences [1 /odd1 /odd2 /odd3 /odd4] >> /ToUnicode 6 0 R >>",
        _stream(cmap),
    ])


DUPLICATE_ANNOTATION_ID = "corpus-duplicate"


def duplicate_annotation_ids() -> bytes:
    """Two highlights and a note that share one annotation id (``/NM``, the id a viewer reports)."""
    import pymupdf

    doc = _document()
    page = doc[0]
    annots = [page.add_highlight_annot(pymupdf.Rect(rect)) for rect in ((20, 30, 100, 45), (20, 60, 100, 75))]
    annots.append(page.add_text_annot((150, 150), "a note"))
    for annot in annots:
        annot.update()
        # Written last: PyMuPDF gives each new annotation its own /NM.
        doc.xref_set_key(annot.xref, "NM", pymupdf.get_pdf_str(DUPLICATE_ANNOTATION_ID))
    return _save(doc)


def header_then_garbage() -> bytes:
    """A ``%PDF-`` header and then bytes that are no PDF at all."""
    return b"%PDF-1.7\n" + bytes(range(256)) * 16 + b"\n%%EOF\n"


def not_a_pdf() -> bytes:
    """A PNG signature and some bytes, uploaded under a ``.pdf`` name."""
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 256


def _stream(data: bytes) -> bytes:
    return b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"


def _hand_written(objects: list[bytes]) -> bytes:
    """A complete PDF from numbered objects (1..n), with a correct xref table."""
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, start)
    return bytes(out)


CORPUS: tuple[Case, ...] = (
    Case("valid", valid),
    Case("truncated", truncated),
    Case("damaged_xref", damaged_xref),
    Case("encrypted", encrypted),
    Case("zero_pages", zero_pages),
    Case("many_pages", many_pages),
    Case("rotated", rotated),
    Case("no_text", no_text),
    Case("odd_font_encoding", odd_font_encoding),
    Case("duplicate_annotation_ids", duplicate_annotation_ids),
    Case("header_then_garbage", header_then_garbage),
    Case("not_a_pdf", not_a_pdf, pdf_magic=False),
)
