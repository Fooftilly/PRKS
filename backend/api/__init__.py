"""The HTTP route families (#1424).

Each ``backend/api/<family>.py`` declares its routes as a ``ROUTES`` tuple
of ``backend.web.routing.Route`` and owns that family's request shape,
domain calls and status mapping. Domain modules keep SQL, files and sync
semantics. ``backend.web.dispatch`` admits each request, reads its body
(one JSON object for a JSON route) and maps refusals raised in a route; see
``docs/http-server.md``.

``session`` and ``setup`` are admission itself (the identity routes and a
first run's setup), so they declare no routes here.
"""

# No ``from __future__ import annotations`` here: it would shadow the
# ``annotations`` family module.
from backend.api import (
    annotations,
    arguments,
    citation_styles,
    concepts,
    deployment,
    diagnostics,
    folders,
    health,
    maintenance,
    pdfs,
    person_groups,
    persons,
    playlists,
    positions,
    processing,
    publishers,
    remote_images,
    research_graph,
    saved_views,
    search,
    settings,
    storage,
    sync,
    tags,
    thumbnails,
    work_roles,
    works,
)
from backend.web.routing import Route

ROUTES: tuple[Route, ...] = (
    *health.ROUTES,
    *diagnostics.ROUTES,
    *deployment.ROUTES,
    *storage.ROUTES,
    *settings.ROUTES,
    *sync.ROUTES,
    *works.ROUTES,
    *thumbnails.ROUTES,
    *pdfs.ROUTES,
    *annotations.ROUTES,
    *work_roles.ROUTES,
    *tags.ROUTES,
    *folders.ROUTES,
    *playlists.ROUTES,
    *persons.ROUTES,
    *person_groups.ROUTES,
    *publishers.ROUTES,
    *concepts.ROUTES,
    *arguments.ROUTES,
    *positions.ROUTES,
    *research_graph.ROUTES,
    *saved_views.ROUTES,
    *search.ROUTES,
    *remote_images.ROUTES,
    *citation_styles.ROUTES,
    *processing.ROUTES,
    *maintenance.ROUTES,
)
