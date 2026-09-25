-- PRKS Database Schema

PRAGMA foreign_keys = ON;

-- Schema version tracking: a single-row table. Fresh DBs insert LATEST_SCHEMA_VERSION
-- after this file is applied. Existing DBs are upgraded by backend/db_migrations.py.
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

-- Server-wide preferences (same for every browser/device using this PRKS instance).
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

-- Works: Metadata for documents and notes.
-- primary_manifestation_id / citation_manifestation_id (#60, schema 17) are
-- guarded by triggers, not FKs: see the Work identity section at the end.
CREATE TABLE IF NOT EXISTS works (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT CHECK(status IN ('Planned', 'In Progress', 'Completed', 'Paused', 'Not Started')) DEFAULT 'Not Started',
    published_date TEXT,
    abstract TEXT,
    text_content TEXT, -- For Markdown notes or OCR text to be indexed
    file_path TEXT, -- Legacy: path to uploaded file (PDFs use /api/pdfs/...)
    -- Source descriptor (non-PDF supported files)
    source_kind TEXT, -- pdf | video (nullable for older rows)
    source_url TEXT,  -- video URL, or original article/page URL for PDFs
    source_mime TEXT, -- optional MIME hint
    thumb_url TEXT,   -- optional remote thumbnail (e.g. YouTube thumbnail)
    provider TEXT,    -- e.g. youtube
    provider_id TEXT, -- e.g. YouTube video id
    urldate TEXT,     -- BibLaTeX @online access date (YYYY-MM-DD)
    thumb_page INTEGER, -- 1-based preferred thumbnail page for PDF cards
    -- Bibliographical metadata
    author_text TEXT,       -- Free-text string (search, cards, video channel); BibTeX authors use linked Author roles
    year TEXT,
    publisher TEXT,
    location TEXT, -- place(s) of publication; semicolon-separated; BibLaTeX joins with "and"
    edition TEXT,
    journal TEXT,
    volume TEXT,
    issue TEXT,
    pages TEXT,
    isbn TEXT,
    doi TEXT,
    doc_type TEXT DEFAULT 'article', -- BibTeX @ entry type (article, book, misc, …)
    private_notes TEXT, -- User reminders (not indexed in FTS)
    hide_pdf_link_annotations INTEGER DEFAULT 0, -- 1 = hide PDF Link annotations in sidebar list only
    canonical_annotation_set_revision INTEGER NOT NULL DEFAULT 0,
    materialized_pdf_annotation_revision INTEGER NOT NULL DEFAULT 0,
    last_opened_at TIMESTAMP, -- For Recent page
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    primary_manifestation_id TEXT,
    citation_manifestation_id TEXT
);

-- Video playlists (ordered collections of works)
CREATE TABLE IF NOT EXISTS playlists (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    original_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (playlist_id, work_id),
    FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
);

-- Enforce one-playlist-per-work (videos cannot belong to multiple playlists).
CREATE UNIQUE INDEX IF NOT EXISTS idx_playlist_items_work_unique ON playlist_items(work_id);

-- Persons: Unique identities (creators and subjects)
CREATE TABLE IF NOT EXISTS persons (
    id TEXT PRIMARY KEY,
    first_name TEXT,
    last_name TEXT NOT NULL,
    aliases TEXT, -- JSON array string or comma separated
    about TEXT,
    image_url TEXT,
    link_wikipedia TEXT,
    link_stanford_encyclopedia TEXT,
    link_iep TEXT,
    links_other TEXT,
    birth_date TEXT,
    death_date TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Concepts: persistent semantic records. Work membership is derived from notes.
CREATE TABLE IF NOT EXISTS concepts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS concept_aliases (
    concept_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (concept_id, normalized_alias),
    FOREIGN KEY (concept_id) REFERENCES concepts(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_concept_aliases_normalized
    ON concept_aliases(normalized_alias);

CREATE TABLE IF NOT EXISTS concept_parents (
    child_concept_id TEXT NOT NULL,
    parent_concept_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (child_concept_id, parent_concept_id),
    CHECK (child_concept_id <> parent_concept_id),
    FOREIGN KEY (child_concept_id) REFERENCES concepts(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_concept_id) REFERENCES concepts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_concept_parents_parent
    ON concept_parents(parent_concept_id);

-- Positions: lightweight claims that Arguments/Stances can target
CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Arguments / Stances: structured research records (not Work metadata)
CREATE TABLE IF NOT EXISTS arguments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('argument', 'stance')),
    main_text TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS argument_verdicts (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);

INSERT OR IGNORE INTO argument_verdicts (id, label, sort_order, enabled) VALUES
    ('supports', 'Supports', 1, 1),
    ('opposes', 'Opposes', 2, 1),
    ('qualifies', 'Qualifies', 3, 1),
    ('holds', 'Holds', 4, 1);

CREATE TABLE IF NOT EXISTS argument_target_positions (
    argument_id TEXT NOT NULL,
    position_id TEXT NOT NULL,
    verdict_id TEXT NOT NULL,
    order_index INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (argument_id, position_id),
    FOREIGN KEY (argument_id) REFERENCES arguments(id) ON DELETE CASCADE,
    FOREIGN KEY (position_id) REFERENCES positions(id) ON DELETE RESTRICT,
    FOREIGN KEY (verdict_id) REFERENCES argument_verdicts(id)
);

CREATE TABLE IF NOT EXISTS argument_target_arguments (
    argument_id TEXT NOT NULL,
    target_argument_id TEXT NOT NULL,
    verdict_id TEXT NOT NULL,
    order_index INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (argument_id, target_argument_id),
    CHECK (argument_id <> target_argument_id),
    FOREIGN KEY (argument_id) REFERENCES arguments(id) ON DELETE CASCADE,
    FOREIGN KEY (target_argument_id) REFERENCES arguments(id) ON DELETE RESTRICT,
    FOREIGN KEY (verdict_id) REFERENCES argument_verdicts(id)
);

CREATE INDEX IF NOT EXISTS idx_argument_target_arguments_target
    ON argument_target_arguments(target_argument_id);

CREATE INDEX IF NOT EXISTS idx_argument_target_positions_position
    ON argument_target_positions(position_id);

-- Staging inbox for files discovered under /data/for_processing.
-- Rows here are isolated from library/search until explicitly imported.
CREATE TABLE IF NOT EXISTS processing_files (
    id TEXT PRIMARY KEY,
    rel_path TEXT NOT NULL UNIQUE,
    abs_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'missing', 'imported', 'error')),
    last_error TEXT,
    imported_work_id TEXT,
    imported_at TIMESTAMP,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    title TEXT,
    status_draft TEXT CHECK(status_draft IN ('Planned', 'In Progress', 'Completed', 'Paused', 'Not Started')) DEFAULT 'Not Started',
    published_date TEXT,
    abstract TEXT,
    source_url TEXT,
    author_text TEXT,
    year TEXT,
    publisher TEXT,
    location TEXT,
    edition TEXT,
    journal TEXT,
    volume TEXT,
    issue TEXT,
    pages TEXT,
    isbn TEXT,
    doi TEXT,
    doc_type TEXT DEFAULT 'article',
    private_notes TEXT,
    thumb_page INTEGER,
    target_folder_id TEXT,
    FOREIGN KEY (imported_work_id) REFERENCES works(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_processing_files_status ON processing_files(status);
CREATE TABLE IF NOT EXISTS processing_file_roles (
    processing_file_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    role_type TEXT NOT NULL,
    order_index INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (processing_file_id, person_id, role_type, order_index),
    FOREIGN KEY (processing_file_id) REFERENCES processing_files(id) ON DELETE CASCADE,
    FOREIGN KEY (person_id) REFERENCES persons(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS processing_file_tags (
    processing_file_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    PRIMARY KEY (processing_file_id, tag_id),
    FOREIGN KEY (processing_file_id) REFERENCES processing_files(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

-- FTS5 Indexing for semantic discovery of Works (includes free-text authors)
CREATE VIRTUAL TABLE IF NOT EXISTS works_fts USING fts5(
    title,
    abstract,
    text_content,
    author_text,
    content='works',
    content_rowid='rowid'
);

-- Triggers to keep FTS5 synchronized with the works table
CREATE TRIGGER IF NOT EXISTS works_ai AFTER INSERT ON works BEGIN
  INSERT INTO works_fts(rowid, title, abstract, text_content, author_text)
  VALUES (new.rowid, new.title, new.abstract, new.text_content, COALESCE(new.author_text, ''));
END;

CREATE TRIGGER IF NOT EXISTS works_ad AFTER DELETE ON works BEGIN
  INSERT INTO works_fts(works_fts, rowid, title, abstract, text_content, author_text)
  VALUES ('delete', old.rowid, old.title, old.abstract, old.text_content, COALESCE(old.author_text, ''));
END;

-- works_au (the UPDATE half) is defined in the Work identity section below:
-- schema 17 scopes it to the indexed columns.

-- Folders: Organizational containers for works/files
CREATE TABLE IF NOT EXISTS folders (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    private_notes TEXT,
    parent_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_id) REFERENCES folders(id) ON DELETE SET NULL
);

-- Folder Files: Linking works/files to folders
CREATE TABLE IF NOT EXISTS folder_files (
    folder_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    PRIMARY KEY (folder_id, work_id),
    FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
);
-- Tags: Flexible organizational labels
CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    color TEXT DEFAULT '#6d6cf7',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Work Tags: Linking works to tags
CREATE TABLE IF NOT EXISTS work_tags (
    work_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    PRIMARY KEY (work_id, tag_id),
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

-- Folder Tags: Linking folders to tags
CREATE TABLE IF NOT EXISTS folder_tags (
    folder_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    PRIMARY KEY (folder_id, tag_id),
    FOREIGN KEY (folder_id) REFERENCES folders(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

-- Alternate labels for a canonical tag (e.g. other languages); search/attach resolve to tag_id
CREATE TABLE IF NOT EXISTS tag_aliases (
    id TEXT PRIMARY KEY,
    tag_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tag_aliases_alias_nocase ON tag_aliases(alias COLLATE NOCASE);

-- Canonical publishers + alternate spellings; search resolves variants on works.publisher
CREATE TABLE IF NOT EXISTS publishers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_publishers_name_nocase ON publishers(name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS publisher_aliases (
    id TEXT PRIMARY KEY,
    publisher_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    FOREIGN KEY (publisher_id) REFERENCES publishers(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_publisher_aliases_alias_nocase ON publisher_aliases(alias COLLATE NOCASE);

-- People groups: hierarchical labels (e.g. Frankfurt School → Philosophy); many-to-many with persons
CREATE TABLE IF NOT EXISTS person_groups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id TEXT,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_id) REFERENCES person_groups(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS person_group_members (
    person_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    PRIMARY KEY (person_id, group_id),
    FOREIGN KEY (person_id) REFERENCES persons(id) ON DELETE CASCADE,
    FOREIGN KEY (group_id) REFERENCES person_groups(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_person_groups_name_nocase ON person_groups(name COLLATE NOCASE);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_name_nocase ON tags(name COLLATE NOCASE);

-- Saved Views: named search definitions. Live results are never stored.
CREATE TABLE IF NOT EXISTS saved_views (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL CHECK(TRIM(name) <> ''),

    mode TEXT NOT NULL
        CHECK(mode IN ('all', 'advanced', 'tag')),

    search_q TEXT NOT NULL DEFAULT '',
    search_tag TEXT NOT NULL DEFAULT '',
    search_author TEXT NOT NULL DEFAULT '',
    search_publisher TEXT NOT NULL DEFAULT '',

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CHECK (
        (
            mode = 'all'
            AND TRIM(search_q) <> ''
            AND search_tag = ''
            AND search_author = ''
            AND search_publisher = ''
        )
        OR
        (
            mode = 'advanced'
            AND search_tag = ''
            AND (
                TRIM(search_q) <> ''
                OR TRIM(search_author) <> ''
                OR TRIM(search_publisher) <> ''
            )
        )
        OR
        (
            mode = 'tag'
            AND TRIM(search_tag) <> ''
            AND search_q = ''
        )
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_saved_views_name_nocase
ON saved_views(name COLLATE NOCASE);

-- Folder title uniqueness is parent-scoped. Do not add a global folders(title) unique index;
-- existing DBs may have the same title under different parents.
CREATE INDEX IF NOT EXISTS idx_folders_parent_id ON folders(parent_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_folders_parent_title_nocase
    ON folders(COALESCE(parent_id, ''), LOWER(TRIM(title)));

CREATE INDEX IF NOT EXISTS idx_playlist_items_playlist_id ON playlist_items(playlist_id);
CREATE INDEX IF NOT EXISTS idx_works_last_opened_at ON works(last_opened_at);

CREATE TABLE sync_operations (
    op_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE sync_entity_revisions (
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (scope_type, scope_id)
);
-- Lifecycle and redirects survive deletion; intentionally no Tag foreign keys.
CREATE TABLE sync_tag_lifecycle (
    tag_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('active', 'merged', 'deleted')),
    target_tag_id TEXT,
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((state = 'merged' AND target_tag_id IS NOT NULL) OR
           (state != 'merged' AND target_tag_id IS NULL))
);

-- Orphaned managed PDFs whose deletion is still owed after the canonical Work
-- row was committed away. One row per managed basename (never a path), written
-- inside the same transaction as the Work-row delete and removed only once the
-- bytes are gone or another Work has claimed them. See "Post-delete cleanup
-- recovery" in AGENTS.md.
CREATE TABLE pending_pdf_cleanup (
    filename TEXT PRIMARY KEY,
    recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- NULL until a retry pass has tried this claim. Selection puts
    -- never-attempted claims first and then the least recently attempted, so
    -- a claim that can never settle rotates behind newer ones instead of
    -- consuming the bounded pass forever.
    last_attempt_at TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Work identity (#60 Slice A, schema 17): Work -> Manifestation -> Asset.
-- See docs/work-identity-model.md. Must stay statement-for-statement equal to
-- _V17_WORK_IDENTITY_SQL in backend/db_migrations.py; validate_current_schema
-- compares every object below by definition.
--
-- At schema 17 `works` is the only authority for every field. The
-- legacy_work_*_mirror views define the projection; the *_mirror_* triggers
-- keep manifestations/assets equal to it (works -> new rows, one direction);
-- the *_mirror_read_only triggers refuse any other write to a mirrored column.
-- roles, annotations and argument_sources are the leaf tables the migration
-- rebuilds with composite ownership FKs.
-- ---------------------------------------------------------------------------

CREATE TABLE manifestations (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    origin_work_id TEXT UNIQUE,
    kind TEXT NOT NULL DEFAULT 'unspecified' CHECK (kind IN ('unspecified', 'preprint', 'accepted_manuscript', 'published', 'edition', 'translation', 'reprint', 'web_page', 'video', 'other')),
    title TEXT CHECK (title IS NULL OR title <> ''),
    subtitle TEXT,
    abstract TEXT CHECK (abstract IS NULL OR abstract <> ''),
    language TEXT,
    doc_type TEXT,
    year TEXT,
    published_date TEXT,
    edition TEXT,
    publisher TEXT,
    location TEXT,
    journal TEXT,
    volume TEXT,
    issue TEXT,
    pages TEXT,
    isbn TEXT,
    doi TEXT,
    url TEXT,
    urldate TEXT,
    primary_asset_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (id, work_id),
    FOREIGN KEY (primary_asset_id, id) REFERENCES assets(id, manifestation_id)
        ON UPDATE NO ACTION ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE assets (
    id TEXT PRIMARY KEY,
    manifestation_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    origin_work_id TEXT UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('managed_file', 'external_stream')),
    role TEXT NOT NULL DEFAULT 'document' CHECK (role IN ('document', 'snapshot', 'readable_text', 'attachment', 'other')),
    storage_locator TEXT,
    source_locator TEXT,
    provider TEXT,
    provider_id TEXT,
    url TEXT,
    media_type TEXT,
    byte_size INTEGER,
    ingest_sha256 TEXT,
    content_sha256 TEXT,
    content_generation INTEGER NOT NULL DEFAULT 0,
    fingerprinted_at TIMESTAMP,
    origin TEXT NOT NULL CHECK (origin IN ('upload', 'processing_import', 'adopted', 'web_capture', 'legacy')),
    origin_url TEXT,
    origin_ref TEXT,
    derived_from_asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
    supersedes_asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
    state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'trashed')),
    thumb_page INTEGER,
    thumb_url TEXT,
    canonical_annotation_set_revision INTEGER NOT NULL DEFAULT 0,
    materialized_pdf_annotation_revision INTEGER NOT NULL DEFAULT 0,
    captured_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (id, manifestation_id),
    UNIQUE (id, work_id),
    FOREIGN KEY (manifestation_id, work_id) REFERENCES manifestations(id, work_id)
        ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE TABLE manifestation_relations (
    work_id TEXT NOT NULL,
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    relation TEXT NOT NULL CHECK (relation IN ('revision_of', 'published_version_of', 'new_edition_of', 'translation_of', 'reprint_of')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (from_id, to_id, relation),
    CHECK (from_id <> to_id),
    FOREIGN KEY (from_id, work_id) REFERENCES manifestations(id, work_id)
        ON UPDATE CASCADE ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (to_id, work_id) REFERENCES manifestations(id, work_id)
        ON UPDATE CASCADE ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE manifestation_identifiers (
    manifestation_id TEXT NOT NULL REFERENCES manifestations(id) ON DELETE CASCADE,
    scheme TEXT NOT NULL CHECK (length(scheme) > 0),
    value TEXT NOT NULL,
    normalized TEXT NOT NULL CHECK (length(normalized) > 0),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (manifestation_id, scheme, normalized)
);

CREATE TABLE sync_work_lifecycle (
    work_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('active', 'merged', 'deleted')),
    target_work_id TEXT,
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((state = 'merged' AND target_work_id IS NOT NULL) OR
           (state != 'merged' AND target_work_id IS NULL))
);

CREATE TABLE work_retirement_guard (
    id INTEGER PRIMARY KEY CHECK (0)
);

CREATE TABLE work_retirement (
    work_id TEXT PRIMARY KEY,
    must_clear INTEGER NOT NULL DEFAULT 1
        REFERENCES work_retirement_guard(id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE migration_quarantine (
    id INTEGER PRIMARY KEY,
    source_table TEXT NOT NULL,
    source_rowid INTEGER,
    row_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    quarantined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE annotations (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL,
    type TEXT,
    content TEXT,
    page_index INTEGER,
    color TEXT,
    geometry_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    asset_id TEXT,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE,
    FOREIGN KEY (asset_id, work_id) REFERENCES assets(id, work_id)
        ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE TABLE roles (
    person_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    role_type TEXT NOT NULL,
    order_index INTEGER DEFAULT 0,
    credit_name TEXT,
    manifestation_id TEXT,
    PRIMARY KEY (person_id, work_id, role_type, order_index),
    FOREIGN KEY (person_id) REFERENCES persons(id) ON DELETE CASCADE,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE,
    FOREIGN KEY (manifestation_id, work_id) REFERENCES manifestations(id, work_id)
        ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE TABLE argument_sources (
    argument_id TEXT NOT NULL,
    order_index INTEGER NOT NULL,
    work_id TEXT NOT NULL,
    manifestation_id TEXT,
    pages TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (argument_id, order_index),
    FOREIGN KEY (argument_id) REFERENCES arguments(id) ON DELETE CASCADE,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE,
    FOREIGN KEY (manifestation_id, work_id) REFERENCES manifestations(id, work_id)
        ON UPDATE CASCADE ON DELETE NO ACTION
);

CREATE INDEX idx_manifestations_work_id ON manifestations(work_id);
CREATE INDEX idx_manifestations_primary_asset ON manifestations(primary_asset_id, id);
CREATE INDEX idx_assets_manifestation ON assets(manifestation_id, work_id);
CREATE INDEX idx_assets_derived_from ON assets(derived_from_asset_id);
CREATE INDEX idx_assets_supersedes ON assets(supersedes_asset_id);
CREATE INDEX idx_manifestation_relations_from ON manifestation_relations(from_id, work_id);
CREATE INDEX idx_manifestation_relations_to ON manifestation_relations(to_id, work_id);
CREATE INDEX idx_manifestation_identifiers_lookup ON manifestation_identifiers(scheme, normalized);
CREATE INDEX idx_annotations_asset ON annotations(asset_id, work_id);
CREATE INDEX idx_roles_manifestation ON roles(manifestation_id, work_id);
CREATE INDEX idx_argument_sources_manifestation ON argument_sources(manifestation_id, work_id);
CREATE UNIQUE INDEX idx_argument_sources_citation ON argument_sources(argument_id, work_id, COALESCE(manifestation_id, ''), pages);

CREATE VIEW legacy_work_asset_mirror AS
SELECT
    s.id AS work_id,
    (COALESCE(s.file_path, '') <> ''
     OR s.kind_norm = 'video'
     OR COALESCE(s.provider, '') <> ''
     OR COALESCE(s.provider_id, '') <> ''
     OR COALESCE(s.source_mime, '') <> ''
     OR COALESCE(s.thumb_url, '') <> ''
     OR s.thumb_page IS NOT NULL
     OR COALESCE(s.canonical_annotation_set_revision, 0) <> 0
     OR COALESCE(s.materialized_pdf_annotation_revision, 0) <> 0) AS has_asset_value,
    CASE WHEN s.is_stream THEN 'external_stream' ELSE 'managed_file' END AS kind,
    s.locator AS storage_locator,
    s.provider AS provider,
    s.provider_id AS provider_id,
    CASE WHEN s.is_stream THEN s.source_url END AS url,
    CASE
        WHEN COALESCE(s.source_mime, '') <> '' THEN s.source_mime
        WHEN NOT s.is_stream AND s.locator IS NOT NULL THEN 'application/pdf'
    END AS media_type,
    s.thumb_page AS thumb_page,
    s.thumb_url AS thumb_url,
    s.canonical_annotation_set_revision AS canonical_annotation_set_revision,
    s.materialized_pdf_annotation_revision AS materialized_pdf_annotation_revision
FROM (
    SELECT
        w.id, w.file_path, w.source_url, w.source_mime, w.thumb_url, w.thumb_page,
        w.provider, w.provider_id, w.canonical_annotation_set_revision,
        w.materialized_pdf_annotation_revision,
        lower(trim(COALESCE(w.source_kind, ''), char(32, 9, 10, 11, 12, 13))) AS kind_norm,
        CASE lower(trim(COALESCE(w.source_kind, ''), char(32, 9, 10, 11, 12, 13)))
            WHEN 'video' THEN 1
            WHEN 'pdf' THEN 0
            ELSE (trim(COALESCE(w.file_path, ''), char(32, 9, 10, 11, 12, 13)) = ''
                  AND trim(COALESCE(w.source_url, ''), char(32, 9, 10, 11, 12, 13)) <> '')
        END AS is_stream,
        CASE
            WHEN substr(w.file_path, 1, 10) = '/api/pdfs/'
             AND length(w.file_path) > 10
             AND instr(substr(w.file_path, 11), '/') = 0
             AND instr(substr(w.file_path, 11), char(92)) = 0
             AND substr(w.file_path, 11) NOT IN ('.', '..')
             AND substr(w.file_path, 11) = trim(substr(w.file_path, 11), char(32, 9, 10, 11, 12, 13))
             AND NOT (substr(w.file_path, 11) GLOB '*%[0-9A-Fa-f][0-9A-Fa-f]*')
            THEN substr(w.file_path, 11)
        END AS locator
    FROM works w
) s;

CREATE VIEW legacy_work_manifestation_mirror AS
SELECT
    w.id AS work_id,
    w.doc_type AS doc_type,
    w.year AS year,
    w.published_date AS published_date,
    w.edition AS edition,
    w.publisher AS publisher,
    w.location AS location,
    w.journal AS journal,
    w.volume AS volume,
    w.issue AS issue,
    w.pages AS pages,
    w.isbn AS isbn,
    w.doi AS doi,
    CASE
        WHEN a.kind = 'external_stream' THEN NULL
        ELSE w.source_url
    END AS url,
    w.urldate AS urldate
FROM works w
LEFT JOIN assets a ON a.origin_work_id = w.id AND a.work_id = w.id;

CREATE TRIGGER works_au
AFTER UPDATE OF title, abstract, text_content, author_text ON works
BEGIN
  INSERT INTO works_fts(works_fts, rowid, title, abstract, text_content, author_text)
  VALUES ('delete', old.rowid, old.title, old.abstract, old.text_content, COALESCE(old.author_text, ''));
  INSERT INTO works_fts(rowid, title, abstract, text_content, author_text)
  VALUES (new.rowid, new.title, new.abstract, new.text_content, COALESCE(new.author_text, ''));
END;

CREATE TRIGGER works_manifestation_pointers_insert
BEFORE INSERT ON works
WHEN NEW.primary_manifestation_id IS NOT NULL OR NEW.citation_manifestation_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'WORK_POINTERS_START_NULL');
END;

CREATE TRIGGER works_manifestation_pointers_owned
BEFORE UPDATE OF primary_manifestation_id, citation_manifestation_id ON works
BEGIN
    SELECT RAISE(ABORT, 'WORK_PRIMARY_MANIFESTATION_REQUIRED')
    WHERE NEW.primary_manifestation_id IS NULL
      AND NOT EXISTS (SELECT 1 FROM work_retirement r WHERE r.work_id = NEW.id);
    SELECT RAISE(ABORT, 'MANIFESTATION_OWNER_MISMATCH')
    WHERE NEW.primary_manifestation_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM manifestations m
                      WHERE m.id = NEW.primary_manifestation_id AND m.work_id = NEW.id);
    SELECT RAISE(ABORT, 'MANIFESTATION_OWNER_MISMATCH')
    WHERE NEW.citation_manifestation_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM manifestations m
                      WHERE m.id = NEW.citation_manifestation_id AND m.work_id = NEW.id);
END;

CREATE TRIGGER works_retirement_clear
AFTER DELETE ON works
BEGIN
    DELETE FROM work_retirement WHERE work_id = OLD.id;
END;

CREATE TRIGGER work_retirement_delete_only_after_work
BEFORE DELETE ON work_retirement
BEGIN
    SELECT RAISE(ABORT, 'WORK_RETIREMENT_WORK_STILL_EXISTS')
    WHERE EXISTS (SELECT 1 FROM works w WHERE w.id = OLD.work_id);
END;

CREATE TRIGGER work_retirement_no_update
BEFORE UPDATE ON work_retirement
BEGIN
    SELECT RAISE(ABORT, 'WORK_RETIREMENT_IMMUTABLE');
END;

CREATE TRIGGER work_retirement_guard_no_update
BEFORE UPDATE ON work_retirement_guard
BEGIN
    SELECT RAISE(ABORT, 'WORK_RETIREMENT_IMMUTABLE');
END;

CREATE TRIGGER manifestations_pointer_target_move
BEFORE UPDATE OF id, work_id ON manifestations
WHEN NEW.id IS NOT OLD.id OR NEW.work_id IS NOT OLD.work_id
BEGIN
    SELECT RAISE(ABORT, 'MANIFESTATION_IS_POINTER_TARGET')
    WHERE EXISTS (SELECT 1 FROM works w WHERE w.id = OLD.work_id
                  AND (w.primary_manifestation_id = OLD.id OR w.citation_manifestation_id = OLD.id));
END;

CREATE TRIGGER manifestations_pointer_target_delete
BEFORE DELETE ON manifestations
BEGIN
    SELECT RAISE(ABORT, 'MANIFESTATION_IS_POINTER_TARGET')
    WHERE EXISTS (SELECT 1 FROM works w WHERE w.id = OLD.work_id
                  AND (w.primary_manifestation_id = OLD.id OR w.citation_manifestation_id = OLD.id));
END;

CREATE TRIGGER manifestation_origin_immutable
BEFORE UPDATE OF origin_work_id ON manifestations
WHEN NEW.origin_work_id IS NOT OLD.origin_work_id
BEGIN
    SELECT RAISE(ABORT, 'ORIGIN_WORK_IMMUTABLE');
END;

CREATE TRIGGER asset_origin_immutable
BEFORE UPDATE OF origin_work_id ON assets
WHEN NEW.origin_work_id IS NOT OLD.origin_work_id
BEGIN
    SELECT RAISE(ABORT, 'ORIGIN_WORK_IMMUTABLE');
END;

CREATE TRIGGER manifestations_mirror_read_only
BEFORE UPDATE OF doc_type, year, published_date, edition, publisher, location, journal, volume, issue, pages, isbn, doi, url, urldate ON manifestations
WHEN NEW.origin_work_id IS NOT NULL AND NEW.work_id = NEW.origin_work_id
 AND (NEW.doc_type, NEW.year, NEW.published_date, NEW.edition, NEW.publisher, NEW.location,
      NEW.journal, NEW.volume, NEW.issue, NEW.pages, NEW.isbn, NEW.doi, NEW.url, NEW.urldate)
     IS NOT (SELECT v.doc_type, v.year, v.published_date, v.edition, v.publisher, v.location,
                    v.journal, v.volume, v.issue, v.pages, v.isbn, v.doi, v.url, v.urldate
             FROM legacy_work_manifestation_mirror v WHERE v.work_id = NEW.work_id)
BEGIN
    SELECT RAISE(ABORT, 'MIRRORED_FIELD_READ_ONLY');
END;

CREATE TRIGGER assets_mirror_read_only
BEFORE UPDATE OF kind, storage_locator, provider, provider_id, url, media_type, thumb_page, thumb_url, canonical_annotation_set_revision, materialized_pdf_annotation_revision ON assets
WHEN NEW.origin_work_id IS NOT NULL AND NEW.work_id = NEW.origin_work_id
 AND (NEW.kind, NEW.storage_locator, NEW.provider, NEW.provider_id, NEW.url, NEW.media_type,
      NEW.thumb_page, NEW.thumb_url, NEW.canonical_annotation_set_revision,
      NEW.materialized_pdf_annotation_revision)
     IS NOT (SELECT v.kind, v.storage_locator, v.provider, v.provider_id, v.url, v.media_type,
                    v.thumb_page, v.thumb_url, v.canonical_annotation_set_revision,
                    v.materialized_pdf_annotation_revision
             FROM legacy_work_asset_mirror v WHERE v.work_id = NEW.work_id)
BEGIN
    SELECT RAISE(ABORT, 'MIRRORED_FIELD_READ_ONLY');
END;

CREATE TRIGGER works_mirror_ai
AFTER INSERT ON works
BEGIN
    INSERT INTO manifestations (id, work_id, origin_work_id, doc_type, year, published_date, edition,
                                publisher, location, journal, volume, issue, pages, isbn, doi, url, urldate)
    SELECT 'MF-' || hex(randomblob(16)), v.work_id, v.work_id, v.doc_type, v.year, v.published_date,
           v.edition, v.publisher, v.location, v.journal, v.volume, v.issue, v.pages, v.isbn, v.doi,
           v.url, v.urldate
    FROM legacy_work_manifestation_mirror v WHERE v.work_id = NEW.id;
    UPDATE works SET primary_manifestation_id =
        (SELECT m.id FROM manifestations m WHERE m.origin_work_id = NEW.id)
    WHERE id = NEW.id;
    INSERT INTO assets (id, manifestation_id, work_id, origin_work_id, kind, role, storage_locator,
                        provider, provider_id, url, media_type, thumb_page, thumb_url,
                        canonical_annotation_set_revision, materialized_pdf_annotation_revision, origin)
    SELECT 'AS-' || hex(randomblob(16)), m.id, m.work_id, v.work_id, v.kind, 'document', v.storage_locator,
           v.provider, v.provider_id, v.url, v.media_type, v.thumb_page, v.thumb_url,
           v.canonical_annotation_set_revision, v.materialized_pdf_annotation_revision, 'legacy'
    FROM legacy_work_asset_mirror v
    JOIN manifestations m ON m.origin_work_id = v.work_id AND m.work_id = v.work_id
    WHERE v.work_id = NEW.id AND v.has_asset_value
      AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.origin_work_id = NEW.id);
END;

CREATE TRIGGER works_mirror_manifestation_au
AFTER UPDATE OF doc_type, year, published_date, edition, publisher, location, journal, volume, issue, pages, isbn, doi, urldate, source_url ON works
BEGIN
    UPDATE manifestations
    SET (doc_type, year, published_date, edition, publisher, location, journal, volume, issue,
         pages, isbn, doi, url, urldate, updated_at) =
        (SELECT v.doc_type, v.year, v.published_date, v.edition, v.publisher, v.location, v.journal,
                v.volume, v.issue, v.pages, v.isbn, v.doi, v.url, v.urldate, CURRENT_TIMESTAMP
         FROM legacy_work_manifestation_mirror v WHERE v.work_id = NEW.id)
    WHERE origin_work_id = NEW.id AND work_id = NEW.id;
END;

CREATE TRIGGER works_mirror_asset_au
AFTER UPDATE OF file_path, source_kind, source_url, source_mime, thumb_url, thumb_page, provider, provider_id, canonical_annotation_set_revision, materialized_pdf_annotation_revision ON works
BEGIN
    INSERT INTO assets (id, manifestation_id, work_id, origin_work_id, kind, role, storage_locator,
                        provider, provider_id, url, media_type, thumb_page, thumb_url,
                        canonical_annotation_set_revision, materialized_pdf_annotation_revision, origin)
    SELECT 'AS-' || hex(randomblob(16)), m.id, m.work_id, v.work_id, v.kind, 'document', v.storage_locator,
           v.provider, v.provider_id, v.url, v.media_type, v.thumb_page, v.thumb_url,
           v.canonical_annotation_set_revision, v.materialized_pdf_annotation_revision, 'legacy'
    FROM legacy_work_asset_mirror v
    JOIN manifestations m ON m.origin_work_id = v.work_id AND m.work_id = v.work_id
    WHERE v.work_id = NEW.id AND v.has_asset_value
      AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.origin_work_id = NEW.id);
    UPDATE assets
    SET (kind, storage_locator, provider, provider_id, url, media_type, thumb_page, thumb_url,
         canonical_annotation_set_revision, materialized_pdf_annotation_revision, updated_at) =
        (SELECT v.kind, v.storage_locator, v.provider, v.provider_id, v.url, v.media_type,
                v.thumb_page, v.thumb_url, v.canonical_annotation_set_revision,
                v.materialized_pdf_annotation_revision, CURRENT_TIMESTAMP
         FROM legacy_work_asset_mirror v WHERE v.work_id = NEW.id)
    WHERE origin_work_id = NEW.id AND work_id = NEW.id;
    UPDATE manifestations
    SET (doc_type, year, published_date, edition, publisher, location, journal, volume, issue,
         pages, isbn, doi, url, urldate, updated_at) =
        (SELECT v.doc_type, v.year, v.published_date, v.edition, v.publisher, v.location, v.journal,
                v.volume, v.issue, v.pages, v.isbn, v.doi, v.url, v.urldate, CURRENT_TIMESTAMP
         FROM legacy_work_manifestation_mirror v WHERE v.work_id = NEW.id)
    WHERE origin_work_id = NEW.id AND work_id = NEW.id;
END;

CREATE TRIGGER assets_mirror_ai
AFTER INSERT ON assets
WHEN NEW.origin_work_id IS NOT NULL AND NEW.origin_work_id = NEW.work_id
BEGIN
    UPDATE manifestations SET primary_asset_id = NEW.id
    WHERE id = NEW.manifestation_id AND primary_asset_id IS NULL;
    UPDATE manifestations
    SET (doc_type, year, published_date, edition, publisher, location, journal, volume, issue,
         pages, isbn, doi, url, urldate, updated_at) =
        (SELECT v.doc_type, v.year, v.published_date, v.edition, v.publisher, v.location, v.journal,
                v.volume, v.issue, v.pages, v.isbn, v.doi, v.url, v.urldate, CURRENT_TIMESTAMP
         FROM legacy_work_manifestation_mirror v WHERE v.work_id = NEW.work_id)
    WHERE origin_work_id = NEW.work_id AND work_id = NEW.work_id;
END;

CREATE TRIGGER annotations_mirror_asset_ai
AFTER INSERT ON annotations
WHEN NEW.asset_id IS NULL
BEGIN
    INSERT INTO assets (id, manifestation_id, work_id, origin_work_id, kind, role, storage_locator,
                        provider, provider_id, url, media_type, thumb_page, thumb_url,
                        canonical_annotation_set_revision, materialized_pdf_annotation_revision, origin)
    SELECT 'AS-' || hex(randomblob(16)), m.id, m.work_id, v.work_id, v.kind, 'document', v.storage_locator,
           v.provider, v.provider_id, v.url, v.media_type, v.thumb_page, v.thumb_url,
           v.canonical_annotation_set_revision, v.materialized_pdf_annotation_revision, 'legacy'
    FROM legacy_work_asset_mirror v
    JOIN manifestations m ON m.origin_work_id = v.work_id AND m.work_id = v.work_id
    WHERE v.work_id = NEW.work_id
      AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.origin_work_id = NEW.work_id);
    UPDATE annotations SET asset_id =
        (SELECT a.id FROM assets a WHERE a.origin_work_id = NEW.work_id AND a.work_id = NEW.work_id)
    WHERE id = NEW.id;
END;

CREATE TRIGGER sync_revisions_mirror_asset_ai
AFTER INSERT ON sync_entity_revisions
WHEN NEW.scope_type IN ('work-source', 'pdf-annotation', 'work-field')
 AND json_valid(NEW.scope_id)
 AND json_type(NEW.scope_id) = 'array'
 AND (NEW.scope_type <> 'work-field' OR json_extract(NEW.scope_id, '$[1]') IS 'thumb_page')
BEGIN
    INSERT INTO assets (id, manifestation_id, work_id, origin_work_id, kind, role, storage_locator,
                        provider, provider_id, url, media_type, thumb_page, thumb_url,
                        canonical_annotation_set_revision, materialized_pdf_annotation_revision, origin)
    SELECT 'AS-' || hex(randomblob(16)), m.id, m.work_id, v.work_id, v.kind, 'document', v.storage_locator,
           v.provider, v.provider_id, v.url, v.media_type, v.thumb_page, v.thumb_url,
           v.canonical_annotation_set_revision, v.materialized_pdf_annotation_revision, 'legacy'
    FROM legacy_work_asset_mirror v
    JOIN manifestations m ON m.origin_work_id = v.work_id AND m.work_id = v.work_id
    WHERE v.work_id = json_extract(NEW.scope_id, '$[0]')
      AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.origin_work_id = v.work_id);
END;

CREATE TRIGGER argument_sources_mirror_pin_ai
AFTER INSERT ON argument_sources
WHEN NEW.manifestation_id IS NULL AND NEW.pages <> ''
BEGIN
    UPDATE argument_sources
    SET manifestation_id = (SELECT w.primary_manifestation_id FROM works w WHERE w.id = NEW.work_id)
    WHERE argument_id = NEW.argument_id AND order_index = NEW.order_index;
END;

-- Pre-existing indexes on the rebuilt leaf tables.
-- One relationship per (person, work, role): the identity the sync
-- protocol scopes a revision by. The table's PK includes order_index,
-- so without this the database would permit two rows for one scope.
CREATE UNIQUE INDEX IF NOT EXISTS idx_roles_person_work_role_unique
    ON roles(person_id, work_id, role_type);
CREATE INDEX IF NOT EXISTS idx_roles_work_id ON roles(work_id);
CREATE INDEX IF NOT EXISTS idx_roles_person_id ON roles(person_id);
CREATE INDEX IF NOT EXISTS idx_annotations_work_id ON annotations(work_id);
CREATE INDEX IF NOT EXISTS idx_argument_sources_work_id
    ON argument_sources(work_id);
