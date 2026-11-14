/**
  A plugin directory as a store path, with its manifest checked against the
  kernel's model at build time.

  # Inputs

  `name`
  : The plugin's directory name under `plugins/`
*/
{
  lib,
  runCommand,
  python3,
  name,
}:
let
  root = ../plugins + "/${name}";
  src = lib.fileset.toSource {
    inherit root;
    fileset = root;
  };
in
runCommand "heycarl-plugin-${name}"
  {
    nativeBuildInputs = [ (python3.withPackages (p: [ p.pydantic ])) ];
    kernelSrc = ../kernel/heycarl/plugins.py;
  }
  ''
    python3 - "$kernelSrc" ${src} ${lib.escapeShellArg name} <<'EOF'
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
    cp -r ${src} $out
  ''
