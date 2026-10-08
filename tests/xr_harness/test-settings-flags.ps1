[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $Viewer
)

# The viewer's user settings reach it as flag files in the mod folder, one
# level above its bin directory, because the play configuration is started
# with a fixed command line by the game's native module (8 October). This
# reproduces that layout in a temporary folder and runs the viewer desktop
# only: a parsable flag is applied, an unparsable one is reported and ignored
# without stopping the viewer, and an argument wins over a flag.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Join-Path ([IO.Path]::GetTempPath()) ('darktidevr-settings-flags-' + [Guid]::NewGuid().ToString('N'))
$bin = Join-Path $root 'bin'
[IO.Directory]::CreateDirectory($bin) | Out-Null
try {
    $viewerDirectory = Split-Path -Parent $Viewer
    foreach ($name in @('darktidevr-xr-harness.exe', 'openxr_loader.dll')) {
        Copy-Item -LiteralPath (Join-Path $viewerDirectory $name) -Destination $bin
    }
    $copy = Join-Path $bin 'darktidevr-xr-harness.exe'

    function Invoke-Viewer([string[]] $Arguments) {
        # Windows PowerShell turns a native program's stderr into an error
        # record, which Stop would make terminating; the exit code is the
        # verdict here.
        $ErrorActionPreference = 'Continue'
        $output = & $copy --no-openxr --frames 3 @Arguments 2>&1 | ForEach-Object { "$_" }
        [pscustomobject]@{ exit_code = $LASTEXITCODE; output = @($output) }
    }
    function Assert-Line($Run, [string] $Pattern, [string] $Case) {
        if (-not ($Run.output | Where-Object { $_ -match $Pattern })) {
            throw "$Case`: no line matching '$Pattern'. Output:`n$($Run.output -join "`n")"
        }
    }
    function Assert-NoLine($Run, [string] $Pattern, [string] $Case) {
        if ($Run.output | Where-Object { $_ -match $Pattern }) {
            throw "$Case`: unexpected line matching '$Pattern'."
        }
    }

    $none = Invoke-Viewer @()
    if ($none.exit_code -ne 0) { throw "no flags: exit $($none.exit_code)" }
    Assert-NoLine $none '^openxr\.(refresh_rate|eye_extent|motion_smoothing)_flag=' 'no flags'

    Set-Content -LiteralPath (Join-Path $root 'darktidevr_refresh_rate.flag') -Value ' 90 ' -Encoding ascii
    Set-Content -LiteralPath (Join-Path $root 'darktidevr_eye_extent.flag') -Value '2160x2160' -Encoding ascii
    Set-Content -LiteralPath (Join-Path $root 'darktidevr_motion_smoothing.flag') -Value 'off' -Encoding ascii
    $applied = Invoke-Viewer @()
    if ($applied.exit_code -ne 0) { throw "applied flags: exit $($applied.exit_code)" }
    Assert-Line $applied '^openxr\.refresh_rate_flag=applied$' 'applied flags'
    Assert-Line $applied '^openxr\.eye_extent_flag=applied$' 'applied flags'
    Assert-Line $applied '^openxr\.motion_smoothing_flag=applied$' 'applied flags'
    # Desktop only: no OpenXR instance, so SteamVR's settings are never touched.
    Assert-NoLine $applied '^steamvr_settings\.' 'applied flags'

    # An argument wins: the flag is not read at all.
    $argument = Invoke-Viewer @('--refresh-rate', '120', '--eye-extent', '1920x1920', '--motion-smoothing', 'on')
    if ($argument.exit_code -ne 0) { throw "argument: exit $($argument.exit_code)" }
    Assert-NoLine $argument '^openxr\.(refresh_rate|eye_extent|motion_smoothing)_flag=' 'argument'

    Set-Content -LiteralPath (Join-Path $root 'darktidevr_refresh_rate.flag') -Value 'fast' -Encoding ascii
    Set-Content -LiteralPath (Join-Path $root 'darktidevr_eye_extent.flag') -Value '0x2160' -Encoding ascii
    Set-Content -LiteralPath (Join-Path $root 'darktidevr_motion_smoothing.flag') -Value 'maybe' -Encoding ascii
    $ignored = Invoke-Viewer @()
    if ($ignored.exit_code -ne 0) { throw "unparsable flags stopped the viewer: exit $($ignored.exit_code)" }
    Assert-Line $ignored '^openxr\.refresh_rate_flag=ignored-unparsable$' 'unparsable flags'
    Assert-Line $ignored '^openxr\.eye_extent_flag=ignored-unparsable$' 'unparsable flags'
    Assert-Line $ignored '^openxr\.motion_smoothing_flag=ignored-unparsable$' 'unparsable flags'

    # A bad argument is the caller's mistake and stops the viewer.
    $bad = Invoke-Viewer @('--refresh-rate', '5')
    if ($bad.exit_code -eq 0) { throw 'an out-of-range --refresh-rate was accepted' }

    # The SteamVR settings mode refuses a bad request before it connects to
    # SteamVR, so these never reach the user's settings.
    foreach ($case in @(
            @('--steamvr-settings'),
            @('--steamvr-settings', 'reset'),
            @('--steamvr-settings', 'apply', '--refresh-rate', '5'),
            @('--steamvr-settings', 'apply', '--motion-smoothing', 'sometimes'),
            @('--steamvr-settings', 'apply', '--volume', '3'))) {
        $ErrorActionPreference = 'Continue'
        $output = & $copy @case 2>&1 | ForEach-Object { "$_" }
        $code = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($code -eq 0) { throw "--steamvr-settings accepted: $($case -join ' ')" }
        if ($output | Where-Object { $_ -match '^steamvr_settings\.' }) {
            throw "--steamvr-settings reached SteamVR for: $($case -join ' ')"
        }
    }

    Write-Output 'settings_flags=pass'
}
finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
