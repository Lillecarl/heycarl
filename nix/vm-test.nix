{ testers }:

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

    with subtest("an unknown API path is a 404, not the shell"):
        assert status(f"{as_alice} http://hey/api/nope") == "404"

    with subtest("plugins are listed and their modules served"):
        plugins = json.loads(curl(f"{as_alice} http://hey/api/plugins"))
        assert sorted(p["name"] for p in plugins) == ["crm", "reminders", "todos"], plugins
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

    with subtest("a write from another origin is refused"):
        cookie_only = as_alice.split(" -H 'origin")[0]
        assert status(
            f"{cookie_only} -H 'origin: https://evil.example' -H 'content-type: application/json' "
            "-d '{\"title\": \"x\"}' http://hey/api/data/todos/todo"
        ) == "403"

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
