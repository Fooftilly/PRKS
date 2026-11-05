-- Search (L-S-1 #637; docs/postgresql-architecture.md §8.3, §8.6): the text
-- search configuration, the query-side helpers, and the two derived tables
-- search reads.
--
-- Before v1.0.0 this is a baseline file: it may be edited in place, and a
-- database that applied an older version of it must be recreated (§4.4).

-- The text search configuration. `simple` only lowercases, so every text
-- indexed with it, and every query token, is first passed through
-- prks.search_fold (NFC, then full case folding pinned to pg_unicode_fast):
-- Greek final and medial sigma fold together and one Unicode
-- implementation, PostgreSQL's, folds both sides. Diacritics fold through
-- prks.search_unaccent, the dictionary prks.search_norm uses, for every
-- token type that can hold a non-ASCII letter (words, hyphenated words and
-- their parts, with or without digits: `café2026` folds like `cafe2026`).
-- No stemming (§8.3): prefix matching stands in for inflection.
CREATE TEXT SEARCH CONFIGURATION prks.prks_search (COPY = pg_catalog.simple);
ALTER TEXT SEARCH CONFIGURATION prks.prks_search
  ALTER MAPPING FOR hword, hword_part, word, numword, numhword, hword_numpart
  WITH prks.search_unaccent, simple;

-- NFC and full case folding, the step before to_tsvector and to_tsquery
-- (§8.3). Every function it calls is IMMUTABLE.
CREATE FUNCTION prks.search_fold(s text) RETURNS text
  LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
BEGIN ATOMIC
  SELECT casefold(normalize(s, NFC) COLLATE pg_unicode_fast);
END;

-- One folded query token as a quoted prefix operand of to_tsquery, quoted by
-- tsquery's own rules: a backslash and a quote are escaped by doubling
-- (`\` -> `\\`, `'` -> `''`). quote_literal is not used: it returns `E'…'`
-- for a token holding a backslash, and to_tsquery would read the `E` as a
-- lexeme (§8.7). The configuration's parser drops punctuation inside the
-- operand as it does on the indexed side.
CREATE FUNCTION prks.tsquery_prefix(f text) RETURNS text
  LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
BEGIN ATOMIC
  SELECT '''' || replace(replace(f, '\', '\\'), '''', '''''') || ''':*';
END;

-- A LIKE needle's literal text: `\`, `%` and `_` escaped for ESCAPE '\'.
-- Applied after normalization, so a character that folds into one of them
-- stays literal (§8.3).
CREATE FUNCTION prks.like_escape(s text) RETURNS text
  LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
BEGIN ATOMIC
  SELECT replace(replace(replace(s, '\', '\\'), '%', '\%'), '_', '\_');
END;

-- A free-text query token for the substring paths (§8.3): normalized, then
-- stripped of punctuation as today's token cleanup does. An empty result is
-- skipped by the caller. IMMUTABLE on the same promise as prks.search_norm.
CREATE FUNCTION prks.search_token(t text) RETURNS text
  LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
BEGIN ATOMIC
  SELECT regexp_replace(prks.search_norm(t), '[[:punct:]]+', '', 'g');
END;

-- A Work's search document and infix-fallback text, from its inputs (§8.3).
-- One definition for every writer of derived.work_search: the refresh
-- (backend/domain/search/refresh.py) and a Work create, which computes its
-- new row from the values it inserts. IMMUTABLE on the same promise as
-- prks.search_norm; nothing indexes or defaults to their output.
CREATE FUNCTION prks.work_search_doc(title text, people text, author_text text, abstract text,
                                     notes text) RETURNS tsvector
  LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
BEGIN ATOMIC
  SELECT setweight(to_tsvector('prks.prks_search'::regconfig, prks.search_fold(left(title, 50000))), 'A')
      || setweight(to_tsvector('prks.prks_search'::regconfig,
                               prks.search_fold(left(people || ' ' || author_text, 50000))), 'B')
      || setweight(to_tsvector('prks.prks_search'::regconfig, prks.search_fold(left(abstract, 50000))), 'C')
      || setweight(to_tsvector('prks.prks_search'::regconfig, prks.search_fold(left(notes, 50000))), 'D');
END;

CREATE FUNCTION prks.work_search_text(title text, author_text text, abstract text, notes text)
  RETURNS text
  LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
BEGIN ATOMIC
  SELECT prks.search_norm(concat_ws(' ', nullif(title, ''), nullif(author_text, ''),
                                    nullif(abstract, ''), nullif(notes, '')));
END;

-- One row per Work: its search document and the normalized text the
-- substring and trigram paths match (§8.3). Written only through
-- backend/domain/search/refresh.py (refresh_work_search), by the commands
-- that change a Work's search input, in their own transactions, and by
-- rebuild-derived and the repair job. Rebuildable and excluded from backup.
-- No foreign key: DELETE_WORK deletes the row itself after the Work, and the
-- refresh never inserts a row for a Work that is gone, so a key would add
-- nothing but a Work delete waiting on, or cascading into, a derived table.
-- The *_norm columns are plain columns that the refresh writes through
-- prks.search_norm; nothing generates or indexes that function's output.
--   doc         title A, linked people and author text B, abstract C,
--               Research Note D (to_tsvector of prks.search_fold); each
--               of the four inputs contributes its first 50000 characters
--               (at most about 700 kB of tsvector even for short distinct
--               words), so a long note can never exceed PostgreSQL's 1 MB
--               tsvector limit and fail the command that saved it. The rest
--               stays in text_norm.
--   title_norm  search_norm(displayed title)
--   people_norm search_norm of each linked person's name and each alias,
--               one per line
--   text_norm   search_norm(title, author text, abstract, Research Note):
--               the infix fallback's text
CREATE TABLE derived.work_search (
  library_id  text        NOT NULL,
  work_id     text        PRIMARY KEY,
  doc         tsvector    NOT NULL,
  title_norm  text        NOT NULL,
  people_norm text        NOT NULL,
  text_norm   text        NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX work_search_library ON derived.work_search (library_id, work_id);
CREATE INDEX work_search_doc ON derived.work_search USING gin (doc);
CREATE INDEX work_search_trgm ON derived.work_search
  USING gin (title_norm ext.gin_trgm_ops, people_norm ext.gin_trgm_ops);
CREATE INDEX work_search_text_trgm ON derived.work_search USING gin (text_norm ext.gin_trgm_ops);

-- Text extracted from a managed PDF Asset (§8.3, PDF text), filled by the
-- `text.extract` job. Keyed by the Asset and the content it was extracted
-- from, so new bytes are a new extraction and a stale row is never served
-- for them. The job locks the Asset row FOR SHARE and writes nothing when
-- the Asset is gone or its content_sha256 is no longer the one extracted;
-- an Asset delete removes the Asset's rows and a content replace its rows
-- for any other hash, in the transaction that changes the Asset. A PDF that
-- cannot be read is an outcome: its row has status 'failed' and no text.
CREATE TABLE derived.asset_text (
  library_id        text     NOT NULL,
  asset_id          text     NOT NULL,
  content_sha256    text     NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  extractor_version integer  NOT NULL CHECK (extractor_version > 0),
  status            text     NOT NULL CHECK (status IN ('indexed', 'empty', 'failed')),
  truncated         boolean  NOT NULL DEFAULT false,
  text              text     NOT NULL DEFAULT '',
  doc               tsvector NOT NULL,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (asset_id, content_sha256),
  CONSTRAINT asset_text_status_text_check CHECK (status = 'indexed' OR text = '')
);
CREATE INDEX asset_text_library ON derived.asset_text (library_id, asset_id);
CREATE INDEX asset_text_doc ON derived.asset_text USING gin (doc);
