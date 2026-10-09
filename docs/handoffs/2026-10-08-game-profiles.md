# Session handover: 8 October 2026 (game folder profiles, VR unblocked)

Branch `claude/game-profiles-2026-10-08`, stacked on
`claude/steamvr-session-settings-2026-10-08`. Pushed to `origin`.

## What the owner asked for

A safe way to manage the game folder: capture the 2D setup (42 mods) exactly,
switch between "vanilla + VR" and "no VR + all my mods" at will, combine them,
and grow VR from vanilla a mod or a batch at a time. The 2D profile is
**living**: the owner updates mods by hand from Nexus, adds and disables
them, and re-patches the loader after game updates with
`toggle_darktide_mods.bat`, and all of that must keep working.

## What was built

[GAME-PROFILES.md](../GAME-PROFILES.md) is the guide. In short:
`tools/profiles` (Python, stdlib only; `Darktide Profiles.bat` menu), a vault
at `%LOCALAPPDATA%\DarktideProfiles`, Steam's depot manifests as the
definition of vanilla, profiles `2d`, `vanilla`, `vr`, `vr-mods`,
`vr-custom`, journaled switches with rollback and `recover`, and absorption
of the user's own changes into the active profile.

## Findings about the install (8 October)

- **AML** (Auto Mod Loader, in `mods\base\mod_manager.lua`) loads every mod
  folder and ignores `mod_load_order.txt`: a folder's presence is what enables
  a mod. The VR mod's own mode switch only edits the load order, which AML
  ignores.
- The 7 October update had reset the loader patch; mods were not loading.
  `switch 2d` re-patched it; the owner played and confirmed all mods.
- `bundle_database.data.bak` was the 29 September build's database. I first
  called the toggle `.bat` a trap; the owner pushed back, rightly: `--toggle`
  only patches when unpatched, writing a fresh `.bak` first, so their routine
  never reads a stale one.
- `Power_DI` ships its `.git` folder with read-only objects; the first round
  trip failed on one, rolled back, and the rollback reported itself incomplete
  (it tried to rewrite read-only files it never removed). Fixed and recovered;
  the round trip is now byte-identical, attributes included.
- **The game now ships NVIDIA Streamline 2.12 (DLSS 310.7)**, updated 29
  September; the VR mod mirrors the Streamline 2.7.30 ABI and only logs the
  version. Streamline appends fields in new struct versions, so the reads are
  probably still sound, but this is the first thing to read in the first VR
  launch's log (`MODULE ... version=` lines, and any frame generation error).
- The release package could not be built from a fresh clone: the `bin/`
  ignore rule hid its two empty flag files. Committed, with an exception.

## VR unblocked

`set-skinner-assert-patch.ps1` takes a table of builds now; build 25681127's
guards are at file offsets 0x7c8ee6 and 0x7c8fe2 (RVAs 0x7c98e6, 0x7c99e2,
0xfc apart as before), found by `tools/stereo/find-skinner-assert-sites.py`
and confirmed in the disassembly. On a copy of the exe: Apply changes exactly
those two bytes, Restore is byte-identical. The vault's VR component is
re-imported from a package built at `399c37b`, and `plan vr` is complete.

## Validation

- 285/285 CTest before the read-only fix; `game_profiles` is 16 scenarios.
- Real install: init, `switch 2d` (played, confirmed), the round trip
  `2d -> vanilla -> 2d` byte-identical in 37 s.

## Next

The first VR launch: `switch vr`, SteamVR running with the Frame, launch
through Steam. Expect to find out whether the mod's Lua hooks and native
Streamline reads survived the two game updates since 19 September.
