"""Waits for Hey Carl, then fills it with demo data."""

from vivarium_runner import Machines


async def test(vms: Machines) -> None:
    hey = vms.hey
    await hey.wait_for_unit("heycarl.service")
    await hey.wait_for_unit("caddy.service")
    await hey.wait_until_succeeds("curl -sf http://127.0.0.1:8080/api/me", 120)
    print(await hey.succeed("heycarl-seed"))
