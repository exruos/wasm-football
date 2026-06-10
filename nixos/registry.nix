{ ... }:
{
  virtualisation.containers.enable = true;
  virtualisation.podman.enable = true;
  virtualisation.oci-containers.backend = "podman";

  systemd.tmpfiles.rules = [
    "d /var/lib/zot 0755 root root -"
  ];

  virtualisation.oci-containers.containers.zot = {
    image = "ghcr.io/project-zot/zot-linux-amd64:v2.1.17";
    ports = [ "5000:5000" ];
    volumes = [ "/var/lib/zot:/var/lib/registry" ];
  };

  networking.firewall.allowedTCPPorts = [ 5000 ];
}
