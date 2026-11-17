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
- Every `/api/*` error has PostgREST's body, `{code, message, details, hint}`.
  The data proxy passes PostgREST's errors through. The kernel's own errors
  (`heycarl/errors.py`, the gate in `access.py`) use code `HC<status>`, as
  PostgREST uses `PT<status>` for a raised status, and a crash answers `HC500`
  with no detail. A client parses one shape. Exception: a browser navigation
  under `/api` (the calendar's OAuth callback) answers plain text for a person.

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
- `frontend.embeds`: table -> custom element that shows one entry inside
  another plugin's page. The element gets the entry's key as its `key`
  property. The SDK's `Embed` renders it, or nothing when the plugin
  declares none, so the host keeps its plain link and names no plugin.

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

## Places

A place is a named point (WGS 84 degrees) with a radius in metres. Entries
bind to a place in two ways, and neither needs code in the other plugin:

- A core link from the entry to the place.
- A tag that the entry and the place both carry. A todo tagged "hardware"
  is bound to every place tagged "hardware".

Decided:

- Hey Carl never stores where the person is. The position is an argument
  to `near` and `here`, not a row, and no job asks for it.
- The page sends the position in a POST body, so no URL and no access log
  holds it. The SDK reads it once per press (`@heycarl/sdk/position`),
  never with `watchPosition`.
- Places themselves are ordinary data: owner-only RLS, history, trash,
  tags and links, like a counter.
- Distance is haversine in SQL. One person has tens of places, so a scan
  is fast and PostGIS adds nothing yet.
- `near(lat, lon, within)`: places within `within` metres, nearest
  first, with `inside`. `here(lat, lon)`: the entries
  bound to the places the point is inside, with `via_tag` for a tag
  binding. An entry bound both ways comes once, as a link.
- The kernel's Permissions-Policy allows `geolocation=(self)`. The browser
  still asks the person.

- "What's here?" renders each bound entry's embed, so a linked stop shows
  its next departures.

Planned:

- A notice on arrival needs a position while the app is closed, which a
  PWA cannot get. A native sender (OwnTracks in HTTP mode, or an iOS
  Shortcuts automation) can POST it to a public route with an app
  password. The route checks it against the places and sends a web push,
  and stores nothing.

## Departures

Live departures from saved SL stops, from SL's Transport API
(`transport.integration.sl.se/v1`), which is public and needs no key.

Decided:

- A stop is a row the person owns: an SL site, a name, and a filter of
  mode, lines and direction. Owner-only RLS, history, trash, tags and
  links, like a place. Empty lines or a null mode or direction means
  every one.
- The direction is SL's `direction_code` (1 or 2), never the destination
  text: a short-turn train carries another destination in the same
  direction.
- Departures are SL's public data and are never stored. The kernel
  fetches them when a page or a tool asks, so no job polls SL.
- `GET /api/plugins/departures/sites/{id}/departures` takes the filter
  as query parameters and reads no rows. The page reads its own stops
  through `/api/data`, as the person, and passes the filter. The MCP tool
  reads stops through `call()`, so RLS applies on both paths.
- A board is in expected-time order. With a `limit`, it shows the first
  `limit` departures, plus the next one of each line and direction that
  they leave out, so a frequent line never hides a rare one. A cancelled
  departure is not a line's next one. The kernel picks them, so the page
  and `departures_next` show the same set.
- The URL to SL is built from an integer site id only, so no caller can
  make the kernel fetch anything else.
- A site's board is cached for 60 s, at most 200 sites, and concurrent
  askers share one fetch. The page asks once a minute.
- SL publishes no rate limit and sends no Retry-After with its 429. After
  a 429, a 5xx or no answer, the kernel asks SL nothing for 1 minute,
  doubling to 15 minutes while refusals go on (longer if SL ever sends a
  Retry-After). Meanwhile it answers from the last board, up to 15
  minutes old, without departures that have left; the page then says
  "SL is not answering" and the board's time.
- The 1.3 MB site list is cached for a day, and stands in for a week
  while SL does not answer. The kernel searches it; the browser gets the
  top 20 matches.
- SL sends local times without an offset. The kernel adds
  Europe/Stockholm's offset, and the page counts minutes from the
  expected time when it renders, so a cached answer never shows a stale
  "2 min".
- SL down or unreadable, with no board to stand in: 502 with a message
  for the person.
- `HEYCARL_SL_URL` replaces SL's address, for tests only.

## Google accounts

The kernel owns every Google grant. A plugin names the scopes it needs, and
gets an access token for an account from the kernel. One person may connect
several Google accounts. Each account is one grant, with its own sealed
refresh token.

- Why the kernel: Google's revoke ends every scope that a Google user gave
  this OAuth client. Two plugins with grants of their own would disconnect
  each other.
- Off by default. The module options `services.heycarl.google.clientId`,
  `clientSecretFile` and `tokenKeyFile` turn it on, all three together.
- The redirect URI to register at the Google client is
  `<publicUrl>/api/google/callback`.
- Manifest: `"google": {"scopes": [...]}`. Every scope starts with
  `https://www.googleapis.com/auth/`. A connect asks for `openid email`
  and the union of the scopes of every installed plugin, with
  `include_granted_scopes=true`. Google lets the person refuse a scope,
  so the kernel stores the scopes Google granted. A plugin uses an
  account only when that account holds all of the plugin's scopes. The
  page offers "Grant access" (a connect with `login_hint`) otherwise.
- Identity: the kernel reads `sub` and `email` from Google's userinfo
  endpoint with the new access token. An account is unique per
  `(owner_sub, google_sub)`, so connecting the same Google account again
  replaces its token and keeps its data.
- The connect flow is the calendar's flow from before: authorization code
  with PKCE (S256), `access_type=offline`, `prompt=consent select_account`.
  The state and the verifier stay in kernel memory for 10 minutes, bound
  to the person and to the hash of their session cookie. The callback is a
  session route, uses the state once, and redirects to the `next` path the
  connect named (a same-site path only).
- Disconnect revokes the account's refresh token at Google, then deletes
  the account whatever Google answered. Plugin rows reference
  `heycarl_private.google_account (id, owner_sub)` with `on delete
  cascade`, so the plugins' copies go with it.
- Tables: `heycarl_private.google_account` (owner, Google sub and email,
  granted scopes, `needs_reconnect`, `last_error`) and
  `heycarl_private.google_token` (sealed refresh token). RLS lets a
  person SELECT their own accounts, without the token, through
  `heycarl.google_account`. `google_token` has no grant for any API role.
  The kernel writes both through SECURITY DEFINER functions in the API
  schema, which only `heycarl_worker` may run. Each one takes the owner.
- Sealing: AES-GCM under a key derived from `tokenKeyFile`, with the owner
  as associated data. A sealed token copied to another owner does not
  open. One owner's tokens can open in another account row of the same
  owner. That gives the owner nothing that they do not already have.
- Plugin API (`ctx.google`, None when off): `accounts(scopes)` lists the
  accounts to sync, `session(account)` gives an HTTP helper that adds a
  fresh access token, refreshes once on a 401, and marks the account
  `needs_reconnect` when Google refuses the refresh token.
  `on_connect(fn)` wakes a plugin's job after a connect. Access tokens
  live only in kernel memory, per account.

## Calendar

Google Calendar into Hey Carl, one way and read-only. The only scope is
`https://www.googleapis.com/auth/calendar.readonly`. Hey Carl never asks
for a write scope.

- The grant is the kernel's (see "Google accounts"). Each connected
  account that granted the scope has its own calendars. Without a Google
  client the page says "not configured", the tools find an empty cache,
  and no job runs.
- Migration 0002 moved each person's grant from the plugin's own token
  table into `heycarl_private.google_account`, sealed token unchanged.
- Tables, in `heycarl_plugin_calendar`: `sync_status` (last sync and
  error, per account), `calendar` (account, Google id, name, color, zone,
  selected), `calendar_sync` (sync token and window) and `event`. The
  `account` view joins the person's Google accounts to their status.
  RLS is owner-only with the composite key, as in counters.
  `heycarl_user` may SELECT `account`, `sync_status`, `calendar`, `event`
  and the `agenda` view, and nothing else. It cannot write anything.
  `calendar_sync` has no grant.
- Every write goes through SECURITY DEFINER functions that only
  `heycarl_worker` may run, and each takes the owner as an argument, as
  `dav_authenticate` does for the gate. The person's own actions (choose
  a calendar, sync now) go to kernel routes, which call those functions
  with the session's `sub`.
- Sync: a job every 10 minutes, woken early by a connect or a newly
  chosen calendar, plus "sync now" (the page, and MCP `calendar_sync`),
  at most once per 10 seconds per person. The kernel's lock per account
  serializes sync, choose and disconnect.
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

## Google Contacts

The `google_contacts` plugin keeps a read-only copy of each connected
Google account's contacts. The person links each one to a contact in the
book and copies fields from Google into it. Google is never told about a
link or a copy. The scope is `contacts.readonly`. Writing back to Google
is a later step: it needs the `contacts` scope in the manifest, and each
account then grants it again.

- `requires: ["prm"]`: the link table references the contact book. The
  kernel orders plugins by `requires` and migrates prm first. A missing
  or circular requirement stops the kernel.
- Tables: `person` (one Google contact: names, organization, biography,
  birthday, and emails, phones, addresses and URLs as `[{value, label}]`),
  `match` (person -> contact), `sync_status` and `sync_state` (the sync
  token, no grant). The `entry` view joins a person to its account and
  its contact. Photos are not stored: the page would load them from
  Google, which would tell Google each time someone looked.
- Sync: as the calendar's, per account. A full list with
  `requestSyncToken`, then the token. The same `personFields` go on every
  call, because a token holds only with the parameters that issued it.
  Deletions arrive with `metadata.deleted`. A rename
  (`previousResourceNames`) updates the row, so its id and link hold. An
  expired token (410, `EXPIRED_SYNC_TOKEN`) means a full sync. Google ends
  a token 7 days after its full sync, and the daily full sync keeps one
  alive.
- Links: `match` is the person's own table, under RLS with the composite
  keys, so neither end can be another owner's. A Google contact has at
  most one contact. A contact may have several Google contacts: the same
  person in two accounts, or a duplicate in one. `match` is tracked, as
  every table a person writes: undo brings back an unlink, and undoing a
  contact's delete brings back its links.
- A Google contact deleted at Google, or a disconnected account, takes
  its links; the contacts stay. Those links are in the trash, and a
  restore reports them as failed, since the Google contact is gone.
- `suggestions()` (invoker) lists likely links for unlinked Google
  contacts: the same email, the same phone (the last nine digits), or the
  same full name. It reads only the database.
- `copy_fields(person_id, fields)` and `import_person(person_id)` are
  invoker functions. They write prm's tables as the person, so prm's RLS,
  grants, CHECKs and history all hold, and undo reverses a copy. A text
  field replaces the book's when Google's is not empty. A list field adds
  the values the book lacks, compared without case. A birthday is added
  when the book has none on that day.
- `person` is tracked for Links, with its version trigger dropped, as
  calendar's `event`.
- MCP: `google_contacts_list`, `_suggestions` (read), `_link`, `_unlink`,
  `_copy`, `_import` and `_sync` (write).

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
