# The base16 schemes Hey Carl offers, from nixpkgs' copy of tinted-theming's
# collection:
#
# - `themes.json`: every scheme, its name, variant and 16 colours;
# - `defaults.css`: the light and dark defaults as `--hc-base0X`, picked by
#   the browser's colour preference. A page shows them before any script
#   runs, and keeps them without one.
{
  lib,
  runCommand,
  python3,
  base16-schemes,
  # Not `light`/`dark`: callPackage would fill those from nixpkgs.
  defaultThemes ? {
    light = "gruvbox-light-medium";
    dark = "gruvbox-dark-hard";
  },
}:
runCommand "heycarl-themes" { nativeBuildInputs = [ (python3.withPackages (ps: [ ps.pyyaml ])) ]; }
  ''
    mkdir -p $out
    python3 -I ${./themes.py} ${base16-schemes}/share/themes \
      ${
        lib.escapeShellArgs [
          defaultThemes.light
          defaultThemes.dark
        ]
      } $out
  ''
