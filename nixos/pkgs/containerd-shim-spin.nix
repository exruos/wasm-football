{
  lib,
  stdenvNoCC,
  fetchzip,
}:

stdenvNoCC.mkDerivation rec {
  pname = "containerd-shim-spin";
  version = "0.25.1";

  src = fetchzip {
    url = "https://github.com/spinframework/containerd-shim-spin/releases/download/v${version}/containerd-shim-spin-v2-linux-x86_64.tar.gz";
    hash = "sha256-1755fbeb2dec7d026faf8c37031d8a025975f5e194b782da10188206a386d6e4";
    stripRoot = false;
  };

  dontBuild = true;

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
