{ testers }:
let
  fakeGoogle = "http://127.0.0.1:8099";
in
testers.runNixOSTest {
  name = "heycarl";

  nodes.machine =
    { pkgs, ... }:
    {
      imports = [ ./module.nix ];
      services.heycarl = {
        enable = true;
        publicUrl = "https://hey.example.com";
        oidc = {
          issuer = "http://127.0.0.1:5556/dex";
          clientSecretFile = pkgs.writeText "client-secret" "test-secret";
        };
        allowedEmails = [
          "alice@example.com"
          "bob@example.com"
        ];
        google = {
          clientId = "hey.apps.example.com";
          clientSecretFile = pkgs.writeText "google-client-secret" "google-test-secret";
          tokenKeyFile = pkgs.writeText "google-token-key" "a test key, at least thirty-two bytes long";
          testEndpoints = {
            authorize = "${fakeGoogle}/o/oauth2/v2/auth";
            token = "${fakeGoogle}/token";
            revoke = "${fakeGoogle}/revoke";
            calendar = "${fakeGoogle}/calendar/v3";
          };
        };
      };

      # Google, faked: the kernel's tests use the same file.
      systemd.services.fake-google = {
        wantedBy = [ "multi-user.target" ];
        before = [ "heycarl.service" ];
        serviceConfig = {
          ExecStart = "${pkgs.python3.interpreter} ${../kernel/tests/fake_google.py} 8099 hey.apps.example.com google-test-secret";
          DynamicUser = true;
        };
      };

      services.dex = {
        enable = true;
        settings = {
          issuer = "http://127.0.0.1:5556/dex";
          storage.type = "memory";
          web.http = "127.0.0.1:5556";
          enablePasswordDB = true;
          oauth2.skipApprovalScreen = true;
          # "password", from dex's example configuration.
          staticPasswords =
            map
              (name: {
                email = "${name}@example.com";
                hash = "$2a$10$2b2cU8CPhOTaGrs1HRQuAueS7JTT5ZHsHSzYiFPm1leZck7Mc8T4W";
                username = name;
                userID = name;
              })
              [
                "alice"
                "bob"
                "eve"
              ];
          staticClients = [
            {
              id = "hey";
              name = "Hey Carl";
              secret = "test-secret";
              redirectURIs = [ "https://hey.example.com/auth/callback" ];
            }
          ];
        };
      };

      environment.systemPackages = [
        pkgs.curl
        (pkgs.writeShellApplication {
          name = "heycarl-login";
          runtimeInputs = [
            pkgs.curl
            pkgs.gnugrep
            pkgs.coreutils
          ];
          text = builtins.readFile ./vm-login.sh;
        })
      ];
      # Stands in for the reverse proxy: a member of the socket group.
      users.users.proxy = {
        isSystemUser = true;
        group = "proxy";
        extraGroups = [ "heycarl" ];
      };
      users.groups.proxy = { };
      users.users.outsider = {
        isNormalUser = true;
      };
    };

  testScript = ''
    import json

    machine.wait_for_unit("dex.service")
    machine.wait_for_unit("heycarl.service")
    machine.wait_for_unit("heycarl-postgrest.service")
    machine.wait_for_file("/run/heycarl/kernel.sock")

    def curl(args, user="proxy"):
        return machine.succeed(
            f"runuser -u {user} -- curl -s --unix-socket /run/heycarl/kernel.sock {args}"
        )

    def status(args):
        return curl(f"-o /dev/null -w '%{{http_code}}' {args}")

    def login(name):
        cookie = machine.succeed(f"runuser -u proxy -- heycarl-login {name}@example.com").strip()
        return f"-H 'cookie: {cookie}' -H 'origin: https://hey.example.com'"

    with subtest("nothing private answers without a session"):
        assert status("http://hey/api/me") == "401"
        assert status("-H 'x-forwarded-user: alice' http://hey/api/me") == "401"
        assert status("http://hey/") == "302"
        assert json.loads(curl("http://hey/manifest.webmanifest"))["name"] == "Hey Carl"
        # A browser will not register a service worker behind a redirect.
        assert status("http://hey/service-worker.js") == "200"
        curl("-f http://hey/icons/icon-192.png -o /dev/null")

    with subtest("MCP clients find the authorization server without a session"):
        headers = curl(
            "-o /dev/null -D - -X POST -H 'content-type: application/json' -d '{}' http://hey/mcp"
        )
        assert headers.startswith("HTTP/1.1 401"), headers
        assert 'resource_metadata="https://hey.example.com/.well-known/oauth-protected-resource/mcp"' in headers, headers
        meta = json.loads(curl("http://hey/.well-known/oauth-authorization-server"))
        assert meta["registration_endpoint"] == "https://hey.example.com/register", meta
        assert status("http://hey/authorize") == "302"

    with subtest("signing in through dex"):
        as_alice = login("alice")
        as_bob = login("bob")
        me = json.loads(curl(f"{as_alice} http://hey/api/me"))
        assert me["email"] == "alice@example.com", me
        machine.fail("runuser -u proxy -- heycarl-login eve@example.com")

    with subtest("the shell is served to a session"):
        assert "<title>Hey Carl</title>" in curl(f"{as_alice} http://hey/")
        assert "<title>Hey Carl</title>" in curl(f"{as_alice} http://hey/p/todos/heycarl-todos")
        assert "<title>Hey Carl</title>" in curl(f"{as_alice} http://hey/p/counters/heycarl-counters")

    with subtest("the CSP allows the shell's own inline script and no other"):
        import base64, hashlib, re
        page = curl(f"-D - {as_alice} http://hey/")
        head, body = page.split("\r\n\r\n", 1)
        [csp] = [l.split(":", 1)[1] for l in head.split("\r\n") if l.lower().startswith("content-security-policy:")]
        scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", body, re.S)
        assert scripts, body
        for s in scripts:
            digest = base64.b64encode(hashlib.sha256(s.encode()).digest()).decode()
            assert f"'sha256-{digest}'" in csp, csp
        assert "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";")[0], csp
        assert "heycarl.theme" in "".join(scripts), scripts

    with subtest("the theme list and the default palette are served"):
        themes = json.loads(curl(f"-f {as_alice} http://hey/themes/themes.json"))
        slugs = {t["slug"] for t in themes}
        assert {"gruvbox-dark-hard", "gruvbox-light-medium"} <= slugs, sorted(slugs)[:10]
        sheets = re.findall(r'href="(/_app/immutable/[^"]+\.css)"', body)
        css = "".join(curl(f"-f {as_alice} 'http://hey{s}'") for s in sheets)
        assert "--hc-base00:#1d2021" in css.replace(" ", ""), sheets

    with subtest("each person keeps their own theme"):
        pref = "http://hey/api/data/core/preference"
        set_theme = "http://hey/api/data/core/rpc/preference_set_theme"
        assert status(pref) == "401"
        assert status(f"-X POST -H 'content-type: application/json' -d '{{\"theme\":\"nord\"}}' {set_theme}") == "401"
        curl(f"-f -X POST {as_alice} -H 'content-type: application/json' -d '{{\"theme\":\"nord\"}}' {set_theme}")
        assert json.loads(curl(f"-f {as_alice} {pref}"))[0]["theme"] == "nord"
        assert json.loads(curl(f"-f {as_bob} {pref}")) == []

    with subtest("an unknown API path is a 404, not the shell"):
        assert status(f"{as_alice} http://hey/api/nope") == "404"

    with subtest("plugins are listed and their modules served"):
        plugins = json.loads(curl(f"{as_alice} http://hey/api/plugins"))
        assert sorted(p["name"] for p in plugins) == ["calendar", "counters", "crm", "reminders", "todos"], plugins
        for p in plugins:
            curl(f"-f {as_alice} 'http://hey{p['frontend']['module']}' -o /dev/null")

    with subtest("data round-trips through PostgREST with row-level security"):
        curl(
            f"-f {as_alice} -H 'content-type: application/json' "
            "-d '{\"title\": \"from the vm\"}' http://hey/api/data/todos/todo"
        )
        todos = json.loads(curl(f"{as_alice} http://hey/api/data/todos/todo"))
        assert [t["title"] for t in todos] == ["from the vm"], todos
        assert json.loads(curl(f"{as_bob} http://hey/api/data/todos/todo")) == []

    with subtest("counters are private, and nobody counts on another's counter"):
        post = "-H 'content-type: application/json' -H 'prefer: return=representation'"
        counters = "http://hey/api/data/counters"
        [cups] = json.loads(curl(
            f"-f {as_alice} {post} -d '{{\"name\": \"cups\", \"step\": 2}}' {counters}/counter"
        ))
        cid = cups["id"]
        bumped = json.loads(curl(f"-f {as_alice} {post} -d '{{\"counter_id\": {cid}}}' {counters}/rpc/bump"))
        assert bumped["value"] == 2, bumped
        assert status(f"{counters}/counter_value") == "401"
        assert json.loads(curl(f"{as_bob} {counters}/counter_value")) == []
        assert json.loads(curl(f"{as_bob} {counters}/event")) == []
        # The composite foreign key: Bob's event can only name Bob's counter.
        assert status(f"{as_bob} {post} -d '{{\"counter_id\": {cid}, \"delta\": 5}}' {counters}/event") == "409"
        for fn, extra in [("bump", ""), ("set_value", ", \"target\": 9"), ("reset_value", ""), ("undo_last", "")]:
            assert status(f"{as_bob} {post} -d '{{\"counter_id\": {cid}{extra}}}' {counters}/rpc/{fn}") == "404", fn
        assert status(f"{as_bob} '{counters}/rpc/daily_totals?counter_id={cid}'") == "404"
        assert status(f"{as_bob} -X DELETE '{counters}/counter?id=eq.{cid}'") == "204"
        [mine] = json.loads(curl(f"{as_alice} '{counters}/counter_value?id=eq.{cid}'"))
        assert (mine["value"], mine["events"]) == (2, 1), mine

    with subtest("Google Calendar through a fake Google"):
        machine.wait_for_unit("fake-google.service")
        machine.wait_for_open_port(8099)
        plugin = "http://hey/api/plugins/calendar"
        cal = "http://hey/api/data/calendar"
        site = "https://hey.example.com"

        def google(op, **args):
            return json.loads(machine.succeed(
                "curl -sf -H 'content-type: application/json' "
                f"-d '{json.dumps({'op': op, 'args': args})}' http://127.0.0.1:8099/_control"
            ))

        def consent(who):
            url = json.loads(curl(f"-f {who} -X POST {plugin}/connect"))["url"]
            assert "code_challenge_method=S256" in url and "calendar.readonly" in url, url
            back = machine.succeed(f"curl -s -o /dev/null -w '%{{redirect_url}}' '{url}'")
            assert back.startswith(f"{site}/api/plugins/calendar/callback?"), back
            return "http://hey" + back.removeprefix(site)

        def sync_now(who):
            # The kernel allows one sync a person asks for each ten seconds.
            return json.loads(machine.wait_until_succeeds(
                f"runuser -u proxy -- curl -sf --unix-socket /run/heycarl/kernel.sock {who} -X POST {plugin}/sync"
            ))

        def agenda(who):
            return {e["google_id"]: e["title"] for e in json.loads(curl(f"{who} {cal}/agenda"))}

        info = json.loads(curl(f"{as_alice} {plugin}/status"))
        assert info == {"configured": True, "redirect_uri": f"{site}/api/plugins/calendar/callback"}, info
        assert status(f"{as_alice} '{plugin}/callback?state=forged&code=x'") == "400"
        assert status(f"{as_bob} '{consent(as_alice)}'") == "403"
        assert status(f"'{consent(as_alice)}'") == "401"
        assert status(f"{as_alice} '{consent(as_alice)}'") == "303"
        # The job, which the connect woke, syncs before anyone asks.
        machine.wait_until_succeeds(
            f"runuser -u proxy -- curl -sf --unix-socket /run/heycarl/kernel.sock {as_alice} "
            f"'{cal}/account?last_sync_at=not.is.null' | grep -q google_account",
            timeout=30,
        )

        first = sync_now(as_alice)
        assert first["ok"] and first["events"] == 6, first
        titles = agenda(as_alice)
        assert titles["standup"] == "Standup" and "lunch" in titles, titles
        for path in ["account", "calendar", "event", "agenda"]:
            assert json.loads(curl(f"{as_bob} {cal}/{path}")) == [], path
            assert status(f"{cal}/{path}") == "401", path
        refuse = f"-H 'content-type: application/json' -d '{{\"owner\": \"alice\"}}' {cal}/rpc/refresh_token"
        assert status(f"{as_bob} {refuse}") == "403"
        assert status(f"{as_alice} {refuse}") == "403"

        google("cancel", id="lunch")
        google("rename", id="standup", title="Standup, later")
        assert sync_now(as_alice)["ok"]
        titles = agenda(as_alice)
        assert "lunch" not in titles and titles["standup"] == "Standup, later", titles

        google("gone")
        google("expire_access")
        grants = google("state")["refresh_grants"]
        assert sync_now(as_alice)["ok"]
        state = google("state")
        assert state["refresh_grants"] == grants + 1, state
        assert any("syncToken" in q for _, q in state["requests"])
        assert len(agenda(as_alice)) == 5

        [refresh] = state["refresh_tokens"]
        sealed = machine.succeed(
            "runuser -u postgres -- psql -d heycarl -tAc "
            "\"select encode(sealed, 'escape') from heycarl_private.calendar_google_token\""
        )
        assert sealed.strip() and refresh not in sealed
        bye = json.loads(curl(f"-f {as_alice} -X POST {plugin}/disconnect"))
        assert bye == {"disconnected": True, "revoked": True}, bye
        assert google("state")["revoked"] == [refresh]
        assert json.loads(curl(f"{as_alice} {cal}/event")) == []
        assert refresh not in machine.succeed("journalctl -u heycarl -u heycarl-postgrest -u postgresql")

    with subtest("a write from another origin is refused"):
        cookie_only = as_alice.split(" -H 'origin")[0]
        assert status(
            f"{cookie_only} -H 'origin: https://evil.example' -H 'content-type: application/json' "
            "-d '{\"title\": \"x\"}' http://hey/api/data/todos/todo"
        ) == "403"

    with subtest("an app password opens /dav/ only, until it is revoked"):
        made = json.loads(curl(
            f"-f {as_alice} -H 'content-type: application/json' "
            "-d '{\"name\": \"vm phone\"}' http://hey/api/data/core/rpc/app_password_create"
        ))
        basic = f"-u {made['username']}:{made['secret']} -H 'depth: 0'"
        assert status(f"{basic} -X PROPFIND http://hey/dav/") == "207"
        assert status("-X PROPFIND http://hey/dav/") == "401"
        assert status(f"{basic} http://hey/api/me") == "401"
        assert status(f"{basic} -X PUT -d x http://hey/dav/x") == "403"
        assert status(f"{basic} -X PROPFIND http://hey/.well-known/carddav") == "301"
        assert status(f"{as_alice} -X DELETE 'http://hey/api/data/core/app_password?id=eq.{made['username']}'") == "204"
        assert status(f"{basic} -X PROPFIND http://hey/dav/") == "401"

    with subtest("a birthday in the CRM makes a reminder"):
        post = f"{as_alice} -H 'content-type: application/json' -H 'prefer: return=representation'"
        [anna] = json.loads(
            curl(f"{post} -d '{{\"first_name\": \"Anna\"}}' http://hey/api/data/crm/contact")
        )
        curl(
            f"-f {post} -d '{{\"contact_id\": {anna['id']}, \"month\": 3, \"day\": 14}}' "
            "http://hey/api/data/crm/important_date"
        )
        [rem] = json.loads(curl(f"{as_alice} http://hey/api/data/reminders/reminder"))
        assert rem["title"] == "Anna's birthday", rem

    with subtest("the kernel made a VAPID key and serves its public half"):
        key = json.loads(curl(f"{as_alice} http://hey/api/push/key"))["key"]
        assert len(key) == 87, key
        machine.succeed("test $(stat -c %a /var/lib/heycarl/vapid.pem) = 600")

    with subtest("only the socket group reaches the kernel"):
        machine.fail(
            "runuser -u outsider -- curl -sf --unix-socket /run/heycarl/kernel.sock http://hey/healthz"
        )

    with subtest("only PostgREST logs in as the authenticator, and only the kernel reaches it"):
        psql = "psql -h /run/postgresql -d heycarl -tAc 'select 1'"
        machine.fail(f"runuser -u heycarl -- {psql} -U heycarl_authenticator")
        machine.succeed(f"runuser -u heycarl-postgrest -- {psql} -U heycarl_authenticator")
        machine.fail(f"runuser -u heycarl-postgrest -- {psql} -U heycarl_owner")
        sock = "--unix-socket /run/heycarl-postgrest/postgrest.sock http://pg/"
        machine.fail(f"runuser -u proxy -- curl -sf {sock}")
        machine.succeed(f"runuser -u heycarl -- curl -s -o /dev/null {sock}")
        machine.fail("runuser -u heycarl -- cat /run/credentials/heycarl-postgrest.service/jwt-secret")

    with subtest("PostgREST refuses a request with no token or a forged one"):
        sock = "--unix-socket /run/heycarl-postgrest/postgrest.sock http://pg"
        get = f"runuser -u heycarl -- curl -s -o /dev/null -w '%{{http_code}}' -H 'Accept-Profile: heycarl_plugin_todos' {sock}/todo"
        assert machine.succeed(get) == "401"
        forged = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiaGV5Y2FybF91c2VyIiwic3ViIjoieCJ9.AAAA"
        assert machine.succeed(f"{get} -H 'Authorization: Bearer {forged}'") == "401"
        unsigned = "eyJhbGciOiJub25lIn0.eyJyb2xlIjoiaGV5Y2FybF91c2VyIiwic3ViIjoieCJ9."
        assert machine.succeed(f"{get} -H 'Authorization: Bearer {unsigned}'") == "401"

    with subtest("another app's role in the same server cannot connect to the database"):
        machine.succeed("runuser -u postgres -- psql -c 'create role outsider login'")
        machine.succeed("runuser -u outsider -- psql -h /run/postgresql -d postgres -tAc 'select 1'")
        err = machine.fail("runuser -u outsider -- psql -h /run/postgresql -d heycarl -tAc 'select 1' 2>&1")
        assert "permission denied for database" in err, err

    with subtest("the units run sandboxed"):
        for unit in ["heycarl", "heycarl-postgrest"]:
            print(machine.succeed(f"systemd-analyze security --no-pager {unit}.service | tail -1"))
            def prop(name):
                return machine.succeed(f"systemctl show --value -p {name} {unit}").strip()
            assert prop("CapabilityBoundingSet") == "", unit
            assert prop("ProtectProc") == "invisible", unit

    with subtest("a restart keeps sessions and data"):
        # A switch restarts setup too; it must leave the kernel its files.
        machine.succeed("systemctl restart heycarl-setup.service")
        machine.succeed("systemctl restart heycarl.service")
        machine.wait_for_file("/run/heycarl/kernel.sock")
        machine.wait_until_succeeds(
            "runuser -u proxy -- curl -sf --unix-socket /run/heycarl/kernel.sock http://hey/healthz"
        )
        todos = json.loads(curl(f"{as_alice} http://hey/api/data/todos/todo"))
        assert len(todos) == 1, todos

    with subtest("logging out ends the session"):
        assert status(f"-X POST {as_alice} http://hey/auth/logout") == "204"
        assert status(f"{as_alice} http://hey/api/me") == "401"
  '';
}
