"""The Works integrity query (``docs/work-identity-model.md`` §4.1).

The schema refuses every ownership contradiction, but two final-state rules
cannot be constraints: a Manifestation with an active Asset must have a
primary Asset, and that primary Asset must be active. Commands that touch
Versions or Files check them before committing; this query finds any row
that breaks them, and the schema tests and backup verification run it.

A Work without a primary Manifestation is listed too, although the
``NOT NULL`` column and the deferred pointer key make one impossible to
commit: the query is the check backup verification relies on, so it states
every rule.
"""

from __future__ import annotations

from typing import LiteralString

# One row per violation: (library_id, entity id, rule). Filtered to one
# Library by %(library_id)s, as every Library-scoped statement is.
INTEGRITY_SQL: LiteralString = """
SELECT w.library_id, w.id AS entity_id, 'work_without_primary_manifestation' AS rule
  FROM prks.works w
  LEFT JOIN prks.manifestations m
    ON m.library_id = w.library_id AND m.id = w.primary_manifestation_id AND m.work_id = w.id
 WHERE w.library_id = %(library_id)s AND m.id IS NULL
UNION ALL
SELECT m.library_id, m.id, 'active_assets_without_primary_asset'
  FROM prks.manifestations m
 WHERE m.library_id = %(library_id)s
   AND m.primary_asset_id IS NULL
   AND EXISTS (SELECT 1 FROM prks.assets a
                WHERE a.library_id = m.library_id AND a.manifestation_id = m.id
                  AND a.state = 'active')
UNION ALL
SELECT m.library_id, m.id, 'primary_asset_not_active'
  FROM prks.manifestations m
  JOIN prks.assets a
    ON a.library_id = m.library_id AND a.id = m.primary_asset_id AND a.manifestation_id = m.id
 WHERE m.library_id = %(library_id)s AND a.state <> 'active'
ORDER BY 3, 2
"""
