# 引数・default・serial動作はDevice Rust CLIへ転送する。
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$forwarded = New-Object 'System.Collections.Generic.List[string]'
foreach ($argument in $args) {
    if ($argument -is [Array]) { $forwarded.Add(($argument -join ',')) }
    else { $forwarded.Add([string]$argument) }
}
$json = ConvertTo-Json -InputObject @($forwarded.ToArray()) -Compress
$encoded = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($json))
$oldOutputEncoding = $OutputEncoding
$oldPythonUtf8 = $env:PYTHONUTF8
$exitCode = 1
try {
    $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $env:PYTHONUTF8 = '1'
    $invocation = @('run', '--project', $repoRoot, '--no-sync', '--no-env-file', '--offline',
                   'python', (Join-Path $repoRoot 'scripts/hardware/selfrionette/run_device_serial_tool.py'),
                   'legacy-monitor', '--powershell-json', $encoded)
    $lines = @($input)
    if ($lines.Count -gt 0) { ($lines -join ([string][char]10)) | & uv @invocation }
    else { & uv @invocation }
    $exitCode = $LASTEXITCODE
} finally {
    $OutputEncoding = $oldOutputEncoding
    $env:PYTHONUTF8 = $oldPythonUtf8
}
exit $exitCode
