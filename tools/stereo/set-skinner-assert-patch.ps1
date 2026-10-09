[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('Inspect', 'Apply', 'Restore')]
    [string] $Action,

    # Resolved from the Steam installation when neither is given; -GameRoot
    # selects among several copies the same way the launcher does.
    [string] $GameExe,

    [string] $GameRoot,

    # Outside the game folder and outside any repository: a package user needs
    # a writable, discoverable place for the pristine executable.
    [string] $Backup = (Join-Path $env:LOCALAPPDATA 'DarktideVR\Darktide.exe.pre-skinner-assert-patch')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $GameExe) {
    . (Join-Path $PSScriptRoot 'resolve-darktide-game-root.ps1')
    $GameExe = Join-Path (Resolve-DarktideGameRoot -GameRoot $GameRoot) 'binaries\Darktide.exe'
}
New-Item -ItemType Directory -Path (Split-Path -Parent $Backup) -Force | Out-Null

# One entry per supported Darktide build: its pristine executable's SHA-256
# and the file offsets of the two `jne skip_assert` (75 31) guards, each made
# `jmp skip_assert` (eb 31). These are the only changed bytes. A new build's
# offsets come from tools/stereo/find-skinner-assert-sites.py, which finds the
# guards by structure (the loads of the assert's expression strings and the
# short jne that skips each); add the build here only after its two sites are
# confirmed in its disassembly.
$builds = @(
    # 19 September 2026, the build the patch was written against.
    @{ Sha256 = 'e0f581d2c63b692c7d9f328e3edeb39c0f484956905569d38ba27bbb3fcc0aae'
       Offsets = @(0x7a7586, 0x7a7682) },
    # Steam build 25681127 (7 October 2026): RVAs 0x7c98e6 and 0x7c99e2,
    # still 0xfc apart; each `cmp [rsi+0xd0], eax; jne +0x31` skips the
    # assert and falls through to `mov [rsi+0xd0], eax`.
    @{ Sha256 = 'a7131fecade52ad4758eb7c7fa26986aa2c32edd545cb50cc61585ebfb4f9deb'
       Offsets = @(0x7c8ee6, 0x7c8fe2) }
)
$originalByte = [byte] 0x75
$patchedByte = [byte] 0xeb

function Get-BytesSha256([byte[]] $Bytes) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        # [Convert]::ToHexString is absent from .NET Framework (Windows PowerShell 5.1).
        return ([BitConverter]::ToString($sha.ComputeHash($Bytes)) -replace '-', '').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

# The build this executable is, and whether it is patched: an original is
# known by its hash; a patched one by un-patching its two bytes in memory and
# finding that hash. Anything else is refused, including a partial patch.
function Resolve-Build([byte[]] $Bytes) {
    $sha256 = Get-BytesSha256 $Bytes
    foreach ($build in $builds) {
        if ($sha256 -eq $build.Sha256) {
            foreach ($offset in $build.Offsets) {
                if ($Bytes[$offset] -ne $originalByte) {
                    throw ('Unexpected byte 0x{0:x2} at file offset 0x{1:x}' -f $Bytes[$offset], $offset)
                }
            }
            return @{ Build = $build; State = 'original' }
        }
    }
    foreach ($build in $builds) {
        $outside = @($build.Offsets | Where-Object { $_ -ge $Bytes.Length })
        if ($outside.Count) { continue }
        $values = @($build.Offsets | ForEach-Object { $Bytes[$_] })
        if (@($values | Where-Object { $_ -ne $patchedByte }).Count) { continue }
        $reversed = [byte[]] $Bytes.Clone()
        foreach ($offset in $build.Offsets) { $reversed[$offset] = $originalByte }
        if ((Get-BytesSha256 $reversed) -eq $build.Sha256) {
            return @{ Build = $build; State = 'patched' }
        }
    }
    throw "Unknown Darktide executable hash: $sha256 (this build is not supported yet)"
}

$gamePath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
    $GameExe)
$backupPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
    $Backup)
if (-not (Test-Path -LiteralPath $gamePath -PathType Leaf)) {
    throw "Darktide executable not found: $gamePath"
}
if (Get-Process Darktide -ErrorAction SilentlyContinue) {
    throw 'Refusing to modify Darktide.exe while the game is running'
}

$bytes = [IO.File]::ReadAllBytes($gamePath)
$resolved = Resolve-Build $bytes
$state = $resolved.State
$originalSha256 = $resolved.Build.Sha256
$offsets = $resolved.Build.Offsets
$sha256 = Get-BytesSha256 $bytes

if ($Action -eq 'Inspect') {
    # Plain lines: object output can be dropped when the host exits right
    # after emitting it, and installers parse these values.
    Write-Output "path=$gamePath"
    Write-Output "state=$state"
    Write-Output "sha256=$sha256"
    Write-Output "build.sha256=$originalSha256"
    Write-Output ("patched.offsets=" + (($offsets | ForEach-Object { '0x{0:x}' -f $_ }) -join ','))
    Write-Output "backup=$backupPath backup.present=$(Test-Path -LiteralPath $backupPath -PathType Leaf)"
    exit 0
}

if ($Action -eq 'Apply') {
    $eac = Get-Service EasyAntiCheat_EOS -ErrorAction SilentlyContinue
    if ($eac -and $eac.Status -ne 'Stopped') {
        throw "Refusing to patch Darktide while EAC is $($eac.Status)"
    }
    if ($state -eq 'patched') {
        throw 'Darktide.exe is already patched'
    }
    if ($sha256 -ne $originalSha256) {
        throw "Unknown Darktide executable hash: $sha256"
    }

    $backupDirectory = Split-Path -Parent $backupPath
    if (-not (Test-Path -LiteralPath $backupDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
    }
    if (Test-Path -LiteralPath $backupPath) {
        $backupSha256 = (Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).
            Hash.ToLowerInvariant()
        if ($backupSha256 -ne $originalSha256) {
            # The pristine copy of an EARLIER supported build is obsolete once
            # Steam has updated the game; anything else is not ours to replace.
            if (-not ($builds | Where-Object { $_.Sha256 -eq $backupSha256 })) {
                throw "Existing backup has unexpected hash: $backupSha256"
            }
            Copy-Item -LiteralPath $gamePath -Destination $backupPath -Force
            Write-Output "Replaced the previous build's pristine backup ($backupSha256)"
        }
    }
    else {
        Copy-Item -LiteralPath $gamePath -Destination $backupPath
    }

    foreach ($offset in $offsets) {
        $bytes[$offset] = $patchedByte
    }
    [IO.File]::WriteAllBytes($gamePath, $bytes)
    $patchedSha256 = (Get-FileHash -LiteralPath $gamePath -Algorithm SHA256).
        Hash.ToLowerInvariant()
    Write-Output "Applied exact two-byte skinner assertion patch"
    Write-Output "patched.sha256=$patchedSha256"
    Write-Output "restore.command=tools\stereo\set-skinner-assert-patch.ps1 -Action Restore"
    exit 0
}

if (-not (Test-Path -LiteralPath $backupPath -PathType Leaf)) {
    throw "Pristine backup not found: $backupPath"
}
$backupSha256 = (Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).
    Hash.ToLowerInvariant()
if ($backupSha256 -ne $originalSha256) {
    throw "Pristine backup has unexpected hash: $backupSha256"
}
if ($state -ne 'patched') {
    throw 'Darktide.exe is not in the expected patched state'
}

$reversed = [byte[]] $bytes.Clone()
foreach ($offset in $offsets) {
    $reversed[$offset] = $originalByte
}
if ((Get-BytesSha256 $reversed) -ne $originalSha256) {
    throw 'Patched executable differs from the known build beyond the two patch bytes'
}
Copy-Item -LiteralPath $backupPath -Destination $gamePath -Force
Write-Output 'Restored pristine Darktide.exe from verified backup'
