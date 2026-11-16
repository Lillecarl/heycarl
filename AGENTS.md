# Hey Carl: agent rules

Architecture: DESIGN.md. This file: frontend conventions. Standard HTML, ARIA and Svelte 5 over invention; a pattern not listed here needs a reason the standard one fails.

## Svelte

- Svelte 5 runes only: `$state`, `$derived`, `$props`, `$effect`. No `export let`, `$:`, `on:`, dispatchers.
- `$derived` for anything computed from state. `$effect` only to sync with the outside (fetch on a route change); read dependencies through the work itself, no `void x` pokes. Writes inside an effect go in `untrack`.
- One-shot work on mount: `onMount`, not a dependency-free `$effect`.
- Every `{#each}` keyed.
- Focus on open: `{@attach (el) => el.focus()}`. Never `autofocus` plus `svelte-ignore`.
- `<select value={...}>`, not `selected=` on options.
- Shared types and helpers of a component: `<script module>`.
- Overlapping loads: a generation counter, so a late answer never replaces a newer one. Typing-driven search: debounce plus the counter.

## Plugin elements

- `<svelte:options customElement={{ tag: 'heycarl-x', extend: withBase }} />`, `withBase` from `@heycarl/sdk/base`. The shadow root adopts `sdk/src/base.css`.
- Child components keep their own `<style>`: Svelte injects it into the shadow root. No `:global` style sheets in the host component.
- `base.css` (layer `hc.base`) owns: box-sizing, controls (3rem min height and width, `font-size: max(1rem, 16px)`, `--hc-muted` border, 0.5rem radius), focus ring, h2/h3, `.plain` lists, `.actions` rows, `.primary`, `.danger`, `.icon`, `.muted`, `.error`, `.visually-hidden`. Never copy these into a component; a component's unlayered rules override.
- The shell imports the same `base.css`.

## Routing

- Plugin view state in the hash, through `@heycarl/sdk/route`: `hashId('x')` in a `$derived`, `hashHref('x', id)` for links, `hashHref()` for the start.
- Navigate with `<a href>`. A button never sets `location.hash`. Exception: after a create, go to the new entry.
- After a delete: `leave()` (`location.replace`), so Back does not reopen the entry.
- Never `location.hash = ''`: the browser loads the page again and aborts requests in flight.
- A hash id can name a deleted entry (an old link, another tab). Load detail only for an id found in the loaded rows; otherwise show "No such X" with a link to the start. Never show the raw 404.
- Open/close toggle: one link, `href={open ? hashHref() : hashHref('x', id)}`, with `aria-expanded`.
- Shell views: state in the URL (`?view=`), as links.
- `aria-current="page"` on the current link only: `aria-current={here ? 'page' : undefined}`, never a boolean.

## Data

- Every request through the SDK: `data()`, `api()`, `json()`. No raw `fetch`: the SDK signs in again on 401.
- Plugin list: `plugins()` / `pluginPages()` from the SDK, cached per module.
- `null` means not loaded: show Loading. `[]` means empty: show the empty text. Never one state for both.

## Errors

- State holds the thrown value: `let error = $state<unknown>(null)`, `error = e`. Never `String(e)`, never a string built from it.
- Render with `<ErrorNotice {error} />` from `@heycarl/sdk/ErrorNotice.svelte`: `role="alert"`, base `.error`, nothing while null. No hand-written error paragraph.
- Context: `new Error('Could not revoke', { cause: e })`; shown as "Could not revoke: <reason>".
- Branch on `e instanceof ApiError` and `e.status` / `e.code` (`status` 0: no answer). Never parse a body or a message.
- Text for a person comes from `errorText()` in the SDK; a new mapping (status, SQLSTATE) goes there, not in a page.
- Kernel `/api/*` errors: PostgREST's shape `{code, message, details, hint}` via `heycarl/errors.py`, code `HC<status>`. Raise `HTTPException` with a message for a person; never build `{"detail": ...}` by hand.
- `.error` without `role="alert"` is for state shown as text (a sync that failed), not for a thrown value.

## HTML and ARIA

- Headings: the shell renders the page `<h1>` and `<title>` from the plugin's page name. Plugins start at h2; `Related` is h3.
- Every input, select and textarea has a name: a visible `<label>`, or `aria-label`. A placeholder is not a label.
- Repeated controls name their item: `aria-label="Delete {title}"`. The visible word stays in the name.
- Toggle button: fixed label plus `aria-pressed`. Disclosure: fixed label plus `aria-expanded`. The state is never only a glyph or a changing label.
- No `aria-label` on `span`/`div`. Hidden text: `.visually-hidden`; decoration: `aria-hidden="true"`.
- No information only in `title=`: show it.
- Errors `role="alert"`; outcome notices `role="status"`.
- A choice of one: a `<fieldset>` with `<legend>` and native radios, styled. Never `role="radiogroup"` over buttons.
- Native `<details>`/`<summary>` for disclosure, `<time datetime>`, `<dl>` for key-value read-outs, `rel="noreferrer"` on `target="_blank"`.

## Confirmation

- Never `window.confirm()`.
- Delete that goes to the trash, one row: no confirm.
- Delete that takes a subtree, a list, a counter, a contact, or an entry shared with others: inline two-step. The first press arms; the second button names what goes (`Delete “Shopping” with 4 todos`), next to Keep.
- Irreversible (purge, empty trash, revoke, disconnect): inline two-step.
- An armed confirm belongs to the view it was armed in; switching views disarms it.

## CSS

- Colours only through `--hc-*` tokens (app.css), no fallbacks: the shell always sets them. Dividers `--hc-border`; control borders `--hc-muted`.
- No inline `style=` widths; no `!important`; no removed focus outline.
- Lay out for 360 px first: grids put the title on its own row before squeezing it.
