{ pkgs, ... }:
{

  services.victoriametrics = {
    enable = true;
    listenAddress = ":8428";
    retentionPeriod = "1y";
  };

  services.grafana = {
    enable = true;
    settings = {
      server = {
        http_addr = "127.0.0.1";
        http_port = 3000;
        enable_gzip = true;
      };
      analytics.reporting_enabled = false;
      security = {
        admin_user = "admin";
        admin_password = "admin";
        secret_key = "dtseaBBX7v6ICbKLYVtmSitF";
      };
    };

    provision = {
      enable = true;
      dashboards.settings.providers = [
        {
          name = "my dashboards";
          options.path = "/etc/grafana-dashboards";
        }
      ];

      datasources.settings.datasources = [
        {
          name = "VictoriaMetrics";
          type = "victoriametrics-metrics-datasource";
          access = "proxy";
          url = "http://127.0.0.1:8428";
          isDefault = true;
        }
      ];
    };

    declarativePlugins = with pkgs.grafanaPlugins; [
      victoriametrics-metrics-datasource
    ];
  };

  networking.useDHCP = true;
  networking.firewall.allowedTCPPorts = [ 8428 ];
}
