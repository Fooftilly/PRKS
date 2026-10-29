"""Extract ``[[label]]`` mentions from managed PDF bytes.

Used when a Work PDF is overwritten to discover person names written as
``[[First Last]]`` / ``[[Last]]`` markup inside the file. Must not use a
backtracking regex over untrusted byte streams (ReDoS).
"""

from __future__ import annotations

from typing import Iterable, Iterator

# Person display names in PDF markup are short; bound inner length so a
# pathological stream cannot force huge allocations even with a linear scan.
_MAX_LABEL_BYTES = 200
_OPEN = b"[["
_CLOSE = b"]]"


def iter_double_bracket_inners(
    data: bytes, *, max_inner: int = _MAX_LABEL_BYTES
) -> Iterator[bytes]:
    """Yield raw inner payloads between ``[[`` and ``]]``.

    Linear ``find`` scan only — no regex. Spans that contain ``[`` or ``]``,
    or exceed ``max_inner``, are skipped. Unclosed opens are ignored.
    """
    if not data or max_inner < 0:
        return
    i = 0
    n = len(data)
    while i < n:
        start = data.find(_OPEN, i)
        if start < 0:
            return
        inner_start = start + 2
        end = data.find(_CLOSE, inner_start)
        if end < 0:
            return
        inner = data[inner_start:end]
        i = end + 2
        if len(inner) > max_inner:
            continue
        # Reject nested / broken brackets so we never accept overlapping opens.
        if b"[" in inner or b"]" in inner:
            continue
        yield inner


def iter_double_bracket_inners_chunked(
    chunks: Iterable[bytes], *, max_inner: int = _MAX_LABEL_BYTES
) -> Iterator[bytes]:
    """:func:`iter_double_bracket_inners` over a stream read in chunks.

    Yields the same payloads as the whole-buffer scan of the concatenated
    chunks, holding at most one chunk, one carried byte and ``max_inner + 1``
    bytes of the current payload: a delimiter split across chunks is found,
    and an oversized payload is counted, not kept.
    """
    if max_inner < 0:
        return
    inside = False
    inner = bytearray()
    oversized = False
    carry = b""
    for chunk in chunks:
        if not chunk:
            continue
        buf = carry + chunk
        carry = b""
        i = 0
        n = len(buf)
        while i < n:
            if not inside:
                start = buf.find(_OPEN, i)
                if start < 0:
                    # A trailing "[" may open a payload with the next chunk.
                    carry = buf[-1:] if buf.endswith(b"[") else b""
                    break
                inside = True
                inner.clear()
                oversized = False
                i = start + 2
                continue
            end = buf.find(_CLOSE, i)
            stop = end if end >= 0 else (n - 1 if buf.endswith(b"]") else n)
            if not oversized:
                inner += buf[i:stop]
                if len(inner) > max_inner:
                    oversized = True
                    inner.clear()
            if end < 0:
                # A trailing "]" may close the payload with the next chunk.
                carry = buf[stop:]
                break
            inside = False
            i = end + 2
            if oversized or b"[" in inner or b"]" in inner:
                continue
            yield bytes(inner)


def normalize_pdf_mentioned_label(raw: bytes | str) -> str:
    """Decode and strip a PDF ``[[...]]`` inner to a person lookup key."""
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="ignore")
    else:
        text = str(raw)
    text = text.strip()
    if not text:
        return ""
    return "".join(c for c in text if c.isalnum() or c.isspace() or c in "-_")


def iter_pdf_mentioned_labels(data: bytes) -> Iterator[str]:
    """Yield non-empty normalized person labels from PDF byte markup."""
    for inner in iter_double_bracket_inners(data):
        label = normalize_pdf_mentioned_label(inner)
        if label:
            yield label


def iter_pdf_mentioned_labels_chunked(chunks: Iterable[bytes]) -> Iterator[str]:
    """:func:`iter_pdf_mentioned_labels` over a stream read in chunks."""
    for inner in iter_double_bracket_inners_chunked(chunks):
        label = normalize_pdf_mentioned_label(inner)
        if label:
            yield label
