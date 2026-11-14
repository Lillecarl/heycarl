{
  lib,
  python3Packages,
  postgresql,
  postgresqlTestHook,
  postgrest,
  todos,
}:

python3Packages.buildPythonApplication (finalAttrs: {
  pname = "heycarl";
  version = "0.1.0";
  pyproject = true;
  __structuredAttrs = true;

  src = lib.fileset.toSource {
    root = ../kernel;
    fileset = lib.fileset.unions [
      ../kernel/pyproject.toml
      (lib.fileset.fileFilter (f: f.hasExt "py" || f.hasExt "sql") ../kernel)
    ];
  };

  build-system = [ python3Packages.hatchling ];

  dependencies = with python3Packages; [
    anyio
    fastapi
    httpx
    psycopg
    pydantic
    pyjwt
    structlog
    uvicorn
  ];

  nativeCheckInputs = [
    python3Packages.pytestCheckHook
    postgresql
    postgresqlTestHook
    postgrest
  ];

  # The integration tests need the roles a superuser creates in production.
  postgresqlTestSetupPost = ''
    PGUSER=postgres psql -v ON_ERROR_STOP=1 -v database=heycarl -f heycarl/schema/roles.sql postgres
  '';
  env.HEYCARL_TEST_PLUGINS = builtins.toJSON [ "${todos}" ];

  pythonImportsCheck = [ "heycarl" ];

  # For the NixOS module, which runs it as the postgres superuser.
  passthru.rolesSql = "${python3Packages.python.sitePackages}/heycarl/schema/roles.sql";

  meta = {
    description = "Kernel of a personal application framework";
    # No licence chosen yet; the repository is private.
    mainProgram = "heycarl";
  };
})
