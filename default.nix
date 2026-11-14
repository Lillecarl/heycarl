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
  plugins = {
    todos = pkgs.callPackage ./nix/plugin.nix { name = "todos"; };
  };
  kernel = pkgs.callPackage ./nix/kernel.nix { inherit (plugins) todos; };
in
{
  inherit kernel plugins;
  default = kernel;

  shell = pkgs.callPackage ./nix/shell.nix { inherit kernel; };
  checks = pkgs.callPackages ./nix/checks.nix { inherit kernel plugins; };
  formatter = pkgs.nixfmt-tree;
}
