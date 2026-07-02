{
  lib,
  stdenvNoCC,
  fetchurl,
}:

stdenvNoCC.mkDerivation rec {
  pname = "containerd-shim-spin";
  version = "0.25.1";

  src = fetchurl {
    url = "https://github.com/spinframework/containerd-shim-spin/releases/download/v${version}/containerd-shim-spin-v2-linux-x86_64.tar.gz";
    hash = "sha256-F1X76y3sfQJvr4w3Ax2KAll19eGUt4LaEBiCBqOG1uQ=";
  };

  sourceRoot = ".";

  installPhase = ''
    runHook preInstall

    mkdir -p $out/bin
    install -Dm755 containerd-shim-spin-v2 \
      $out/bin/containerd-shim-spin-v2

    runHook postInstall
  '';

  meta = with lib; {
    description = "Spin containerd shim";
    platforms = platforms.linux;
  };
}
