# Build the API and UI images from the repo root and push them to ECR.
# Usage (from the repo root):  .\scripts\aws\push_images.ps1 -Region us-east-1 [-Tag latest]
# Needs: Docker running, AWS CLI configured (aws sts get-caller-identity works).
param(
    [Parameter(Mandatory = $true)][string]$Region,
    [string]$Tag = "latest"
)
$ErrorActionPreference = "Stop"

$account = (aws sts get-caller-identity --query Account --output text)
if (-not $account) { throw "aws sts get-caller-identity returned nothing: configure the CLI first." }
$registry = "$account.dkr.ecr.$Region.amazonaws.com"

$images = @(
    @{ Repo = "olist-api"; File = "Dockerfile" },
    @{ Repo = "olist-ui";  File = "Dockerfile.ui" }
)

aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $registry

foreach ($img in $images) {
    $repo = $img.Repo
    # Create the repository if it does not exist (scan on push is cheap and useful).
    aws ecr describe-repositories --repository-names $repo --region $Region *> $null
    if ($LASTEXITCODE -ne 0) {
        aws ecr create-repository --repository-name $repo --region $Region --image-scanning-configuration scanOnPush=true | Out-Null
    }
    docker build -f $img.File -t "${repo}:$Tag" .
    docker tag "${repo}:$Tag" "$registry/${repo}:$Tag"
    docker push "$registry/${repo}:$Tag"
    Write-Host "pushed $registry/${repo}:$Tag"
}
