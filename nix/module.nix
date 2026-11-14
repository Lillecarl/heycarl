# Hey Carl on one NixOS host: the kernel and PostgREST on unix sockets, and
# the roles in the host's PostgreSQL.
#
# The kernel signs people in itself, over OIDC, and checks every request,
# so the `heycarl` group on its socket grants a way to talk HTTP to it and
# nothing more. PostgREST's socket and the token key are the heycarl
# user's alone.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.heycarl;
  # Built from the host's pkgs; default.nix reads its own lock only when no
  # pkgs is given.
  heycarl = import ../. { inherit pkgs; };
  runDir = "/run/heycarl";
  stateDir = "/var/lib/heycarl";
  jwtSecret = "${stateDir}/jwt-secret";
  kernelSocket = "${runDir}/kernel.sock";
  postgrestSocket = "${runDir}/postgrest.sock";
  pgSocket = "/run/postgresql";
  pluginsFile = pkgs.writeText "heycarl-plugins.json" (builtins.toJSON (map toString cfg.plugins));
  schemas = [ "heycarl" ] ++ map (p: "heycarl_plugin_${p.pluginName}") cfg.plugins;

  environment = {
    HEYCARL_DATABASE = "host=${pgSocket} dbname=${cfg.database} user=heycarl_owner";
    HEYCARL_POSTGREST_SOCKET = postgrestSocket;
    HEYCARL_JWT_SECRET_FILE = jwtSecret;
    HEYCARL_PLUGINS_FILE = toString pluginsFile;
    HEYCARL_STATIC_DIR = "${cfg.frontend}/shell";
    HEYCARL_VAPID_KEY_FILE = "${stateDir}/vapid.pem";
    HEYCARL_PUBLIC_URL = cfg.publicUrl;
    HEYCARL_OIDC_ISSUER = cfg.oidc.issuer;
    HEYCARL_OIDC_CLIENT_ID = cfg.oidc.clientId;
    # LoadCredential's copy: readable by this unit only, whoever owns the
    # original.
    HEYCARL_OIDC_CLIENT_SECRET_FILE = "/run/credentials/heycarl.service/oidc-client-secret";
    HEYCARL_ALLOWED_EMAILS = lib.concatStringsSep "," cfg.allowedEmails;
  };
  host = lib.head (lib.splitString "/" (lib.removePrefix "https://" cfg.publicUrl));

  hardening = {
    User = "heycarl";
    Group = "heycarl";
    NoNewPrivileges = true;
    PrivateTmp = true;
    ProtectSystem = "strict";
    ProtectHome = true;
    ReadWritePaths = [
      runDir
      # The kernel makes its VAPID key here on first start.
      stateDir
    ];
  };
in
{
  options.services.heycarl = {
    enable = lib.mkEnableOption "Hey Carl";

    package = lib.mkOption {
      type = lib.types.package;
      default = heycarl.kernel;
      defaultText = lib.literalExpression "heycarl.kernel";
      description = "The kernel.";
    };

    frontend = lib.mkOption {
      type = lib.types.package;
      default = heycarl.frontend;
      defaultText = lib.literalExpression "heycarl.frontend";
      description = "The shell and plugin frontends.";
    };

    plugins = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = lib.attrValues heycarl.plugins;
      defaultText = lib.literalExpression "lib.attrValues heycarl.plugins";
      description = "Plugin directories, as `plugins.<name>` in default.nix builds them.";
    };

    publicUrl = lib.mkOption {
      type = lib.types.str;
      example = "https://hey.example.com";
      description = ''
        Where people reach the app. Push services get it as the VAPID
        contact, so it must be an https URL that names the sender.
      '';
    };

    oidc = {
      issuer = lib.mkOption {
        type = lib.types.str;
        example = "https://dex.example.com";
        description = "The OpenID provider people sign in with.";
      };
      clientId = lib.mkOption {
        type = lib.types.str;
        default = "hey";
        description = ''
          This app's client at the provider. Its redirect URI is
          `<publicUrl>/auth/callback`.
        '';
      };
      clientSecretFile = lib.mkOption {
        type = lib.types.path;
        example = "/run/agenix/dex-client-hey";
        description = "The client secret. Read through LoadCredential, never copied to the store.";
      };
    };

    allowedEmails = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [ "someone@example.com" ];
      description = ''
        Who may sign in. Empty lets in everyone the provider signs in, which
        is right only for a provider that knows nobody else.
      '';
    };

    caddy.enable = lib.mkEnableOption ''
      a Caddy virtual host for the publicUrl's host, proxying to the kernel
      socket. Caddy gets the certificate'';

    database = lib.mkOption {
      type = lib.types.str;
      default = "heycarl";
      description = "Database name in the host's PostgreSQL.";
    };

    kernelSocket = lib.mkOption {
      type = lib.types.str;
      readOnly = true;
      default = kernelSocket;
      description = "Where the kernel listens, for a reverse proxy.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = lib.hasPrefix "https://" cfg.publicUrl || !cfg.caddy.enable;
        message = "services.heycarl.caddy needs an https publicUrl.";
      }
    ];

    services.caddy = lib.mkIf cfg.caddy.enable {
      enable = true;
      virtualHosts.${host}.extraConfig = "reverse_proxy unix/${kernelSocket}";
    };
    systemd.services.caddy.serviceConfig.SupplementaryGroups = lib.mkIf cfg.caddy.enable [
      "heycarl"
    ];

    users.users.heycarl = {
      isSystemUser = true;
      group = "heycarl";
    };
    users.groups.heycarl = { };

    systemd.tmpfiles.rules = [
      "d ${runDir} 0710 heycarl heycarl -"
      "d ${stateDir} 0700 heycarl heycarl -"
    ];

    services.postgresql = {
      enable = true;
      # Peer authentication as the heycarl OS user, for both logins: no
      # password exists to leak.
      identMap = ''
        heycarl heycarl heycarl_owner
        heycarl heycarl heycarl_authenticator
      '';
      # Before the host's own lines: pg_hba takes the first match.
      authentication = lib.mkBefore ''
        local ${cfg.database} heycarl_owner,heycarl_authenticator peer map=heycarl
      '';
    };

    systemd.services.heycarl-setup = {
      description = "Hey Carl: database roles and the PostgREST token key";
      wantedBy = [ "multi-user.target" ];
      after = [ "postgresql.service" ];
      requires = [ "postgresql.service" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        # Not StateDirectory=: this unit runs as root, and systemd would chown
        # the directory and everything in it to root on each start.
      };
      path = [
        config.services.postgresql.package
        pkgs.openssl
        pkgs.util-linux
      ];
      script = ''
        if [ ! -s ${jwtSecret} ]; then
          (umask 077; openssl rand -base64 48 | tr -d '\n' > ${jwtSecret})
        fi
        chown -R heycarl:heycarl ${stateDir}
        runuser -u postgres -- psql -v ON_ERROR_STOP=1 -v database=${cfg.database} \
          -f ${cfg.package}/${cfg.package.passthru.rolesSql} postgres
      '';
    };

    systemd.services.heycarl = {
      description = "Hey Carl kernel";
      wantedBy = [ "multi-user.target" ];
      after = [ "heycarl-migrate.service" ];
      requires = [ "heycarl-migrate.service" ];
      inherit environment;
      serviceConfig = hardening // {
        ExecStartPre = "${pkgs.coreutils}/bin/rm -f ${kernelSocket}";
        ExecStart = "${lib.getExe cfg.package} serve --socket ${kernelSocket}";
        LoadCredential = [ "oidc-client-secret:${cfg.oidc.clientSecretFile}" ];
        # uvicorn creates the socket: 0660, so the proxy's group can open it.
        UMask = "0007";
        Restart = "on-failure";
        RestartSec = 2;
      };
    };

    # Its own unit, so PostgREST starts after the schemas exist: started
    # beside them, it caught the reload NOTIFY mid-retry and answered 503.
    # The kernel migrates at start too, which is then a no-op.
    systemd.services.heycarl-migrate = {
      description = "Hey Carl database migrations";
      after = [ "heycarl-setup.service" ];
      requires = [ "heycarl-setup.service" ];
      inherit environment;
      serviceConfig = hardening // {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = "${lib.getExe cfg.package} migrate";
      };
    };

    systemd.services.heycarl-postgrest = {
      description = "Hey Carl PostgREST";
      wantedBy = [ "multi-user.target" ];
      after = [ "heycarl-migrate.service" ];
      requires = [ "heycarl-migrate.service" ];
      environment = {
        PGRST_DB_URI = "postgresql:///${cfg.database}?host=${pgSocket}&user=heycarl_authenticator";
        PGRST_DB_SCHEMAS = lib.concatStringsSep "," schemas;
        PGRST_JWT_SECRET = "@${jwtSecret}";
        PGRST_SERVER_UNIX_SOCKET = postgrestSocket;
        PGRST_SERVER_UNIX_SOCKET_MODE = "600";
        PGRST_DB_CHANNEL_ENABLED = "true";
        PGRST_OPENAPI_MODE = "disabled";
      };
      serviceConfig = hardening // {
        ExecStartPre = "${pkgs.coreutils}/bin/rm -f ${postgrestSocket}";
        ExecStart = lib.getExe pkgs.postgrest;
        Restart = "on-failure";
        RestartSec = 2;
      };
    };
  };
}
