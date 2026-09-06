# PRKS design language

PRKS is a dense, calm, utilitarian research workspace. It should feel closer to a native research/database application than a consumer website. Information hierarchy comes from typography, spacing, borders, selection and restrained color—not decorative surfaces.

Core visual attributes: dense, structured, flat, square, quiet, precise, information-first, desktop-app-like, still usable on narrow/mobile PWA layouts.

This document is the authoritative visual and interaction contract for every future PRKS UI change.

---

## Purpose and authority

This file exists so UI work does not invent a second look, a second button family, or a second status language.

It governs:

- `frontend/css/style.css` (the only runtime stylesheet)
- `frontend/index.html` and first-party `frontend/js/` templates
- `tests/browser/design_system.html` (the production-class visual reference)
- browser/PWA chrome that carries the application accent (`manifest.webmanifest`, `theme-color`)

It does not govern:

- EmbedPDF rendering, selection, or annotation mechanics
- EasyMDE/CodeMirror editor internals beyond the PRKS-owned chrome around them
- Cytoscape graph node/edge palettes (domain visualization)
- illustrated logo/icon artwork

### Precedence

```text
user requirements
    ↓
DESIGN.md
    ↓
existing canonical PRKS primitives
    ↓
feature-specific requirements
    ↓
generic design skill/advice
```

A generic Agent Skill (including frontend-design advice) may critique composition, accessibility, and polish. It must not override this document. It must not independently:

- replace Inter
- change the application accent
- introduce rounded cards
- increase whitespace substantially
- add gradients
- add decorative shadows
- create marketing-site hero layouts
- turn every section into a card
- add decorative animation

`DESIGN.md` wins.

### Approving a new component

Any future feature that needs a genuinely new primitive must:

1. Confirm an existing primitive cannot express it.
2. Define the semantic need.
3. Update this document.
4. Add the primitive to `tests/browser/design_system.html`.
5. Then use it in production.

Do not create a new button, card, tab, status, or dialog style inside a feature file first. This is especially important for upcoming workspace tabs, tiling, splitters, and PWA offline/sync UI.

Exceptions must be documented in [Documented exceptions](#documented-exceptions) rather than silently invented.

---

## Design principles

### Information first

Visual treatment must improve scanning and comprehension. Density is a feature. Do not sacrifice information for screenshot cleanliness.

### Dense but not cramped

PRKS may expose more information than a consumer app. Spacing follows the 4px rhythm. Similar relationships use the same gap. Do not interpret “design system” as larger controls or more whitespace everywhere.

### Structure over decoration

Borders, typography, and alignment establish hierarchy before backgrounds or shadows. Surfaces are flat. Corners are square. Shadows are absent unless a documented overlay exception exists.

### One interaction means one visual pattern

The same semantic action looks the same across pages. A primary save, a secondary cancel, a ghost chrome control, and a destructive delete are four patterns—not a new family per feature.

### Stable under resizing

Components respond to their available container, especially in preparation for future tiled workspace tabs. A Work page inside a 600px tile must behave like a 600px container even if the monitor is 1920px wide.

### State must be visible

Saving, offline, queued, error, and similar operational states may never rely on color alone. Icon + text when the state matters.

### Accessibility is part of the design system

Focus, keyboard behavior, reduced motion, and touch-target requirements are design rules, not optional cleanup.

### Do not hide actions unnecessarily

Desktop research applications benefit from discoverability. Keep primary and common actions visible. Overflow menus are for rare, dangerous, advanced, or contextual secondary operations—not for making the chrome look empty.

### People and profiles

People index rows prioritize identity, lifespan, short biography, roles, and Person Group classification over external-reference completeness. Person profiles present research content and library relationships before maintenance controls. Linked-file relationship editing belongs in the Linked files section; normal profile actions keep Edit profile and View in graph direct, while template and deletion actions remain secondary. Person Group relationships are canonical route anchors. Profile edit presentation may group fields for scanning, but Person persistence and template formats remain unchanged.

Person Groups are classification entities. Group detail presents description, hierarchy, and membership as primary content. Metadata editing and membership management are separate modes; add/remove membership belongs in Members, while the normal right panel remains summary plus actions. A filtered Group tree is automatically expanded for hierarchy context and exposes no misleading collapse controls. Group-library live runtime belongs to its rendered root, never a `window` singleton.

---

## Identity to preserve

Keep:

- Inter
- purple UI accent (`--accent: #6d6cf7` in light)
- thin 1px borders
- flat surfaces
- square corners
- minimal/no shadows
- Lucide icons
- left application navigation (250px desktop baseline)
- top command ribbon
- contextual right panel
- light / dark / system themes
- compact controls
- container-aware layouts where already present

Avoid:

- large rounded cards
- glassmorphism
- gradients
- floating decorative panels
- large drop shadows
- huge page titles
- oversized whitespace
- pill-shaped controls everywhere
- decorative animations
- marketing-site layout conventions
- emoji as UI icons

---

## Brand mark versus application accent

The illustrated PRKS logo/icon may retain its existing multi-color / blue / turquoise / yellow artwork. That does **not** mean blue is the application interaction accent. Do not recolor the multi-color application icon to make it monochrome.

Canonical application accent:

```css
--accent: #6d6cf7;
```

Light/dark variants may differ for contrast (dark uses a lighter accent). Brand illustration colors stay on the logo. Application accent is used for:

- active navigation
- focus rings
- primary actions
- selected controls
- links where appropriate
- browser / PWA theme chrome (`theme-color`, `manifest.webmanifest` `theme_color`)

`theme_color` must match the documented application accent, not a logo-illustration blue.

---

## Semantic token system

Do not introduce a second competing theme system. Tokens live in `frontend/css/style.css` `:root`. Keep existing good names. Semantic completeness, not token-name verbosity: do not rename `--accent` to `--prks-color-interactive-brand-primary`.

All primitives derive colors from these tokens. A component is incorrect if it needs an unrelated hardcoded light and dark palette when a token could represent its meaning.

### Surfaces

| Token | Meaning |
| --- | --- |
| `--bg-color` | Application canvas |
| `--surface` | Normal chrome / panel / card surface |
| `--surface-muted` | Subtle controls / secondary grouping |
| `--surface-inset` | Editors / input-like recessed regions |
| `--surface-selected` | Selected or focused list / navigation state |
| `--sidebar-bg` | Left application navigation |

Do not create arbitrary page-specific grays when one of these meanings applies. `--card-bg` remains an alias of `--surface`.

### Text

| Token | Role |
| --- | --- |
| `--text-primary` | Titles, important values, normal reading text |
| `--text-secondary` | Metadata, explanatory text |
| `--text-tertiary` | Low-priority labels, timestamps, section labels |
| `--text-inverse` | Text over strong accent or danger surfaces |
| `--text-danger` | Destructive copy |
| `--text-success` | Success copy (alias; use with icon, not color alone) |
| `--text-warning` | Warning copy (alias; use with icon, not color alone) |

Do not use opacity hacks to manufacture a fourth hierarchy.

### Interaction colors

| Token | Role |
| --- | --- |
| `--accent` | Primary interaction |
| `--accent-hover` | Hover of accent |
| `--accent-soft` | Soft accent fill |
| `--danger` / `--danger-soft` | Destructive |
| `--success` / `--success-soft` | Success |
| `--warning` / `--warning-soft` | Warning |
| `--info` / `--info-soft` | Informational |

Matching border/text tokens exist only where theme contrast requires separate values.

Domain-specific colors are allowed for Progress states, document types, user-selected tag colors, and graph semantic categories. Those are data semantics, not general UI colors. Do not force them into the generic accent palette.

### Borders and focus

```css
--border
--border-strong
--focus-ring
```

Default component outline is a 1px border.

Focus-visible contract:

```css
outline: 2px solid var(--focus-ring);
outline-offset: 2px;
```

All interactive primitives must expose equivalent keyboard focus. Do not implement focus solely as a subtle background change.

### Square geometry

```css
--radius-sm: 0;
--radius-md: 0;
--radius-lg: 0;
--radius-round: 999px;
```

`--radius-round` is permitted only for geometry that is semantically circular: avatar, status dot, radio-like indicator, true circular icon.

Do not use it for button, card, input, dialog, tag, badge, toolbar, keyboard hint, or code chip.

### Shadows

```css
--shadow-sm: none;
--shadow-md: none;
```

PRKS remains predominantly shadowless. Communicate hierarchy with border, surface difference, and spacing. A future exceptional overlay shadow must be documented here before use.

### Spacing

4px rhythm:

```css
--space-xs: 4px;
--space-sm: 8px;
--space-md: 12px;
--space-lg: 16px;
--space-xl: 20px;
--space-2xl: 24px;
--space-3xl: 32px;
```

Normal component and layout spacing comes from this set. Prefer the nearest token over static 6/10/14/18px. Exceptions are allowed for geometry that is not spacing (for example an icon’s pixel dimensions).

### Typography

```css
--font-family: "Inter", sans-serif;

--text-2xs: 0.68rem;
--text-xs:  0.75rem;
--text-sm:  0.82rem;
--text-md:  0.88rem;
--text-lg:  1rem;
--text-xl:  1.15rem;
--text-2xl: 1.5rem;

--weight-normal: 400;
--weight-medium: 500;
--weight-semibold: 600;
--weight-bold: 700;

--line-compact: 1.2;
--line-normal: 1.4;
--line-reading: 1.55;
```

Do not make every exact legacy font size a token. Migrate 0.72 / 0.78 / 0.80 / 0.85 / 0.86 / 0.90 / 0.95 to the closest intended role.

Typography exceptions (not forced onto the UI scale): Markdown content, PDF viewer internals, generated or document-specific typography, necessary third-party editor internals.

### Type hierarchy

| Role | Size | Weight |
| --- | --- | --- |
| App / brand | compact (`text-lg` title, `text-2xs` caption) | 700 / 400 |
| Page title | `text-xl` | 700 |
| Section title | `text-lg` | 600–700 |
| Card / list title | `text-md` or `text-lg` | 600 |
| Normal UI copy | `text-md` | 400 |
| Metadata | `text-sm` | 400, secondary |
| Labels | `text-xs` | 600 |

Do not use huge headings. Uppercase is reserved for genuine categorization / section labels (sidebar sections). Do not uppercase normal field labels for decoration.

### Control sizing

```css
--control-height-sm: 28px;
--control-height-md: 32px;
--control-height-lg: 36px;

--icon-sm: 14px;
--icon-md: 16px;
--icon-lg: 18px;
--icon-xl: 20px;
```

Most PRKS controls use 32px on desktop, 28px dense/small, 36px primary/global chrome. Do not create arbitrary 31/34/38px families. Touch / mobile layouts may increase interactive target area without making desktop permanently oversized.

### Motion

```css
--duration-fast: 120ms;
--duration-normal: 200ms;
--ease-standard: ease
--ease-out-soft: cubic-bezier(0.33, 1, 0.68, 1)
```

Use motion for hover/focus color, small press state, modal appearance, and panel disclosure.

Avoid large slide animations, spring/bounce, decorative movement, and content flying across the screen.

All meaningful animation must respect `prefers-reduced-motion: reduce`. No functionality may depend on animation finishing.

---

## Canonical component vocabulary

Vanilla HTML/CSS/JS only. No component framework. Classes are the API.

### Layout primitives

A very small set, only to replace repeated static inline flex layout. Not a utility framework.

| Class | Role |
| --- | --- |
| `.prks-stack` | Vertical stack; `--stack-gap` defaults to `--space-md` |
| `.prks-cluster` | Wrapping horizontal cluster; `--cluster-gap` defaults to `--space-sm` |
| `.prks-row` | Non-wrapping horizontal row, centered on the cross axis |
| `.prks-spacer` | Flex grow spacer |

If a layout is specific to one real component, give that component a meaningful class instead.

### Buttons

```text
.prks-btn
.prks-btn--primary
.prks-btn--secondary
.prks-btn--ghost
.prks-btn--danger
.prks-btn--sm
.prks-btn--md
.prks-btn--lg
.prks-btn--icon
```

| Variant | When |
| --- | --- |
| Primary | The single strongest action in a local action group |
| Secondary | Normal explicit action |
| Ghost | Low-priority / chrome action |
| Danger | Destructive action |

Rules:

- no component-level default `width: 100%`; the surrounding layout owns width
- all variants use the same sizing, type, and focus rules
- icon + label spacing is identical everywhere
- disabled treatment is identical
- one primary per local action group

Legacy visual families (`ribbon-btn` as a generic gray button, `add-new-btn`, `form-actions__btn`, `create-entity-btn`, `btn-danger-outline`) are retired. Appearance comes from `.prks-btn` + a semantic variant. `ribbon-btn` may remain only on top-ribbon commands as a structural modifier for icon/label fitting. `ribbon-btn__icon` / `ribbon-btn__label` are layout slots, not a second type system.

### Entity choice

```text
.prks-entity-choice
```

Selectable tiles in the Create New Entity chooser. They are not the single primary commit action, so they must not use `.prks-btn--primary`.

### Icon buttons

```text
.prks-icon-btn
.prks-icon-btn--ghost
.prks-icon-btn--danger
.prks-icon-btn--sm
.prks-icon-btn--md
.prks-icon-btn--lg
```

Must have an accessible name, a square hit area, a centered Lucide icon, and the same hover / focus / disabled language as `.prks-btn`.

Do not use raw `<button style="background:none;border:none">` for new UI.

### Fields and forms

```text
.prks-field
.prks-field__label
.prks-field__control
.prks-field__help
.prks-field__error
.prks-input
.prks-select
.prks-textarea
.prks-form-actions
.prks-form-actions--split
```

All controls share height, border, background, text, focus ring, disabled state, and error state. Textareas use the same border/surface/focus language but are not forced to fixed control height.

Preserve specialized EasyMDE, combobox, and segmented controls; their outer visual treatment must conform.

`.prks-field__label` is `text-xs` / 600. Do not uppercase it.

`.prks-form-actions--split` is an optional layout modifier: secondary `flex: 1`, primary `flex: 2`. Use it only when a form genuinely needs that ratio. It must not change control height, type, border, or color.

### Segmented controls

Existing `.prks-segmented` is canonical. Keep:

- normal segmented
- status segmented (Progress colors — domain, not generic accent)
- icon segmented

Selection uses accent / border / background rather than arbitrary per-page patterns.

### Page anatomy

Every ordinary PRKS page:

```text
Page
├── Page Header
│   ├── optional Back/context path
│   ├── Title
│   ├── optional summary/count
│   └── Actions
├── optional Toolbar / Filter area
└── Content
```

```text
.prks-page
.prks-page-header
.prks-page-header__context
.prks-page-header__title-row
.prks-page-title
.prks-page-header__actions
.prks-toolbar
.prks-page-content
```

Do not reinvent `display:flex; justify-content:space-between; align-items:center; gap:12px` inside generated HTML.

Consistency means shared visual grammar, not identical page structure. A tree remains a tree. A dense list remains a list.

### Cards versus rows

PRKS must not turn everything into a card.

Use a **card** (`.prks-card`) when the object is a discrete visual item, a thumbnail/cover is useful, and actions/metadata belong together.

Use a **list row** (`.prks-list-row`) when comparison/scanning density matters, many objects are displayed, or hierarchy/tree behavior matters.

Both share surface, border, selection, hover, focus, and metadata hierarchy. Work cards, person cards, and folder tree rows are domain layouts that use this grammar—they are not a license for per-feature decoration.

### Work-card metadata hierarchy

`prksWorkCardHtml()` (`frontend/js/components/work-cards.js`) is the single shared Work-card renderer across Recent, Progress, Search, Person profiles, and the Folder Library. Do not fork it into per-context components; vary presentation through its `options` (`subtitle`, `thumbPage`, `hideDocTypeBadge`).

Bibliographic identity precedes contextual metadata. The card reads, top to bottom:

1. Title (`.card-title`), clamped to 2 lines; the full title stays in a `title` attribute, never truncated in data.
2. `.work-card__meta` — stable bibliographic identity only: Author/Editor fallback credit line, then year.
3. `.work-card__context` — route-specific, lower-emphasis context (added/opened date, search abstract excerpt, Person credit/role). This is what `options.subtitle` renders into; it is never concatenated into `.work-card__meta`.
4. `.work-card__badges` — Progress status, document type, file size. Status/type stay at the bottom; do not promote them above the title.

Thumbnails carry a source class (`work-card__thumb--pdf` or `work-card__thumb--video`) from already-known `source_kind`/`file_path` data — no extra request to determine it. PDF thumbnails get a neutral padded frame (`object-fit: contain`, page visually separated from the frame) so a bright page doesn't read as a full-bleed photo in dark mode; video thumbnails stay `object-fit: cover`, full-bleed. Empty/broken thumbnails fall back to a source-appropriate label ("PDF"/"VIDEO"), never a blanket "PDF". Thumbnail `alt` stays empty — the card title is the semantic identity, not the image.

Work cards are navigation surfaces, not control panels: no per-card `…` menu, favorite, quick delete/edit, or status buttons. Clicking the card remains the one action; existing bulk-selection behavior is unaffected.

### Panels

```text
.prks-panel
.prks-panel__header
.prks-panel__body
.prks-panel__footer
```

Use for right-panel cards, grouped metadata, local editor/tool sections, and secondary detail surfaces. Do not nest bordered panels endlessly. Prefer one border boundary per meaningful grouping.

### Local / content tabs versus workspace tabs

These are different concepts. Mixing them will break the later workspace project.

**Local / content tabs** switch content inside a component (example: Details | Annotations). Canonical:

```text
.prks-tabs
.prks-tab
.prks-tab.is-active
```

**Workspace tabs (stacked + tiled v1)** represent independently navigable PRKS pages. Cold-parked tabs are state only; ordinary tab switching may keep a bounded warm-suspended PDF runtime. Reserved names:

```text
.prks-workspace-tabs
.prks-workspace-tab
.prks-workspace-canvas
.prks-tile
.prks-tile--main
.prks-tile--secondary
.prks-tile--focused
.prks-tile-header
.prks-tile__body
.prks-splitter
```

The component gallery shows the accessible tab-strip structure (activation control, optional Split action, close control). Main is permanently the single root pane and never recursively splits. Secondary is a recursive tree of `left-right`/`top-bottom` split nodes, up to a visible-pane cap (1 Main + 3 Secondary, i.e. 4 mounted TabContexts at once). Every split node owns its own local ratio (default 0.5), independent of the root Main/Secondary ratio and of every other split node.

User-facing copy uses **Split view**, **Split right**, **Split down**, **Make main**, **Hide from split**, and **Close**. Internal architecture still says tile / Secondary / `secondaryTree`; internal identifiers like split-node IDs and tree paths are never user-facing.

Parked tile-capable tabs expose a Split action that calls `prksWorkspaceTileTab(tabId)` (no duplicate tab). The workspace Split control is state-aware: **Split** opens the picker, **Show split** restores the whole parked Secondary tree, **Hide split** parks every visible Secondary leaf at once while preserving the tree logically. Clicking a parked tab's main area still makes it Main. Clicking a *visible* Secondary tab's entry in the global tab strip focuses it in place; it does not promote it to Main.

A focused Secondary tile keeps Close directly on the header. Infrequent pane actions — **Split right**, **Split down**, **Make main**, and **Hide from split** — live in the shared workspace tab menu, opened from the header **Pane actions** (`…`) control or from the tab-strip context menu. Those two entry points use the same action list and handlers; there is no second tile-specific menu. A separate **Hide from split** action parks just that one leaf's logical tab (keeps it open, removes it from the tree) without closing it — distinct from the global **Hide split** button, which parks every leaf but keeps the whole tree intact for **Show split** to restore later.

### Workspace / tab visual contract

Workspace tabs should feel like application/document tabs, not browser chrome pasted into the page.

Stacked: one main/visible tab (`mainTabId == focusedTabId`). Non-PDF and cold-parked tabs are unmounted. Up to three PDFs displaced by ordinary tab switching may remain warm-suspended in the hidden parking host. Main tile chrome is visually transparent.

Tiled: Main occupies the left master column; the Secondary region holds the recursive tree of one or more Secondary leaves. `focusedTabId` may differ from `mainTabId` and may identify any visible leaf, at any tree depth. The right details panel follows the focused tile. Browser URL, document title, sidebar, and History stay with Main, no matter how deep the focused Secondary leaf is nested.

Tiled pane headers are intentionally restrained. Secondary: drag grip, route/entity icon, title, **Pane actions** (`…`), Close. Main: icon and title only — no grip, Split, Make main, or Close. Main is identified by a persistent structural marker (`.prks-tile--main`) plus an accessible “Main pane: …” label, not a textual badge or a colored accent edge. Focus (`.prks-tile--focused`) is communicated purely by a header/surface highlight (`.prks-tile--focused .prks-tile-header`), never a pane-accent stripe, glow, or border — the focused pane's header is the one and only focus signal. When Main is also focused, both treatments combine without stacking heavy outlines.

| State | Visual |
| --- | --- |
| Main tab | Strongest selected indication (accent border/background) |
| Tiled Main pane | Structural marker + accessible Main label; no accent edge |
| Tiled secondary tab | Visible as a tile, not visually equal to main |
| Focused secondary tile | Subtle header highlight; does not imply promotion to main |
| Parked / open tab | Normal tab-strip state; tile-capable parked tabs show a quiet Split action |
| Dirty / queued / syncing / error | Small semantic state marker (icon + not color alone) |

Main is not the same state as focus. Main owns the browser route. Focus marks the tile that receives pointer/keyboard and the right panel. A focused Secondary keeps its split marker and must not reuse Main’s selected background/accent bar.

### Workspace interaction invariants

Tab strip: one row, fixed height, titles ellipsize. Tabs do not shrink below a usable width. Overflow scrolls horizontally; Main (and focused Secondary) are revealed when they change. A compact overflow menu lists open tabs with Main/Split markers when the strip overflows.

Tab context menu (user copy only: Split view):

- Parked: Make main, Open in split view (if tile-capable), Close, Close other tabs, Close tabs to the right
- Secondary: Focus (if not already focused), Make main, Split right, Split down, Hide from split, Close
- Main: Open another tab in split view, Close, Close other tabs, Close tabs to the right

Split right / Split down open the same split picker used by the global Split button, scoped to that specific focused leaf (`{ targetLeafTabId, axis, placement: 'second' }`); selecting an already-open (often parked) tab reuses it rather than duplicating it. Both actions are disabled with an explanation once the visible-pane cap is reached: "Maximum of 4 visible panes. Close or hide a pane to split again." Ordinary New Tab is unaffected by the cap — it always creates a parked tab.

Generic "Open in split view" (parked tab strip/context menu, `target:'tile'` navigation, Alt-click) is additive: it never evicts an existing Secondary pane. There is no user-facing "Replace split pane" operation. Default placement: no Secondary tree yet → the new tab becomes the bare Secondary leaf; exactly one Secondary leaf B → split B right with the new tab (B stays first, new tab second/focused); a recursive tree with a focused Secondary leaf B → split B right with the new tab. A recursive tree with no focused Secondary leaf is ambiguous and the system never guesses an arbitrary leaf deep in the tree — this generic entry point fails closed (no new logical tab, no tree mutation, no mount, no paint, no leave check) and announces that an explicit Split right/down from a specific focused leaf is required. The same pane-cap check applies: at 4 visible panes, generic split placement fails closed with the cap explanation instead of silently falling back to opening a parked tab. Ordinary New Tab is a distinct operation, unaffected by the cap or by split-placement ambiguity — it always creates a parked tab.

Close: parked closes that tab; a Secondary leaf's close removes it and normalizes the tree (redundant split nodes collapse; if it was the last leaf, the whole Secondary region disappears and the view returns to stacked) — it never touches Main or any other leaf's runtime; Main close promotes the first surviving Secondary leaf (in deterministic tree order) or, absent one, the right neighbor, then left, then Home. Promoting a Secondary leaf into Main's position removes only that one leaf from the tree and normalizes it; every other surviving Secondary leaf keeps its tree position, mounted state, and runtime untouched. Never flash Home while a successor exists. Leave guards apply before any close or hide; a rejected leave changes nothing (no partial tree/DOM mutation).

Ordinary global-tab switching warm-suspends an eligible PDF Work by moving its existing TabContext root into `#prks-tab-warm-parking`. Resume moves that same root into its visible tile, preserves viewer/editor state, requests a container resize only, and performs no route render or Work/PDF fetch. The warm cache uses deterministic LRU eviction with at most three parked PDF contexts. Explicit hide, narrow fallback, close/batch close, and teardown remain cold lifecycle operations; non-PDF pages never enter the warm cache. Warm state is ephemeral and is not workspace persistence.

Hide from split (local, per-leaf) removes that one leaf from the tree and normalizes it, but keeps its logical tab open and parked — distinct from Close (which destroys the tab) and from the global Hide split (which parks every visible leaf at once but keeps the entire tree intact, restorable via Show split).

Focus after a leaf disappears (close, local hide, or narrow fallback) prefers the closest surviving sibling in the locally-collapsed subtree; otherwise the nearest remaining leaf in deterministic depth-first tree order; otherwise Main.

Focus restoration after close / hide / split / Make main / narrow fallback prefers the resulting focused tile, else that tab’s activation control. Do not leave DOM focus on a destroyed node. Pointer or keyboard entering a tile focuses it without promoting Main or changing the URL.

Split control: **Split** (no leaf), **Show split** (tree exists but is parked/hidden), **Hide split** (tree visually tiled). Narrow fallback must not show Hide split; it explains that split needs a wider window. The global Split button only ever controls whether the Secondary region as a whole exists/is visible — it never adds another pane; adding panes is always a local Split right/down action on a specific focused leaf.

Tile chrome exists only in tiled mode. Headers keep a stable height. Loading and errors stay inside the route root; the tile shell/header is not torn down. The Work Notes divider (`.work-split-handle`) keeps its nested-content grip and sizing behavior; hover, drag, and keyboard-focus strength match the workspace splitter’s idle-thin / obvious-when-interacting language.

Work notes side-by-side layout follows that Work’s tile/container width, not a global viewport class that would restyle the other tile.

In the dense tiled desktop shell (and on mobile), the right Details/Annotations panel is a dismissible, fixed-position overlay (`position: fixed`, slides via `transform`), not a layout participant — opening or closing it never changes pane widths, split ratios, or workspace canvas width. It closes via Escape, the Details ribbon toggle, or an explicit Close (`×`, labeled "Close details") in its own header, all routed through the one canonical `prksToggleRightPanelOverlay()` path. Closing the panel is purely a visibility change: it must not clear a Graph node/edge selection or any other owning route's state — reopening the panel restores whatever it was already showing.

Tiling:

- main/master tile owns the left column; the Secondary region holds a recursive tree of one or more tiles
- the root Main/Secondary split is directly resizable (default ~58/42); every internal Secondary split node has its own independent, locally-scoped ratio (default 0.5)
- a thin `.prks-splitter` divider separates every pair of adjacent tiles/regions (root and nested alike); no heavy card shadow around any tile
- stacked mode has no visible tile chrome, and no separator
- at most 4 TabContexts are ever mounted at once (1 Main + 3 Secondary); the cap is enforced predictably (see below), not silently

### Main/Secondary divider

The root split between Main and Secondary is workspace-owned canonical preference: one normalized ratio (`mainSplitRatio`, default `0.58`, meaning Main width / usable split width, usable width excluding the separator track). Workspace Persistence v1 stores that preferred ratio (and each nested split node's preferred ratio) in browser `localStorage`. Constrained/effective ratios applied by ResizeObserver are not stored. The preference follows workspace *roles*, not tab identity — Make Main, adding/removing Secondary panes, Hide/Show split, and the narrow responsive fallback all preserve the current ratio unchanged.

Divider contract:

- 1px normal separator (`.prks-splitter.prks-splitter--vertical`); a substantially wider invisible pointer hit target via `::after`
- idle: almost invisible 1px track; hover / active drag: a centered indicator (`::before`) without changing the grid track; keyboard focus: the same indicator plus a clear `:focus-visible` outline
- do not make the divider visually thick just to make it draggable; the visual indicator must not shift pane geometry
- pointer drag uses `setPointerCapture()` so dragging stays stable while the pointer crosses tile content (PDF viewer, EasyMDE, buttons)
- `role="separator"`, `tabindex="0"`, `aria-orientation="vertical"`, `aria-valuemin`/`aria-valuemax`/`aria-valuenow`/`aria-valuetext` (whole percentages) kept current on every resize
- keyboard: Left/Right resize by a small step, Shift+Left/Right by a larger step, Home/End jump to the dynamic min/max allowed width, double-click resets to default with a live-region announcement
- Main/Secondary each have one centralized minimum pixel width; the ratio is clamped against those minimums recomputed from the *measured* canvas width, not a fixed 0.25–0.75 band
- resizing is layout-only: it never mounts/unmounts a TabContext, never re-renders a route, and never triggers a leave guard; the canonical ratio updates without a full workspace repaint, and the DOM applies the resulting exact pixel width/height via a CSS custom property
- rendered only when split view is actually visible (never in stacked/narrow-fallback mode)

### Nested Secondary split dividers

Every internal Secondary split node (`type: "split"`, `axis: "left-right" | "top-bottom"`) gets its own separator, sharing the root divider's exact mechanics and visual language — there is one separator implementation, not two:

- `left-right` splits get a vertical `.prks-splitter--vertical` divider (col-resize cursor); `top-bottom` splits get a horizontal `.prks-splitter--horizontal` divider (row-resize cursor)
- `role="separator"`, `tabindex="0"`, `aria-orientation`, `aria-valuemin`/`aria-valuemax`/`aria-valuenow`/`aria-valuetext` kept current on every resize; keyed by that split node's own stable ID (`data-prks-split-id`)
- keyboard: the axis-appropriate arrow keys resize by a small step, Shift+arrow by a larger step, Home/End jump to that split's own dynamic min/max, double-click resets that one split to 0.5 (not the root's 58/42) with a live-region announcement
- each split node's minimum sizes are centralized constants (`PRKS_NESTED_MIN_WIDTH_PX` / `PRKS_NESTED_MIN_HEIGHT_PX`) and are measured against that split's own container, never the window or workspace root; both children of a nested split share the same minimum (no Main/Secondary role distinction inside Secondary)
- if a split's container becomes too small to honor both children's minimums, its ratio clamps safely to that split's own midpoint rather than producing a negative/overflowing pane — this is expected, container-local behavior, not an error
- dragging or keyboard-resizing one split node never touches the root `mainSplitRatio` or any other split node's ratio; it never mounts/unmounts a TabContext, re-renders a route, or triggers a leave guard
- nested ratios are canonical user preference, exactly like the root ratio; Workspace Persistence v1 stores the preferred value, not a temporarily constrained effective ratio

### Recursive Secondary tree

`secondaryTree` is `null` (no Secondary), a bare `{ type: "leaf", tabId }` (exactly one Secondary tab — the common case), or a `{ type: "split", id, axis, ratio, first, second }` node whose `first`/`second` children are themselves leaves or splits. Split-node IDs are stable per-runtime keys (DOM reuse, resize ownership, focus, targeted mutation) — never array index, DOM position, or a child's tab ID. They are in-memory only; Workspace Persistence v1 stores topology, axis, preferred ratio, and leaf tab IDs, then assigns fresh split-node IDs on restore.

A recursive DOM renderer mounts exactly one stable host per visible leaf (keyed by `tabId`) and one split container + separator per split node (keyed by `split.id`), reconciling rather than rebuilding: an unrelated leaf's host and TabContext survive any sibling being split, closed, hidden, resized, or promoted to Main elsewhere in the tree. Removing a leaf normalizes the tree — a split node left with only one child is replaced by that child, repeated upward as needed — so the tree never carries a redundant single-child split node.

### Workspace drag and drop

Drag is an alternate input path for the same canonical workflows above (reorder, split placement, pane move, hide/park) — never a parallel layout model, never persisted, never canonical state. `workspace-drag.js` owns one transient session (source, origin, pointer ID, live target) and computes/previews the user's spatial intent; on a successful drop it calls exactly the same state APIs the menus already use. Pointer Events (`pointerdown`/`pointermove`/`pointerup`/`pointercancel`/`lostpointercapture`) are used throughout — no native HTML5 DnD.

Interaction contract:

- **Global tab drag = reorder.** Any tab (Main, visible Secondary, or parked) dragged along the workspace tab bar only reorders `state.tabs`; it never changes `mainTabId`, `focusedTabId`, `secondaryTree`, mounted contexts, or the URL. Dragging a visible Secondary's *global tab* onto another pane's edge, however, repositions its existing leaf (see below) — a global tab still carries pane identity, it does not fork into a duplicate.
- **Parked tab → Secondary edge = split left/right/above/below.** Dropping a parked, tile-eligible tab on one of a Secondary leaf's four edge bands inserts it as a new sibling on that side, reusing its existing logical tab ID (never a duplicate tab, never a second mount).
- **Parked tab → empty Secondary region = create the first Secondary.** Offered only while `secondaryTree` is `null`; equivalent to "Open in split view", not a pointless nested split.
- **Secondary grip → Secondary edge = move pane.** Dragging a visible pane's header grip onto another leaf's edge repositions its existing leaf via one atomic tree transaction (`moveLeafRelativeToTarget`). This is spatial repositioning only — it is explicitly *not* a leave operation, so it never runs PDF leave confirmation or a Notes flush-for-unmount, and it never remounts the moved pane or any unrelated pane.
- **Secondary grip → tab bar = park.** Dragging a pane's grip onto the tab strip removes that leaf from `secondaryTree`, normalizes the tree, and unmounts its TabContext — exactly "Hide from split", so it does require leave preflight; a rejected leave leaves the tree, tab order, and focus completely unchanged.
- **Main is never spatially draggable.** Main's global tab can still be reordered in the tab bar, but it can never be dropped into the Secondary tree; **Make main** remains the only way to change Main ownership.
- **Self-drop is invalid.** A pane dragged over its own edge offers no target and mutates nothing.
- **The pane cap blocks additions, not moves.** At 4 visible panes, dropping a *new* parked tab into the Secondary tree is unavailable (no highlight, capped-explanation announcement); repositioning an already-visible pane remains allowed at the cap.
- **Route eligibility is checked live and re-checked at commit.** Only tile-capable routes may become new Secondary leaves; the state API is the final authority regardless of what the drag preview offered.
- **No spatial pane drag while stacked/narrow-fallback** — only tab-bar reordering remains available at that width. An active spatial drag is cancelled *before* a live wide→narrow transition mutates layout, not after.

Visual states (all restrained, flat/square — no heavy shadows or cards beyond what non-drag menus already use):

- **Drag source**: stays visible, dimmed (`.is-drag-source`, `opacity: 0.5`) — layout changes only on a committed drop, never mid-drag.
- **Drag preview**: a small floating chip (icon + truncated title) that follows the pointer (`.prks-drag-preview`); it never clones an entire pane and never intercepts pointer events.
- **Insertion marker**: a narrow accent bar between two tabs in the strip, positioned by tab-midpoint geometry, recomputed live during tab-strip autoscroll so it never goes stale while the strip scrolls under a stationary pointer.
- **Secondary edge overlay**: a translucent, accent-bordered rectangle sized to the resulting pane region (`.prks-drag-edge-overlay`) for a valid target; a dimmed neutral variant (`.is-invalid`) for a capped/ineligible target — never a highlight across the whole pane.
- **Empty-Secondary overlay**: a dashed accent rectangle labeled "Open in split view", shown only while `secondaryTree` is `null`.
- **Park target**: the tab bar gets an inset accent outline (`.is-drop-target-park`) only while dragging a pane by its grip.
- No drop-zone chrome of any kind exists outside an active, eligible drag.

Lifecycle: one idempotent `cleanup()` tears down pointer capture, every document/window listener, the preview element, every overlay/marker, the autoscroll animation frame, source styling, and the body drag class — safe to call more than once. `cancel()` (exported as `prksWorkspaceCancelActiveDrag`) runs that same cleanup and leaves canonical state completely untouched; it fires on Escape, `pointercancel`, `lostpointercapture`, window blur, and is also called defensively (and harmlessly, when nothing is active) by `workspace-tiling.js` right before a real narrow/wide transition and right before pruning any stale tile that could contain the live drag source. A drag only ever begins after the pointer clears a small movement threshold (distinguishing it from a plain click), and click suppression is armed only for the click a completed `pointerup` gesture synthesizes — a cancelled drag never swallows the user's next intentional click.

### Workspace persistence

Workspace logical state is persistent; workspace runtime state is ephemeral.

`frontend/js/workspace-persistence.js` is the only workspace module that may read or write `localStorage`. It serializes the existing workspace state machine; it is not part of that state machine. Other workspace modules (`workspace-tabs.js`, `workspace-tree.js`, `workspace-tiling.js`, `workspace-split.js`, `workspace-drag.js`, `tab-context.js`) stay storage-free and notify persistence after a successful canonical mutation. Writes are debounced (~200 ms) and flushed on `pagehide`. Pointer-move layout clamping must not write; user-committed canonical ratios may.

Storage key: `prks.workspace.v1` (explicit schema `version: 1`). v1 is last-writer-wins on this browser profile. There is no `storage` event sync, BroadcastChannel, live multi-window merge, or server-side workspace copy.

Persisted:

- tab IDs and tab order
- canonical tab routes
- Main tab ID
- logical Secondary tree (topology, axis, preferred nested ratio, leaf tab IDs)
- preferred root `mainSplitRatio`
- split shown/hidden (`mode` stacked vs tiled, with the tree preserved when hidden)
- cached tab title and icon (display hints only)

Not persisted:

- TabContext / mounted status / DOM / route generation / AbortController / timers / requests
- editor instances, dirty flags, draft buffers, Research Notes / private-note save state
- PDF, graph, playlist, right-panel, and focused-entity runtime
- pointer/drag state, ResizeObserver instances, effective constrained ratios, `narrowFallback`
- contextual Back/Forward history (each restored tab starts as `history = [route]`, `historyIndex = 0`)
- split-node runtime IDs (fresh IDs are assigned on rehydrate)

Startup restores before the first normal workspace mount: read + validate the snapshot atomically, build logical workspace state, reconcile the current URL, then mount only currently visible leaves. Restored parked tabs and hidden Secondary leaves stay state-only and must not fetch; warm runtime state is never restored. The current startup URL outranks a stale persisted Main route: keep a valid restored workspace where possible, but make Main represent the opened hash and never silently redirect back to the stored Main. `narrowFallback` is recomputed from the live viewport; a wide layout stored on a wide display remains logically intact when reopened narrow, and the preferred ratios return when the viewport is eligible again.

A corrupt, unknown-version, or contradictory snapshot is discarded (the invalid `localStorage` value is removed) and PRKS bootstraps from the current URL. localStorage unavailability, quota errors, and serialize failures leave the running workspace intact and must not break navigation. Restored titles render through existing safe text paths (`textContent`); mounted routes refresh metadata normally.

Workspace persistence is not an alternative store for Research Notes, private reminders, PDFs, or other feature data. Those keep their existing backend save paths.

### Chips, tags, and badges

Three different concepts. Do not use them interchangeably because everything is small. All remain predominantly square.

| Class | Meaning |
| --- | --- |
| `.prks-tag` | Actual research/library tag. May have user/domain color via `--tag-accent`. |
| `.prks-chip` | Compact interactive selection/removal item. |
| `.prks-badge` | Non-interactive categorical/status metadata. |

Document-type badges are domain badges (`--doc-type-color`), not generic accent chips.

### Status language

Semantic states: `neutral`, `active`, `success`, `warning`, `danger`/`error`, `info`.

Application operational states:

| State | Today | Visual |
| --- | --- | --- |
| loading | used | `.prks-state--loading`, progress bar / spinner + text |
| saving | used | status text + optional icon |
| saved | used | calm success text, not a toast takeover |
| error | used | danger + icon + text |
| empty | used | compact `.prks-state--empty` |
| warning | used | warning + text |
| offline | reserved | calm, not destructive; icon + “Offline” |
| stale | reserved | visible but calm; cached data remains readable |
| queued | reserved | not styled like error; “N changes queued” |
| syncing | reserved | “Checking…” / “Syncing…” |
| conflict | reserved | warning/danger with icon + “Conflict” |

Only implement operational states that exist today. Reserve the PWA states here so the offline project does not create a separate visual system.

Rules:

- icon + text when important
- color is supplementary
- queued is not styled like error
- offline is not automatically styled as destructive
- stale cached data is visible but calm

### Empty / loading / error

```text
.prks-state
.prks-state--empty
.prks-state--loading
.prks-state--error
.prks-state--warning
```

An empty state contains at most: optional icon, short heading, short explanation, one primary or secondary action. Avoid giant illustration-driven empty pages. Keep them compact.

`.prks-inline-message` remains valid for compact in-flow messages (form feedback, “not found”). Prefer `.prks-state` when the message *is* the page content.

### Dialogs

One visual family: modal, confirm, destructive confirm, alert.

They share scrim, panel, header, body, actions, focus behavior, and spacing.

Do not maintain visually separate systems for normal modal, confirmation, unsaved confirmation, or feature-specific modal unless behavior truly differs.

Destructive action uses the danger visual treatment, not ordinary accent primary.

Close controls are `.prks-icon-btn`.

### New File / Work creation

Creating a Work is a short primary workflow with optional depth, not a database form. Production creation goes through `#work-modal` (`POST /api/works`). Do not duplicate that path.

**Entry:** One ribbon control (`#prks-ribbon-create`). Primary click opens New File. The chevron menu lists the existing create actions (New File, New Folder, New Person, New Group) and calls the same `openModal` handlers. Do not keep a second permanent `New…` button. Command-palette create commands remain available.

**Hierarchy:** Source (type + PDF drop or YouTube URL) → basic information (title, folder, document type, date/year, people) → optional bibliographic details and tags/status/notes via disclosure. Do not remove fields; hide advanced ones until opened. Collapsed disclosure content is not keyboard-focusable (`inert`).

**Source state:** The current source type and the selected file or URL stay visible. A selected PDF uses a compact filename/size/Change chip, not an oversized empty drop target. Switching PDF ↔ YouTube clears the other source so a hidden value cannot be submitted. Video sources are YouTube-only today (`youtube.com`/`youtu.be`, matched by explicit hostname, not substring); the URL is validated client-side and authoritatively server-side before a Work is created.

**Folder:** The default destination reads `Uncategorized`, matching what the server actually stores for an empty `folder_id` (`ensure_default_uncategorized_folder_id()`) — never `Library root`. Free text typed into the folder search is a query, not a selection; only an explicitly chosen result or the explicit default commits a destination. An uncommitted query blocks submission with an inline error and focuses the field. New File inherits its folder context from the focused TabContext's route (`prksFocusedRouteRecord()`), not from `window.location.hash`; a focused pane that isn't a Folder defaults to Uncategorized rather than inheriting Main's folder.

**Actions:** Sticky modal footer keeps Cancel (secondary) and Create File (primary) visible while the body scrolls. Create File is the only prominent submit. Duplicate submit is blocked (`Creating…`). Recoverable validation stays inline (`aria-invalid`), focuses the first invalid control, and preserves entered metadata. Success closes the modal and uses `prksNavigate` to the new Work.

Processing Inbox stays a separate production path. This dialog is not a post-creation PDF attach flow.

### Settings

Settings is task-grouped, not one long scrolling page. `#settings-modal` shows a fixed header, a quiet vertical category nav (`role="tablist"`), and one scrollable content pane holding six category panels: **General**, **Reading & layout**, **Export**, **Backup**, **Maintenance**, **Diagnostics**. Inactive panels stay in the DOM as `hidden` + `inert` — never removed/recreated — so operations (an in-flight backup, a chosen restore file, a running maintenance action) and unsaved control state survive switching categories. `prksActivateSettingsCategory(categoryId, options)` is the single helper that shows/hides panels, updates `aria-selected`/`tabindex`, and drives any category-specific lazy behavior; no per-category logic is scattered across separate click handlers. Category nav supports Arrow Up/Down/Left/Right, Home, and End with selection-follows-focus.

**General is the default and stays calm.** It only shows Appearance (theme), Annotation author, and Show help hints — no backup, maintenance, or diagnostics language anywhere in it. This is the everyday-preferences first impression the reorganization exists to protect.

**Reading & layout** groups the three device-local layout toggles (Force mobile layout, Research notes beside PDF on mobile, Remember PDF page per file) with a panel-level "Stored on this device" note. The mobile-notes toggle explains its narrow-layout dependency without ever disabling the control itself.

**Export** is the BibTeX/BibLaTeX field toggles plus Restore export defaults, given real width instead of a squeezed disclosure. A compact "N of M fields included" summary sits above the toggle grid and is derived from the existing toggle `aria-checked` state — it is not a second source of truth.

**Backup** keeps the full existing verified backup/restore workflow (progress, cancel, verify, the separate RESTORE confirmation modal) unchanged, but visually separates "Create backup" from "Restore backup" with headings and a rule instead of one undifferentiated block.

**Maintenance** groups PDF text index rebuild and PDF linearization behind a short "these tools are normally unnecessary" intro and ordinary secondary-button styling — no danger treatment, no additional maintenance tools beyond what already existed.

**Diagnostics** holds all performance instrumentation (summary, route table, spans, thumbnail metrics, client request coordinator, Refresh/Reset/Copy). Diagnostics loads lazily: opening Settings never calls `prksLoadPerformanceDiagnostics()` by itself. The first activation of the Diagnostics category triggers exactly one load; switching away and back reuses the retained `__prksPerfSnapshot`/`__prksClientRequestSnapshot` without an automatic re-fetch; Refresh explicitly fetches again.

**Storage-scope badges** (`Library-wide` / `This device`) sit next to Annotation author, BibTeX export fields, Appearance, and Show help hints so persistence scope is scannable without a paragraph per row. `Library-wide` means stored server-side in the PRKS library, not cloud sync. Backup, Maintenance, and Diagnostics are actions, not persisted preferences, and carry no scope badge.

**Category selection is presentation state only**, not a setting: it lives in an in-memory module variable, defaults to General on page load, and may remember the last-viewed category for the remainder of that page session. It is never written to `localStorage`, `/api/settings`, or a URL hash — Settings stays a modal with no `#/settings/...` routes. Closing Settings restores focus to whatever launched it (icon button or command palette), never to the last category tab.

At narrow modal widths the vertical nav becomes a single-line horizontally scrollable strip instead of squeezing panel content; the active category auto-scrolls into view and stays visually obvious.

### Application shell

The shell is one visual system, not three.

**Sidebar:** 250px desktop baseline, flat surface, 1px separator, compact navigation, Lucide icons. Selected, hover, disclosure, nested indentation, and section spacing are shared. Selected state uses `--surface-selected` and accent, not a unique sidebar palette. Uppercase section labels (`Library`, `Organize`) mark groups of independent links; a disclosure family (People, Research, Progress) does not get a redundant standalone heading on top of its own row — `nav-disclosure--section-break` gives it the same separator/spacing a heading would have.

**Sidebar disclosure (People/Research/Progress):** tri-state per family — `unset` (no explicit choice), `expanded`, or `collapsed` — stored under `prks.nav.<family>Expanded` (`"1"`/`"0"`; missing key is `unset`). An explicit user choice always wins over the active route. Only while `unset` may entering a route inside that family (e.g. `#/people/role/Reviewer`) auto-expand it. Pressing the disclosure toggle always visibly flips the family — there is no "forced open" case that silently reopens it. A family whose active route it contains, but which is collapsed, still gets a restrained `nav-disclosure--contains-current` indicator (accent label/icon) distinct from the `.active`/`aria-current="page"` treatment reserved for the actual destination link. People keeps a real `#/people` link plus a separate small chevron toggle, since it has a real landing page; Research and Progress have no useful landing page, so their whole row is one native `<button>` (icon + label + chevron) — not a link, and not a `<span>` wearing a click handler.

**Top ribbon:** Canonical global command bar. Controls use the same button primitives as elsewhere; “ribbon button” is not a separate semantic button system. Global search (`.prks-palette-launch`) remains visually prominent but restrained. Keyboard hint chips stay square (not pill).

**Right panel:** Contextual auxiliary role. Local Details/Annotations tabs use `.prks-tabs`. Cards, fields, and actions inside it use the same primitives as the main page, at denser sizing where needed. Do not create a separate right-panel design system.

---

## Theme model

Retain `system`, `light`, and `dark`.

Primitives derive from semantic tokens. Encode light/dark differences on the tokens once. Do not duplicate large blocks of `:root[data-theme="dark"]` and `prefers-color-scheme: dark` for the same component when a token can carry the difference.

Do not rewrite functional third-party overrides merely for aesthetic purity.

---

## Responsive strategy

Design for desktop application, medium/narrow application window, mobile/narrow PWA, and future tiled containers.

**Viewport media queries** are for genuine application-shell transitions:

- sidebar drawer
- right-panel overlay
- global mobile chrome

**Container queries** are for feature/page layout decisions:

- Work page internals
- cards / list density
- toolbars
- future tiled tab contents
- detail layouts

Every component should specify one of: fluid, compact, stacked, or overflow-scroll where semantically required. Do not hardcode `viewport >= X` into page components.

Minimum-width philosophy: components adapt instead of shrinking their desktop internals until they break. Touch/coarse-pointer hit areas must remain usable on mobile without making desktop oversized.

---

## Accessibility contract

### Keyboard

Every clickable non-native interaction must be keyboard-operable.

### Focus

Always visible with keyboard navigation. The focus-visible contract above is mandatory for interactive primitives.

### Touch target

Dense desktop visual controls may be compact. Mobile / coarse-pointer hit areas must remain usable.

### Color

Never the sole carrier of state.

### Reduced motion

Respect `prefers-reduced-motion: reduce`. No functionality depends on animation finishing.

### Text zoom

Layouts must tolerate browser zoom without important controls disappearing.

### ARIA

Use native HTML semantics first. Future workspace splitters use `role="separator"`, `aria-orientation`, `aria-valuenow` where applicable, and keyboard arrows.

---

## Interaction feedback contract

Established by the Usability Polish 10 pass. Governs how async mutations, destructive actions, and copy actions communicate with the user.

- **Async mutations expose busy state and prevent duplicate submission.** A button whose click starts an awaited request disables itself, sets `aria-busy="true"`, and shows an active verb (`Saving…`, `Adding…`, `Linking…`) before the request starts — not after. `prksSetButtonBusy(button, busy, { busyLabel })` in `frontend/js/ui.js` is the shared helper: it snapshots the idle contents on the first `busy(true)` call and restores them exactly (icon markup included) on `busy(false)`, so it is safe to call from a `finally` block unconditionally. Not every button needs this — navigation, disclosure toggles, local-only filters, and local copy actions do not have meaningful request latency and stay untouched.
- **Failed mutations always restore the control.** Every converted action restores busy state in a `finally` (or an equivalent guaranteed path), so a rejected request never leaves a control stuck disabled. The user can always retry without a route reload.
- **Errors stay near the action when there's a local surface for them.** Prefer an inline field/status message over a modal alert for recoverable async failures. Reserve `prksAlertDialog`/`prksConfirmDestructive` (`frontend/js/ui.js`) for cases with no local surface, duplicate-entry conflicts, or explanatory content that needs acknowledgement.
- **Successful saves do not require acknowledgement.** A normal save updates the UI (and usually closes the editor) rather than leaving a permanent "Saved!" banner or popping a confirmation dialog. A temporary inline "Saved" status is fine when the surface doesn't otherwise visibly change. Autosave (Research Notes, private/reminder notes) stays deliberately quiet — no new animation, no visual promotion beyond its existing tertiary status line.
- **Destructive confirmations name the destructive action.** The confirm button reads `Delete Person`, `Delete annotation`, `Remove from group`, `Unlink person`, etc. — never a bare `Confirm`/`Yes`/`OK` when a concrete verb is available. `Delete` destroys the entity itself; `Remove` ends a membership/relationship; `Unlink` ends a relationship between two records. `Cancel` stays `Cancel`.
- **Native dialogs are avoided except for one documented, intentional exception.** `window.confirm` is reserved for the synchronous pending-annotation-sync route-leave guard in `frontend/js/app.js` (`prksCanLeaveTabContext` and the mirrored check inside `prksRenderTabRoute`). See `AGENTS.md` for why this one stays synchronous. Every other confirmation — including both PDF annotation-delete entry points — goes through `prksConfirmDestructive`/`prksConfirmDeletePdfAnnotation`.
- **Copy feedback reflects the actual Clipboard promise.** Never show "Copied" until the clipboard write has resolved; show a distinct failure state ("Copy failed") when it rejects, then restore the idle label/icon after a short interval. `prksFlashInlineCopyButton` (icon-only buttons) and `prksFlashButtonLabel` (textual buttons, e.g. annotation "Copy link") are the two shared helpers — prefer one of them over a new one-off timeout.
- **No new notification framework.** Toasts, a notification center, and progress overlays are explicitly out of scope. Inline status regions, inline field errors, temporary button feedback, and the existing confirm/alert modal cover this app's needs.
- **Focus-visible and reduced motion are application-wide, not per-feature.** New interaction states must render through `:focus-visible` (never suppress focus without a replacement) and must not add motion outside what `prefers-reduced-motion: reduce` already accounts for.

---

## Inline-style policy

Static layout/style attributes in `frontend/index.html` and first-party `frontend/js/` are forbidden.

Bad:

```html
<div style="display:flex; gap:8px; margin-top:10px;">
```

Replace with a meaningful component or layout class.

Allowed inline style is limited to **data-driven CSS custom properties**, for example:

```html
style="--depth:3"
style="--tag-accent:#…"
style="--tag-scale:…"
style="--doc-type-color:#…"
style="--prks-icon-size:22px"
```

where the value genuinely comes from application data. Prefer `--semantic-custom-prop:value` over injecting complete CSS declarations.

Dynamic document / tag / user colors are legitimate data, not design-system violations.

Icon pixel sizes passed into `prksIcon({ size: number })` must set `--prks-icon-size`, not `width`/`height` style attributes.

---

## Hardcoded color policy

General UI colors belong in tokens.

After migration, hardcoded colors outside token definitions should be limited to:

- document-type definitions
- tag defaults / domain colors
- graph semantic palette
- brand artwork
- third-party integration requirements

Classify before deleting a hex:

| Kind | Destination |
| --- | --- |
| UI semantic color | token |
| domain / data color | domain constant |
| brand artwork | asset |
| third-party workaround | documented exception |

Do not implement a naïve “no `#` in frontend” test.

---

## CSS organization

One runtime file: `frontend/css/style.css`.

Do not add Sass, PostCSS, Tailwind, Webpack, Vite, or extra CSS HTTP requests. The upcoming PWA/tunnel work benefits from a compact runtime dependency graph.

Sections, in order:

1. Tokens
2. Reset / base
3. Accessibility
4. Layout primitives
5. Buttons / controls
6. Forms
7. Tabs / navigation
8. Cards / lists
9. Status / messages
10. Dialogs / overlays
11. Application shell
12. Feature layouts
13. Third-party integrations
14. Responsive / container rules
15. Reduced motion

Feature-specific selectors are still allowed when the feature genuinely has unique layout.

Migration of existing families:

```text
introduce canonical primitive
→ alias legacy
→ migrate callers
→ prove no caller
→ remove legacy selector
```

Do not delete a legacy rule first and then repair every broken page.

---

## Research entities

Concept, Position, Argument/Stance, and People indexes use dense `.prks-list-row` / `.prks-research-row` grammar:

```text
icon + title (+ kind)
secondary descriptive line / relationship metadata
```

Metadata is split into compact items, not one punctuation-heavy sentence. Indexes stay one list request. Do not fetch per-row extras.

Research insertion pickers use canonical dialog / field / list / action primitives (`.prks-dialog`, `.prks-input`, `.prks-list-row`, `.prks-btn`). `.prks-research-picker` is overlay/sizing/behavior only, not a second visual system.

Argument/Stance detail is read-first. Relationships are named rows, not raw ids. Edit uses the same research picker as note insertion.

Concepts, Positions, and Arguments/Stances share one scanning/search language, not three independent implementations. Each index route filters its own already-loaded array locally (`prksBindResearchIndexSearch`, defined once in `concepts.js` and reused by `positions.js`/`arguments.js`); typing never issues a network request, and the Argument kind filter (`?kind=`) stays the canonical route/query state that search narrows within, never a client-only replacement for it. Search state itself is runtime-only — never written to the URL, `localStorage`, `/api/settings`, or workspace persistence. An index distinguishes a genuinely empty dataset (creation action front and center) from a nonempty dataset with no query match (a "no matches" message plus a clear-search action) — the two states never share the same copy or actions.

Research detail pages (Concept, Position, Argument) share one section hierarchy: `.research-entity` containing `.research-entity__section` blocks, each opening with a `research-entity__section-head` (title, optional count, optional section-local action button) built from the one shared `prksResearchSectionHeadHtml` helper. A section's edit action lives in that section's own head, never floating in a trailing paragraph below unrelated content. Relationships (Concept parents/subconcepts, Position's targeting Arguments/Stances, Argument targets/sources/responses/mentions) render as real anchors via `prksResearchIndexRowHtml`, never clickable `<div>`s or bare `<li><a>` lists — this preserves keyboard activation, context menu, Ctrl/Cmd-click, and workspace tile interception for free. Argument detail is the reference structure other research entities are brought toward, not a page redesigned to match them.

Destructive page-header actions (Concept Delete, Argument Delete) are visually subordinate to frequent actions (View in graph, Edit, Rename, New response) via a quiet `.prks-btn--quiet-danger` treatment (transparent until hover/focus, then red) plus a small leading margin — never hidden behind a menu built solely for this, and never styled as prominently as the actions used every day.

Graph inspector selection uses one `doc-meta-card` (identity, action, neighbor lists). Do not wrap each neighbor group in another bordered card.

Profile pages separate summary metadata (lifespan, aliases, groups) from long-form biography and from external references.

## Research visualization

The Research Graph canvas is the primary surface: when nothing is being configured or inspected, almost all available space belongs to it. The permanently visible toolbar is compact (Find, Fit, Reset layout, Filters, Legend); filter checkboxes and the legend are disclosed on demand from that toolbar, not rendered permanently underneath it. Chrome around the graph (header, find, filters, legend) uses page / toolbar / field / panel / filter-toggle primitives.

Filters and Legend are mutually exclusive inline disclosure panels local to the Research Graph, not a floating popover: opening one closes the other, and each collapses to zero height when closed while its controls stay alive in the DOM. Graph filter state (which node/relation types are shown) is runtime-only — never persisted to localStorage, workspace persistence, `/api/settings`, or the URL — except the People toggle, which still triggers a graph reload with `people=1/0` as before.

The inspector exists only for an actual node or edge selection; it is TabContext-owned runtime state exposed by the focused Research Graph runtime (`hasSelection()`), never inferred from DOM markup. With no selection the global right panel is hidden and the canvas reclaims that width — there is no permanent empty "Selection" placeholder card. Selecting a node or edge reveals the panel; clearing the selection (canvas click, the inspector's explicit close control, or a filter hiding the current selection) hides it again. An unfocused Graph tile never controls the global right panel — only the focused Graph runtime's own selection does, and switching workspace focus away and back preserves that selection without remounting the graph or refetching data.

Status messages (a node hidden by filters, a failed reload, a graph-too-large notice) belong to a Graph-local status region near the toolbar, not the inspector — the inspector's contract is selection details only, so it can be safely hidden when there is no selection without losing status feedback.

Showing or hiding the inspector must not reset the graph's pan, zoom, or layout: it only triggers a Cytoscape container resize (`cy.resize()`) on the next frame, never `fit()` or a layout re-run.

Graph node colors are domain visualization (documented exception). Node types use the same Lucide icons as Research / People navigation (Concept `network`, Position `flag`, Argument/Stance `messages-square`, Work `file-text`, Person `user`) on a muted surface with a semantic border.

Full relation descriptions belong in the inspector. Canvas edge labels are exceptional: short, horizontal (`text-rotation: none`), and only for the hovered or selected edge. Selecting a node highlights incident edges and dims the rest; it does not mass-label those edges.

---

## Third-party integration boundaries

### EasyMDE

PRKS owns: outer border, surface, toolbar colors, focus relationship, theme overrides, spacing around the editor.

EasyMDE owns: editor mechanics.

### EmbedPDF

PRKS owns: viewer chrome, toolbar integration, pane border/surface, sync indicators, surrounding layout.

EmbedPDF owns: rendering, selection, annotation mechanics.

### Research Graph / Cytoscape

PRKS owns: page chrome, filters, legend, canvas frame, and the right-panel selection inspector.

Cytoscape owns: layout, hit-testing, and in-canvas node/edge drawing. Style/config for those live in `research-graph.js`, not a parallel CSS theme.

The selection inspector is plain right-panel content — it uses the same contextual-sidebar language as every other route's right panel (`right-panel-stack`, a small kicker label, a title, quiet metadata, a primary action, then relationship sections built from `person-sidebar__section-label` and `prks-list-row`/`prks-research-row` rows). It is not wrapped in a `doc-meta-card`; the right panel itself is already the container. The inspector's own "Clear selection" (a quiet ghost/text action) only clears the Graph's node/edge selection — it is distinct from the right panel's Close, which only hides the panel.

Do not modify their behavior during visual normalization. Do not try to make them disappear into generic HTML styling.

---

## Component gallery

`tests/browser/design_system.html` is not a mock. It loads production CSS and Lucide:

- `/frontend/css/style.css`
- `/frontend/vendor/inter/inter.css`
- `/frontend/vendor/lucide/lucide.min.js`

It uses the exact production classes. It must not define a second private design system.

The gallery’s own `<style>` block may own **fixture layout only** (max width, section arrangement, swatch specimen dimensions). Inline styles in this fixture that demonstrate token values or swatch colors are outside the production inline-style policy.

Themes: `?theme=light` and `?theme=dark` (deterministic).

Representative container widths for review: 1440, 900, 600, 390.

The gallery is the place to answer “how should a PRKS button / tab / field / status look?” without searching a random feature page.

Future workspace examples in the gallery are static visual specimens only.

---

## Visual acceptance

A migration is not complete merely because every page uses classes beginning with `prks-`.

- **Buttons:** An action with the same importance looks like the same type of button everywhere.
- **Typography:** Page titles, section titles, metadata, and help text are immediately distinguishable without arbitrary local font sizes.
- **Spacing:** Similar component relationships have the same spacing rhythm.
- **Surfaces:** A panel / card / input means the same thing visually across pages.
- **States:** Selected, hovered, disabled, focused, saving, and error states follow the same grammar.
- **Light / dark:** Hierarchy is equivalent in both themes.
- **Narrow layout:** Components adapt instead of simply shrinking their desktop internals.
- **Density:** A reasonable result generally fits at least as much useful research information on screen as the current UI.

---

## Documented exceptions

Each exception states the component, the rule being broken, why, and whether it is temporary or permanent.

| Component | Rule | Why | Duration |
| --- | --- | --- | --- |
| PDF canvas / EmbedPDF pane | Surface tokens; square chrome only around the viewer | The canvas background is owned by the PDF renderer; light-theme pane chrome `--pdf-pane-chrome-light` is a documented PDF-integration surface, not a generic page gray | Permanent |
| EasyMDE / CodeMirror internals | UI type scale, focus ring, square chrome | Editor mechanics and generated markup are third-party; PRKS styles the wrap, toolbar, and theme tokens only | Permanent |
| User-selected tag color | Generic accent palette | Tag color is user/domain data via `--tag-accent` | Permanent |
| Document-type colors | Generic accent palette | Type identity is domain data via `--doc-type-color` | Permanent |
| Research Graph node/edge palette | Generic accent palette; no decorative color | Graph categories are domain visualization inside Cytoscape, not UI chrome | Permanent |
| Progress status colors | Generic success/warning/danger | Reading-progress states are a fixed domain set (`--status-*`) | Permanent |
| Brand logo / PWA icons | Application accent is purple | Multi-color illustration is brand artwork, not interaction chrome | Permanent |
| Native checkbox / radio UA styling | Fully tokenized form controls | Native form widgets retain UA metrics; custom check glyphs in role pickers stay square | Permanent |
| Markdown preview typography | UI type scale | Rendered note content may use document-like sizes inside the preview | Permanent |
| Icon numeric `size` | Spacing/control tokens | Geometry, not spacing; expressed as `--prks-icon-size` | Permanent |
| Tag cloud / type-list sizes below `--text-2xs` | UI type scale | Density and `--tag-scale` calculations, not ordinary chrome type | Permanent |
| Top-ribbon `.ribbon-btn` padding `7px 9px` | Control padding tokens | Ribbon fitting at narrow widths; not a second button type scale | Permanent |
| Work `.page-header--work` padding `4px 6px` | Page-header padding | Workspace chrome tightness around the PDF/notes split | Permanent |
| Component gallery fixture | Production inline-style policy | Gallery `<style>` and token/swatch inline styles are test-fixture layout only | Permanent |
| Future overlay shadow | `--shadow-*` is `none` | If a floating overlay later needs a shadow, document it here before shipping | Reserved |

---

## Maintenance

This document is updated in the same change that introduces a new primitive. The component gallery is updated in that same change. Production usage comes last.

Upcoming work that must not invent a parallel visual system:

- tiling / window-manager behavior
- PWA offline / stale / queued / syncing / conflict UI

Those features are out of scope for the design-system migration. Their visual contracts are already specified above.
