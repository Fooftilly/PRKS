"""Helpers the route families share (#1424)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from backend.etags import etag_for_representation
from backend.web.http import Request, Response, json_response

if TYPE_CHECKING:
    from backend.storage.objects import LocalFilesystemStorage


def objects(req: Request) -> "LocalFilesystemStorage":
    """The served Library's managed objects (Work PDFs, portraits, saved images)."""
    from backend.storage.objects import LocalFilesystemStorage

    storage = req.library.storage
    if storage is None:
        raise RuntimeError("this PostgreSQL library has no data root")
    return LocalFilesystemStorage.for_config(storage)


def pdfs_dir(req: Request) -> str:
    """The data root's PDF directory."""
    storage = req.library.storage
    if storage is None:
        raise RuntimeError("this PostgreSQL library has no data root")
    return storage.pdfs_dir


def optional_pdfs_dir(req: Request) -> Optional[str]:
    """The data root's PDF directory; ``None`` when the Library has none (the command decides)."""
    storage = req.library.storage
    return None if storage is None else storage.pdfs_dir


def revalidated(req: Request, label: str, data: Any, missing: str = "Work not found") -> Response:
    """``data`` with a representation ETag (304 when the client holds it); 404 ``missing`` for ``None``."""
    if data is None:
        return json_response(req, 404, {"error": missing})
    return json_response(req, 200, data, etag=etag_for_representation(label, data))


def text_field(data: dict[str, Any], name: str) -> str:
    """``data[name]`` stripped when it is a string, else ``""`` (a missing or mistyped field)."""
    value = data.get(name)
    return value.strip() if isinstance(value, str) else ""
