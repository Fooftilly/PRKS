-- PRKS Database Schema

PRAGMA foreign_keys = ON;

-- Schema version tracking: a single-row table updated by init_db after all migrations run.
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

-- Concepts: Abstract entities or theories
CREATE TABLE IF NOT EXISTS concepts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Arguments: Structured logical claims linked to works
CREATE TABLE IF NOT EXISTS arguments (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL,
    premise TEXT,
    conclusion TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
);

-- Roles: Bridge between Persons and Works
CREATE TABLE IF NOT EXISTS roles (
    person_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    role_type TEXT NOT NULL,
    order_index INTEGER DEFAULT 0,
    PRIMARY KEY (person_id, work_id, role_type, order_index),
    FOREIGN KEY (person_id) REFERENCES persons(id) ON DELETE CASCADE,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
);

-- Granular annotations/highlights
CREATE TABLE IF NOT EXISTS annotations (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL,
    type TEXT,
    content TEXT,
    page_index INTEGER,
    color TEXT,
    geometry_json TEXT, -- Serialized rects/quadPoints
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
);

-- Persisted annotation/comment snapshots (Legacy/Cache)
CREATE TABLE IF NOT EXISTS work_annotations (
    work_id TEXT PRIMARY KEY,
    annotations_json TEXT NOT NULL DEFAULT '[]',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (work_id) REFERENCES works(id) ON DELETE CASCADE
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

-- Folder title uniqueness (case-insensitive, mirrors app-level check in add_folder)
CREATE UNIQUE INDEX IF NOT EXISTS idx_folders_title_nocase ON folders(title COLLATE NOCASE);

-- Performance indexes for frequently queried FK columns
CREATE INDEX IF NOT EXISTS idx_roles_work_id ON roles(work_id);
CREATE INDEX IF NOT EXISTS idx_roles_person_id ON roles(person_id);
CREATE INDEX IF NOT EXISTS idx_annotations_work_id ON annotations(work_id);
CREATE INDEX IF NOT EXISTS idx_arguments_work_id ON arguments(work_id);
CREATE INDEX IF NOT EXISTS idx_playlist_items_playlist_id ON playlist_items(playlist_id);
CREATE INDEX IF NOT EXISTS idx_works_last_opened_at ON works(last_opened_at);
