{ pkgs, ... }:
{
  environment.systemPackages = with pkgs; [
    kubectl
    kubernetes-helm
  ];

  services.k3s = {
    enable = true;
    role = "server";
  };

  environment.etc."rancher/k3s/registries.yaml".text = ''
    mirrors:
      "localhost:5000":
        endpoint:
          - "http://localhost:5000"
  '';

  networking.firewall.allowedTCPPorts = [ 
    80   # HTTP
    443  # HTTPS

    6443 # k3s API server

    8080 # kube-state-metrics
    28282 # kepler
  ]; 
}
