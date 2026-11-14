{
  description = "Hey Carl: a personal application framework";

  inputs = {
    nixpkgs.url = "https://channels.nixos.org/nixpkgs-unstable/nixexprs.tar.xz";
    # Read by nix/compat.nix, so default.nix uses this same lock.
    flake-compatish.url = "github:lillecarl/flake-compatish";
    # vivarium, for the dev environment. Fetched only when `dev` is used.
    nixidae = {
      url = "github:nixidae/nixidae";
      flake = false;
    };
  };

  # A wrapper: every output is an attribute of default.nix.
  outputs =
    inputs:
    let
      inherit (inputs.nixpkgs) lib;
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      perSystem = lib.genAttrs systems (system: import ./default.nix { inherit inputs system; });
      forAllSystems = f: lib.mapAttrs (_: f) perSystem;
    in
    {
      packages = forAllSystems (p: {
        inherit (p) default kernel;
      });
      devShells = forAllSystems (p: {
        default = p.shell;
      });
      checks = forAllSystems (p: p.checks);
      formatter = forAllSystems (p: p.formatter);
    };
}
