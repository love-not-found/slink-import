{
  description = "Import images from postimg.cc direct links into Slink";

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
            python = pkgs.python3.withPackages (ps: [ ps.requests ]);
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
            text = ''exec python ${./slink_import.py} "$@"'';
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
