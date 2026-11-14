# Hey Carl in a vivarium guest, with demo data, to browse from the host.
#
#     vivarium up --file . dev --out ./dev     # or: nix run --file . dev.up -- --out ./dev
#     jq . dev/access.json                     # the address of port 8080
#     vivarium down --out ./dev
#
# No gate and no sign-in: a Caddy on port 8080 signs every request in as
# `demo`, the way the gate signs in a real user.
{
  curl,
  writers,
  vivarium,
}:

vivarium.mkTest {
  name = "heycarl-dev";

  nodes.hey =
    { config, ... }:
    {
      imports = [ ./module.nix ];
      vivarium.memory = "1536M";
      vivarium.forward = [
        {
          ports = [
            config.vivarium.sshPort
            { guest = 8080; }
          ];
        }
      ];
      services.heycarl = {
        enable = true;
        publicUrl = "http://localhost:8080";
      };
      services.caddy = {
        enable = true;
        virtualHosts."http://:8080".extraConfig = ''
          reverse_proxy unix/${config.services.heycarl.kernelSocket} {
            header_up X-Forwarded-User demo
            header_up X-Forwarded-Email demo@example.com
            header_up X-Forwarded-Preferred-Username demo
          }
        '';
      };
      systemd.services.caddy.serviceConfig.SupplementaryGroups = [ "heycarl" ];
      environment.systemPackages = [
        curl
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
