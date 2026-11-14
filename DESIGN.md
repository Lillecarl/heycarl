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
- The kernel sets HSTS, `frame-ancestors 'none'`, nosniff and
  `Referrer-Policy: same-origin` on every response.

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
