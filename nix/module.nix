# Hey Carl on one NixOS host: the kernel and PostgREST on unix sockets, and
# the roles in the host's PostgreSQL.
#
# The kernel signs people in itself, over OIDC, and checks every request,
# so the `heycarl` group on its socket grants a way to talk HTTP to it and
# nothing more. PostgREST runs as its own user: only it logs in as
# heycarl_authenticator, and only the kernel reaches its socket.
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
  # PostgREST's own directory: the kernel reaches it through the group, and
  # Caddy, in the heycarl group, does not.
  postgrestDir = "heycarl-postgrest";
  postgrestSocket = "/run/${postgrestDir}/postgrest.sock";
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
  googleOn = cfg.google.clientId != null;
  credentials = "/run/credentials/heycarl.service";
  # The kernel only, not migrate: only the running kernel reads them.
  googleEnvironment = lib.optionalAttrs googleOn (
    {
      HEYCARL_GOOGLE_CLIENT_ID = cfg.google.clientId;
      HEYCARL_GOOGLE_CLIENT_SECRET_FILE = "${credentials}/google-client-secret";
      HEYCARL_GOOGLE_TOKEN_KEY_FILE = "${credentials}/google-token-key";
    }
    // lib.optionalAttrs (cfg.google.testEndpoints != { }) {
      HEYCARL_GOOGLE_ENDPOINTS = builtins.toJSON cfg.google.testEndpoints;
    }
  );
  host = lib.head (lib.splitString "/" (lib.removePrefix "https://" cfg.publicUrl));

  sandbox = {
    NoNewPrivileges = true;
    PrivateTmp = true;
    PrivateDevices = true;
    ProtectSystem = "strict";
    ProtectHome = true;
    ProtectKernelTunables = true;
    ProtectKernelModules = true;
    ProtectKernelLogs = true;
    ProtectControlGroups = true;
    ProtectClock = true;
    ProtectHostname = true;
    ProtectProc = "invisible";
    ProcSubset = "pid";
    RestrictNamespaces = true;
    RestrictRealtime = true;
    RestrictSUIDSGID = true;
    LockPersonality = true;
    RemoveIPC = true;
    CapabilityBoundingSet = "";
    AmbientCapabilities = "";
    SystemCallArchitectures = "native";
    SystemCallFilter = [
      "@system-service"
      "~@privileged"
    ];
    SystemCallErrorNumber = "EPERM";
  };
  kernelSandbox = sandbox // {
    User = "heycarl";
    Group = "heycarl";
    ReadWritePaths = [
      runDir
      # The kernel makes its VAPID key here on first start.
      stateDir
    ];
    # The provider, push services and DNS; netlink for getaddrinfo.
    RestrictAddressFamilies = [
      "AF_UNIX"
      "AF_INET"
      "AF_INET6"
      "AF_NETLINK"
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

    google = {
      clientId = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        example = "1234-abc.apps.googleusercontent.com";
        description = ''
          A Google OAuth client ("Web application") for the kernel's Google
          accounts. A connect asks for `openid email` and the scopes of every
          plugin that names some, such as calendar.readonly. Enable each
          plugin's API in the client's Google Cloud project. Null turns the
          feature off. Register the redirect URI
          `<publicUrl>/api/google/callback` at the client.
        '';
      };
      clientSecretFile = lib.mkOption {
        type = lib.types.nullOr lib.types.path;
        default = null;
        example = "/run/agenix/heycarl-google-client-secret";
        description = ''
          The client's secret. Read through LoadCredential; give a path as a
          string, so it never lands in the Nix store.
        '';
      };
      tokenKeyFile = lib.mkOption {
        type = lib.types.nullOr lib.types.path;
        default = null;
        example = "/run/agenix/heycarl-google-token-key";
        description = ''
          At least 32 random bytes, such as `openssl rand -base64 48`. The
          kernel seals each refresh token with a key made from it, so a
          database dump alone holds no usable token. Losing it means
          everyone connects again. A path as a string, never in the store.
        '';
      };
      testEndpoints = lib.mkOption {
        type = lib.types.attrsOf lib.types.str;
        default = { };
        internal = true;
        description = ''
          Google's endpoints replaced, by name (authorize, token, revoke,
          userinfo, or a Google API's name such as calendar), for the VM
          test's fake Google only.
        '';
      };
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
      {
        assertion =
          (cfg.google.clientId != null)
          == (cfg.google.clientSecretFile != null && cfg.google.tokenKeyFile != null);
        message = "services.heycarl.google needs clientId, clientSecretFile and tokenKeyFile together.";
      }
    ];

    services.caddy = lib.mkIf cfg.caddy.enable {
      enable = true;
      # /register and /token parse a body before any check; the largest real
      # body is a plugin's data write.
      virtualHosts.${host}.extraConfig = ''
        request_body {
          max_size 1MB
        }
        reverse_proxy unix/${kernelSocket}
      '';
    };
    systemd.services.caddy.serviceConfig.SupplementaryGroups = lib.mkIf cfg.caddy.enable [
      "heycarl"
    ];

    users.users.heycarl = {
      isSystemUser = true;
      group = "heycarl";
      extraGroups = [ "heycarl-postgrest" ];
    };
    users.groups.heycarl = { };
    # The kernel runs Python with plugins in it; PostgREST holds the only
    # login as heycarl_authenticator, which can become any user.
    users.users.heycarl-postgrest = {
      isSystemUser = true;
      group = "heycarl-postgrest";
    };
    users.groups.heycarl-postgrest = { };

    systemd.tmpfiles.rules = [
      "d ${runDir} 0710 heycarl heycarl -"
      "d ${stateDir} 0700 heycarl heycarl -"
    ];

    services.postgresql = {
      enable = true;
      # Peer authentication, one OS user per login: no password exists to
      # leak.
      identMap = ''
        heycarl heycarl heycarl_owner
        heycarl heycarl-postgrest heycarl_authenticator
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
      environment = environment // googleEnvironment;
      serviceConfig = kernelSandbox // {
        ExecStartPre = "${pkgs.coreutils}/bin/rm -f ${kernelSocket}";
        ExecStart = "${lib.getExe cfg.package} serve --socket ${kernelSocket}";
        LoadCredential = [
          "oidc-client-secret:${cfg.oidc.clientSecretFile}"
        ]
        ++ lib.optionals googleOn [
          "google-client-secret:${cfg.google.clientSecretFile}"
          "google-token-key:${cfg.google.tokenKeyFile}"
        ];
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
      serviceConfig = kernelSandbox // {
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
        PGRST_JWT_SECRET = "@/run/credentials/heycarl-postgrest.service/jwt-secret";
        PGRST_SERVER_UNIX_SOCKET = postgrestSocket;
        PGRST_SERVER_UNIX_SOCKET_MODE = "660";
        PGRST_DB_CHANNEL_ENABLED = "true";
        PGRST_OPENAPI_MODE = "disabled";
      };
      serviceConfig = sandbox // {
        User = "heycarl-postgrest";
        Group = "heycarl-postgrest";
        RuntimeDirectory = postgrestDir;
        RuntimeDirectoryMode = "0750";
        LoadCredential = [ "jwt-secret:${jwtSecret}" ];
        RestrictAddressFamilies = [ "AF_UNIX" ];
        ExecStart = lib.getExe pkgs.postgrest;
        Restart = "on-failure";
        RestartSec = 2;
      };
    };
  };
}
