"""Read-only Locust workload for the bounded load pilot (#859, #765 T08).

Run only by ``scripts/ci/locust_pilot.py``, which starts a disposable
``--testing`` server on loopback and passes the seeded ids through the
environment. Every task is a GET of a route a browser uses while reading the
library, and every answer is checked for its meaning, not only its status:
a fast 4xx/5xx or an incomplete catalog is a failure, never a fast success.
"""

from __future__ import annotations

import os
import random

from locust import HttpUser, between, task

WORK_IDS = [value for value in os.environ.get("PRKS_LOCUST_WORK_IDS", "").split(",") if value]
SEARCH_TERM = os.environ.get("PRKS_LOCUST_SEARCH", "")
WORKS = int(os.environ.get("PRKS_LOCUST_WORKS", "1"))
TAGS = int(os.environ.get("PRKS_LOCUST_TAGS", "1"))
MIN_SEARCH_HITS = int(os.environ.get("PRKS_LOCUST_MIN_SEARCH_HITS", "1"))


class Reader(HttpUser):
    """One browser reading the library: catalog, Work detail, search, tags, the research graph."""

    wait_time = between(0.05, 0.2)

    def _check(self, path: str, name: str, valid, problem: str) -> None:
        """GET ``path``; a non-200, non-JSON or invalid answer is a failure of ``name``."""
        with self.client.get(path, name=name, catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"status {response.status_code}")
                return
            try:
                body = response.json()
            except ValueError:
                response.failure("not JSON")
                return
            if valid(body):
                response.success()
            else:
                response.failure(problem)

    @task(4)
    def catalog(self) -> None:
        self._check("/api/works", "/api/works",
                    lambda body: isinstance(body, list) and len(body) == WORKS,
                    "catalog is not the seeded library")

    @task(4)
    def work_detail(self) -> None:
        work_id = random.choice(WORK_IDS)
        self._check(f"/api/works/{work_id}", "/api/works/:id",
                    lambda body: isinstance(body, dict) and body.get("id") == work_id,
                    "detail is not the requested Work")

    @task(3)
    def search(self) -> None:
        self._check(f"/api/search?q={SEARCH_TERM}", "/api/search",
                    lambda body: isinstance(body, list) and len(body) >= MIN_SEARCH_HITS,
                    "search found fewer Works than seeded")

    @task(1)
    def tags(self) -> None:
        self._check("/api/tags", "/api/tags", lambda body: isinstance(body, list) and len(body) == TAGS > 0,
                    "tags are not the seeded tags")

    @task(1)
    def research_graph(self) -> None:
        self._check("/api/research-graph", "/api/research-graph",
                    lambda body: isinstance(body, dict) and isinstance(body.get("nodes"), list)
                    and bool(body["nodes"]),
                    "research graph has no nodes")
