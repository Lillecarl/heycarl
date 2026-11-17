let
  compat = import ./nix/compat.nix;
in
{
  inputs ? compat.inputs,
  system ? builtins.currentSystem,
  pkgs ? import inputs.nixpkgs {
    inherit system;
    config = { };
    overlays = [ ];
  },
}:

let
  themes = pkgs.callPackage ./nix/themes.nix { };
  frontend = pkgs.callPackage ./nix/frontend.nix { inherit themes; };
  plugin = name: pkgs.callPackage ./nix/plugin.nix { inherit name frontend; };
  pluginNames = [
    "todos"
    "reminders"
    "prm"
    "counters"
    "calendar"
    "tags"
    "places"
    "departures"
  ];
  plugins = pkgs.lib.genAttrs pluginNames plugin;
  kernel = pkgs.callPackage ./nix/kernel.nix {
    # Source only: the tests need migrations and backends, not frontends.
    testPlugins = map (name: pkgs.callPackage ./nix/plugin.nix { inherit name; }) pluginNames;
  };
in
{
  inherit
    kernel
    frontend
    plugins
    themes
    ;
  default = kernel;
  nixosModule = ./nix/module.nix;

  shell = pkgs.callPackage ./nix/shell.nix { inherit kernel frontend; };
  checks = pkgs.callPackages ./nix/checks.nix { inherit kernel frontend plugins; };
  formatter = pkgs.nixfmt-tree;

  dev = pkgs.callPackage ./nix/dev.nix {
    vivarium = (import "${inputs.nixidae}/vivarium" { inherit pkgs; }).lib;
  };
}
