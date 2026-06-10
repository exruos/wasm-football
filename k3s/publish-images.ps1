param(
    [ValidateSet('All', 'JsNode', 'RustAxum', 'JsWasm', 'RustWasm', 'RustWasmMonolith', 'KotlinSpring')]
    [Parameter(Mandatory = $true)]
    [string]$Target
    ,
    [Parameter(Mandatory = $false)]
    [string]$RegistryHost = 'localhost:5000'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptRoot

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,

        [Parameter(Mandatory = $true)]
        [scriptblock]$Command
    )

    Push-Location $WorkingDirectory
    try {
        & $Command
    }
    finally {
        Pop-Location
    }
}

function Invoke-DockerPublish {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,

        [Parameter(Mandatory = $true)]
        [string]$DockerfilePath,

        [Parameter(Mandatory = $true)]
        [string]$LocalImage,

        [Parameter(Mandatory = $true)]
        [string]$RegistryImage
    )

    Write-Host "Building $LocalImage from $WorkingDirectory"
    Invoke-CheckedCommand -WorkingDirectory $WorkingDirectory -Command {
        docker build -t $LocalImage -f $DockerfilePath .
    }

    Write-Host "Tagging $LocalImage as $RegistryImage"
    docker tag $LocalImage $RegistryImage

    Write-Host "Pushing $RegistryImage"
    docker push $RegistryImage
}

function Invoke-SpinPublish {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,

        [Parameter(Mandatory = $true)]
        [string]$RegistryImage
    )

    Write-Host "Publishing Spin app $RegistryImage from $WorkingDirectory"
    Invoke-CheckedCommand -WorkingDirectory $WorkingDirectory -Command {
        spin registry push --build $RegistryImage --insecure
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'docker is not available on PATH.'
}

if (-not (Get-Command spin -ErrorAction SilentlyContinue)) {
    throw 'spin is not available on PATH.'
}

switch ($Target) {
    'All' {
        Invoke-DockerPublish `
            -WorkingDirectory (Join-Path $repoRoot 'spin-js') `
            -DockerfilePath 'container/Dockerfile' `
            -LocalImage 'football-js:node' `
            -RegistryImage "$RegistryHost/football-js:node"

        Invoke-DockerPublish `
            -WorkingDirectory (Join-Path $repoRoot 'spin-rust') `
            -DockerfilePath 'container/Dockerfile' `
            -LocalImage 'football-rust:axum' `
            -RegistryImage "$RegistryHost/football-rust:axum"

        Invoke-SpinPublish `
            -WorkingDirectory (Join-Path $repoRoot 'spin-js') `
            -RegistryImage "$RegistryHost/football-js:wasm"

        Invoke-SpinPublish `
            -WorkingDirectory (Join-Path $repoRoot 'spin-rust') `
            -RegistryImage "$RegistryHost/football-rust:wasm"

        Invoke-SpinPublish `
            -WorkingDirectory (Join-Path $repoRoot 'spin-rust') `
            -RegistryImage "$RegistryHost/football-rust:wasm-mono"
    }

    'JsNode' {
        Invoke-DockerPublish `
            -WorkingDirectory (Join-Path $repoRoot 'spin-js') `
            -DockerfilePath 'container/Dockerfile' `
            -LocalImage 'football-js:node' `
            -RegistryImage "$RegistryHost/football-js:node"
    }

    'RustAxum' {
        Invoke-DockerPublish `
            -WorkingDirectory (Join-Path $repoRoot 'spin-rust') `
            -DockerfilePath 'container/Dockerfile' `
            -LocalImage 'football-rust:axum' `
            -RegistryImage "$RegistryHost/football-rust:axum"
    }

    'JsWasm' {
        Invoke-SpinPublish `
            -WorkingDirectory (Join-Path $repoRoot 'spin-js') `
            -RegistryImage "$RegistryHost/football-js:wasm"
    }

    'RustWasm' {
        Invoke-SpinPublish `
            -WorkingDirectory (Join-Path $repoRoot 'spin-rust') `
            -RegistryImage "$RegistryHost/football-rust:wasm"
    }

    'RustWasmMonolith' {
        $RegistryImage = "$RegistryHost/football-rust:wasm-mono"
        Write-Host "Publishing Spin monolith app $RegistryImage from spin-rust"
        Invoke-CheckedCommand -WorkingDirectory (Join-Path $repoRoot 'spin-rust') -Command {
            spin registry push --build --from spin.monolith.toml $RegistryImage --insecure
        }
    }

    'KotlinSpring' {
        $LocalImage = 'football-kotlin:spring'
        $RegistryImage = "$RegistryHost/football-kotlin:spring"
        Write-Host "Tagging $LocalImage as $RegistryImage"
        docker tag $LocalImage $RegistryImage

        Write-Host "Pushing $RegistryImage"
        docker push $RegistryImage
    }
}