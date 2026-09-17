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

-- Works: Metadata for documents and notes
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
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

CREATE TABLE IF NOT EXISTS argument_sources (
    argument_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    pages TEXT NOT NULL DEFAULT '',
    order_index INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (argument_id, work_id),
    FOREIGN KEY (argument_id) REFERENCES arguments(id) ON DELETE CASCADE,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_argument_sources_work_id
    ON argument_sources(work_id);

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

-- Roles: Bridge between Persons and Works
CREATE TABLE IF NOT EXISTS roles (
    person_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    role_type TEXT NOT NULL,
    order_index INTEGER DEFAULT 0,
    credit_name TEXT,
    PRIMARY KEY (person_id, work_id, role_type, order_index),
    FOREIGN KEY (person_id) REFERENCES persons(id) ON DELETE CASCADE,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
);

-- Canonical PRKS annotation metadata (sidebar/comments). PDF bytes hold rendered markup.
CREATE TABLE IF NOT EXISTS annotations (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL,
    type TEXT,
    content TEXT,
    page_index INTEGER,
    color TEXT,
    geometry_json TEXT, -- Remaining JSON fields for EmbedPDF round-trip
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
);

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

CREATE TRIGGER IF NOT EXISTS works_au AFTER UPDATE ON works BEGIN
  INSERT INTO works_fts(works_fts, rowid, title, abstract, text_content, author_text)
  VALUES ('delete', old.rowid, old.title, old.abstract, old.text_content, COALESCE(old.author_text, ''));
  INSERT INTO works_fts(rowid, title, abstract, text_content, author_text)
  VALUES (new.rowid, new.title, new.abstract, new.text_content, COALESCE(new.author_text, ''));
END;

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

-- Performance indexes for frequently queried FK columns
-- One relationship per (person, work, role): the identity the sync
-- protocol scopes a revision by. The table's PK includes order_index,
-- so without this the database would permit two rows for one scope.
CREATE UNIQUE INDEX IF NOT EXISTS idx_roles_person_work_role_unique
    ON roles(person_id, work_id, role_type);
CREATE INDEX IF NOT EXISTS idx_roles_work_id ON roles(work_id);
CREATE INDEX IF NOT EXISTS idx_roles_person_id ON roles(person_id);
CREATE INDEX IF NOT EXISTS idx_annotations_work_id ON annotations(work_id);
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
