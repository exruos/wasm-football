{
  lib,
  pkgs,
  hostname,
  ...
}:
{
  imports = [
    ./disk-config.nix
  ];
  boot.loader.grub = {
    # no need to set devices, disko will add all devices that have a EF02 partition to the list already
    # devices = [ ];
    efiSupport = true;
    efiInstallAsRemovable = true;
  };
  boot.kernelPackages = pkgs.linuxPackages_latest;

  services.openssh.enable = true;

  time.timeZone = "Europe/Berlin";
  console.keyMap = "de";

  environment.systemPackages = map lib.lowPrio [
    pkgs.curl
    pkgs.gitMinimal
  ];

  networking.hostName = hostname;

  users.users.root.openssh.authorizedKeys.keys = [
    # change this to your ssh key
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAWYJS0TZ/pFvaAaYtjdHRDU9u6zgibfEB0lMcKg8bUD"
  ];

  system.stateVersion = "26.05";
}
