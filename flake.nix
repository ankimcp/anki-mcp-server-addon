# Nix flake for anki-mcp-server — MCP server addon for Anki.
# Usage:
#   nix run .#           — launch Anki with the addon pre-installed
#   nix build .#addon    — build just the addon package
#
# NixOS / home-manager users can use the overlay:
#   pkgs.ankiAddons.anki-mcp-server
{
  description = "Anki addon that runs an MCP server, exposing collection operations to AI assistants";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    { self, nixpkgs }:
    let
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];

      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};

          pythonDeps = pkgs.python3.withPackages (
            ps: with ps; [
              mcp
              pydantic
              pydantic-settings
              starlette
              uvicorn
              anyio
              httpx
              websockets
            ]
          );

          addon = pkgs.anki-utils.buildAnkiAddon {
            pname = "anki-mcp-server";
            version = "0.12.0";
            src = ./anki_mcp_server;

            postFixup = ''
              local addonDir="$out/share/anki/addons/anki-mcp-server"

              # Replace bundled vendor directory with symlinks to Nix-provided packages
              rm -rf "$addonDir/vendor/shared"
              mkdir -p "$addonDir/vendor/shared"
              for entry in ${pythonDeps}/${pkgs.python3.sitePackages}/*; do
                ln -s "$entry" "$addonDir/vendor/shared/"
              done

              # Clean up build cache if present
              rm -rf "$addonDir/_cache"
            '';

            meta = {
              description = "MCP server addon for Anki — expose collection operations to AI assistants";
              homepage = "https://github.com/ankimcp/anki-mcp-server-addon";
              license = pkgs.lib.licenses.agpl3Plus;
            };
          };
        in
        {
          inherit addon;
          default = pkgs.anki.withAddons [ addon ];
        }
      );

      overlays.default = _final: prev: {
        ankiAddons = (prev.ankiAddons or { }) // {
          anki-mcp-server = self.packages.${prev.stdenv.hostPlatform.system}.addon;
        };
      };
    };
}
