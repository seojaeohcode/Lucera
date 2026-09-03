param(
    [string] $User = "root",
    [string] $HostKeyFingerprint = "ssh-ed25519 255 SHA256:UdTiy0n77FCIzWM3HESLqY7plu8UATBBpzjWe+UK09g",
    [switch] $SkipUpload,
    [switch] $SkipArchive
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$terraformWrapper = Join-Path $PSScriptRoot "terraform_ncloud.ps1"
$archive = Join-Path $repoRoot "tmp\lucera-runtime.tgz"

function Resolve-PuttyBinary {
    param([string] $Name)

    $fromPath = Get-Command "$Name.exe" -ErrorAction SilentlyContinue
    if ($fromPath) {
        return $fromPath.Source
    }

    $candidate = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Filter "$Name.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($candidate) {
        return $candidate.FullName
    }

    throw "$Name.exe is required. Install PuTTY or make it available on PATH."
}

function Invoke-Checked {
    param(
        [string] $Program,
        [string[]] $Arguments
    )

    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Program failed with exit code $LASTEXITCODE"
    }
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $archive) | Out-Null
$publicIp = (& $terraformWrapper output -raw server_public_ip).Trim()
$rootPassword = (& $terraformWrapper output -raw root_password).Trim()
if (-not $publicIp -or -not $rootPassword) {
    throw "Terraform outputs do not contain a server public IP and root password."
}

$plink = Resolve-PuttyBinary "plink"
$pscp = Resolve-PuttyBinary "pscp"
$remote = "$User@$publicIp"
$commonOptions = @(
    "-batch",
    "-hostkey", $HostKeyFingerprint,
    "-pw", $rootPassword
)

try {
    if (-not $SkipUpload -and (-not $SkipArchive -or -not (Test-Path -LiteralPath $archive))) {
        if (Test-Path -LiteralPath $archive) {
            [System.IO.File]::Delete($archive)
        }
        Invoke-Checked "tar.exe" @(
            "-czf", $archive,
            "-C", $repoRoot,
            "lucera",
            "web",
            "db",
            "deploy",
            "scripts",
            "config.py",
            "data/reference/gazetteer"
        )
    }

    Invoke-Checked $plink ($commonOptions + @($remote, "mkdir -p /opt/lucera /tmp"))
    if (-not $SkipUpload) {
        Invoke-Checked $pscp ($commonOptions + @($archive, "$remote`:/tmp/lucera-runtime.tgz"))
    }
    if ($SkipUpload) {
        $remoteInstallCommand = "systemctl restart lucera && systemctl reload nginx && for attempt in `$(seq 1 30); do curl --fail --silent --show-error http://127.0.0.1/health && exit 0; sleep 2; done; exit 1"
    }
    else {
        $remoteInstallCommand = "systemctl stop lucera 2>/dev/null || true; tar -xzf /tmp/lucera-runtime.tgz -C /opt/lucera; bash /opt/lucera/deploy/bootstrap.sh; python3 /opt/lucera/scripts/rebuild_demo_db.py --db /opt/lucera/data/db/lucera_minutes.sqlite3 --replace; chown -R lucera:lucera /opt/lucera/data; systemctl restart lucera; systemctl reload nginx; for attempt in `$(seq 1 30); do curl --fail --silent --show-error http://127.0.0.1/health && rm -f /tmp/lucera-runtime.tgz && exit 0; sleep 2; done; exit 1"
    }
    Invoke-Checked $plink ($commonOptions + @($remote, $remoteInstallCommand))
    Invoke-Checked $plink ($commonOptions + @($remote, "curl --fail --silent --show-error http://127.0.0.1/health"))
    Write-Output "Lucera deployed to http://$publicIp/"
}
finally {
    $rootPassword = $null
    $commonOptions = $null
}
