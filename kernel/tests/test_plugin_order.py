from __future__ import annotations

from pathlib import Path

import pytest

from heycarl.plugins import Manifest, Plugin, in_requires_order


def plugin(name: str, *requires: str) -> Plugin:
    manifest = Manifest(contract=1, name=name, title=name, requires=list(requires))
    return Plugin(manifest=manifest, root=Path("/nowhere"))


def names(plugins: list[Plugin]) -> list[str]:
    return [p.name for p in plugins]


def test_a_required_plugin_comes_first() -> None:
    # A Nix attribute set lists them by name: google_contacts before prm.
    found = in_requires_order(
        [plugin("calendar"), plugin("google_contacts", "prm"), plugin("prm")]
    )
    assert names(found) == ["calendar", "prm", "google_contacts"]


def test_a_missing_or_circular_requirement_fails() -> None:
    with pytest.raises(RuntimeError, match="not installed"):
        in_requires_order([plugin("google_contacts", "prm")])
    with pytest.raises(RuntimeError, match="require each other"):
        in_requires_order([plugin("a", "b"), plugin("b", "a")])
