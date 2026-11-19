"""The HTTP transport that serves PRKS (#1329, ``docs/http-server.md``).

``settings`` reads the transport settings. ``asgi`` serves the existing
``backend.server.PRKSHandler`` dispatch under Uvicorn; it is imported only
when that transport is selected, so the stdlib transport loads no Uvicorn.
"""
