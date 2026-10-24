-- Schemas, extensions and the shared text functions (docs/postgresql-architecture.md
-- §6.1, §8.1, §8.3). Applied first, to an empty database, by backend/db/migrate.py.
--
-- Before v1.0.0 this is a baseline file: it may be edited in place, and a
-- database that applied an older version of it must be recreated (§4.4).
-- Every name is schema-qualified; the runner applies files with an empty
-- search_path. The runner itself creates schema prks and prks.schema_migrations.

CREATE SCHEMA ext;      -- extensions only
CREATE SCHEMA derived;  -- rebuildable projections; its tables arrive with their lanes (§6.5)

CREATE EXTENSION pg_trgm SCHEMA ext;
CREATE EXTENSION unaccent SCHEMA ext;

-- The one diacritic-folding dictionary (§8.3). Search's text search
-- configuration (L-S-1) and prks.search_norm both use it, so the rules are
-- chosen in one place. A PRKS rules file, if ever needed, ships as a new
-- name ('prks_unaccent_v<N>'), never an edit in place.
CREATE TEXT SEARCH DICTIONARY prks.search_unaccent (TEMPLATE = ext.unaccent, RULES = 'unaccent');

-- Identity key (§8.1, PG9): when two names are "the same name".
--   identity_key(s) = casefold(collapse_whitespace(trim(NFC(s))))
-- Whitespace is the explicit set Python's str.split() splits on (Unicode
-- White_Space plus U+001C..U+001F), so the rule does not depend on a locale's
-- regex classes. casefold is pinned to pg_unicode_fast (full case folding:
-- "ß" -> "ss"), which makes the result independent of the caller's collation.
-- Every function the body calls is IMMUTABLE; tests/pg/test_text_identity.py
-- proves it from the stored body. backend/domain/text_identity.py is the
-- Python twin; tests/fixtures/text_identity_vectors.json is run against both.
CREATE FUNCTION prks.identity_key(s text) RETURNS text
  LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
BEGIN ATOMIC
  SELECT casefold(
    btrim(
      regexp_replace(
        normalize(s, NFC),
        '[\x09-\x0D\x1C-\x20\x85\xA0\x1680\x2000-\x200A\x2028\x2029\x202F\x205F\x3000]+',
        ' ',
        'g'),
      ' ')
    COLLATE pg_unicode_fast);
END;

-- Search normalization (§8.3): NFC, full case folding, then diacritic folding
-- through prks.search_unaccent. unaccent is STABLE and reads rules from the
-- server's disk, so IMMUTABLE here is a promise PRKS makes, not one PostgreSQL
-- checks: the rules are fixed per image, and nothing indexes or defaults to
-- this function's output (the *_norm columns are written by commands and
-- rebuild-derived). The schema snapshot check asserts that no index
-- expression or column default depends on it (scripts/check_pg_schema.py).
CREATE FUNCTION prks.search_norm(s text) RETURNS text
  LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
BEGIN ATOMIC
  SELECT ext.unaccent(
    'prks.search_unaccent'::regdictionary,
    casefold(normalize(s, NFC) COLLATE pg_unicode_fast));
END;
