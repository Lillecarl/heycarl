# Hey Carl

A personal application framework at `https://hey.lillecarl.com`. One kernel,
and features as plugins: contacts (a Monica-style PRM, a personal relationship manager), calendar sync,
reminders, todos, and more later. It installs as a PWA on Android.

The patterns follow Torg (solid-kubernetes `pkgs/torg`): PostgreSQL holds the
rules, PostgREST serves the data, a Python kernel serves the rest, and a
SvelteKit shell loads plugin frontends. The code is new. Hey Carl runs on one
NixOS host (hetztop), not on Kubernetes.

## Request path

```
internet :443 -> Caddy (TLS) -> unix:/run/heycarl/kernel.sock
                                  kernel (FastAPI)
                                    -> unix:/run/heycarl/postgrest.sock
                                         PostgREST -> PostgreSQL
```

- The NixOS module sets up Caddy (`services.heycarl.caddy`). dex, from
  croshome `modules/nixos/gate.nix`, is the identity provider.
- The kernel and PostgREST listen only on unix sockets. Hetztop runs agents as
  local users, so a loopback port is not a trust boundary there. Socket file
  permissions are.

## Identity

The kernel is the OIDC client and checks every request. The SPA holds no
token.

- Sign-in: authorization code flow with PKCE, state and nonce
  (`kernel/heycarl/oidc.py`). The kernel checks the ID token's signature
  against dex's JWKS (fetched again when dex rotates keys), and its issuer,
  audience, expiry and nonce. `allowedEmails` limits who gets in.
- Sessions live in `heycarl_private.session`, keyed by the SHA-256 of a
  random token. The browser holds the token in a `__Host-` cookie: HttpOnly,
  Secure, SameSite=Lax. Logout deletes the row.
- Deny by default (`kernel/heycarl/access.py`). A path is public or bearer
  only when that file lists it; every other path needs a session. Without
  one, `/api/*` answers 401 and a page redirects to sign-in. A path with a
  `.` or `..` segment answers 400.
- A request that changes state must carry `Origin` equal to `publicUrl`.
  SameSite=Lax already keeps the cookie off cross-site POSTs; this holds
  without it.
- No request header names a person. The kernel never reads
  `X-Forwarded-User`.
- The kernel sets HSTS, a content security policy, a Permissions-Policy,
  nosniff and `Referrer-Policy: same-origin` on every response.

## PostgREST tokens

The kernel mints a short-lived JWT for every PostgREST request. PostgREST
never sees a dex token.

- Its claims are `role`, `sub` and `exp`. The signing key is a local secret
  that only the kernel and PostgREST read.
- The kernel controls the claims, so their shape does not depend on dex.
- Roles:
  - `heycarl_user`: a signed-in person. RLS checks `sub`.
  - `heycarl_worker`: background jobs, with no person signed in. Each plugin
    grants it only what its jobs need.
  - `heycarl_authenticator`: PostgREST's login, member of both, NOINHERIT.
  - `heycarl_owner`: owns the tables and runs migrations.
- Ownership is per row (`owner_sub`), even with one user. It costs one column
  and keeps a second user possible.

## Database

- `heycarl_private`: tables, RLS policies, helpers. Forward-only numbered
  migrations, each recorded with a checksum. An edited applied migration
  fails the start.
- `heycarl`: the API. `security_invoker` views and grants, dropped and
  recreated on every start.
- `heycarl_plugin_<name>`: one schema per plugin, with its own migrations.
  Exposed through `Accept-Profile` / `Content-Profile`.
- Migrations run in one transaction under an advisory lock, and end with
  `NOTIFY pgrst`. The `heycarl-migrate` unit runs them before PostgREST
  starts, and the kernel runs them again at its start.

Traps from Torg that apply here:

- Views must be `security_invoker`. A definer view bypasses RLS.
- The authenticator needs `USAGE` on every exposed schema, or PostgREST
  answers 404.
- An invariant belongs in a constraint or a trigger, not in the page.

## Plugins

A plugin is a directory with `heycarl-plugin.json`:

- `migrations/`: SQL for its own schema.
- `backend`: a Python module with `register(ctx)`. It adds routes under
  `/api/plugins/<name>`, webhook routes under `/api/public/<name>`, and jobs.
- `frontend`: a Svelte custom-element module the shell loads, with its pages,
  panels and settings.

Nix checks each manifest at build time and bakes the plugin set into the
package. A plugin is enabled by being in the set; there is no runtime install.

## Background work

Calendar sync and reminder delivery run with nobody signed in.

- Jobs run in the kernel process as `heycarl_worker`.
- Work that must happen is a row in a table, picked up by a polling loop
  with `FOR UPDATE SKIP LOCKED`. `LISTEN`/`NOTIFY` only shortens the wait.
  NOTIFY is lossy and is never the only signal.
- A reminder row records each delivery attempt, so a restart neither drops
  nor repeats one.

## Public routes

Webhooks arrive with no session: 46elks call callbacks and Google Calendar
push channels. `access.py` lists `/api/public/` as public, with the PWA
manifest, icons and service worker, and the sign-in endpoints. Every public
route under `/api/public/` checks its own secret or signature. The list is
narrow by intent.

## PWA

- Web app manifest, service worker, installable on Android Chrome.
- Web Push with VAPID keys. The kernel stores each device's push
  subscription and sends through the push service.
- The manifest is fetched without cookies, so it is a public route.

## First plugins

1. **todos**: the thin slice that proves the stack end to end.
2. **reminders**: a reminder on any object. Delivery by web push. A phone
   call through 46elks is planned; how 46elks speaks text is not yet
   checked.
3. **prm**, the personal relationship manager: Monica's model: people, contact fields, relationships, notes,
   activities, tags, gifts and important dates. See below.
4. **calendar**: Google Calendar, read-only. See below. Planned later:
   Google Workspace (work) and CalDAV/ICS, and mirroring events between
   calendars at a chosen visibility: full details, or "busy" with no
   title.
5. **counters**: named counts, such as cups of coffee. See below.

## Counters

A counter's value is the sum of its events. Every change is an event
with a nonzero delta, a note and a time, so the history is the data.

- Set and reset add one event with the difference. Undo deletes the
  newest event (the highest id), and the trash keeps it.
- Tenancy: an event references `(counter_id, owner_sub)`, as in the
  PRM. An event can only count on the caller's own counter.
- Events have no update grant. A wrong event is undone, not edited.
- Sums are SQL: the `counter_value` view, and `daily_totals` and
  `count_since`, which are stable and so answer GET. A read-only MCP
  connection can call them.
- Set, reset and undo lock the counter row. A concurrent event waits,
  so the difference is computed from the value it replaces.
- Days in `daily_totals` are in a zone the caller names. The page
  sends the browser's zone.

## Calendar

Google Calendar into Hey Carl, one way and read-only. The only scope is
`https://www.googleapis.com/auth/calendar.readonly`. Hey Carl never asks
for a write scope.

- Off by default. The module options `services.heycarl.google.clientId`,
  `clientSecretFile` and `tokenKeyFile` turn it on, all three together.
  Without them the page says "not configured", the tools find an empty
  cache, and no job runs.
- The redirect URI to register at the Google client is
  `<publicUrl>/api/plugins/calendar/callback`, for hey.lillecarl.com
  `https://hey.lillecarl.com/api/plugins/calendar/callback`.
- Connect: the page POSTs `/api/plugins/calendar/connect` and gets
  Google's URL. That is the authorization code flow with PKCE (S256),
  `access_type=offline` and `prompt=consent`. The random `state` and the
  verifier stay in kernel memory for 10 minutes, bound to the person and
  to the hash of their session cookie. A restart drops a connect in
  progress, and the person starts again.
- The callback is a session route, so the gate refuses it without a
  session. Google's redirect is a top-level GET, which carries the
  SameSite=Lax cookie. The callback uses the state once and refuses it
  when the person or the session differs from the one that started. It
  also refuses a grant without the scope or without a refresh token.
- Disconnect revokes the refresh token at Google, then deletes the token,
  the calendars and the events, whatever Google answered. The answer says
  whether Google confirmed.
- Tables, in `heycarl_plugin_calendar`: `account` (status, last sync and
  error), `calendar` (Google id, name, color, zone, selected),
  `calendar_sync` (sync token and window) and `event`. RLS is owner-only
  with the composite key, as in counters. `heycarl_user` may SELECT
  `account`, `calendar`, `event` and the `agenda` view, and nothing else.
  It cannot write anything. `calendar_sync` has no grant.
- Every write goes through SECURITY DEFINER functions that only
  `heycarl_worker` may run, and each takes the owner as an argument, as
  `dav_authenticate` does for the gate. The person's own actions (choose
  a calendar, sync now, disconnect) go to kernel routes, which call those
  functions with the session's `sub`.
- The refresh token lives in `heycarl_private.calendar_google_token`,
  which no API role can reach. The kernel seals it with AES-GCM before
  storing it, under a key derived from `tokenKeyFile` (a LoadCredential).
  The owner is the associated data, so a sealed token copied to another
  row does not open. The key never reaches PostgreSQL. pgcrypto would put
  it in every SQL call, where a statement log could catch it. A dump
  alone holds no usable token. Access tokens live only in kernel memory.
  The client secret is read from its credential file, and no log line
  carries a token.
- Sync: a job every 10 minutes, woken early by a connect or a newly
  chosen calendar, plus "sync now" (the page, and MCP `calendar_sync`),
  at most once per 10 seconds per person. One lock per person serializes
  sync, connect, choose and disconnect.
- The window is 30 days back to 365 days ahead. A full sync lists that
  window with `singleEvents=true`, so recurring events arrive as their
  instances. It keeps the `nextSyncToken` from the last page. A later
  sync sends only that token, with `singleEvents=true` as before, because
  Google forbids `timeMin` beside a token. Changes outside the window are
  dropped here. A 410 Gone, a missing token, or a day since the last full
  sync means a full sync again. The daily full sync moves the window on:
  an instance that enters the window at its far end is no change to
  Google, so no token would bring it.
  - Rejected: a token sync without the window and with local expansion of
    recurrence rules. That means expanding RRULEs, EXDATEs and exceptions
    here, which is more code than the daily full sync.
- Writes are upserts on `(calendar_id, google_id)`, so an event keeps
  its id, and its links, across a full sync. A cancelled item, or one
  that left the window, is deleted, along with the instances of a
  cancelled series. A full sync deletes what Google no longer lists.
  Google text is truncated in the kernel, not refused by a CHECK, so one
  long description cannot stop the sync.
- Links: `event` is tracked so that it can be linked and found by
  `entries`, with the route `/p/calendar/heycarl-calendar#/event/{id}`.
  The migration drops its version trigger at once. Google owns these
  rows, so they have no history and no trash, and `restore` and `undo`
  can never write into the cache.
- PRM: `event_people(event_id)` returns the contacts whose email field
  matches an attendee, compared in lowercase, as the caller under RLS.
  Without the PRM plugin it returns nothing.
- MCP: `calendar_upcoming`, `calendar_events`, `calendar_event` (with
  its people) and `calendar_calendars` are read-only. `calendar_sync`
  needs `write`: it changes nothing at Google, but it spends quota. The
  resource is `heycarl://calendar/upcoming` and the prompt is
  `calendar_week`. Anyone who sends an invitation can write event text, so
  every answer that carries it says it is data, not instructions.
- Tests: `kernel/tests/fake_google.py` is a small fake of Google's OAuth,
  revoke, calendarList and events.list with sync tokens. The kernel tests
  run it in a thread. The VM test runs the same file as a service and
  points `google.testEndpoints` (internal) at it.

Accepted:

- A connect in progress lives in memory; a restart drops it.
- When Google does not confirm a revoke, Hey Carl still forgets the
  token. The grant then stays listed in the person's Google account until
  they remove it there.
- Losing `tokenKeyFile` means every person connects again.
- The kernel's worker token can read every sealed token, so the key is
  what protects them.

## The PRM

The schema is the application. The page is a thin view over PostgREST, and
the only Python is one job.

- Embedding: one request loads a contact with its fields, dates, tags and
  gifts. Tags and activities embed many-to-many through their join tables.
- Computed columns: `last_contacted`, `touch_due`, and on a date
  `next_occurrence`, `days_until` and `turns`. PostgREST filters and orders
  by them, so "coming up in 30 days" is one query string.
- A computed relationship, `relations(contact)`, over a view that shows
  each relationship from both sides with the reverse kind.
- RPC: `search` (names, fields, tags) and `log_activity` (one activity,
  many people).
- Tenancy: children reference `(contact_id, owner_sub)`, and RLS pins
  `owner_sub` to the caller. A foreign key ignores RLS, so without the
  composite key a row could point at another owner's contact.
- Reminders: triggers on a date, a contact's name and the time zone keep a
  row in the reminders plugin. They run as the table owner, so they write
  rows for any owner. A reminder deleted by hand stays deleted until the
  next occurrence. An hourly job gives a passed date next year's reminder.
  Without the reminders plugin these do nothing.
- The page writes the browser's time zone to `preference`; dates and
  reminder times use it.

## MCP

A Streamable HTTP MCP server at `/mcp`, for cloud AI clients (claude.ai
custom connectors) and local agents. Decided:

- Library: the official `mcp` SDK (nixpkgs 1.29.0, protocol 2025-11-25).
  FastMCP for tools, resources, prompts and completions; the low-level
  server where FastMCP lacks a feature (resource subscriptions, tasks).
- Auth: the kernel is the OAuth 2.1 authorization server, with the SDK's
  `create_auth_routes` and a provider that keeps clients, codes and tokens
  in `heycarl_private`, tokens hashed. Dynamic client registration is on:
  cloud clients need it. The issuer is the site root, so the endpoints are
  `/authorize`, `/token`, `/register`, `/revoke` and
  `/.well-known/oauth-authorization-server` and `-protected-resource`.
  All but `/authorize` go on `access.py`'s public list; `/mcp` is bearer.
- `/authorize` needs a session. `provider.authorize()` sees no request, so
  it stores the pending request and redirects to a consent page. The page
  shows the client's redirect host (open registration makes the name
  attacker-chosen) and the scopes, and approves with a POST that mints the
  code for the session's `sub`.
- Scopes: `read` and `write`, chosen per client on the consent page. One
  helper checks `write` for curated and generic tools alike. Destructive
  tools confirm through elicitation and refuse when the client does not
  support it. Sessions stay stateful: elicitation needs them.
- Surface: each plugin registers tools, resources and prompts through
  `ctx.mcp`, plus one generic PostgREST tool. A tool calls PostgREST as
  `minter.user(token.subject)`, so RLS applies. Under `read`, the generic
  tool allows GET and HEAD only.
- Mounting: the SDK app's routes join the FastAPI router before the shell
  catch-all, and the kernel lifespan enters `session_manager.run()`.
  Pass `transport_security` explicitly: the default allows localhost only.
- Tests: drive discovery, registration, authorize, token, initialize and a
  tool call with elicitation through the SDK's own client against a real
  uvicorn. After deploy: an unauthenticated `POST /mcp` answers 401 with
  `WWW-Authenticate: ... resource_metadata`, and an event stream lives
  past 30 seconds through Caddy.
- MCP never reaches the core scope: a push subscription there would get
  every reminder. Every generic write asks the person first.
- Prompts embed records as resources and say they are data, not
  instructions: notes are free text.
- Not yet built: resource subscriptions and tasks.

## App passwords

CalDAV and CardDAV clients cannot sign in through OIDC. They send HTTP
Basic credentials on every request. An app password is that credential,
one per device, and it works under `/dav/` only (issue #1).

PostgreSQL holds every rule, and the kernel never reads the table as
`heycarl_owner`. The kernel only reaches PostgREST, as for any data:

- `heycarl.app_password` is a `security_invoker` view without the hash.
  A person lists and revokes (DELETE) through it, under RLS. The Connect
  page uses the plain `/api/data/core/` proxy; the kernel has no routes.
- `heycarl.app_password_create(name, write, days)` is the only way in. It
  is SECURITY DEFINER because the caller may not choose the hash. It makes
  a 256-bit secret with pgcrypto, stores its SHA-256, returns it once, and
  takes the owner from the token's `sub` and the address from its `email`.
  The secret has full entropy, so a slow hash adds nothing.
- `heycarl.dav_authenticate(username, secret, address)` checks a Basic
  credential. Only the new role `heycarl_gate` may run it, and that role
  may run nothing else. The kernel calls it with a 60-second gate token,
  as it calls `claim_due` with a worker token. It counts failures, refuses
  when throttled, and records use in the same transaction.
- The username is the password's own random id, not the email, so each
  device has its own pair and a guessed username names nobody. A wrong
  username and a wrong password get the same answer.
- Each password has a name, scopes (`read`, or `read write`) and an
  expiry of at most a year. Revocation, expiry and an `allowedEmails`
  change take effect on the next request: the kernel checks the email
  that `dav_authenticate` returns, as it does for sessions.
- The data proxy now puts the session's `email` in the token, beside
  `sub`, so SQL can record who made a password.
- `access.py` gives `/dav`, `/dav/` and `/.well-known/caldav|carddav`
  their own kind, `DAV`. Basic credentials count on no other path, and a
  cookie never counts on these. A request with a foreign `Origin`, or
  `Sec-Fetch-Site: cross-site`, is refused: a browser that cached the
  credentials must not carry them for another site.
- A read-only password may use GET, HEAD, OPTIONS, PROPFIND and REPORT
  only. The gate checks this before any route runs.
- Failed attempts go into `heycarl_private.auth_failure`. Past 10 a
  username, or 20 an address, in 15 minutes, the gate answers 429 and
  checks nothing. Past 10000 in all, it refuses every attempt, so the
  failure rows themselves stay bounded. The address is the last
  `X-Forwarded-For` entry, which Caddy writes.
- No MCP tool lists or makes app passwords: an AI never mints a
  credential. MCP never reaches the core scope, and the core RPC
  allowlist leaves these out.
- Sessions, logins and OAuth still use the kernel's owner pool. They are
  the next candidates for the same move.
- `/dav/` answers OPTIONS and a PROPFIND for the principal, through
  defusedxml with no DTD, Depth 0 or 1, and a 64 KiB body limit. Address
  books and calendars come with issues #2 and #3, which also mint
  PostgREST tokens with `via = "dav:<id>"`.

## History and trash

Every change to a person's data keeps the state before it. A delete goes
to a trash, and only the person empties it, in the app. Then neither a
model mistake nor a hostile MCP client can destroy data: the worst a
client with `write` can do is make work to undo.

Decided:

- One table, `heycarl_private.version`, for every plugin. A row is one
  state of one record: table, primary key as jsonb (join tables have a
  composite key, `preference` is keyed by `owner_sub`), the whole row as
  jsonb, the operation, the time, the transaction and the actor. Insert
  and update store the new state; delete stores the last state. So a
  record's versions are every state it had, and the newest is the live
  row. A transaction is `(pg_current_xact_id(), now())`: an xid alone
  repeats after a dump and restore.
- A plugin opts a table in from its migration:
  `select heycarl_sdk.track('contact', ignore => array['updated_at'])`.
  That adds an AFTER trigger and stores a version of each existing row.
  `ignore` names bookkeeping columns: a change to them alone makes no
  version (reminder delivery state, `reminder_id`). The table needs
  `owner_sub` and a primary key. Shared tables (`relationship_kind`) and
  core tables (push subscriptions) are not tracked. A test fails when
  `heycarl_user` may update or delete in a plugin table that is not
  tracked: an untracked table is a hole, and the next plugin would open
  one silently.
- The trigger is SECURITY DEFINER: no API role reads or writes
  `heycarl_private`. The functions below check `owner_sub` against the
  caller.
- Actor: the kernel adds a `via` claim to the PostgREST token: `app` for
  a browser session, `mcp:<client_id>` for an MCP tool.
  `Minter.user()` requires it. A job has no person and no claim. The
  trigger records it.
- The functions are SECURITY DEFINER with dynamic SQL: table names
  quoted with `%I` and checked against the tracked list, a pinned
  `search_path`, and a refusal when no person calls. Their helpers live
  in `heycarl_history`, which the kernel drops and recreates on every
  start like `heycarl`.
- Trash: the deleted records that have not come back, grouped by
  transaction. Deleting a contact cascades to its notes, dates and
  fields, and to the date's reminder; they share a transaction, so they
  are one trash entry and come back together.
- Undo takes a version id and reverses the whole transaction that made
  it: every record it touched goes back to its state before. A record it
  made is deleted (to the trash), one it deleted comes back under its
  old ids (`OVERRIDING SYSTEM VALUE`) so links to it hold, and one it
  changed gets the earlier state. One call undoes a PATCH over every
  note; per-record restore would take one call per row.
- Undo never overwrites later work: a record changed again since is
  left alone and reported in `changed_since`.
- Undo puts in what fits and reports the rest. It makes passes, each
  step in a savepoint, retrying a row whose parent is not back yet, and
  stops when a pass adds nothing. A row that still fails stays as it is:
  its parent was deleted later, or a new row took its unique name.
  Undoing again after the cause is gone finishes it.
- Restore takes a version id and puts that one record back to that
  state; a trash entry's id brings back the whole entry (an undo of its
  delete). Restoring an old delete of a record that came back since is a
  409, not a silent nothing.
- A live record never gets its `ignore` columns back. And a restore
  marks its transaction (`heycarl.restoring`), so the reminders plugin
  does not re-arm a reminder whose old time comes back: restoring a
  sent reminder's earlier time must not send it again.
- A stored row may be older than its table. Restore writes only the
  columns both have, so a column added since gets its default, and one
  dropped since is left out.
- A restore is a change like any other, so it has a version and can be
  undone.
- Purge: deleting a trash entry for good removes every version of its
  records. It needs `via = app`, checked in the database. MCP has no
  purge tool and the generic tool never reaches the core scope, so a
  token minted for MCP cannot purge.
- MCP gets `history`, `trash` (read), `restore` and `undo` (write), as
  tools of their own that call the core functions; the generic tool
  still never reaches core. A read-only client sees history, deleted
  records and edited-out text included: it is the person's own data.
- The test: through MCP with a `write` token, build a contact with a
  field, note, tag, activity, relationship, date and reminder; delete on
  every table; try the purge function and the version table; restore;
  and compare the whole graph with the original.
- The app gets a History page: recent changes, with who made them, and
  the trash. Undo, restore and purge from there.
- Nothing expires by itself. The trash empties when the person empties
  it: a deletion nobody noticed for months is the case this is for.

Costs, accepted:

- Text the person edits out stays in the history until they purge the
  record. The History page says so.
- Storage grows with every edit. The data is small: one person's
  contacts and notes.
- A restored date whose occurrence has passed may get a second
  reminder: the trigger makes one for the next occurrence, and the old
  one comes back from the trash.
- Turning a date's reminder off deletes the reminder, so it lands in the
  trash as its own entry. Restoring it brings back a reminder the date
  no longer links to.
- A pending reminder restored from the trash after its time is sent at
  once: it never went out.
- A tracked table is identified by OID. A migration that drops and
  recreates a tracked table orphans its history; alter it instead.

Found in review after the build, and open:

- A client can flood the trash with junk entries. "Empty the trash"
  then takes the real ones with it, so the person must purge one entry
  at a time. A purge by `via` (everything one client deleted) would fix
  it.
- Storage has no limit per person, and `todo.notes` and
  `reminder.body` have no length check. A client can write without end;
  every version is a full row. It destroys nothing, but a full disk
  stops the database. A quota per owner is the fix.
- Any request that carries `via = app` may purge, so one script
  injection in any plugin frontend could empty the trash. The CSP is the
  barrier. A step-up confirmation for purge (a single-use token from the
  kernel) would add a second.
- `track()` does not refuse a table whose key columns `heycarl_user`
  may update. A key change would leave a record neither live nor in the
  trash. No table allows it today.

## Links

Any entry can link to any other: a todo to the person it is for, a
reminder to a todo, a note to a gift. An entry is a record in a tracked
table (see History), so every plugin takes part without code of its own.

Decided:

- One core table, `heycarl_private.link`: owner, two ends as
  (table, key jsonb) like a version, and an optional label of up to 200
  characters ("for the trip"). Undirected: the ends are stored in a fixed
  order, so a pair has one link.
- It is tracked like a plugin table: a link added or removed is in the
  history and can be undone.
- No foreign key can point at "any table", so a link to a deleted entry
  stays and is hidden. When the entry comes back from the trash, its
  links show again with no extra step. Purging the entry deletes its
  links for good.
- The core API, SECURITY DEFINER like History: `link`, `unlink`,
  `links` (the live entries linked to one entry) and `entries` (search
  by label across every tracked table, for a picker). `link` checks that
  both ends are live and the caller's.
- An entry's label is a guess from its columns: a name, a title, a body.
  A plugin may name a route for a table, with
  `heycarl_sdk.entry_route('contact', '/p/prm/heycarl-prm#/contact/{id}')`;
  the reminders the PRM makes carry such URLs already. The panel links an
  entry there, or to the plugin's page when it names none.
- MCP: `entries_search`, `links_list`, `link`, `unlink`. Unlink asks no
  confirmation: undo brings a link back.
- The UI: a Svelte component in the SDK, `Related.svelte`, that each
  plugin page embeds under an entry. It lists the links, adds one through
  the picker, and removes one.
- How two people are related stays in the PRM (`relationship`, with kinds
  such as parent and child, each with its reverse). MCP gets
  `people_relate` and `people_unrelate` for it. A generic link between
  two people says only that they are linked, with a label.

Found after the build, accepted:

- `entries` takes up to `max_rows` from each table, not in all; the panel
  shows the first ten.
- A link removed before its entry was purged stays in the trash, and
  restoring it brings back a link nobody sees. Purge removes only the
  links that were live.
- Join rows and preferences have no label, so the search leaves them
  out; the API still links them.

## Security

The data is personal: people, notes about them, dates. A review in
November 2026 found the holes the MCP section closes, and these.

Accepted for now, by the owner:

- Hetztop runs agents as `lillecarl`, a PostgreSQL superuser with
  password-free root through `ai-rebuild`. Any agent session there can
  read all Hey Carl data. The real fix is a host with no agents.
- One factor: dex's local password. Caddy limits sign-in POSTs per
  address (croshome `gate.nix`); there is no second factor.
- No backups and no disk encryption.

Closed:

- A grant ends 90 days after consent, however often it refreshes. A
  rotated refresh token that comes back ends its grant.
- `allowedEmails` holds at every request, not only at sign-in: a session
  or grant whose address left the list stops working. Only a verified
  address counts.
- The CSP allows scripts from this site only, plus the shell's inline
  bootstrap by its hash, which the kernel reads from `index.html`.
- At most 50 clients without a grant exist at a time.
- The units run with an empty capability set, a system call filter and
  the usual `Protect*` options. PostgREST is its own OS user, the only
  one that logs in as `heycarl_authenticator`, and only the kernel
  reaches its socket.

Open:

- A client can approve its own elicitation, so confirmation guards
  against model mistakes, not a hostile client. History and trash (see
  above) now make every write undoable, so a hostile client costs work,
  not data; the open items there remain.

## Open questions

- Google OAuth apps in "testing" status may expire refresh tokens after
  about a week, and a Workspace admin may have to allow the app. The
  calendar plugin then marks the account "connect again". Check this
  against the real client once it exists.

## Build order

1. Kernel, schema, PostgREST and the shell, with the todos plugin. Tests
   against real PostgreSQL and PostgREST in the Nix sandbox.
2. NixOS module `services.heycarl`. The croshome gate takes a hostname per
   app, a unix-socket upstream and the public routes.
3. Deploy to hetztop. Install the PWA and send one web push.
4. reminders, contacts, calendar.
