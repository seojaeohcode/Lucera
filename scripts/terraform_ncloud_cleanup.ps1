param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $TerraformArgs
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repoRoot ".env.ncloud"
$tfRoot = Join-Path $repoRoot "infra\ncloud\cleanup"

if (-not (Test-Path -LiteralPath $envFile)) {
    throw "NCloud credential file not found: $envFile"
}

$previousValues = @{}
try {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*#' -or $line -notmatch '^\s*([^=]+?)\s*=\s*(.*)\s*$') {
            continue
        }

        $name = $matches[1].Trim()
        $value = $matches[2].Trim()
        if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        if ($name -notin @('NCLOUD_ACCESS_KEY', 'NCLOUD_SECRET_KEY', 'NCLOUD_REGION', 'NCLOUD_SITE')) {
            continue
        }

        $previousValues[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }

    if (-not $env:NCLOUD_REGION) {
        $env:NCLOUD_REGION = 'KR'
    }

    & terraform "-chdir=$tfRoot" @TerraformArgs
    $exitCode = $LASTEXITCODE
}
finally {
    foreach ($name in @('NCLOUD_ACCESS_KEY', 'NCLOUD_SECRET_KEY', 'NCLOUD_REGION', 'NCLOUD_SITE')) {
        if ($previousValues.ContainsKey($name) -and $null -ne $previousValues[$name]) {
            [Environment]::SetEnvironmentVariable($name, $previousValues[$name], 'Process')
        }
        else {
            [Environment]::SetEnvironmentVariable($name, $null, 'Process')
        }
    }
}

exit $exitCode
