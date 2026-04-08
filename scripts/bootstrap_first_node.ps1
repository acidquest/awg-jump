param(
    [string]$HostName,
    [string]$SshUser,
    [int]$SshPort,
    [string]$RemoteDir
)

$ErrorActionPreference = "Stop"

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing required command: $Name"
    }
}

function Prompt-WithDefault {
    param(
        [string]$Label,
        [string]$Default = ""
    )

    if ($Default) {
        $value = Read-Host "$Label [$Default]"
        if ([string]::IsNullOrWhiteSpace($value)) {
            return $Default
        }
        return $value
    }

    do {
        $value = Read-Host $Label
    } while ([string]::IsNullOrWhiteSpace($value))
    return $value
}

function Set-EnvValue {
    param(
        [string]$Path,
        [string]$Key,
        [string]$Value
    )

    $lines = Get-Content -Path $Path
    $updated = $false

    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i].TrimStart().StartsWith("$Key=")) {
            $lines[$i] = "$Key=$Value"
            $updated = $true
            break
        }
    }

    if (-not $updated) {
        $lines += "$Key=$Value"
    }

    [System.IO.File]::WriteAllLines($Path, $lines, [System.Text.UTF8Encoding]::new($false))
}

function Quote-ForSh {
    param([string]$Value)
    return "'" + ($Value -replace "'", "'\"'\"'") + "'"
}

Require-Command ssh
Require-Command scp

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir

if ([string]::IsNullOrWhiteSpace($HostName)) {
    $HostName = Prompt-WithDefault "Remote node IP or hostname"
}
if ([string]::IsNullOrWhiteSpace($SshUser)) {
    $SshUser = Prompt-WithDefault "SSH username" "root"
}
if (-not $SshPort) {
    $SshPort = [int](Prompt-WithDefault "SSH port" "22")
}
if ([string]::IsNullOrWhiteSpace($RemoteDir)) {
    $RemoteDir = Prompt-WithDefault "Remote deploy directory" "/opt/awg-jump"
}

$DockerNamespace = Prompt-WithDefault "Docker Hub namespace" "your-dockerhub-namespace"
$ImageTag = Prompt-WithDefault "Docker image tag" "latest"

$ImageJump = "docker.io/$DockerNamespace/awg-jump:$ImageTag"
$ImageNginx = "docker.io/$DockerNamespace/awg-jump-nginx:$ImageTag"

$RemoteRootPrefix = ""
if ($SshUser -ne "root") {
    $RemoteRootPrefix = "sudo "
}

$TmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("awg-jump-bootstrap-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $TmpDir | Out-Null

try {
    Copy-Item (Join-Path $RepoRoot "deploy/docker-compose.images.yml") (Join-Path $TmpDir "docker-compose.yml")
    Copy-Item (Join-Path $RepoRoot "deploy/.env.images.example") (Join-Path $TmpDir ".env.images")
    Copy-Item (Join-Path $RepoRoot ".env.ru.example") (Join-Path $TmpDir ".env.ru.example")
    Copy-Item (Join-Path $RepoRoot ".env.en.example") (Join-Path $TmpDir ".env.en.example")
    Copy-Item (Join-Path $RepoRoot ".env.ru.example") (Join-Path $TmpDir ".env")

    Set-EnvValue (Join-Path $TmpDir ".env") "TLS_COMMON_NAME" $HostName
    Set-EnvValue (Join-Path $TmpDir ".env") "SERVER_HOST" $HostName
    Set-EnvValue (Join-Path $TmpDir ".env.images") "AWG_JUMP_IMAGE" $ImageJump
    Set-EnvValue (Join-Path $TmpDir ".env.images") "AWG_NGINX_IMAGE" $ImageNginx

    $remoteBootstrap = @'
#!/usr/bin/env bash
set -euo pipefail

REMOTE_DIR="$1"

if [[ "$EUID" -ne 0 ]]; then
    echo "Run remote bootstrap as root." >&2
    exit 1
fi

install_docker_apt() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl

    if ! command -v docker >/dev/null 2>&1; then
        curl -fsSL https://get.docker.com | sh
    fi

    if ! docker compose version >/dev/null 2>&1; then
        apt-get update -qq
        apt-get install -y -qq docker-compose-plugin || true
    fi
}

install_docker_dnf() {
    dnf install -y ca-certificates curl

    if ! command -v docker >/dev/null 2>&1; then
        curl -fsSL https://get.docker.com | sh
    fi
}

if command -v apt-get >/dev/null 2>&1; then
    install_docker_apt
elif command -v dnf >/dev/null 2>&1; then
    install_docker_dnf
else
    echo "Unsupported Linux distribution: no apt-get or dnf found." >&2
    exit 1
fi

systemctl enable --now docker

if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose plugin is not available after installation." >&2
    exit 1
fi

mkdir -p "$REMOTE_DIR"
mkdir -p "$REMOTE_DIR/data/certs" "$REMOTE_DIR/data/backups" "$REMOTE_DIR/data/geoip" "$REMOTE_DIR/data/wg_configs"

if [[ ! -c /dev/net/tun ]]; then
    mkdir -p /dev/net
    mknod /dev/net/tun c 10 200 || true
    chmod 666 /dev/net/tun || true
fi
'@

    [System.IO.File]::WriteAllText((Join-Path $TmpDir "REMOTE_BOOTSTRAP.sh"), $remoteBootstrap, [System.Text.UTF8Encoding]::new($false))

    $target = "${SshUser}@${HostName}"
    $quotedRemoteDir = Quote-ForSh $RemoteDir

    Write-Host "Preparing remote temp directory"
    & ssh -p $SshPort $target "mkdir -p /tmp/awg-jump-bootstrap"
    if ($LASTEXITCODE -ne 0) { throw "Failed to create remote temp directory." }

    Write-Host "Uploading deploy files"
    $uploadFiles = @(
        "docker-compose.yml",
        ".env",
        ".env.ru.example",
        ".env.en.example",
        ".env.images",
        "REMOTE_BOOTSTRAP.sh"
    )

    foreach ($file in $uploadFiles) {
        & scp -P $SshPort (Join-Path $TmpDir $file) "${target}:/tmp/awg-jump-bootstrap/$file"
        if ($LASTEXITCODE -ne 0) { throw "Failed to upload $file" }
    }

    Write-Host "Installing Docker and unpacking files on remote host"
    $remoteCommand = "bash -lc ""set -euo pipefail; ${RemoteRootPrefix}bash /tmp/awg-jump-bootstrap/REMOTE_BOOTSTRAP.sh $quotedRemoteDir; ${RemoteRootPrefix}mkdir -p $quotedRemoteDir; ${RemoteRootPrefix}cp /tmp/awg-jump-bootstrap/docker-compose.yml $quotedRemoteDir/docker-compose.yml; ${RemoteRootPrefix}cp /tmp/awg-jump-bootstrap/.env $quotedRemoteDir/.env; ${RemoteRootPrefix}cp /tmp/awg-jump-bootstrap/.env.ru.example $quotedRemoteDir/.env.ru.example; ${RemoteRootPrefix}cp /tmp/awg-jump-bootstrap/.env.en.example $quotedRemoteDir/.env.en.example; ${RemoteRootPrefix}cp /tmp/awg-jump-bootstrap/.env.images $quotedRemoteDir/.env.images; rm -rf /tmp/awg-jump-bootstrap"""
    & ssh -p $SshPort $target $remoteCommand
    if ($LASTEXITCODE -ne 0) { throw "Remote bootstrap failed." }

    Write-Host ""
    Write-Host "Bootstrap complete."
    Write-Host ""
    Write-Host "Remote directory: $RemoteDir"
    Write-Host ""
    Write-Host "Files created:"
    Write-Host "  $RemoteDir/docker-compose.yml"
    Write-Host "  $RemoteDir/.env"
    Write-Host "  $RemoteDir/.env.ru.example"
    Write-Host "  $RemoteDir/.env.en.example"
    Write-Host "  $RemoteDir/.env.images"
    Write-Host ""
    Write-Host "Next steps on the node:"
    Write-Host "  1. Edit $RemoteDir/.env and set at least ADMIN_PASSWORD and SECRET_KEY."
    Write-Host "  2. Verify image tags in $RemoteDir/.env.images."
    Write-Host "  3. Start the stack:"
    Write-Host "     cd $RemoteDir"
    Write-Host "     docker compose --env-file .env.images pull"
    Write-Host "     docker compose --env-file .env.images up -d"
}
finally {
    if (Test-Path $TmpDir) {
        Remove-Item -Recurse -Force $TmpDir
    }
}
