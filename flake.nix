{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.disko.url = "github:nix-community/disko";
  inputs.disko.inputs.nixpkgs.follows = "nixpkgs";
  inputs.nixos-facter-modules.url = "github:nix-community/nixos-facter-modules";

  outputs =
    {
      nixpkgs,
      disko,
      nixos-facter-modules,
      ...
    }:
    {
      # nixos-anywhere --flake .#bench --generate-hardware-config nixos-facter facter-bench.json <hostname>
      nixosConfigurations.bench = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        specialArgs = {
          hostname = "nixos-bench";
        };
        modules = [
          disko.nixosModules.disko
          ./nixos/k3s.nix
          ./nixos/registry.nix
          ./nixos/configuration.nix
          nixos-facter-modules.nixosModules.facter
          {
            config.facter.reportPath =
              if builtins.pathExists ./facter-bench.json then
                ./facter-bench.json
              else
                throw "Have you forgotten to run nixos-anywhere with `--generate-hardware-config nixos-facter ./facter-bench.json`?";
          }
        ];
        specialArgs = { inherit disko; };
      };

      # nixos-anywhere --flake .#vm --generate-hardware-config nixos-facter facter-vm.json <hostname>
      nixosConfigurations.vm = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        specialArgs = {
          hostname = "nixos-vm";
        };
        modules = [
          disko.nixosModules.disko
          ./nixos/vm.nix
          ./nixos/configuration.nix
          nixos-facter-modules.nixosModules.facter
          {
            config.facter.reportPath =
              if builtins.pathExists ./facter-vm.json then
                ./facter-vm.json
              else
                throw "Have you forgotten to run nixos-anywhere with `--generate-hardware-config nixos-facter ./facter-vm.json`?";
          }
        ];
        specialArgs = { inherit disko; };
      };
    };
}
