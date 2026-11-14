# Hey Carl in a vivarium guest, with demo data, to browse from the host.
#
#     vivarium up --file . dev --out ./dev
#     open http://127.0.0.1:8180         # demo@example.com, password "password"
#     vivarium down --out ./dev
#
# A real dex signs people in, as in production. The site and dex sit on
# fixed ports on the host's 127.0.0.1, so the browser and the kernel agree
# on every URL: inside the guest, 127.0.0.1 is the guest itself. Not another
# loopback address: the MCP SDK accepts a plain-HTTP issuer only on
# localhost and 127.0.0.1.
{
  curl,
  gnugrep,
  coreutils,
  writers,
  writeShellApplication,
  writeText,
  vivarium,
}:

let
  address = "127.0.0.1";
  port = 8180;
  publicUrl = "http://${address}:${toString port}";
  issuer = "http://${address}:5556/dex";
in
vivarium.mkTest {
  name = "heycarl-dev";

  nodes.hey =
    { config, ... }:
    {
      imports = [ ./module.nix ];
      vivarium.memory = "1536M";
      vivarium.forward = [
        { ports = [ config.vivarium.sshPort ]; }
        {
          inherit address;
          ports = [
            port
            5556
          ];
        }
      ];
      services.heycarl = {
        enable = true;
        inherit publicUrl;
        oidc = {
          inherit issuer;
          clientSecretFile = writeText "client-secret" "dev-secret";
        };
      };
      services.dex = {
        enable = true;
        settings = {
          inherit issuer;
          storage.type = "memory";
          web.http = "0.0.0.0:5556";
          enablePasswordDB = true;
          oauth2.skipApprovalScreen = true;
          staticPasswords = [
            {
              email = "demo@example.com";
              # "password", from dex's example configuration.
              hash = "$2a$10$2b2cU8CPhOTaGrs1HRQuAueS7JTT5ZHsHSzYiFPm1leZck7Mc8T4W";
              username = "demo";
              userID = "demo";
            }
          ];
          staticClients = [
            {
              id = "hey";
              name = "Hey Carl";
              secret = "dev-secret";
              redirectURIs = [ "${publicUrl}/auth/callback" ];
            }
          ];
        };
      };
      services.caddy = {
        enable = true;
        virtualHosts."http://:${toString port}".extraConfig =
          "reverse_proxy unix/${config.services.heycarl.kernelSocket}";
      };
      systemd.services.caddy.serviceConfig.SupplementaryGroups = [ "heycarl" ];
      environment.variables.HEYCARL_PUBLIC_URL = publicUrl;
      environment.systemPackages = [
        curl
        (writeShellApplication {
          name = "heycarl-login";
          runtimeInputs = [
            curl
            gnugrep
            coreutils
          ];
          text = builtins.readFile ./vm-login.sh;
        })
        (writers.writePython3Bin "heycarl-seed" {
          flakeIgnore = [ "E501" ];
        } (builtins.readFile ./dev-seed.py))
      ];
    };

  phases.seed = {
    script = ./dev-seed-phase.py;
    after = [ "boot" ];
  };
}
