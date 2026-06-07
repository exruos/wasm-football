{
    inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    inputs.disko.url = "github:nix-community/disko";
    inputs.disko.inputs.nixpkgs.follows = "nixpkgs";
    inputs.nixos-facter-modules.url = "github:nix-community/nixos-facter-modules";

    outputs = { nixpkgs, disko, nixos-facter-modules,  ... }:
    {
         # nixos-anywhere --flake .#bench --generate-hardware-config nixos-facter facter.json <hostname>
        nixosConfigurations.bench = nixpkgs.lib.nixosSystem{
            system = "x86_64-linux";
            modules = [
                disko.nixosModules.disko
                ./modules/k3s.nix
                ./modules/monitoring.nix
                ./modules/registry.nix
                ./configuration.nix
                nixos-facter-modules.nixosModules.facter
                {
                    config.facter.reportPath = 
                        if builtins.pathExists ./facter.json then
                            ./facter.json
                        else
                            throw "Have you forgotten to run nixos-anywhere with `--generate-hardware-config nixos-facter ./facter.json`?";
                }
            ];
            specialArgs = { inherit disko; };
        };
        
        nixosConfigurations.vm = nixpkgs.lib.nixosSystem {
            system = "x86_64-linux";
            modules = [
                disko.nixosModules.disko
                ./modules/vm.nix
                ./modules/k3s.nix
                ./modules/monitoring.nix
                ./modules/registry.nix
                ./configuration.nix
                nixos-facter-modules.nixosModules.facter
                {
                    config.facter.reportPath = 
                        if builtins.pathExists ./facter.json then
                            ./facter.json
                        else
                            throw "Have you forgotten to run nixos-anywhere with `--generate-hardware-config nixos-facter ./facter.json`?";
                }
            ];
            specialArgs = { inherit disko; };   
        };
    };
}