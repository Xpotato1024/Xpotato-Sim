[CmdletBinding()]
param(
    [string]$HostName = "127.0.0.1",
    [int]$PublisherPort = 8768,
    [int]$ViewerPort = 5176,
    [string]$Preset = "sweep_x",
    [int]$Steps = 6,
    [double]$IntervalS = 0.033,
    [int]$GracePeriodS = 90,
    [switch]$OpenBrowser,
    [switch]$NoBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
# 既存のsmoke引数を起動profileへ投影し、process管理を共通ownerへ委譲する。
# v1はloopback限定。LAN操作は明示した低位CLI手順を使用する。
$profile = @{
    schema_version = "xpotato-sim-launch-profile/v1"
    name = "browser-smoke"
    workspace = $repoRoot
    mode = "replay"
    robot = @{ name = "fast_arm"; version = 1 }
    input = @{ plugin = @{ name = "programmed_target"; version = 1 }; provider = $null; preset = $Preset }
    mapping = @{ plugin = @{ name = "replay_mapping"; version = 1 }; parameters = @{} }
    execution = @{ steps = $Steps; dt_s = 1.0 / 60.0; interval_s = $IntervalS; grace_period_s = $GracePeriodS }
    web = @{ host = $HostName; port = $ViewerPort; websocket_port = $PublisherPort; open_browser = [bool]($OpenBrowser -and -not $NoBrowser) }
}
$profilePath = Join-Path ([System.IO.Path]::GetTempPath()) ("selfrionette-smoke-" + [guid]::NewGuid().ToString("N") + ".json")
$exitCode = 1
try {
    [System.IO.File]::WriteAllText($profilePath, ($profile | ConvertTo-Json -Depth 8), [System.Text.UTF8Encoding]::new($false))
    Push-Location $repoRoot
    try {
        $arguments = @("run", "xpotato-sim", "app", "--profile", $profilePath)
        if ($NoBrowser) { $arguments += "--startup-check" }
        & uv @arguments
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
} finally {
    if (Test-Path -LiteralPath $profilePath) { Remove-Item -LiteralPath $profilePath }
}
exit $exitCode
