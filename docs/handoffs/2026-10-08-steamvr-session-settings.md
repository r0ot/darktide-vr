# Session handover: 8 October 2026 (SteamVR session settings)

Branch `claude/steamvr-session-settings-2026-10-08`, stacked on
`claude/windows-build-setup-2026-10-08` (see
[the fork setup handover](2026-10-08-fork-setup.md)). Pushed to `origin`
(the owner's fork). Still nothing deployed into the Darktide installation.

## What the owner asked for

"When we start testing actual Darktide ... for those settings to be applied
automatically when launching the mod and put back when done ... it's a
little tedious to wander through config menus." The settings were the two
from the first Frame runs: SteamVR at 144 Hz (experimental) and 150 per cent
render resolution (3244x3244 per eye). Then: "didn't you mention there's a
way for you to dismiss the dashboard ... wouldn't you want to add that and
then do your tests?" Yes.

## What was built

Described in full in
[STEAMVR-STEAM-FRAME.md](../STEAMVR-STEAM-FRAME.md#session-settings-and-the-dashboard-automatic-8-october):
four flags (`refresh_rate`, `motion_smoothing`, `eye_extent`,
`hide_dashboard`) read by the viewer at every start; the refresh rate and
motion smoothing set in SteamVR's own settings with a backup written first
and restored at exit, or at the next start after a crash; the eye extent
pinned in the viewer; the dashboard closed with SteamVR's `vrcmd`.

The route was found by measurement rather than chosen up front:

1. `XR_FB_display_refresh_rate` first, since a runtime restores an
   application's rate itself. SteamVR enumerates the extension and offers one
   rate, the current one. Dead end on SteamVR; kept for other runtimes.
2. `vrcmd --settings-int` exists but its argument form is undocumented and it
   printed nothing for three guesses.
3. OpenVR's IVRSettings through SteamVR's own `openvr_api.dll`, from a Python
   ctypes script first: it read the live values and a live set moved the
   Frame 144 -> 90 -> 144 in about 0.2 s each way. That is what was built,
   in C++, behind a fake-store unit test.

## Worn runs, and what each changed

| run | outcome | change |
|---|---|---|
| FB request for 90 | `display_refresh_rates=144.009`, nothing changed | route 3 |
| ctypes set 90, probe, set 144 | probe saw 90; restore reached the headset | built it |
| viewer apply/restore, first | apply and restore fine, `xrCreateSession` `XR_ERROR_RUNTIME_FAILURE` 50 ms after SteamVR saved the rate | apply before the instance, 2 s settle |
| same, after the fix | pass; killed mid-session left 90/off and a backup; manual restore put 144/on back | none |
| dashboard close | session held at VISIBLE with the dashboard up, FOCUSED never came, so the hide never fired | hide on VISIBLE or FOCUSED |
| dashboard close, after the fix | hide 0.3 s after scene start, FOCUSED, Frame profile bound, hand tracked 2656/2700 with no button | none |

The owner's account of the dashboard behaviour was the key to the last two:
the desktop view and dashboard "stayed open the entire time", and in the
earlier probe runs they had dismissed it with the Steam button themselves.

## Validation

- Release build clean (`/W4 /WX`), 284 of 284 CTest pass serially, including
  the new `steamvr_session_settings` (backup round trip and refusals, crash
  recovery, cleared flags, unchanged values, rollback on a failed set,
  unreachable SteamVR, unreadable backup kept, failed restore retried) and
  the extended `xr_settings_flags` (flags applied, ignored and overridden;
  `--steamvr-settings` refuses bad requests before it connects).
- Worn on the Frame: the table above. The left controller tracked 0 frames
  in the last four runs (the right one tracked normally); most likely it was
  off or asleep, but it is unconfirmed.

## Open

- A script to write the four flags into the installed mod folder, with the
  first install. Suggested starting values: 90 Hz, motion smoothing off, the
  dashboard hidden, and an eye extent of 2160x2160 (SteamVR's 100 per cent
  for the Frame, per its own log line `HMD driver recommended: 2160x2160`).
- In-game options for the same flags, written by the Lua mod as the
  crosshair scale is.
- The 2 s settle is the gap that worked, not a measured minimum.
- `xr_theatre_smoke` and `xr_stereo_sbs_smoke` use `--debug-layer`, which
  SteamVR's runtime trips in the theatre loop; they would fail on a SteamVR
  machine if the headset tests were enabled.
