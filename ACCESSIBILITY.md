# Accessibility Statement

This statement covers the **PRKS self-hosted browser application**, the web interface served by `prks_app.py`. It does not cover native desktop clients, which are planned but have not shipped ([#617](https://github.com/Fooftilly/PRKS/issues/617)).

PRKS is pre-1.0 software maintained by one person. This page describes what has been checked, what is known to be broken, and how to report problems. It is not a conformance claim.

## Goal and standard

PRKS **aims** to meet the [Web Content Accessibility Guidelines (WCAG) 2.2](https://www.w3.org/TR/WCAG22/) at level **AA**. That is a target. PRKS has **not** been audited against WCAG 2.2, holds no accessibility certification, and does **not** currently conform: several known AA failures are listed below.

Focus, keyboard behavior, reduced motion and touch-target size are part of the PRKS design rules in [DESIGN.md](DESIGN.md) ("Accessibility is part of the design system"). The v1.0 release gate ([#616](https://github.com/Fooftilly/PRKS/issues/616)) tracks accessibility as a release criterion.

## What is verified, and how

Only the checks below run as tests. Anything not listed here has not been verified.

| Area | Evidence | Limits |
| --- | --- | --- |
| Keyboard behavior of specific features | The required Chromium end-to-end (E2E) gate (Playwright, run on every pull request that changes the app) includes keyboard tests. Examples: Escape closing a pristine modal and returning focus to the control that opened it; a dirty modal asking before it closes; the workspace split dividers resizing with arrow keys, Home and End while keeping `aria-valuenow` current; tab actions and the tab overflow menu operated by keyboard only; focus moving back to Main when a split pane closes; the folder navigation opened, used and closed by keyboard. | These cover the features they name. They do not show that the whole app is usable by keyboard. Known keyboard gaps are listed below. |
| Component names, labels and states | Vitest contract tests for the shared Vue components (`frontend-app`) check accessible names, label/`for` pairing, error live regions, disclosure `aria-expanded`/`aria-controls`, and disabled/busy states. See [frontend-app/README.md](frontend-app/README.md#accessibility-coverage). | They cover the shared Vue components, not every screen. Much of the interface is still the classic JavaScript UI. |
| Automated rule scan | An **advisory** [axe-core](https://github.com/dequelabs/axe-core) scan ([#856](https://github.com/Fooftilly/PRKS/issues/856)) runs WCAG 2.0/2.1 A and AA rules in Chromium over five synthetic screens: app shell, Folder library, Work detail, PDF viewer and Settings. It also checks that Enter opens Settings and Escape restores focus. Details are in [Testing](docs/wiki/Testing.md#advisory-axe-core-browser-accessibility-smoke-856). | It runs on demand, not on every change, and it does not block merges. It reports violations as debt and does not fail on them. Automated rules find only part of the possible problems. |
| Other browser engines | An optional Firefox and WebKit smoke test includes opening and closing Settings by keyboard with focus restored. | It is manual and is not part of the required gate. |

**Browsers.** Chromium is the only browser engine with a required test gate. Firefox and WebKit have an optional smoke test only. No browser or operating system is formally supported yet.

**Assistive technology.** PRKS has **not** been tested with any screen reader (for example NVDA, JAWS, VoiceOver, Orca or TalkBack), screen magnifier, voice control or switch access. Reports from people who use them are especially welcome.

**Reduced motion.** The interface honors the `prefers-reduced-motion: reduce` setting by removing transitions. This is implemented in CSS but has no dedicated test.

**Themes and layout.** Settings offers System, Light and Dark themes and a **Force mobile layout** option (drawer navigation and an overlay details panel). Neither a high-contrast theme nor support for forced colors (Windows High Contrast) exists yet. Browser zoom and 320 px reflow have not been tested.

## Known limitations

These are open, tracked problems. Each fix belongs to its own issue.

- **Contrast:** several text and control colors fail the AA contrast ratio in both Light and Dark, including primary buttons, tertiary and placeholder text, document-type badges and Light accent links ([#425](https://github.com/Fooftilly/PRKS/issues/425)). Accessibility-oriented themes are planned in [#423](https://github.com/Fooftilly/PRKS/issues/423).
- **Focus trapping:** create and settings modals do not keep keyboard focus inside the modal; Tab can reach the page behind ([#154](https://github.com/Fooftilly/PRKS/issues/154)).
- **No skip link:** keyboard users pass about 27 sidebar, ribbon and tab controls before reaching page content ([#830](https://github.com/Fooftilly/PRKS/issues/830)).
- **Forms that cannot be finished by keyboard:** New Folder, New Group and New Person ([#603](https://github.com/Fooftilly/PRKS/issues/603)), and New Playlist ([#589](https://github.com/Fooftilly/PRKS/issues/589)).
- **Pickers that ignore ArrowDown and Enter:** parent pickers in New Folder, Move folder and New Group ([#467](https://github.com/Fooftilly/PRKS/issues/467)), and the Person Group member picker ([#709](https://github.com/Fooftilly/PRKS/issues/709)).
- **Focus lost or misplaced after dialogs:** closing some prompts drops focus to the page body ([#487](https://github.com/Fooftilly/PRKS/issues/487)); a "Close without saving?" prompt focuses its destructive button ([#842](https://github.com/Fooftilly/PRKS/issues/842)).
- **Missing state cues:** the Cards/List toggle has no visible selected state or focus ring ([#411](https://github.com/Fooftilly/PRKS/issues/411)).
- **Other keyboard gaps:** Enter does not add a Tag alias ([#608](https://github.com/Fooftilly/PRKS/issues/608)); there are few keyboard shortcuts beyond Ctrl+K and no shortcut reference ([#566](https://github.com/Fooftilly/PRKS/issues/566)); a PDF reopened on its remembered page ignores keyboard paging for several seconds ([#377](https://github.com/Fooftilly/PRKS/issues/377)).
- **PDF content:** the reader shows PDFs as they are. PRKS does not add structure, tags or alternative text to a PDF that lacks them, and the accessibility of the PDF viewer with assistive technology is untested.

This list may be incomplete. Search [open UX issues](https://github.com/Fooftilly/PRKS/issues?q=is%3Aissue+is%3Aopen+label%3Aux-finding) for the current set.

## Reporting a barrier

Report accessibility problems as public GitHub issues using the **UX / UI finding** template, with the classification **ACCESSIBILITY**: [open a new issue](https://github.com/Fooftilly/PRKS/issues/new/choose).

A useful report says:

- what you were trying to do, and where in PRKS (for example "New Folder dialog");
- what happened, and what you expected instead;
- the steps to reproduce it;
- your browser and version, and operating system;
- any assistive technology or browser setting involved (screen reader, magnifier, zoom level, high-contrast or reduced-motion settings);
- optionally, a screenshot or recording **made from synthetic data**, never from your real library.

You never need to explain a disability or share health or identity information to report a barrier. Describe the problem and the setup that shows it.

**Reports that need privacy.** If a report can only be made with personal or sensitive details, do not put them in a public issue. Open a minimal public issue that says you need a private channel, without those details, and the maintainer will arrange one. Security vulnerabilities go through the private process in [SECURITY.md](SECURITY.md).

PRKS is maintained on a best-effort basis. There is no guaranteed response time for accessibility reports.

## Keeping this statement current

This statement is reviewed when any of these changes:

- an accessibility issue listed above is fixed, or a new one is confirmed;
- accessibility testing changes (for example the axe-core scan becomes required, or a browser or assistive technology is tested);
- the supported browsers or the v1.0 release criteria in [#616](https://github.com/Fooftilly/PRKS/issues/616) change.

Changes to this file go through ordinary pull requests, so its history shows each revision.

_Last reviewed: 2026-11-06._
