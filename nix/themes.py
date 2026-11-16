"""base16 schemes to themes.json and defaults.css. See themes.nix."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROLES = [f"base0{digit}" for digit in "0123456789ABCDEF"]
COLOUR = re.compile(r"#[0-9a-fA-F]{6}")


def scheme(path: Path) -> dict[str, Any]:  # Any: parsed YAML of no fixed shape
    data = yaml.safe_load(path.read_text())
    palette = {role: data["palette"][role].lower() for role in ROLES}
    # A colour that is not #rrggbb would reach CSS unchecked.
    if not all(COLOUR.fullmatch(colour) for colour in palette.values()):
        sys.exit(f"{path.name}: a colour is not #rrggbb: {palette}")
    if data["variant"] not in {"light", "dark"}:
        sys.exit(f"{path.name}: variant {data['variant']!r} is neither light nor dark")
    return {
        "slug": path.stem,
        "name": data["name"],
        "author": data.get("author", ""),
        "variant": data["variant"],
        "palette": palette,
    }


def declarations(theme: dict[str, Any]) -> str:  # Any: see scheme
    lines = [f"--hc-{role}: {theme['palette'][role]};" for role in ROLES]
    return "\n".join([*lines, f"color-scheme: {theme['variant']};"])


def main(source: Path, light: str, dark: str, out: Path) -> None:
    schemes = sorted(
        (scheme(path) for path in source.glob("*.yaml")),
        key=lambda s: str(s["name"]).lower(),
    )
    by_slug = {s["slug"]: s for s in schemes}
    for slug, wanted in ((light, "light"), (dark, "dark")):
        if slug not in by_slug or by_slug[slug]["variant"] != wanted:
            sys.exit(f"default {wanted} theme {slug!r} is not a {wanted} scheme here")
    (out / "themes.json").write_text(json.dumps(schemes, separators=(",", ":")))
    (out / "defaults.css").write_text(
        f"/* {by_slug[light]['name']} and {by_slug[dark]['name']}: Hey Carl's defaults. */\n"
        "@layer hc.tokens {\n"
        f":root {{\n{declarations(by_slug[light])}\n}}\n"
        "@media (prefers-color-scheme: dark) {\n"
        f":root {{\n{declarations(by_slug[dark])}\n}}\n"
        "}\n"
        "}\n"
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4]))
