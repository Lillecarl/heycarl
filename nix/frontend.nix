{
  lib,
  stdenv,
  nodejs,
  pnpm_10,
  pnpmConfigHook,
  fetchPnpmDeps,
  librsvg,
  themes,
}:
# The whole pnpm workspace in one build: the shell and every plugin frontend.
# One lock, so one dependency fetch.
let
  plugins = lib.filter (name: builtins.pathExists (../plugins + "/${name}/frontend/package.json")) (
    lib.attrNames (lib.filterAttrs (_: type: type == "directory") (builtins.readDir ../plugins))
  );
  pluginSources = name: [
    (../plugins + "/${name}/frontend/package.json")
    (../plugins + "/${name}/frontend/tsconfig.json")
    (../plugins + "/${name}/frontend/vite.config.ts")
    (../plugins + "/${name}/frontend/src")
  ];
in
stdenv.mkDerivation (finalAttrs: {
  pname = "heycarl-frontend";
  version = "0.1.0";
  strictDeps = true;
  __structuredAttrs = true;

  # Named files only: node_modules, dist, build and .svelte-kit in the
  # working copy would otherwise change the hash.
  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions (
      [
        ../package.json
        ../pnpm-lock.yaml
        ../pnpm-workspace.yaml
        ../sdk/package.json
        ../sdk/src
        ../frontend/package.json
        ../frontend/tsconfig.json
        ../frontend/vite.config.ts
        ../frontend/src
        ../frontend/static
      ]
      ++ lib.concatMap pluginSources plugins
    );
  };

  nativeBuildInputs = [
    nodejs
    pnpm_10
    pnpmConfigHook
    librsvg
  ];

  pnpmDeps = fetchPnpmDeps {
    inherit (finalAttrs) pname version src;
    pnpm = pnpm_10;
    fetcherVersion = 3;
    hash = "sha256-eVN+6B71O9Xcgiqbie4YR2eDblIdQtV1hocANm6vxV8=";
  };

  buildPhase = ''
    runHook preBuild
    # Android needs PNG icons to install the app; the SVG is the source.
    for size in 192 512; do
      rsvg-convert -w $size -h $size frontend/static/icons/icon.svg \
        -o frontend/static/icons/icon-$size.png
    done
    # base16 schemes for the picker, and the defaults the root layout
    # imports, so they are inlined with the rest of the CSS (nix/themes.nix).
    install -Dm644 ${themes}/themes.json frontend/static/themes/themes.json
    install -Dm644 ${themes}/defaults.css frontend/src/themes/defaults.css
    pnpm --filter @heycarl/shell exec svelte-kit sync
    pnpm --recursive build
    runHook postBuild
  '';

  doCheck = true;
  checkPhase = ''
    runHook preCheck
    pnpm --recursive check
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p $out/plugins
    cp -r frontend/build $out/shell
    ${lib.concatMapStrings (name: ''
      cp -r plugins/${name}/frontend/dist $out/plugins/${name}
    '') plugins}
    runHook postInstall
  '';

  passthru = { inherit plugins; };

  meta.description = "Shell and plugin frontends of Hey Carl";
})
