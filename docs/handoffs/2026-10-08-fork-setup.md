# Session handover: 8 October 2026 (fork setup on a new PC)

Branch `claude/windows-build-setup-2026-10-08`, from `main` at `e2861b5`
(the 0.3.0-alpha.1 release state). First session on a new owner's fork and
a new Windows PC. The brief for this pass: clone, install dependencies, build
and test, and **stop short of the Darktide installation** -- nothing was
synced, installed, launched or read from the game folder, and no headset or
runtime was touched.

## The machine

Windows 11 Pro 10.0.26200, RTX 5090 (driver 591.44), Python 3.12.10. The
previous project history ran on an RTX 4090 with a Quest 3 through Virtual
Desktop; this owner's headset has not been confirmed yet, and if it is a
Steam Frame, [STEAMVR-STEAM-FRAME.md](../STEAMVR-STEAM-FRAME.md) is the entry
point and the SteamVR branch of the readiness preflight applies.

None of the previous owner's infrastructure exists here: no Forgejo, no N150,
no `minipc`/`forgejo-n150` SSH aliases, no `github` remote.
[PROJECT-INFRASTRUCTURE.md](../PROJECT-INFRASTRUCTURE.md) describes the old
setup, not this one. Remotes: `origin` is the owner's fork
(`github.com/r0ot/darktide-vr`); `upstream` is the original
(`github.com/Brobert-in-aus/darktide-vr`), fetch-only (push URL `DISABLED`).
At setup the fork's `main` equalled upstream's; upstream's
`codex/alpha-4-2026-09-14` is one docs-only commit ahead (the release
record). The headset is a Steam Frame.

## What was installed

| what | how | version |
|---|---|---|
| CMake | `winget install Kitware.CMake` | 4.4.4 (not used for the build) |
| VS 2022 Build Tools | winget, `VCTools` workload with recommended + `VC.CMake.Project` | 17.14.41 |
| CMake used for the build | bundled with the Build Tools | 3.31.6-msvc6 |
| Windows SDK | with the Build Tools | 10.0.26100.0 |
| Graphics Tools (D3D12 debug layer) | `Add-WindowsCapability ... Tools.Graphics.DirectX~~~~0.0.1.0`, elevated | -- |
| numpy, Pillow | `requirements-analysis.txt` | 2.2.5, 12.3.0 |
| capstone, pefile (unpinned, optional) | pip | 5.0.9, 2024.8.26 |
| LuaJIT validator | `tools/lua/build-luajit.ps1` | `24c20c9`, sha256 `577817FB...E0EB` |
| DXC runtime | `tools/dependencies/get-dxc-runtime.ps1` | v1.8.2505.1, digest verified |

The bundled CMake path, for the commands in the release checklist:
`C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin`
(the checklist's path names `Community`; this machine has `BuildTools`).
CMake 4.x was installed first and left on PATH, but MinHook's
`cmake_minimum_required` is below what 4.x accepts without
`CMAKE_POLICY_VERSION_MINIMUM`, so the bundled 3.31 is the one to use.

## Two things that cost time, worth knowing

1. **The D3D12 debug layer is a Windows optional feature, and the README did
   not say so.** Without Graphics Tools, `D3D12GetDebugInterface` returns
   `0x887A002D` (`DXGI_ERROR_SDK_COMPONENT_MISSING`) and three tests fail:
   `d3d12_debug_resize`, `billboard_draw_readback` and `stereo_input_copy`
   (the last as `0xc0000409`). The README's build section now names it.
2. **An agent shell may set `NoDefaultCurrentDirectoryInExePath=1`.** Claude
   Code's shells do. With it set, `cmd` will not run a program from the
   current directory, so `build-luajit.ps1` fails with "'msvcbuild.bat' is
   not recognized" (and LuaJIT's own `minilua`/`buildvm` steps would fail the
   same way). Clear it for the build: `Remove-Item
   Env:NoDefaultCurrentDirectoryInExePath`. An ordinary user shell does not
   set it; the script is unchanged.

## Validation

- `cmake --preset windows-vs2022 -DDARKTIDEVR_ENABLE_HEADSET_TESTS=OFF`:
  configured (one upstream deprecation warning from MinHook's CMakeLists).
- `cmake --build --preset windows-vs2022-release`: clean, no warnings under
  `/WX`. Outputs: `build\windows-vs2022\src\producer\Release\darktidevr_native_capture.dll`,
  the `d3d12.dll` proxy, and
  `build\windows-vs2022\tests\xr_harness\Release\darktidevr-xr-harness.exe`.
- `ctest --test-dir build/windows-vs2022 -C Release --output-on-failure`
  (serial): **282 of 282 pass** in 103 s, after Graphics Tools. Parallel
  (`-j 8`) also passed everything except the three debug-layer tests before
  Graphics Tools; serial is the validated mode. The release checklist
  recorded 283 on `a5dae4c`; the one-test difference is unexplained and not
  a failure (the three headset tests are off here, and `engine_unwind_ownership`
  is registered and passing).
- `tools/stereo/test-darktide-lua-source.ps1`: `lua_source_check=pass`,
  103 chunks.
- Logs: ignored `artifacts/local/build-release.log` and
  `artifacts/local/ctest-release.log`.

## Not done, by instruction

No `sync-darktide-vr-dev.ps1`, no shortcut, no package install, no
`Darktide VR Mode.bat`, no readiness preflight, no launch. The next step
toward the game is the owner's call: which Darktide install
(`-GameRoot` if more than one), whether DMF and the mod loader are already
present ([SOLOPLAY-SETUP.md](../SOLOPLAY-SETUP.md),
[DEPLOYMENT-TRANSACTIONS.md](../DEPLOYMENT-TRANSACTIONS.md)), and which
headset and runtime (the preflight refuses anything but VDXR and SteamVR).

## Steam Frame, first pass (no headset run yet)

The owner chose to start from [STEAMVR-STEAM-FRAME.md](../STEAMVR-STEAM-FRAME.md)
before the Darktide installation. SteamVR is already the registered OpenXR
runtime here (`steamxr_win64.json`, the only one available). The Frame
bindings were rechecked against Valve's post-launch page and match; bring-up
steps 1 to 3 are now `tools/stereo/probe-steamvr-frame.ps1`, which refused
correctly with SteamVR stopped. Its first real run waits on SteamVR being
started with the Frame awake and the controllers in hand.

## Open questions for the owner

- Whether the previous owner's standing brief (no public missions, new
  features default off, unattended-launch rules, the worn checklist as the
  acceptance gate) carries over as-is.
