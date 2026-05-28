$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptRoot
$registryConfig = Join-Path $scriptRoot 'registries.yaml'
$namespaceManifest = Join-Path $repoRoot 'kustomize\base\namespace.yaml'

function Assert-Command {
	param(
		[Parameter(Mandatory = $true)]
		[string]$Name
	)

	if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
		throw "$Name is not available on PATH."
	}
}

Assert-Command 'k3d'
Assert-Command 'kubectl'
Assert-Command 'helm'

Write-Host 'Creating k3d cluster wasm-cluster'
k3d cluster create wasm-cluster --image ghcr.io/spinframework/containerd-shim-spin/k3d:v0.24.0 --port '8081:80@loadbalancer' --registry-config $registryConfig

Write-Host 'Connecting registry container to k3d-wasm-cluster network'
try {
	docker network connect k3d-wasm-cluster registry
}
catch {
	if ($_.Exception.Message -notmatch 'endpoint with name registry already exists in network k3d-wasm-cluster') {
		throw
	}
}

Write-Host 'Installing cert-manager'
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.20.2/cert-manager.yaml

kubectl wait --for=condition=available --timeout=300s deployment/cert-manager-webhook -n cert-manager

Write-Host 'Installing runtime-class-manager'
kubectl apply -f https://github.com/spinframework/spin-operator/releases/download/v0.6.1/spin-operator.runtime-class.yaml

Write-Host 'Installing Spin operator CRDs'
kubectl apply -f https://github.com/spinframework/spin-operator/releases/download/v0.6.1/spin-operator.crds.yaml

Write-Host 'Installing Spin operator'
helm upgrade --install spin-operator --namespace spin-operator --create-namespace --version 0.6.1 --wait oci://ghcr.io/spinframework/charts/spin-operator

Write-Host 'Creating football namespace'
kubectl apply -f $namespaceManifest

Write-Host 'Installing shim executor'
kubectl apply -n football -f https://github.com/spinframework/spin-operator/releases/download/v0.6.1/spin-operator.shim-executor.yaml