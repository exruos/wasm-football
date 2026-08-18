param(
    [ValidateSet('All', 'JsNode', 'RustAxum', 'JsWasm', 'RustWasm', 'RustWasmComponents', 'KotlinSpring', 'KotlinNative')]
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

    Write-Host "Done publishing $RegistryImage`n"
}

function Invoke-SpinPublish {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,

        [Parameter(Mandatory = $true)]
        [string]$RegistryImage,

        [Parameter(Mandatory = $false)]
        [string]$Toml = 'spin.toml'
    )

    Write-Host "Publishing Spin app $RegistryImage from $WorkingDirectory"
    Invoke-CheckedCommand -WorkingDirectory $WorkingDirectory -Command {
        spin registry push --build $RegistryImage --from $Toml --insecure
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
            -RegistryImage "$RegistryHost/football-rust:wasm-components" `
            -Toml 'spin.components.toml'
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

    'RustWasmComponents' {
        Invoke-SpinPublish `
            -WorkingDirectory (Join-Path $repoRoot 'spin-rust') `
            -RegistryImage "$RegistryHost/football-rust:wasm-components" `
            -Toml 'spin.components.toml'
    }

    'KotlinSpring' {
        $LocalImage = 'football-kotlin:spring'
        $RegistryImage = "$RegistryHost/football-kotlin:spring"
        Write-Host "Tagging $LocalImage as $RegistryImage"
        docker tag $LocalImage $RegistryImage

        Write-Host "Pushing $RegistryImage"
        docker push $RegistryImage
    }

    'KotlinNative' {
        $LocalImage = 'football-kotlin:native'
        $RegistryImage = "$RegistryHost/football-kotlin:native"
        Write-Host "Tagging $LocalImage as $RegistryImage"
        docker tag $LocalImage $RegistryImage

        Write-Host "Pushing $RegistryImage"
        docker push $RegistryImage
    }
}