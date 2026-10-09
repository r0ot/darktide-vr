# Game profiles: 2D mods, VR, vanilla, and anything between

`tools/profiles` manages the Darktide game folder as a set of profiles and
switches between them safely. It exists because the owner plays 2D with 42
mods every day and needs to get back to exactly that at any moment, while VR
work starts from a near-vanilla game and adds mods back one at a time
(8 October 2026).

Run it from `tools\profiles`: double-click **`Darktide Profiles.bat`** for a
menu, or `python -m dtprofiles <command>`. Python 3.10+ only, no packages.
Darktide must be closed, and Steam must not be updating it.

## The profiles

| profile | loader | VR | mods | settings |
|---|---|---|---|---|
| `2d` | on | off | everything you had (42) | 2D |
| `vanilla` | off | off | none | 2D |
| `vr` | on | on | none | VR |
| `vr-mods` | on | on | all of `2d`'s, following it | VR |
| `vr-custom` | on | on | the ones you add | VR |

"Loader" is the Darktide Mod Loader with the Auto Mod Loader and the Darktide
Mod Framework, as installed. **AML loads every mod folder present** and ignores
`mod_load_order.txt`, so a profile decides which mods load by which folders
are in `mods\`.

Each profile names a **settings slot**. `user_settings.config` holds video,
audio, input AND every mod's settings, so 2D and VR keep separate copies; the
VR slot starts as a copy of the 2D one (mod settings included) the first time
it is used.

## Commands

    status                    what is installed, what changed since the last switch
    plan <profile>            what a switch would change; changes nothing
    switch <profile>          shows the plan, asks for "yes", applies, verifies
    mods list|add|remove|next|clear [names | count]   edit vr-custom (--profile for another)
    capture [--label]         a restore point
    captures                  the restore points
    restore-capture <id|first>  every managed file and the settings back exactly as captured
    recover                   roll back a switch that was interrupted (power cut, crash)
    vr-import <package.zip>   store the VR mod from a runtime package
    verify                    re-hash everything in the vault
    init                      the first capture; builds the profiles (done 8 October)

The incremental workflow: `switch vr` until VR works on its own; then
`mods next` (or `mods next 5`, or `mods add Power_DI`), `switch vr-custom`,
play, repeat. `mods remove <name>` takes one back out.

## What makes it safe

- **Steam's own manifests define vanilla.** Each installed depot's manifest in
  `Steam\depotcache` lists every file with its size and SHA-1 (243,519 files
  for build 25681127). Every file in the game folder is vanilla, modified or
  extra by lookup, not by guesswork, and the tool only trusts a copy of the
  bundle database or the executable as vanilla when its SHA-1 matches.
- **Every byte goes to the vault first.** `%LOCALAPPDATA%\DarktideProfiles`
  keeps content-addressed copies (by SHA-256, written atomically, read back and
  verified, never modified). Before a switch writes or removes anything, the
  current bytes are in the vault.
- **Switches are journaled.** The plan, with every file's before and after
  hash, is written before the first change; any failure rolls back from it;
  a journal left by a crash blocks everything except `recover`. Every written
  file is re-hashed in place.
- **Your changes are kept, not reverted.** Before a switch, whatever you did
  to the mods while a profile was active (updated a mod, added one, deleted
  one, changed mod settings) is absorbed into that profile and the vault, so
  switching back returns it exactly as you left it. The loader or the VR mod
  appearing or disappearing by hand is refused unless you pass `--absorb`.
- **Unmanaged files are never touched.** Anything the tool cannot attribute to
  the loader, a mod or the VR mod (your `New Text Document.txt`) is recorded in
  captures and left alone.
- **The first capture is the panic button.** `restore-capture first` puts
  every managed file and `user_settings.config` back to 8 October. A capture
  taken on another game build never writes that build's database or executable
  over the current one.
- **Refusals before changes.** Darktide or its launcher running, Steam
  updating, a missing vanilla copy, or the VR executable patch not supporting
  the build each stop the switch before anything is written.

## How the two in-place patches are handled

- **The bundle database** (`bundle\bundle_database.data`) is patched by the
  loader's own `tools\dtkit-patch.exe --patch`, run on a copy in the vault,
  never in place; the result is checked for `patch_999` and installed by the
  journal. A Steam update replaces the file with the new build's vanilla one,
  which is why mods stop loading after an update: switching to a modded
  profile patches the new one.
  The tool never calls `--unpatch` or the toggle `.bat`: dtkit-patch unpatches
  by renaming `bundle_database.data.bak` over the database, and the `.bak` is
  whatever build was patched last. On 8 October it was the 29 September
  build's database, from before the 7 October update. Modded profiles write
  the `.bak` as THIS build's vanilla database, so the toggle is safe again.
- **The executable** is patched by the VR mod's own
  `tools\set-skinner-assert-patch.ps1`, also on a copy. It knows exactly one
  build's hash; **build 25681127 is not supported yet** (`Unexpected byte 0x49
  at file offset 0x7a7586`), so every VR profile refuses until the patch is
  ported. The VR proxy `binaries\d3d12.dll` is the VR package's own
  `bin\d3d12.dll`.

The VR component is imported from a runtime package
(`tools/release/build-runtime-package.ps1`), the archive a player would get,
with the SteamVR session flags added (`refresh_rate` 90, `motion_smoothing`
off, `hide_dashboard` on, `eye_extent` 2160x2160; see
[STEAMVR-STEAM-FRAME.md](STEAMVR-STEAM-FRAME.md)). Change one by editing the
flag in the game's `mods\darktidevr` folder while a VR profile is active; the
next switch absorbs it.

## Known limits

- Mod dependencies are not modelled: `mods next` adds by name order, and a mod
  that needs another (DMF extensions, for example) must be added with it.
- The VR settings slot is a copy of the 2D one from its first use; later 2D
  mod-setting changes do not flow into it.
- A switch takes about 15 s, most of it walking the 243,000-file game folder.
- The tool does not install or update mods or the loader: install them the
  usual way while a profile is active, and the next switch takes them in.

## On the real install (8 October)

- `init`: 617 files and both settings files captured in 14 s; 62 MB vault.
- `switch 2d`: the bundle database re-patched after the 7 October update
  (byte-identical to what `toggle_darktide_mods.bat` produces) and the `.bak`
  replaced with this build's vanilla database. The owner played: "ran
  perfect, just as I'd expect with all my mods".
- **The first round trip failed safely.** `Power_DI` ships its `.git` folder,
  git marks object files read-only, and Windows refused to delete one. The
  switch rolled back, but the rollback then tried to rewrite read-only files
  it had never removed and reported itself incomplete, leaving the journal;
  the game itself was unchanged. Fixed: writes clear and restore the
  read-only attribute (recorded per file, so `.git` objects come back
  read-only), and a write that would change nothing is skipped, so a rollback
  or `recover` can be repeated. `recover` then completed.
- The round trip `2d` -> `vanilla` -> `2d`, after the fix, in 37 s: in
  vanilla the only non-vanilla file was the owner's note, the database and
  executable matched Steam's SHA-1 and nothing vanilla was missing; back in
  2d all 617 managed files and `user_settings.config` were byte-identical to
  before, and Power_DI's 100 read-only `.git` objects were read-only again.

## Validation

`tests/profiles/test_dtprofiles.py` (CTest `game_profiles`) builds a fake Steam
install, with depot manifests in Steam's binary format, and a fake game folder,
then checks: capture; byte-exact round trips through vanilla; plans changing
nothing; absorbing an updated, added and deleted mod; VR profiles, settings
slots and incremental mods; an unsupported executable patch stopping before
any change; a failed write rolling everything back; a crash mid-switch blocked
until `recover` restores it; the loader appearing by hand being refused;
`restore-capture` being exact; a game update between switches (new database
patched, the old build's never restored); Steam updating blocking a switch;
read-only files switched out and back read-only; a rollback repeated.
