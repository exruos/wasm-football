$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptRoot
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

Assert-Command 'kubectl'
Assert-Command 'helm'

$currentContext = (kubectl config current-context) 2>$null

Write-Host "`n=========================================" -ForegroundColor Cyan
Write-Host "         KUBERNETES TARGET CLUSTER        " -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "  CONTEXT   : " -NoNewline; Write-Host $currentContext -ForegroundColor Yellow
Write-Host "=========================================`n" -ForegroundColor Cyan

$confirmation = Read-Host "Are you sure you want to proceed against this cluster? (y/n)"

if ($confirmation -ne 'y') {
	Write-Host "Operation cancelled."
	exit 1
}

Write-Host 'Installing cert-manager'
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.20.2/cert-manager.yaml

kubectl wait --for=condition=available --timeout=300s deployment/cert-manager-webhook -n cert-manager

Write-Host 'Installing runtime-class-manager'
helm upgrade --install runtime-class-manager --namespace runtime-class-manager --create-namespace --version 0.2.0 oci://ghcr.io/spinframework/charts/runtime-class-manager

Write-Host 'Create Shim resource for installing the containerd-shim-spin binary'
kubectl apply -f $scriptRoot\runtime-class-manager-shim.yaml

Write-Host "Label all Nodes where the shim should be installed"
kubectl label node --all spin=true

Write-Host 'Installing Spin operator CRDs'
kubectl apply -f https://github.com/spinframework/spin-operator/releases/download/v0.6.1/spin-operator.crds.yaml

Write-Host 'Installing Spin operator'
helm upgrade --install spin-operator --namespace spin-operator --create-namespace --version 0.6.1 --wait oci://ghcr.io/spinframework/charts/spin-operator

Write-Host 'Creating football namespace'
kubectl apply -f $namespaceManifest

Write-Host 'Installing shim executor'
kubectl apply -n football -f https://github.com/spinframework/spin-operator/releases/download/v0.6.1/spin-operator.shim-executor.yaml


Write-Host 'Installing monitoring stack'
helm repo add vm https://victoriametrics.github.io/helm-charts/
helm repo update

helm install vmks vm/victoria-metrics-k8s-stack -n monitoring --create-namespace  -f (Join-Path $repoRoot 'k3s\vm-values.yaml')

Write-Host 'Installing Kepler'
helm install kepler https://github.com/sustainable-computing-io/kepler/releases/download/v0.11.4/kepler-helm-0.11.4.tgz -n monitoring

kubectl apply -f (Join-Path $repoRoot 'k3s\kepler-scrape.yaml')

Write-Host 'Please restart k3s via "sudo systemctl restart k3s" to ensure containerd picks up the shim configuration.' -ForegroundColor Yellow