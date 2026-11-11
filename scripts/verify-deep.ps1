# Focused, fail-closed V25 Deep-mode source and native validation for Windows.
# Use: powershell -ExecutionPolicy Bypass -File scripts/verify-deep.ps1
# Optional: -Report C:\path\outside\source\deep-validation.json
param([string]$Report = "")

$ErrorActionPreference = "Stop"
$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($Report)) {
    $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
    $suffix = [Guid]::NewGuid().ToString("N").Substring(0, 8)
    $Report = Join-Path (Split-Path -Parent $sourceRoot) ("Craft_Anything_V25_Deep_Gate_{0}_{1}.json" -f $stamp, $suffix)
}
$script = Join-Path $PSScriptRoot "native_validation.py"

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $script --root $sourceRoot --profile deep --report $Report
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python $script --root $sourceRoot --profile deep --report $Report
} else {
    Write-Error "Python 3 is required for this validation runner."
    exit 2
}
exit $LASTEXITCODE
