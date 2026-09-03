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

**Workspace tabs (stacked + tiled v1)** represent independently navigable PRKS pages. Parked tabs are state only. Reserved names:

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

The component gallery shows the accessible tab-strip structure (activation control, optional Split action, close control). Tiled v1 ships one Secondary on the right. Recursive splits and draggable splitters are not implemented.

User-facing copy uses **Split view**. Internal architecture still says tile / Secondary / `secondaryTree`.

Parked tile-capable tabs expose a Split action that calls `prksWorkspaceTileTab(tabId)` (no duplicate tab). The workspace Split control is state-aware: **Split** opens the picker, **Show split** restores a parked Secondary, **Hide split** stacks the view. Clicking a parked tab's main area still makes it Main.

### Workspace / tab visual contract

Workspace tabs should feel like application/document tabs, not browser chrome pasted into the page.

Stacked: one main/visible tab (`mainTabId == focusedTabId`). Parked tabs are unmounted. Main tile chrome is visually transparent.

Tiled v1: Main occupies the left master column; one Secondary occupies the right. `focusedTabId` may differ from `mainTabId`. The right details panel follows the focused tile. Browser URL, document title, sidebar, and History stay with Main.

| State | Visual |
| --- | --- |
| Main tab | Strongest selected indication (accent border/background) |
| Tiled secondary tab | Visible as a tile, not visually equal to main |
| Focused secondary tile | Subtle focus indicator; does not imply promotion to main |
| Parked / open tab | Normal tab-strip state; tile-capable parked tabs show a quiet Split action |
| Dirty / queued / syncing / error | Small semantic state marker (icon + not color alone) |

Tiling v1:

- main/master tile owns the left column (~58/42 split, fixed)
- one secondary tile lives on the right
- a simple border separates tiles
- no heavy card shadow around every tile
- stacked mode has no visible tile chrome

Future recursive Secondary splits / vertical-horizontal splitter:

- `secondaryTree` may grow `type: "split"` nodes (`axis`, `ratio`, `first`, `second`)
- 1px normal separator
- larger invisible pointer hit target
- accent focus/drag indication
- do not make the divider visually thick just to make it draggable
- `role="separator"`, `aria-orientation`, `aria-valuenow` where applicable, keyboard arrows
- splitter resizing is not shipped yet

This specification governs later recursive tiling. Do not invent another design language then.

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

### Application shell

The shell is one visual system, not three.

**Sidebar:** 250px desktop baseline, flat surface, 1px separator, compact navigation, uppercase section labels, Lucide icons. Selected, hover, disclosure, nested indentation, and section spacing are shared. Selected state uses `--surface-selected` and accent, not a unique sidebar palette.

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

Graph inspector selection uses one `doc-meta-card` (identity, action, neighbor lists). Do not wrap each neighbor group in another bordered card.

Profile pages separate summary metadata (lifespan, aliases, groups) from long-form biography and from external references.

## Research visualization

The Research Graph is allowed a full-width canvas. Chrome around it (header, find, filters, legend) uses page / toolbar / field / panel / filter-toggle primitives. Selection details live in the application right panel (`doc-meta-card` / `right-panel-stack`), the same auxiliary column as Work and Person — not a second boxed inspector beside the canvas.

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
