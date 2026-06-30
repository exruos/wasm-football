{ ... }:
{
  services.k3s = {
    enable = true;
    role = "server";
    extraFlags = [
      "--tls-san=hetzner-metal"
    ];

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
