{
  description = "Web UI to import images from direct links into Slink";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems =
        f:
        nixpkgs.lib.genAttrs systems (
          system:
          let
            pkgs = nixpkgs.legacyPackages.${system};
            python = pkgs.python3.withPackages (ps: [
              ps.fastapi
              ps.uvicorn
              ps.requests
              ps.itsdangerous
            ]);
          in
          f { inherit pkgs python; }
        );
    in
    {
      devShells = forAllSystems (
        { pkgs, python }:
        {
          default = pkgs.mkShell { packages = [ python ]; };
        }
      );

      packages = forAllSystems (
        { pkgs, python }:
        {
          default = pkgs.writeShellApplication {
            name = "slink-import";
            runtimeInputs = [ python ];
            text = ''
              export PYTHONPATH=${./.}
              exec python -m app "$@"
            '';
          };
        }
      );

      apps = forAllSystems (
        { pkgs, ... }:
        {
          default = {
            type = "app";
            program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.default}/bin/slink-import";
          };
        }
      );
    };
}
