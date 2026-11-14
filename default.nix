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
  frontend = pkgs.callPackage ./nix/frontend.nix { };
  plugin = name: pkgs.callPackage ./nix/plugin.nix { inherit name frontend; };
  pluginNames = [
    "todos"
    "reminders"
  ];
  plugins = pkgs.lib.genAttrs pluginNames plugin;
  kernel = pkgs.callPackage ./nix/kernel.nix {
    # Source only: the tests need migrations and backends, not frontends.
    testPlugins = map (name: pkgs.callPackage ./nix/plugin.nix { inherit name; }) pluginNames;
  };
in
{
  inherit kernel frontend plugins;
  default = kernel;
  nixosModule = ./nix/module.nix;

  shell = pkgs.callPackage ./nix/shell.nix { inherit kernel; };
  checks = pkgs.callPackages ./nix/checks.nix { inherit kernel frontend plugins; };
  formatter = pkgs.nixfmt-tree;
}
