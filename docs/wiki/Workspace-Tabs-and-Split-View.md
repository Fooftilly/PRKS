# Workspace Tabs and Split View

PRKS uses in-app tabs so research navigation does not require opening many browser tabs. The same workspace can show a Main page and several Secondary panes at once.

## Tabs

Opening supported destinations creates or reuses PRKS tabs. Tabs can be switched, reordered, closed, or parked.

Common actions include:

- close;
- close other tabs;
- close tabs to the right;
- open a parked tab in split view;
- make a visible Secondary pane Main;
- hide a pane from split view while keeping its tab open.

The tab strip has an overflow control when every open tab cannot fit.

## Main and Secondary panes

Main owns the browser URL. Secondary panes are additional visible tabs rendered beside/below Main.

PRKS supports up to four visible panes at once: Main plus up to three Secondary panes. Secondary panes can themselves be split right or down.

Focusing a Secondary pane changes which pane receives pane-scoped actions/details, but it does not rewrite the browser URL. **Make main** swaps the selected pane into the Main role.

## TabContext

Each mounted tab has its own TabContext. Route state, page DOM, async-generation state, and live resources such as PDF viewers, notes editors, and graph instances belong to that context.

This rule is important for contributors: code that assumes a single document-wide route root can work in stacked mode and fail in split view.

## Persistence

Open tabs, split layout, and preferred split sizes are remembered in browser-local state. Workspace state is device/browser-profile specific and is not part of the server backup.

Cold parked tabs do not keep rendering/network resources alive. Selected recently used Work/PDF tabs may be kept warm so switching back can preserve viewer state.

## Navigation shortcuts

Supported interaction patterns include:

- normal click — open in the originating PRKS tab;
- Ctrl/Cmd-click or middle-click — open a background PRKS tab;
- Alt-click / Alt+Enter from supported navigation — open in split view;
- pane focus — direct pane-scoped actions to that pane.

## Resizing and drag/drop

Adjacent panes have resizable dividers. Keyboard resizing is supported, and a divider can be reset to its default.

Drag/drop is an optional shortcut for tab reorder, creating/adding split panes, moving panes, and parking a pane. Equivalent menu/keyboard actions remain available.

## Page eligibility

Not every route is appropriate in a Secondary pane. Eligibility should be explicit and centralized rather than reimplemented ad hoc by individual pages. Operational surfaces such as Processing Files can have different workspace rules from ordinary research pages.

For the implementation contract and current UI rules, use [DESIGN.md](https://github.com/Fooftilly/PRKS/blob/master/DESIGN.md), `frontend/js/tab-context.js`, and the `frontend/js/workspace-*.js` modules.
