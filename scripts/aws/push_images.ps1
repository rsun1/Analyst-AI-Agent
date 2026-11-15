# Build the API and UI images from the repo root and push them to ECR.
# Usage (from the repo root):  .\scripts\aws\push_images.ps1 -Region us-east-2 -Profile olist-aws [-Tag latest]
# Needs: Docker running, AWS CLI logged in (aws sts get-caller-identity --profile <p> works).
#
# Windows notes (both learned the hard way):
#  * Sessions from `aws login` give an ECR token (~4000 bytes) larger than the Windows Credential
#    Manager accepts ("The stub received bad data"), so this script logs in with a temporary Docker
#    config that stores the token in a plain file. The folder is deleted when the script ends and the
#    token expires after 12 hours anyway.
#  * Piping the token through PowerShell corrupts it (400 Bad Request); the pipe runs inside cmd.exe.
param(
    [Parameter(Mandatory = $true)][string]$Region,
    [string]$Profile = "olist-aws",
    [string]$Tag = "latest"
)
# Docker writes progress and warnings to stderr; with 'Stop' Windows PowerShell 5.1 treats that as a
# failure when stderr is captured. Every native call below is checked via its exit code instead.
$ErrorActionPreference = "Continue"

$aws = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $aws) { $aws = Join-Path $env:LOCALAPPDATA "Programs\Amazon\AWSCLIV2\aws.exe" }
$account = (& $aws sts get-caller-identity --profile $Profile --query Account --output text)
if (-not $account) { throw "aws sts get-caller-identity returned nothing: log in first (aws login --profile $Profile)." }
$registry = "$account.dkr.ecr.$Region.amazonaws.com"

$images = @(
    @{ Repo = "olist-api"; File = "Dockerfile" },
    @{ Repo = "olist-ui";  File = "Dockerfile.ui" }
)

function Assert-Ok([string]$what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)." } }

# --- temporary Docker config (no credential store) ---------------------------------------------
$srcDocker = Join-Path $env:USERPROFILE ".docker"
$tmpCfg = Join-Path $env:TEMP ("docker-ecr-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$oldCfg = $env:DOCKER_CONFIG
New-Item -ItemType Directory -Path $tmpCfg | Out-Null
try {
    if (Test-Path "$srcDocker\contexts") { Copy-Item "$srcDocker\contexts" "$tmpCfg\contexts" -Recurse }
    $cfg = if (Test-Path "$srcDocker\config.json") { Get-Content "$srcDocker\config.json" -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
    foreach ($k in 'credsStore', 'credHelpers', 'auths') { $cfg.PSObject.Properties.Remove($k) }
    # An (empty) entry for the registry stops Docker from auto-selecting the Windows credential store.
    $cfg | Add-Member -NotePropertyName auths -NotePropertyValue ([pscustomobject]@{ $registry = [pscustomobject]@{} })
    $cfg | ConvertTo-Json -Depth 10 | Set-Content "$tmpCfg\config.json" -Encoding ASCII
    $env:DOCKER_CONFIG = $tmpCfg

    cmd /c "`"$aws`" ecr get-login-password --region $Region --profile $Profile | docker login --username AWS --password-stdin $registry"
    Assert-Ok "docker login to ECR"

    foreach ($img in $images) {
        $repo = $img.Repo
        # Expected to fail when the repo does not exist; relax 'Stop' for the probe (PowerShell 5.1 stderr quirk).
        $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        & $aws ecr describe-repositories --repository-names $repo --region $Region --profile $Profile 2>&1 | Out-Null
        $repoExists = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $prev
        if (-not $repoExists) {
            & $aws ecr create-repository --repository-name $repo --region $Region --profile $Profile --image-scanning-configuration scanOnPush=true | Out-Null
            Assert-Ok "create ECR repository $repo"
        }
        docker build -f $img.File -t "${repo}:$Tag" .
        Assert-Ok "docker build $repo"
        docker tag "${repo}:$Tag" "$registry/${repo}:$Tag"
        Assert-Ok "docker tag $repo"
        docker push "$registry/${repo}:$Tag"
        Assert-Ok "docker push $repo"
        Write-Host "pushed $registry/${repo}:$Tag"
    }
}
finally {
    if ($oldCfg) { $env:DOCKER_CONFIG = $oldCfg } else { Remove-Item Env:DOCKER_CONFIG -ErrorAction SilentlyContinue }
    Remove-Item $tmpCfg -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "`nVerifying what ECR holds:"
foreach ($img in $images) {
    & $aws ecr describe-images --repository-name $img.Repo --region $Region --profile $Profile `
        --query "imageDetails[].{tags:imageTags[0],MB:imageSizeInBytes,pushed:imagePushedAt}" --output table
}
