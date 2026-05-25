param(
    [string]$EnvPath = (Join-Path $PSScriptRoot "..\.env.local")
)

if (-not (Test-Path -LiteralPath $EnvPath)) {
    throw "Env file not found: $EnvPath"
}

Get-Content -LiteralPath $EnvPath -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) {
        return
    }
    $parts = $line -split "=", 2
    if ($parts.Count -ne 2) {
        return
    }
    [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
}

Write-Host "Preference env loaded for current PowerShell process."
