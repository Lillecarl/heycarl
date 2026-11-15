# Hey Carl

A personal application framework at `https://hey.lillecarl.com`. One kernel,
and features as plugins: contacts (a Monica-style CRM), calendar sync,
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
3. **crm**: Monica's model: people, contact fields, relationships, notes,
   activities, tags, gifts and important dates. See below.
4. **calendar**: Google (private), Google Workspace (work) and CalDAV/ICS.
   Mirrors events between calendars at a chosen visibility: full details,
   or "busy" with no title.

## The CRM

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
  a browser session, `mcp:<client_id>` for an MCP tool, `kernel` for a
  plugin's own code. `Minter.user()` requires it. A job has no person
  and no claim. The trigger records it.
- The functions are SECURITY DEFINER with dynamic SQL: table names
  quoted with `%I` and checked against the tracked list, a pinned
  `search_path`, and a refusal when no person calls.
- Trash: the deleted records that have not come back, grouped by
  transaction. Deleting a contact cascades to its notes, dates and
  fields, and to the date's reminder; they share a transaction, so they
  are one trash entry and come back together.
- Restore takes a version id. A live record gets that state back, except
  its `ignore` columns: an old reminder state must not send it again. A
  deleted record comes back with its whole trash entry, whole, under its
  old ids (`OVERRIDING SYSTEM VALUE`), so links to it hold.
- Restore puts in what fits and reports the rest. It makes passes over
  the entry's rows, each insert in a savepoint, retrying a row whose
  parent is not back yet, and stops when a pass adds nothing. A row
  already live is done. A row that still fails stays in the trash: its
  parent was deleted later, or a new row took its unique name. Restoring
  the entry again after the cause is gone adds it.
- A stored row may be older than its table. Restore writes only the
  columns both have, so a column added since gets its default, and one
  dropped since is left out.
- A restore is a change like any other, so it has a version and can be
  undone.
- Purge: deleting a trash entry for good removes every version of its
  records. It needs `via = app`, checked in the database. MCP has no
  purge tool and the generic tool never reaches the core scope, so a
  token minted for MCP cannot purge.
- MCP gets `history`, `trash` (read) and `restore` (write), as tools of
  their own that call the core functions; the generic tool still never
  reaches core.
- The test: through MCP with a `write` token, build a contact with a
  field, note, tag, activity, relationship, date and reminder; delete on
  every table; try the purge function and the version table; restore;
  and compare the whole graph with the original.
- The app gets a History page: recent changes, with who made them, and
  the trash. Restore and purge from there.
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
  against model mistakes, not a hostile client. A trash the person
  empties in the app would.

## Open questions

- Google OAuth apps in "testing" status may expire refresh tokens after
  about a week, and a Workspace admin may have to allow the app. Check
  before the calendar plugin is built.

## Build order

1. Kernel, schema, PostgREST and the shell, with the todos plugin. Tests
   against real PostgreSQL and PostgREST in the Nix sandbox.
2. NixOS module `services.heycarl`. The croshome gate takes a hostname per
   app, a unix-socket upstream and the public routes.
3. Deploy to hetztop. Install the PWA and send one web push.
4. reminders, contacts, calendar.
