{
  mkShell,
  nixfmt,
  postgresql,
  postgrest,
  kernel,
}:

mkShell {
  inputsFrom = [ kernel ];
  packages = [
    nixfmt
    postgresql
    postgrest
  ];
}
