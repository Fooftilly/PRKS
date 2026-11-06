"""Synthetic multilingual search corpus with judged queries (L-S-2 #638).

The acceptance of L-S-2 is a before/after comparison of keyword search
order on a synthetic multilingual corpus, approved in review
(``docs/postgresql-architecture.md`` §8.3, Ranking; §15.3). This module is
that corpus. It is invented data: titles, people and notes imitate a
research library in English, Greek, Serbian (Cyrillic and Latin), German
and French, but no record comes from a real library.

Each :class:`CorpusWork` is created in list order and edited one minute
after the previous one, so a later entry is always the more recently
updated Work. Recency is the tie-break inside a Phase 1 tier, so the order
of the list is part of the corpus. The relevant Works of a query are
usually listed before its distractors, which keeps recency from helping
them.

Each :class:`JudgedQuery` grades the Works it should find:

* 3: what the person is looking for;
* 2: clearly relevant;
* 1: a marginal match, acceptable low in the list.

The graded Works are exactly the Works the query finds, so the corpus also
pins the found set: L-S-2 changes the order of the Works a search finds,
never which Works it finds (``tests/pg/test_search_ranking.py``). The
report still scores an ungraded Work as grade 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CorpusWork:
    key: str
    title: str
    abstract: str = ""
    notes: str = ""
    author_text: str = ""
    # Linked people as (first name, last name), credited as Author.
    people: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class JudgedQuery:
    text: str
    language: str
    grades: dict[str, int] = field(default_factory=dict)
    # What the query is meant to show about the ranking.
    why: str = ""


def _repeat(word: str, times: int, filler: str) -> str:
    return " ".join(f"{word} {filler}" for _ in range(times))


WORKS: tuple[CorpusWork, ...] = (
    # English: political philosophy.
    CorpusWork("theory-of-justice", "A Theory of Justice", people=(("Johanna", "Ravel"),),
               abstract="Principles chosen behind a veil of ignorance."),
    CorpusWork("justice-lectures", "Seminar notes, week 3",
               notes=_repeat("justice", 14, "came up again in the discussion of")),
    CorpusWork("justice-review", "Review: fairness and its critics",
               abstract="A short review that mentions justice once."),
    CorpusWork("the-republic", "The Republic", people=(("Aristocles", "Platonides"),),
               abstract="A dialogue on the just city and the just soul."),
    CorpusWork("republic-companion", "A Companion to the Republic",
               abstract="Essays on books I to X."),
    CorpusWork("republic-notes", "Reading log",
               notes="Plato's republic, then the laws; republic again next week."),
    CorpusWork("leviathan", "Leviathan", author_text="Hobbes, T.",
               abstract="On the commonwealth and the state of nature."),
    CorpusWork("state-of-nature", "The State of Nature Revisited",
               abstract="Hobbes and Locke compared."),
    CorpusWork("statecraft", "Statecraft in Early Modern Europe"),
    # English: people.
    CorpusWork("kant-critique", "Critique of Pure Reason", people=(("Immanuel", "Kantor"),)),
    CorpusWork("kant-groundwork", "Groundwork of the Metaphysics of Morals",
               people=(("Immanuel", "Kantor"),)),
    CorpusWork("about-kantor", "Kantor and the Limits of Reason",
               abstract="A study of the first Critique."),
    CorpusWork("kantor-mention", "Notes on idealism", notes="See also Kantor, but briefly."),
    # German: compounds and infixes.
    CorpusWork("erkenntnistheorie", "Erkenntnistheorie und Logik",
               abstract="Grundfragen der Wissenschaft."),
    CorpusWork("theorie-gerechtigkeit", "Vorlesungen",
               notes="Eine Theorie der Gerechtigkeit wurde nur am Rande besprochen."),
    CorpusWork("wissenschaftstheorie", "Einführung in die Wissenschaftstheorie"),
    CorpusWork("gerechtigkeit", "Gerechtigkeit als Fairness",
               abstract="Eine Einführung in die Gerechtigkeit."),
    # French: diacritics, suffixes and repetition.
    CorpusWork("liberte-anciens", "De la liberté des anciens comparée à celle des modernes",
               people=(("Benjamine", "Constantin"),)),
    CorpusWork("liberte-notes", "Carnet de lecture",
               notes=_repeat("liberté", 10, "revient sans cesse, comme la")),
    CorpusWork("phenomenologie", "Phénoménologie de l'esprit",
               abstract="Une lecture suivie."),
    CorpusWork("anthropologie-mention", "Essais divers",
               abstract="Quelques remarques sur l'anthropologie."),
    # Greek: case, tonos, final sigma.
    CorpusWork("politeia", "Πολιτεία", people=(("Αριστοκλής", "Πλατωνίδης"),)),
    CorpusWork("politika", "Πολιτικά", abstract="Για την πολιτεία και τους πολίτες."),
    CorpusWork("sokratis", "Σωκράτης και η απολογία"),
    CorpusWork("sokratis-notes", "Σημειώσεις", notes="Ο Σωκράτης αναφέρεται στο τέλος."),
    # Serbian, Cyrillic and Latin, never transliterated.
    CorpusWork("drzava-cyr", "Држава", abstract="Дијалог о праведности."),
    CorpusWork("drzava-cyr-notes", "Белешке", notes="Држава, па онда закони."),
    CorpusWork("drzava-lat", "Država i pravo", abstract="Uvod u teoriju države."),
    CorpusWork("drzava-lat-notes", "Beleške", notes="Država se pominje usput."),
    CorpusWork("pravda", "O pravdi", abstract="Pravda kao vrlina."),
    # Fuzzy title matches: a long title the query names closely.
    CorpusWork("nicomachean", "Nicomachean Ethics", people=(("Aristos", "Stageira"),)),
    CorpusWork("ethics-intro", "Introduction to Ethics",
               abstract="Covers Nicomachean themes in passing."),
    CorpusWork("ethics-notes", "Course plan",
               notes="Ethics week: nicomachean selections, then Kantor."),
    # Recent distractors: the newest Works, so recency favors them in every tier.
    CorpusWork("newest-justice-note", "Inbox", notes="Unsorted: justice, republic, liberté, state."),
    CorpusWork("newest-theorie", "Neu", notes="Theorie (nachlesen)."),
)


QUERIES: tuple[JudgedQuery, ...] = (
    JudgedQuery("justice", "en",
                {"theory-of-justice": 3, "justice-review": 2, "justice-lectures": 1,
                 "newest-justice-note": 1},
                "A title hit should outrank a note that repeats the word many times."),
    JudgedQuery("theory of justice", "en", {"theory-of-justice": 3},
                "An exact title should come first."),
    JudgedQuery("republic", "en",
                {"the-republic": 3, "republic-companion": 2, "republic-notes": 1,
                 "newest-justice-note": 1},
                "The short exact title before longer titles and notes."),
    JudgedQuery("republic platonides", "en", {"the-republic": 3},
                "Title and credited person together; the reading log names Plato only by name."),
    JudgedQuery("state", "en",
                {"state-of-nature": 3, "statecraft": 2, "leviathan": 2, "newest-justice-note": 1},
                "Title hits first; the abstract and the note after."),
    JudgedQuery("kantor", "en",
                {"kant-critique": 3, "kant-groundwork": 3, "about-kantor": 2,
                 "kantor-mention": 1, "ethics-notes": 1},
                "Works credited to a person, and a title naming them, before passing mentions."),
    JudgedQuery("nicomachean ethics", "en",
                {"nicomachean": 3, "ethics-intro": 2, "ethics-notes": 1},
                "An exact title first."),
    JudgedQuery("theorie", "de",
                {"erkenntnistheorie": 3, "wissenschaftstheorie": 3,
                 "theorie-gerechtigkeit": 1, "newest-theorie": 1},
                "A German compound in a title is only an infix match, yet it is the target."),
    JudgedQuery("gerechtigkeit", "de",
                {"gerechtigkeit": 3, "theorie-gerechtigkeit": 1},
                "A title hit before a note."),
    JudgedQuery("liberte", "fr",
                {"liberte-anciens": 3, "liberte-notes": 1, "newest-justice-note": 1},
                "Unaccented query; a title hit before a repetitive note."),
    JudgedQuery("logie", "fr",
                {"phenomenologie": 2, "anthropologie-mention": 1},
                "Two infix-only matches: the title one first."),
    JudgedQuery("πολιτεια", "el", {"politeia": 3, "politika": 2},
                "Tonos folded; the title before the abstract."),
    JudgedQuery("σωκρατης", "el", {"sokratis": 3, "sokratis-notes": 1},
                "Final sigma and tonos folded; title before note."),
    JudgedQuery("држава", "sr-Cyrl", {"drzava-cyr": 3, "drzava-cyr-notes": 1},
                "Cyrillic title before Cyrillic note; Latin Works are not found."),
    JudgedQuery("drzava", "sr-Latn", {"drzava-lat": 3, "drzava-lat-notes": 1},
                "Diacritics folded; Latin title before Latin note."),
    JudgedQuery("pravd", "sr-Latn", {"pravda": 3},
                "A prefix of an inflected word."),
)


def validate() -> list[str]:
    """Problems in the corpus itself, empty when it is consistent."""
    problems: list[str] = []
    keys = [work.key for work in WORKS]
    if len(keys) != len(set(keys)):
        problems.append("duplicate Work keys")
    known = set(keys)
    texts = [query.text for query in QUERIES]
    if len(texts) != len(set(texts)):
        problems.append("duplicate query texts")
    for query in QUERIES:
        if not query.grades:
            problems.append(f"{query.text!r} grades no Work")
        if max(query.grades.values(), default=0) < 2:
            problems.append(f"{query.text!r} has no Work graded 2 or 3")
        for key, grade in query.grades.items():
            if key not in known:
                problems.append(f"{query.text!r} grades unknown Work {key!r}")
            if grade not in (1, 2, 3):
                problems.append(f"{query.text!r} grades {key!r} {grade}, not 1 to 3")
    return problems
