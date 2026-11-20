"""The HTTP server that serves PRKS (#1329, ``docs/http-server.md``).

``settings`` reads the server settings, ``asgi`` serves
``backend.server.PRKSHandler`` dispatch under Uvicorn, and ``handler_base``
is the response surface that handler writes to.
"""
