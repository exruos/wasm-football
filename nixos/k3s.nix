{ pkgs, ... }:
let
  containerd-shim-spin = pkgs.callPackage ./pkgs/containerd-shim-spin.nix { };
in
{
  services.k3s = {
    enable = true;
    role = "server";
    extraFlags = [
      "--tls-san=hetzner-metal"
      "--node-label=spin=true"
    ];

    containerdConfigTemplate = ''
      {{ template "base" . }}
      
      [plugins."io.containerd.cri.v1.runtime".containerd.runtimes.spin]
        runtime_type = "io.containerd.spin.v2"

      [plugins."io.containerd.cri.v1.runtime".containerd.runtimes.spin.options]
        SystemdCgroup = true
    '';

    manifests.traefik-gateway-config.content = {
      apiVersion = "helm.cattle.io/v1";
      kind = "HelmChartConfig";
      metadata = {
        name = "traefik";
        namespace = "kube-system";
      };
      spec = {
        valuesContent = ''
          providers:
            kubernetesGateway:
              enabled: true
        '';
      };
    };
  };

  systemd.services.k3s.path = [
    containerd-shim-spin
  ];

  environment.etc."rancher/k3s/registries.yaml".text = ''
    mirrors:
      "localhost:5000":
        endpoint:
          - "http://localhost:5000"
  '';

  networking.firewall.allowedTCPPorts = [
    80 # HTTP
    443 # HTTPS

    6443 # k3s API server
  ];
}
