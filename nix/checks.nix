{
  lib,
  runCommand,
  nixfmt,
  kernel,
  plugins,
}:

plugins
// {
  inherit kernel;

  nixfmt =
    runCommand "nixfmt-check"
      {
        nativeBuildInputs = [ nixfmt ];
        src = lib.fileset.toSource {
          root = ../.;
          fileset = lib.fileset.fileFilter (file: file.hasExt "nix") ../.;
        };
      }
      ''
        find "$src" -name '*.nix' -exec nixfmt --check {} +
        touch $out
      '';
}
