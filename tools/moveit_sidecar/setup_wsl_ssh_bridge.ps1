param(
    [string]$User = "joshua",
    [int]$Port = 2222,
    [string]$KeyDir = "$env:TEMP\codex-wsl-ssh",
    [string]$KnownHosts = "$env:TEMP\codex-wsl-known-hosts"
)

$ErrorActionPreference = "Stop"

function Invoke-Wsl {
    param([Parameter(Mandatory = $true)][string]$Command)
    & wsl.exe bash -lc $Command
}

function Invoke-WslRoot {
    param([Parameter(Mandatory = $true)][string]$Command)
    & wsl.exe -u root bash -lc $Command
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "wsl.exe was not found on PATH."
}

$wslList = & wsl.exe -l -v 2>&1
if ($LASTEXITCODE -ne 0) {
    $message = ($wslList | Out-String).Trim()
    throw "wsl.exe is present, but no usable WSL distro was found. wsl.exe output: $message"
}

New-Item -ItemType Directory -Force $KeyDir | Out-Null
$privateKey = Join-Path $KeyDir "id_ed25519"
$publicKey = Join-Path $KeyDir "id_ed25519.pub"

if (-not (Test-Path $privateKey)) {
    cmd.exe /c "ssh-keygen -t ed25519 -N """" -f ""$privateKey"""
    if ($LASTEXITCODE -ne 0) {
        throw "ssh-keygen failed."
    }
}

$pub = (Get-Content $publicKey -Raw).Trim()

Invoke-Wsl "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
$escapedPub = $pub.Replace("'", "'\''")
Invoke-Wsl "grep -qxF '$escapedPub' ~/.ssh/authorized_keys || echo '$escapedPub' >> ~/.ssh/authorized_keys"

Invoke-WslRoot "if ! command -v sshd >/dev/null 2>&1; then apt update && apt install -y openssh-server; fi"
Invoke-WslRoot "mkdir -p /run/sshd && (/usr/sbin/sshd -p $Port || true)"

$sshArgs = @(
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=$KnownHosts",
    "-i", $privateKey,
    "-p", "$Port",
    "$User@127.0.0.1",
    "hostname; whoami; lsb_release -d"
)

& ssh @sshArgs
if ($LASTEXITCODE -ne 0) {
    throw "SSH verification failed for $User@127.0.0.1:$Port."
}

Write-Host ""
Write-Host "WSL SSH bridge is ready."
Write-Host "Use:"
Write-Host "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=`"$KnownHosts`" -i `"$privateKey`" -p $Port $User@127.0.0.1 `"<command>`""
