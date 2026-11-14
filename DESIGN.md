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
internet :443 -> Caddy (TLS) -> oauth2-proxy gate -> unix:/run/heycarl/kernel.sock
                                                         kernel (FastAPI)
                                                           -> unix:/run/heycarl/postgrest.sock
                                                                PostgREST -> PostgreSQL
```

- Caddy and the gate come from croshome `modules/nixos/gate.nix`. dex is the
  identity provider.
- The kernel and PostgREST listen only on unix sockets. Hetztop runs agents as
  local users, so a loopback port is not a trust boundary there. Socket file
  permissions are.

## Identity

The gate is the only OIDC client. The SPA holds no token.

- The gate signs the user in with dex, keeps the session in an HttpOnly cookie
  and sends `X-Forwarded-User` and `X-Forwarded-Email` to the kernel.
- The kernel trusts those headers because only the gate's group can open the
  kernel socket.
- This differs from Torg, where the SPA does PKCE and passes the IdP token on.
  For one user behind a gate, the second sign-in adds nothing. Plugin
  JavaScript also cannot read a cookie it never sees.
- Session expiry: the gate answers `/api/*` with 401, not a redirect
  (`--api-route`). The shell then reloads the page and the gate signs in again.

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
- Migrations run at kernel start, in one transaction under an advisory lock,
  and end with `NOTIFY pgrst`.

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
push channels. The gate skips auth for `^/api/public/` and for the PWA
manifest and icons. Every public route checks its own secret or signature.
This is the one path past the gate, and it is narrow by intent.

## PWA

- Web app manifest, service worker, installable on Android Chrome.
- Web Push with VAPID keys. The kernel stores each device's push
  subscription and sends through the push service.
- The manifest is fetched without cookies, so it is a public route.

## First plugins

1. **todos**: the thin slice that proves the stack end to end.
2. **reminders**: a reminder on any object. Delivery by web push, and by a
   phone call through 46elks with text-to-speech.
3. **contacts**: Monica's model: people, relationships, notes, activities,
   important dates (which create reminders), "last talked to".
4. **calendar**: Google (private), Google Workspace (work) and CalDAV/ICS.
   Mirrors events between calendars at a chosen visibility: full details,
   or "busy" with no title.

## Open questions

- Google OAuth apps in "testing" status may expire refresh tokens after
  about a week, and a Workspace admin may have to allow the app. Check
  before the calendar plugin is built.
- `hey.lillecarl.com` needs an A record to 65.108.150.98 before the first
  switch. Caddy gets the certificate over HTTP-01.

## Build order

1. Kernel, schema, PostgREST and the shell, with the todos plugin. Tests
   against real PostgreSQL and PostgREST in the Nix sandbox.
2. NixOS module `services.heycarl`. The croshome gate takes a hostname per
   app, a unix-socket upstream and the public routes.
3. Deploy to hetztop. Install the PWA and send one web push.
4. reminders, contacts, calendar.
