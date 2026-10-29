-- PDF annotations (docs/postgresql-architecture.md §6.5, §7.4;
-- docs/work-identity-model.md §6). L-A-1 (#635) owns this file.
--
-- An annotation is drawn on one file's pages, so it belongs to that file's
-- Asset, never to the Work: its identity is `(asset_id, id)` because the
-- viewer mints `id`, and one id embedded in two copies of a PDF names two
-- annotations. `work_id` is the Asset's Work, kept by the composite key's
-- ON UPDATE CASCADE when a Manifestation (and so its Assets) moves; the key
-- includes `library_id`, so an annotation never pairs with another Library's
-- Asset.
--
-- The first-class columns are those of `backend/pdf_annotations.py`
-- (`normalize_annotation`); `geometry` holds every other field the viewer
-- sent, as one JSON object. The Asset's
-- `canonical_annotation_set_revision` counts changes to the set and its
-- `materialized_pdf_annotation_revision` says which one the stored bytes
-- embed. Each annotation's revision scope is `asset-annotation/[asset, id]`
-- in `entity_revisions`, so a deleted annotation leaves its scope behind.
--
-- An annotation owns a revision scope, so it never cascades away (§5.2): the
-- Asset key is NO ACTION, and a Work delete removes the Work's annotations
-- and tombstones their scopes before it deletes the Assets
-- (`backend/domain/annotations/store.py` `release_work`).

CREATE TABLE prks.annotations (
  library_id text NOT NULL,
  asset_id   text NOT NULL,
  id         text NOT NULL CHECK (length(id) BETWEEN 1 AND 200),
  work_id    text NOT NULL,                 -- the Asset's Work, kept by cascade
  type       text NOT NULL DEFAULT '',
  content    text NOT NULL DEFAULT '',      -- the comment
  page_index integer CHECK (page_index >= 0),
  color      text NOT NULL DEFAULT '',
  geometry   jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(geometry) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (asset_id, id),
  CONSTRAINT annotations_asset_fkey FOREIGN KEY (library_id, asset_id, work_id)
    REFERENCES prks.assets (library_id, id, work_id) ON UPDATE CASCADE
);
CREATE INDEX annotations_asset ON prks.annotations (library_id, asset_id, work_id);
CREATE INDEX annotations_work ON prks.annotations (library_id, work_id);
