"""The HTTP server that serves PRKS (#1329, #1424, ``docs/http-server.md``).

``settings`` reads the server settings, ``asgi`` serves the route table under
Uvicorn, ``dispatch`` admits each request and runs its route (``routing``),
``http`` is the typed request and response, ``app`` what one process serves,
``hosts`` the Host/Origin checks and ``static`` the frontend files.
"""
