{
  lib,
  callPackage,
  runCommand,
  nixfmt,
  kernel,
  frontend,
  plugins,
}:

plugins
// {
  inherit kernel frontend;

  vm = callPackage ./vm-test.nix { };

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
