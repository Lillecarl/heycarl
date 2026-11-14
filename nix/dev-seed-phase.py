"""Waits for Hey Carl, then fills it with demo data."""

from vivarium_runner import Machines


async def test(vms: Machines) -> None:
    hey = vms.hey
    await hey.wait_for_unit("heycarl.service")
    await hey.wait_for_unit("caddy.service")
    await hey.wait_for_unit("dex.service")
    await hey.wait_until_succeeds("curl -sf http://127.0.0.1:8080/healthz", 120)
    # A login shell, for HEYCARL_PUBLIC_URL from environment.variables.
    print(await hey.succeed("bash -lc heycarl-seed"))
