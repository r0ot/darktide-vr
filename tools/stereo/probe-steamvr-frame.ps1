[CmdletBinding()]
param(
    [string] $OutputDirectory,

    [ValidateSet('Debug', 'Release')]
    [string] $Configuration = 'Release',

    # About 30 seconds at 90 Hz: long enough to pick both controllers up, move
    # the sticks and press the face buttons, which is what makes the
    # controller_profile lines appear at all (they wait for tracked hands).
    [ValidateRange(120, 16000)]
    [int] $XrFrames = 2700,

    # Withhold khr/simple_controller, so a session that still comes up on the
    # generic profile cannot be blamed on SteamVR preferring it.
    [switch] $NoSimpleProfile,

    # Pin the per-eye extent (WIDTHxHEIGHT) instead of SteamVR's
    # recommendation, for like-for-like comparisons.
    [ValidatePattern('^\d+x\d+$')]
    [string] $EyeExtent,

    # This display refresh rate and motion smoothing for the probe's session
    # only. Under SteamVR the viewer sets SteamVR's own settings and puts the
    # user's back when it exits (--steamvr-settings).
    [ValidateRange(30, 1000)]
    [int] $RefreshRate,

    [ValidateSet('on', 'off')]
    [string] $MotionSmoothing,

    # Theatre runs the viewer's tracking loop, the only path that syncs the
    # controller actions: without it there is no controller_profile and no
    # controller counter (first Frame run, 8 October). Stereo is that loop
    # submitting a projection pair, which is also where the eye views are
    # measured (canted_views, runtime IPD), so it answers every question of
    # bring-up steps 1 to 3 at once. Synthetic is the plain rendering smoke of
    # the readiness preflight.
    [ValidateSet('Stereo', 'Theatre', 'Synthetic')]
    [string] $Mode = 'Stereo'
)

# The Steam Frame bring-up of docs/STEAMVR-STEAM-FRAME.md, steps 1 to 3: the
# viewer alone against SteamVR, no game, no mod, no Darktide installation.
# It reads which runtime is registered, refuses anything but SteamVR with a
# live vrserver, runs one bounded rendering session and keeps the lines the
# document asks to record. It starts nothing but the viewer: SteamVR and the
# headset are the user's to bring up.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
. (Join-Path $repoRoot 'tools\unattended\xr-readiness.ps1')

$runtimeProperty = Get-ItemProperty `
    -Path 'HKLM:\SOFTWARE\Khronos\OpenXR\1' `
    -Name ActiveRuntime `
    -ErrorAction SilentlyContinue
$activeRuntime = if ($runtimeProperty) { $runtimeProperty.ActiveRuntime } else { '' }
$runtimeProfile = Get-XrRuntimeProfile -Runtime $activeRuntime
if ($runtimeProfile -ne 'SteamVR') {
    throw "The active OpenXR runtime is '$activeRuntime' ($runtimeProfile). Set SteamVR as the OpenXR runtime (SteamVR Settings > OpenXR) and retry."
}
if (-not (Test-Path -LiteralPath $activeRuntime -PathType Leaf)) {
    throw "The SteamVR runtime manifest is registered but missing: $activeRuntime"
}
if (-not (Get-Process vrserver -ErrorAction SilentlyContinue)) {
    throw 'SteamVR is not running (no vrserver). Start SteamVR with the Frame connected and awake, then retry.'
}
if (Get-Process darktidevr-xr-harness -ErrorAction SilentlyContinue) {
    throw 'An XR viewer is already running; stop it before probing.'
}

$harness = Join-Path $repoRoot `
    "build\windows-vs2022\tests\xr_harness\$Configuration\darktidevr-xr-harness.exe"
if (-not (Test-Path -LiteralPath $harness -PathType Leaf)) {
    throw "$Configuration XR harness not found: $harness"
}

$timestamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repoRoot "artifacts\steamvr-probe\$timestamp"
}
[IO.Directory]::CreateDirectory($OutputDirectory) | Out-Null

$arguments = "--frames 30 --require-openxr --require-rendering --xr-frames $XrFrames"
# Not the debug layer in the theatre loop: SteamVR's own runtime calls
# ID3D12CompatibilityDevice::ReflectSharedProperties on the theatre's quad
# images, the layer reports it as two errors, and the viewer fails a run that
# rendered every frame (first Frame run, 8 October). The viewer never makes
# that call. The synthetic smoke keeps the layer, as the preflight does.
switch ($Mode) {
    'Stereo' { $arguments += ' --theatre --stereo-sbs' }
    'Theatre' { $arguments += ' --theatre' }
    'Synthetic' { $arguments += ' --debug-layer' }
}
if ($NoSimpleProfile) { $arguments += ' --no-simple-profile' }
if ($EyeExtent) { $arguments += " --eye-extent $EyeExtent" }
if ($RefreshRate) { $arguments += " --refresh-rate $RefreshRate" }
if ($MotionSmoothing) { $arguments += " --motion-smoothing $MotionSmoothing" }
# The slowest Frame refresh is 72 Hz; a minute on top covers instance and
# session start-up.
$timeoutSeconds = [Math]::Min(300, [int][Math]::Ceiling($XrFrames / 72.0) + 60)

Write-Output "steamvr_probe=starting runtime=$activeRuntime xr_frames=$XrFrames timeout_s=$timeoutSeconds"
Write-Output 'Hold both controllers, move both sticks and press the face buttons while it runs.'
$run = Invoke-BoundedXrSmoke -FilePath $harness -Arguments $arguments `
    -TimeoutSeconds $timeoutSeconds
$output = @($run.output)
$logPath = Join-Path $OutputDirectory 'harness.log'
[IO.File]::WriteAllLines($logPath, [string[]] $output)

# What the document's bring-up order asks to record, and what the 18
# September work added for this runtime (rounding, sample count, layer
# count, canted views).
$recordPattern = '^(result=|steamvr_settings\.|openxr\.(active_runtime|runtime_name|runtime_version|runtime_ipd_metres|extension|stereo_views|recommended_size|eye_extent|display_refresh_rate|runtime_fov|swapchain|max_layer_count|layers_clamped|floor_space|interaction_profile|controller_profile|controller_samples|controller_(left|right)_(aim_tracked|thumbstick_active|thumbstick_changed|held)_frames|canted_views|session|frames|submitted_frames|not_rendered_frames|flat_fallback_frames|submit_hz|lifecycle))'
$recorded = @($output | Where-Object { $_ -match $recordPattern })
$resultLine = $output | Where-Object { $_ -match '^result=' } | Select-Object -Last 1

$summary = [ordered]@{
    schema_version = 1
    captured_utc = (Get-Date).ToUniversalTime().ToString('o')
    active_runtime = $activeRuntime
    runtime_profile = $runtimeProfile
    arguments = $arguments
    exit_code = $run.exit_code
    timed_out = $run.timed_out
    output_complete = $run.output_complete
    result = if ($resultLine) { $resultLine.Substring(7) } else { 'missing' }
    recorded = $recorded
    log = $logPath
}
$summaryPath = Join-Path $OutputDirectory 'summary.json'
$summary | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $summaryPath -Encoding utf8

$recorded | ForEach-Object { Write-Output $_ }
Write-Output "steamvr_probe=$($summary.result) exit=$($run.exit_code) timed_out=$($run.timed_out) summary=$summaryPath"
if ($run.timed_out -or $run.exit_code -ne 0 -or $summary.result -ne 'pass') {
    exit 1
}
