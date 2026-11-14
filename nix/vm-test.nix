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
      };
      environment.systemPackages = [ pkgs.curl ];
      # Stands in for the gate: a member of the socket group.
      users.users.gate = {
        isSystemUser = true;
        group = "gate";
        extraGroups = [ "heycarl" ];
      };
      users.groups.gate = { };
      users.users.outsider = {
        isNormalUser = true;
      };
    };

  testScript = ''
    import json

    machine.wait_for_unit("heycarl.service")
    machine.wait_for_unit("heycarl-postgrest.service")
    machine.wait_for_file("/run/heycarl/kernel.sock")

    def kernel(user, args):
        return machine.succeed(
            f"runuser -u {user} -- curl -sf --unix-socket /run/heycarl/kernel.sock {args}"
        )

    as_alice = "-H 'X-Forwarded-User: alice'"

    with subtest("the shell and the manifest are served"):
        assert "<title>Hey Carl</title>" in kernel("gate", "http://hey/")
        assert "<title>Hey Carl</title>" in kernel("gate", "http://hey/p/todos/heycarl-todos")
        assert json.loads(kernel("gate", "http://hey/manifest.webmanifest"))["name"] == "Hey Carl"
        kernel("gate", "http://hey/icons/icon-192.png -o /dev/null")

    with subtest("an unknown API path is a 404, not the shell"):
        machine.fail(
            "runuser -u gate -- curl -sf --unix-socket /run/heycarl/kernel.sock http://hey/api/public/nope"
        )

    with subtest("plugins are listed and their modules served"):
        plugins = json.loads(kernel("gate", f"{as_alice} http://hey/api/plugins"))
        assert sorted(p["name"] for p in plugins) == ["crm", "reminders", "todos"], plugins
        for p in plugins:
            kernel("gate", f"{as_alice} http://hey{p['frontend']['module']} -o /dev/null")

    with subtest("data round-trips through PostgREST with row-level security"):
        kernel(
            "gate",
            f"{as_alice} -H 'content-type: application/json' "
            "-d '{\"title\": \"from the vm\"}' http://hey/api/data/todos/todo",
        )
        todos = json.loads(kernel("gate", f"{as_alice} http://hey/api/data/todos/todo"))
        assert [t["title"] for t in todos] == ["from the vm"], todos
        bob = json.loads(kernel("gate", "-H 'X-Forwarded-User: bob' http://hey/api/data/todos/todo"))
        assert bob == [], bob

    with subtest("a birthday in the CRM makes a reminder"):
        post = f"{as_alice} -H 'content-type: application/json' -H 'prefer: return=representation'"
        [anna] = json.loads(
            kernel("gate", f"{post} -d '{{\"first_name\": \"Anna\"}}' http://hey/api/data/crm/contact")
        )
        kernel(
            "gate",
            f"{post} -d '{{\"contact_id\": {anna['id']}, \"month\": 3, \"day\": 14}}' "
            "http://hey/api/data/crm/important_date",
        )
        [rem] = json.loads(kernel("gate", f"{as_alice} http://hey/api/data/reminders/reminder"))
        assert rem["title"] == "Anna's birthday", rem

    with subtest("the kernel made a VAPID key and serves its public half"):
        key = json.loads(kernel("gate", f"{as_alice} http://hey/api/push/key"))["key"]
        assert len(key) == 87, key
        machine.succeed("test $(stat -c %a /var/lib/heycarl/vapid.pem) = 600")

    with subtest("only the socket group reaches the kernel"):
        machine.fail(
            "runuser -u outsider -- curl -sf --unix-socket /run/heycarl/kernel.sock http://hey/healthz"
        )

    with subtest("a restart migrates nothing and keeps the data"):
        # A switch restarts setup too; it must leave the kernel its files.
        machine.succeed("systemctl restart heycarl-setup.service")
        machine.succeed("systemctl restart heycarl.service")
        machine.wait_for_file("/run/heycarl/kernel.sock")
        machine.wait_until_succeeds(
            "runuser -u gate -- curl -sf --unix-socket /run/heycarl/kernel.sock http://hey/healthz"
        )
        todos = json.loads(kernel("gate", f"{as_alice} http://hey/api/data/todos/todo"))
        assert len(todos) == 1, todos
  '';
}
