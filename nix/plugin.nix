/**
  A plugin directory as a store path, with its manifest checked against the
  kernel's model at build time.

  # Inputs

  `name`
  : The plugin's directory name under `plugins/`

  `frontend`
  : The workspace build (./frontend.nix), or null for a plugin without its
    built frontend, as the kernel's tests use it
*/
{
  lib,
  runCommand,
  python3,
  name,
  frontend ? null,
}:
let
  root = ../plugins + "/${name}";
  # The frontend sources build in ./frontend.nix; only the output lands here.
  src = lib.fileset.toSource {
    inherit root;
    fileset = lib.fileset.difference root (lib.fileset.maybeMissing (root + "/frontend"));
  };
  hasFrontend = frontend != null && lib.elem name frontend.plugins;
in
runCommand "heycarl-plugin-${name}"
  {
    nativeBuildInputs = [ (python3.withPackages (p: [ p.pydantic ])) ];
    kernelSrc = ../kernel/heycarl/plugins.py;
  }
  ''
    cp -r ${src} $out
    chmod -R u+w $out
    ${lib.optionalString hasFrontend ''
      mkdir -p $out/frontend
      cp -r ${frontend}/plugins/${name} $out/frontend/dist
    ''}
    python3 - "$kernelSrc" $out ${lib.escapeShellArg name} <<'EOF'
    import importlib.util, sys
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("plugins", sys.argv[1])
    plugins = importlib.util.module_from_spec(spec)
    # pydantic resolves the deferred annotations through sys.modules.
    sys.modules["plugins"] = plugins
    spec.loader.exec_module(plugins)
    p = plugins.load_plugin(Path(sys.argv[2]))
    if p.name != sys.argv[3]:
        sys.exit(f"manifest name {p.name!r} != directory {sys.argv[3]!r}")
    EOF
  ''
