{
  mkShell,
  nixfmt,
  postgresql,
  postgrest,
  kernel,
  frontend,
}:

mkShell {
  inputsFrom = [
    kernel
    frontend
  ];
  packages = [
    nixfmt
    postgresql
    postgrest
  ];
}
