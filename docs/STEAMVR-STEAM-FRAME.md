# SteamVR and Steam Frame readiness

Investigation, 17 September 2026. The project has only ever run against
Virtual Desktop's VDXR runtime on a Quest 3. A Steam Frame arrives within the
week; Virtual Desktop is not expected to support it at once, so the first
session will use **SteamVR as the OpenXR runtime**, with the headset streaming
over its own 6 GHz adapter. This note records what is already runtime-neutral,
what will break or degrade, and the order in which to fix it.

Nothing here has been measured: no SteamVR runtime, no Frame and no Windows
machine were available to this investigation. Every claim about the codebase is
cited to source; every claim about SteamVR or the Frame is cited to public
documentation and must be confirmed by the first live run.

## 8 October: a Frame on the desk, and what was rechecked first

The project now has an owner with a Steam Frame (the Frame shipped on
22 September) and an RTX 5090. Before its first run:

- **The binding set was checked against Valve's published profile after
  launch** ([Steam Frame Input](https://partner.steamgames.com/doc/steamhardware/steamframe/input),
  read 8 October). Every one of the 20 Frame paths in `src/xr/main.cpp` is
  spelled as Valve lists it. The face-button positions agree with the
  comment: Valve says Touch's top button maps to the Frame's "Dpad Left,
  Dpad Up, Dpad Right, or X, Y, B", so on the right hand A is the bottom
  button and Y the top, and on the left `dpad_down` is the bottom and
  `dpad_up` the top. The shoulder/bumper question below is settled as
  `bumper` (`bumper/click`, `bumper/touch` on both hands) and stays unbound.
  The fallback order Valve states is the one below: generic controller,
  then Touch.
- **Bring-up steps 1 to 3 are one script**:
  `tools/stereo/probe-steamvr-frame.ps1`. It refuses unless SteamVR is the
  registered runtime and `vrserver` is alive, runs the viewer alone for a
  bounded session (default 2700 XR frames, about 30 s at 90 Hz, with
  `--require-openxr --require-rendering`), and writes the full log and a
  `summary.json` of the lines the bring-up order asks to record to
  `artifacts/steamvr-probe/<UTC>/`. No game, no mod and no Darktide folder
  are involved. `-NoSimpleProfile` and `-EyeExtent WxH` pass through to the
  viewer. The controller lines need tracked hands, so the controllers must be
  held and moved during the run.
- **Item 3's pinned extent exists in the viewer** (`--eye-extent WIDTHxHEIGHT`,
  `e5471d2`, 18 September) but not yet in the launcher.

### The first Frame runs (8 October, worn, viewer only)

Three probe runs, SteamVR 2.17.10, the Frame on the owner's head; summaries
under `artifacts/steamvr-probe/` (ignored). Bring-up steps 1 to 3 are
**done** and every one came back the better way:

| question | answer |
|---|---|
| D3D12 | `XR_KHR_D3D12_enable=available`; session, swapchains and 2700 of 2700 frames submitted, `not_rendered_frames=0` |
| OpenXR version | SteamVR refuses a 1.1 instance; the viewer's existing retry takes 1.0 (`openxr.api_retry=1.0`). The loader prints two `xrCreateInstance failed` lines first: harmless |
| extent | `recommended_size=3244x3244` per eye, which is the 2160 panel at SteamVR's 150 per cent render resolution. Both eyes of Darktide at that size is the item 3 problem, in numbers |
| refresh | `last_display_period_ms=6.944`, so the Frame was at 144 Hz (experimental; SteamVR's `preferredRefreshRate`). SteamVR's own stats for the run: 2705 presents, 0 dropped, 0 reprojected. Set 90 Hz before the first game run; see the streaming-rate result of 18 September |
| extensions | 41 enumerated (read through the loader, no instance). Of note: `XR_FB_display_refresh_rate` (the session can ask for its own rate), `XR_EXT_user_presence` (a candidate for the open "what keeps a Frame awake" question), `XR_KHR_visibility_mask`, `XR_META_recommended_layer_resolution`, `XR_EXT_eye_gaze_interaction`. **Not** `XR_EXT_frame_synthesis` or `XR_FB_space_warp`, though both names are in the runtime DLL |
| app key | SteamVR files the viewer under `system.generated.openxr.darktidevr harness.darktidevr-xr-harness.exe` (the application name plus the executable), which is the key any per-application SteamVR setting for the mod lives under |
| floor | `floor_space=stage` |
| layers | `max_layer_count=16`, swapchains to 8192x8192; no rounding line, 3 images each |
| controllers | **both hands bound `/interaction_profiles/valve/frame_controller_valve`**: the native profile, not Touch emulation. All twelve controls delivered (held-frame counters nonzero for trigger, squeeze, primary, secondary, stick click and menu on both hands; sticks changed on both) |
| canted views | **not canted**: no `openxr.canted_views` line in 2700 stereo frames, so the two eye orientations never differed by 0.1 degrees. Item 5 closes with no change; the eye-0 head orientation is correct on the Frame |
| field of view | per eye, radians left/right/up/down: eye 0 `-1.0285, 0.8824, 0.8611, -1.0494`; eye 1 `-0.8797, 1.0304, 0.8670, -1.0429`. About 109.5 degrees each way, wider outward (58.9 against 50.5) and **downward** (60.1 against 49.3). The recentred symmetric projection handles both; its displacement is about 4.2 degrees horizontally and 5.4 vertically, both eyes pitched down by the same amount |
| grip to aim | `aim_from_grip` on the Frame profile: a 42.8 degree rotation about the hand's X axis (`q=0.365,0,0,0.931`) and an offset of `(±0.0158, -0.0346, 0.0738)` m, mirrored between hands. The mod's hand, weapon and holster offsets were tuned on Touch, so expect them to sit off until checked worn in the game |

Three things the runs found and fixed in the viewer or the probe:

1. **`xrGetCurrentInteractionProfile` reads `<null>` when the hands first
   track.** The profile was bound 1.2 s later, announced by
   `XR_TYPE_EVENT_DATA_INTERACTION_PROFILE_CHANGED`, which the viewer did not
   handle, so the one `controller_profile` line could never answer step 3.
   It now logs `openxr.interaction_profile_changed hand= profile=` and writes
   the geometry line again against the bound profile.
   **The delay was the dashboard, not the runtime.** The owner was wearing
   the headset with SteamVR's dashboard and desktop view open and dismissed
   them with the Steam button once the probe appeared. SteamVR's own log
   (`Steam/logs/vrserver.txt`) shows it: the app became the scene app at
   14:36:13.8, and at 14:36:14.99 the driver dropped
   `SystemBehaviorFlag_LaserMouse` and
   `SystemBehaviorFlag_DriverRequestsApplicationPause`, the moment the hands
   were handed over. **The OpenXR session state said `FOCUSED` the whole
   time**: on SteamVR the session state does not reveal the dashboard, and a
   `<null>` interaction profile does. Anything that waits for input focus
   (the launcher, an unattended run) must not take `FOCUSED` as proof that
   the controllers are the game's.
2. **Only the sticks were counted.** `openxr.controller_<hand>_held_frames`
   now counts held frames for trigger, squeeze, primary, secondary, stick
   click and menu.
3. **The debug layer fails a theatre run that rendered every frame.**
   SteamVR's runtime calls `ID3D12CompatibilityDevice::ReflectSharedProperties`
   on the theatre's quad images ("Resource provided was not shared by
   D3D11"); the viewer never makes that call. The probe runs the theatre loop
   without `--debug-layer`. `xr_theatre_smoke` and `xr_stereo_sbs_smoke` would
   fail the same way on SteamVR if the headset tests were enabled.

The plain smoke (`-Mode Synthetic`, the preflight's) never syncs actions, so
it has no controller lines at all, and the view measurements (canted views,
runtime FOV) only run in the stereo path. The probe's default is therefore
`-Mode Stereo` (`--theatre --stereo-sbs`), which answers all of steps 1 to 3
in one run. `runtime_ipd_metres` stays 0 without the game: it is filled only
when the head pose is published to it.

**Next, from the bring-up order: step 4, deploy the mod and launch.** Before
that: SteamVR at 90 Hz and a render resolution well under 150 per cent,
SteamVR motion smoothing off for the first sessions (item 6), and a worn
check of hand and weapon alignment on the Frame profile. The first three are
now automatic; see the next section.

### Session settings and the dashboard, automatic (8 October)

The owner asked for the refresh rate and resolution to be set when the mod
launches and put back afterwards, without visiting SteamVR's menus. Built on
branch `claude/steamvr-session-settings-2026-10-08`, worn-verified on the
Frame. Each is a flag in the mod folder, one level above the viewer's `bin`,
read at every start because the shipped viewer is started by the native
module with a fixed command line; an argument of the same name wins:

| flag | argument | effect |
|---|---|---|
| `darktidevr_refresh_rate.flag` (`90`) | `--refresh-rate HZ` | SteamVR's `steamvr/preferredRefreshRate` for the session, put back afterwards |
| `darktidevr_motion_smoothing.flag` (`off`) | `--motion-smoothing on\|off` | SteamVR's `steamvr/motionSmoothing` likewise |
| `darktidevr_eye_extent.flag` (`2160x2160`) | `--eye-extent WxH` | the viewer's own render and swapchain extent; SteamVR's settings untouched |
| `darktidevr_hide_dashboard.flag` (`on`) | `--hide-dashboard` | closes SteamVR's dashboard when the session is shown |

All four are `PersistentModFlags` in `tools/unattended/xr-readiness.ps1`.

**Why SteamVR's settings and not OpenXR.** SteamVR enumerates
`XR_FB_display_refresh_rate` but lists only the rate it is running at
(`display_refresh_rates=144.009`), so a request for 90 is not offered. Its
own setting is applied live: the Frame went 144 -> 90 -> 144 Hz in about
0.2 s each way (`vrlink: SendUpdatedFramerateRequest` in `vrserver.txt`).
The viewer still makes the FB request when the runtime offers the rate, which
may matter on other runtimes.

**How.** `src/core/steamvr_session_settings` holds the logic: read the user's
values, write `%LOCALAPPDATA%\DarktideVR\steamvr-session-backup.txt` first,
then set; restore and delete afterwards; a backup found at the next start is
restored before anything else, so a killed game is undone by the next launch;
a failed set rolls back. `src/xr/openvr_settings` reaches SteamVR through
SteamVR's OWN `bin\win64\openvr_api.dll` as a background application (it
never starts SteamVR), with the few flat-API signatures declared in the
project, as the Streamline ABI is. The viewer runs that in a child of itself,
`darktidevr-xr-harness --steamvr-settings apply|restore`, so OpenVR and the
OpenXR runtime never share a process; `restore` is also the manual recovery.
The dashboard is closed with SteamVR's own `bin\win64rcmd.exe
--hidedashboard`.

**Two things the worn runs found.**

1. A session created 50 ms after SteamVR saved a new rate failed with
   `XR_ERROR_RUNTIME_FAILURE` (the restore on the way out still ran). The
   settings now go in before the OpenXR instance when SteamVR is running, or
   just after it when the instance had to start SteamVR, and a rate change
   settles 2 s before the session (`steamvr_settings.settle_ms=2000`).
2. With the dashboard and desktop view up the whole time, SteamVR held the
   session at `VISIBLE` and never sent `FOCUSED`, where earlier runs had
   reported `FOCUSED` behind the dashboard. Either can be the last state, so
   the dashboard is closed on whichever comes first. Worn: VISIBLE, the hide
   0.3 s after SteamVR made the probe its scene app, FOCUSED, the Frame
   profile bound and the hand tracked 2656 of 2700 frames with no button
   pressed.

Worn evidence, 8 October (the probe, `-RefreshRate 90 -MotionSmoothing off`):
`steamvr_settings.apply=ok refresh_rate=144->90 motion_smoothing=1->0`,
`display_refresh_rates=90 current=90`, `result=pass`,
`steamvr_settings.restore=ok refresh_rate=144 motion_smoothing=1`. Killed
mid-session: 90/off with the backup on disk; `--steamvr-settings restore`
put back 144/on and removed it. A restore overwrites a change the user made
in SteamVR during the session; that is the price of putting the rest back.

Not done: an in-game options entry for these (the Lua mod writes flags the
same way for the crosshair scale), and a script to write the flags into an
installed mod folder, which waits for the first install.

## What the headset changes

- The PC side is ordinary SteamVR. Valve documents the Frame as maintaining
  "full compatibility with SteamVR and OpenXR"; the headset creates a
  point-to-point 6 GHz link that SteamVR on the PC joins through the bundled
  adapter. So the target is the SteamVR OpenXR runtime
  (`steamxr_win64.json`), not a new streaming runtime of its own.
- Panels are 2160x2160 per eye, refresh options from 72 Hz to 120 Hz with
  144 Hz marked experimental, about 110 degrees, pancake lenses. SteamVR's
  recommended eye extent will be the panel resolution plus its distortion
  oversampling and the per-application render-resolution setting, so it will
  be materially larger than the Quest 3 through VD.
- Controllers are Frame controllers. By Valve's documentation they are
  presented to applications as **emulated Oculus Touch** unless the
  application enables `XR_VALVE_frame_controller_interaction` and suggests
  bindings for `/interaction_profiles/valve/frame_controller_valve`.
  SteamVR's fallback order for an application with no Frame binding is:
  Frame binding, then an OpenXR generic-controller binding, then an Oculus
  Touch binding, remapped to the Frame layout.
- Foveated streaming is applied in the encoder from eye tracking. It needs
  nothing from the application.

## What already ports without change

The OpenXR viewer is written against the core specification, not against VDXR:

- One graphics binding, D3D12, enabled from the enumerated extension list
  (`src/xr/main.cpp:212`, `src/xr/main.cpp:227`). SteamVR exposes
  `XR_KHR_D3D12_enable`; the first run must confirm it in the probe output.
- `XR_EXT_frame_synthesis` and `XR_FB_space_warp` are probed and logged only
  (`src/xr/main.cpp:216`-`src/xr/main.cpp:225`). Neither is used for
  submission, so SteamVR's lack of them costs nothing. The mod's generated
  frames come from the game's own DLSS frame generation, not from the runtime.
- `LOCAL`, `VIEW` and, when enumerated, `STAGE` spaces
  (`src/xr/main.cpp:362`-`src/xr/main.cpp:389`). SteamVR provides all three;
  the standing calibration keeps its floor reference.
- Colour formats are chosen from the runtime's own enumeration, preferring
  8-bit sRGB (`src/xr/main.cpp:5273`-`src/xr/main.cpp:5282`). SteamVR's D3D12
  colour list contains those formats.
- Asymmetric per-eye frusta are already handled generally: the submitted views
  are recentred to a symmetric frustum with a per-eye orientation offset
  (`src/core/xr_math.cpp`, `recentered_symmetric_projection`;
  `src/xr/main.cpp:4128`-`src/xr/main.cpp:4167`). A runtime whose optical
  centres differ from VDXR's needs no new maths.
- The eye extent follows the runtime recommendation and is republished to the
  game, which replaces its bootstrap 2112x2304 with the live extent
  (`src/xr/main.cpp:1610`-`src/xr/main.cpp:1614`;
  `mods/.../darktidevr.lua:2739`-`2746`). Resolution adaptation is already
  dynamic; only its cost is new.
- The one Virtual Desktop-specific call in the viewer, reading LibOVR's last
  error after a swapchain failure, is guarded by `GetModuleHandleW` and is
  simply skipped under another runtime (`src/xr/main.cpp:5305`).
- The native producer, the Lua mod and the launcher contain no runtime-specific
  code paths; the only "SteamVR" reference in the mod is a log of the engine's
  absent VR namespace (`mods/.../darktidevr.lua:649`).

## What needs work, in priority order

### 1. Controller bindings — the one true blocker — DONE (18 September)

Valve's own documentation turned out to be richer than the summary below, and
corrected it twice: **the left controller has four face buttons**, spelled
`dpad_up/down/left/right`, and **the left hand does have a menu button** --
it is spelled `view/click`, while the right hand's is `menu/click`.

What shipped: `XR_VALVE_frame_controller_interaction` probed and enabled from
the enumerated list (the enabled-extension array is a built vector now), a full
20-binding set for `/interaction_profiles/valve/frame_controller_valve`, and a
21-binding set for `/interaction_profiles/valve/index_controller`. The Frame
face buttons are mapped by POSITION so Touch muscle memory carries over: bottom
is primary (Touch X/A), top is secondary (Touch Y/B) -- on the Frame's right
hand the top button is spelled `y`, where Touch puts `b`. `menu` is read from
the LEFT hand only (`core/gameplay_input.cpp`), so left `view/click` keeps
today's behaviour rather than moving the menu to the other hand.

Index has no menu button and `system/click` is runtime-reserved, so its menu is
the left trackpad's force press -- SteamVR's binding editor can move it.

Neither new profile can stop the session: both are suggested through a
non-fatal helper that logs `openxr.interaction_profile.<name>=suggested` or
`=rejected result=<n>`, because a path that differs from the documentation by a
character rejects the whole call, and a viewer that then refuses to start is
worse than one that comes up emulated and says so. `--no-simple-profile`
withholds the generic binding SteamVR prefers.

`tests/tooling/test-interaction-profiles.py` holds the contract from source,
since no runtime here can: a Frame or Index session must not reach the game
with fewer actions, on fewer hands, than a Touch session, and every component
path must be one the vendor documents.
`tools/lua/mutate-interaction-profiles.py` proves it catches all of that.

*The original analysis follows.*

#### The original analysis

The viewer suggests exactly two profiles: `oculus/touch_controller` with the
full gameplay set, and `khr/simple_controller` with select and menu only
(`src/xr/main.cpp:4941`-`src/xr/main.cpp:4970`). Against SteamVR that is a
risk rather than a certainty: SteamVR looks for a generic-controller binding
**before** a Touch binding, and this application offers one. If SteamVR
auto-rebinds from the simple profile, the session comes up with select and
menu and no trigger, squeeze, thumbstick or face buttons — the failure mode
already described for non-Touch controllers in the user guide, now reachable on
the Frame.

Work:

- Enable `XR_VALVE_frame_controller_interaction` when the runtime enumerates
  it, and suggest a full binding set for
  `/interaction_profiles/valve/frame_controller_valve`. The enabled-extension
  array at `src/xr/main.cpp:227` is a fixed single entry and must become a
  built vector; `xrSuggestInteractionProfileBindings` for a profile whose
  extension is not enabled fails with `XR_ERROR_PATH_UNSUPPORTED`, so the
  suggestion must be conditional on the same probe.
- Frame paths, from Valve's profile: `trigger/value`, `squeeze/value`,
  `thumbstick` and `thumbstick/click`, `a`/`b` (right), `dpad_*` (left),
  `menu/click` (right) and `view/click` (left), `system/click`, `output/haptic`,
  plus a shoulder/bumper click whose component name changed from `bumper` to
  `shoulder` in a SteamVR update — do not make the binding set depend on it
  until it is read from the live runtime.
  Note that the left hand has no X/Y: the current left-hand primary and
  secondary bindings (`x/click`, `y/click`) have no direct Frame equivalent,
  and the menu button lives on the right hand, not the left as bound today.
  Decide the mapping deliberately rather than inheriting Touch emulation.
- Add `/interaction_profiles/valve/index_controller` while the binding code is
  open: it is cheap, it is the rest of the SteamVR ecosystem, and the Nexus
  page already promises those bindings as future work
  (`docs/NEXUS-PAGE.md:73`).
- Keep the simple profile for genuinely generic runtimes, but add a switch to
  suppress it (`--no-simple-profile` or equivalent) so a degraded SteamVR
  session can be diagnosed in one run instead of a rebuild.
- Fallback that needs no code: SteamVR's per-application binding editor can
  rebind an OpenXR application by hand. Worth knowing on day one; not worth
  shipping as the answer.

Alignment follows from the same change. Emulated Touch supplies Touch grip and
aim poses, not Frame ones; Valve offers the native profile partly for
"Frame Controller specific offsets". Expect hand, weapon and holster alignment
to sit slightly off until the native profile is in use, and re-check the worn
calibration afterwards rather than tuning against the emulated poses.

### 2. Readiness and preflight tooling — DONE (18 September)

`Assert-XrReadiness` takes a runtime profile now, decided by
`Get-XrRuntimeProfile` from the active manifest's name:
`virtualdesktop-openxr.json` is VDXR, `steamxr_win64.json` is SteamVR, an empty
registration is `none` and anything else is `unsupported` and refused. VDXR
asks exactly what it did. SteamVR asks for the Steam manifest and a live
`vrserver`, and nothing else -- there is no Streamer, no ADB and no proximity
override on a Frame.

The preflight reads the runtime BEFORE the Quest section rather than after it,
which is what actually makes a Frame run possible: the transport resolution,
the proximity broadcast and the power dump are skipped, not failed, when
`$questExpected` is false. The summary carries
`openxr_runtime_profile` and `quest_expected` so a run says which path it took.

`validate-xr-readiness.ps1` covers both profiles and, in particular, that the
split does not leak in either direction -- a SteamVR session must not pass with
`vrserver` down because a Quest happens to be ready, and a VDXR session must
not pass with no Streamer because SteamVR happens to be running.
`test-preflight-device-inventory.ps1` gained five SteamVR cases asserting that
none of them rejects and none of them touches the Quest.
`tools/lua/mutate-xr-readiness.py` puts eight mutations through the gate.

`AGENTS.md`'s policy is updated, including that the proximity override is a
Quest procedure that does not apply to a Frame.

*The original analysis follows.*

#### The original analysis

`Assert-XrReadiness` hard-requires a running `VirtualDesktop.Streamer`, an
active runtime whose file is literally `virtualdesktop-openxr.json`, and an
ADB read of Quest power state (`tools/unattended/xr-readiness.ps1:58`-`72`,
called from `tools/unattended/invoke-unattended-preflight.ps1:94`-`155`). On a
Frame every one of those throws: there is no Streamer, the active runtime is
`steamxr_win64.json`, and there is no ADB.

Work: give the readiness gate a runtime profile — `VDXR` as today, `SteamVR`
requiring the Steam runtime manifest and a live `vrserver`/`vrmonitor`, with
the ADB power check replaced by the existing bounded XR smoke run as the
"headset is really there and rendering" proof. The fixtures in
`tests/tooling/validate-xr-readiness.ps1` and the device-inventory and
watcher tests need the same split. `AGENTS.md`'s "Live XR readiness" section
states the VD/VDXR/Quest requirement as policy and must be updated with it;
the Quest proximity-override procedure simply does not apply to the Frame, and
whatever keeps a Frame awake for unattended work has to be found separately.

### 3. Render cost at Frame resolution

The viewer renders and submits at exactly the runtime's recommendation, by
deliberate contract ("The application render and OpenXR swapchain extents
follow the runtime's exact recommendation", `src/xr/main.cpp:745`-`752`), and
the game's stereo render target is sized from it. `OutputLayoutRequest` has an
`eye_override` field (`src/core/output_layout.h:24`) but the production viewer
never calls `choose_output_layout`, so today the only lever is SteamVR's own
per-application render-resolution setting.

Two eyes of Darktide at Frame resolution, with DLSS super resolution and frame
generation on top, is a large step up from the tested Quest 3 setup even on a
4090. Before the first mission run, set SteamVR's render resolution well below
100% and raise it; and wire `eye_override` to a viewer option so the working
extent can be pinned from the launcher for repeatable comparisons instead of
drifting with a SteamVR setting. The existing evidence tooling compares only
like-for-like extents, so any performance comparison against the VD baseline
must state both extents or it means nothing.

### 4. Swapchain creation robustness — DONE except the alpha question (18 September)

- `sampleCount` is **requested as 1**, not inherited from
  `recommendedSwapchainSampleCount`. Every delivery path writes the image with
  `CopyTextureRegion`, which is invalid against a multisampled destination. A
  runtime recommending anything else now says so
  (`openxr.swapchain_sample_count.recommended=`) instead of producing a stream
  of D3D12 errors and no image.
- The **allocated extent is read back** from the enumerated images' `GetDesc()`
  and compared with what was requested. SteamVR is reported to round up to a
  multiple of four, and every copy and the submitted `imageRect` are built from
  the requested extent, so a silent rounding would place the eye image in a
  corner of a larger surface. `openxr.swapchain_extent_rounded` before the
  first frame.
- `XrSystemGraphicsProperties::maxLayerCount` is **read and logged**
  (`openxr.max_layer_count`), and `layer_count` is clamped to it. The
  specification's floor is 16 and this viewer submits at most 8, so it has
  never bitten -- but submitting more layers than a runtime accepts fails
  `xrEndFrame`, which stops the viewer, and the layers are appended in order of
  importance so the tail is what goes.
- **Premultiplied alpha stays open**: it is a worn observation, not something
  code can settle. Look at HUD and pointer edges on the first Frame session.

*The original analysis follows.*

#### The original analysis

- `sampleCount` is taken from `recommendedSwapchainSampleCount`
  (`src/xr/main.cpp:5296`). Every delivery path writes the swapchain image with
  `CopyTextureRegion` (for example `src/xr/main.cpp:3057`), which is invalid
  against a multisampled destination. Both current runtimes are expected to
  recommend 1; request 1 explicitly rather than depending on that.
- SteamVR is reported to round swapchain extents up to a multiple of four. The
  copies and the submitted `imageRect` are built from the requested extent, so
  read back `GetDesc()` on the enumerated images and log any difference before
  trusting the first frame.
- The equal-extent assertion (`src/xr/main.cpp:292`-`src/xr/main.cpp:299`)
  should hold on SteamVR; if it throws, that is the message to look for.
- Up to eight composition layers are submitted (`src/xr/main.cpp:4484`) and
  `XrSystemGraphicsProperties::maxLayerCount` is never read. Read it once and
  log it; drop the optional quads rather than failing `xrEndFrame` if a runtime
  ever offers fewer.
- Quad and UI-projection layers assume premultiplied alpha
  (`src/xr/ui_projection_layer.cpp:20`-`21`). Compositors differ here. Look at
  HUD and pointer edges on the first worn run before concluding the layer
  geometry is wrong.

### 5. Head orientation from eye 0 — MEASURED, not changed (18 September)

The head orientation is still the left eye's, and deliberately so: switching to
the `VIEW` reference space would move the recentre anchor on the one path that
is proven, to fix a fault no runtime here exhibits.

What is new is that the viewer now measures the thing the decision depends on.
The angle between the two located eye orientations is computed each frame and
reported once when it first exceeds a tenth of a degree:

```
openxr.canted_views degrees=<n> head_orientation_source=eye0 note=the_head_pose_should_come_from_VIEW_space
```

A Frame session that prints that line is the evidence the VIEW-space head pose
is needed, and by how much. A session that never prints it is the evidence it
is not, which is worth as much.

*The original analysis follows.*

#### The original analysis

The published head orientation is the left eye's orientation, with the position
averaged across both eyes (`src/xr/main.cpp:1505`-`src/xr/main.cpp:1519`). On
a headset with canted displays the left eye is rotated outward, and the
in-game camera would inherit that yaw; on the Quest 3 through VD the eyes are
parallel, so this has never shown. Log `openxr.runtime_fov.eye0/eye1` and
compare the two located view orientations on the first Frame session; if they
differ, take the head pose from the `VIEW` reference space, which is correct
for any optics and already created (`src/xr/main.cpp:372`).

### 6. Frame pacing, motion smoothing and diagnostics

Pair-driven pacing and the delivery cadence were tuned against VDXR, and the
unresolved "VDXR submission slowdown" has no meaning on SteamVR — it may be
absent, or replaced by something else. SteamVR's motion smoothing is a second
frame-synthesis stage on top of the mod's own generated frames; run the first
sessions with it off, then compare. The VDXR trace and application-statistics
tooling (`tools/stereo/summarize-vdxr-*.py`, `analyze-vdxr-wait-overlap.py`
and their tests) has no SteamVR counterpart: for SteamVR, the frame-timing
graph and `XR-FRAME-STAGE-TIMING` counters from our own viewer are the
starting evidence. Do not port the VDXR parsers speculatively.

### 7. User-facing text

`docs/USER-GUIDE.md:19`-`25`, `docs/NEXUS-PAGE.md:97`-`98` and
`tools/release/package-README.txt:13`-`15` all state VDXR on a Quest 3 as the
tested setup and warn that non-Touch controllers fall back to select and menu.
Those statements stay true until a Frame session passes; update them from
evidence afterwards, not in advance.

## Bring-up order for the first session

1. Set SteamVR as the active OpenXR runtime, start SteamVR with the Frame
   connected, and run the viewer's probe alone (no game). Record:
   `openxr.runtime_name`, the three extension lines, `openxr.stereo_views`,
   `openxr.recommended_size`, `openxr.swapchain_formats`,
   `openxr.floor_space`. This answers D3D12 support, extent and STAGE in one
   run and needs no mod deployment.
2. Run the bounded XR smoke with rendering required. A submitted layer proves
   session, swapchain and compositor before any game work.
3. Read `openxr.controller_profile` for both hands and the thumbstick and
   button counters in the same run. That single line says whether SteamVR
   chose Touch emulation or the generic profile, which decides whether item 1
   above is urgent or merely correct.
4. Only then deploy the mod and launch, and judge stereo delivery by the usual
   shared-stereo evidence summary rather than by "the headset showed
   something".
5. Expect to retune, not to be finished: alignment after the binding change,
   resolution against frame rate, and the cadence against SteamVR's own
   reprojection.

## Estimate

Items 1, 2 and 4 are a day of focused work each at most, and none of them needs
the headset to write — only to verify. Item 3 is a settings and measurement
exercise, item 5 is a one-line fix behind a live check, and item 6 is open
ended. The realistic reading is that a Frame session can be brought up in its
degraded emulated-Touch form on day one, and that a week of evenings turns that
into a supported second runtime.

## Sources

- Valve OpenXR Utilities (Steam Frame controller profile, Touch emulation):
  <https://github.com/ValveSoftware/Unity/blob/main/com.valvesoftware.openxr.utils/Documentation~/index.md>
- Steam Frame controllers and compatibility, Steamworks:
  <https://partner.steamgames.com/doc/steamhardware/steamframe/controllers>,
  <https://partner.steamgames.com/doc/steamframe>
- Steam Frame hardware summary: <https://en.wikipedia.org/wiki/Steam_Frame>,
  <https://vr-compare.com/headset/steamframe>
- SteamVR automatic rebinding: <https://www.uploadvr.com/steamvr-automatic-controller-rebinding-update/>
- SteamVR OpenXR D3D12 reports (formats, extent rounding, ignored usage bits):
  <https://steamcommunity.com/app/250820/discussions/3/4034725980849397816>

## The first game launches on the Frame (8-9 October, worn)

Seven launches through `tools/profiles` (`switch vr`), the owner in the
headset. None reached a visible VR image; each one removed a cause. In order:

| launch | what was seen | what the logs showed | change |
|---|---|---|---|
| 1 | Steam's theatre showed the launcher, then a frozen loading frame | viewer up, settings applied, dashboard hidden; two Lua hooks gone in the 7 October build; SteamVR left at 90 Hz after exit | guard process outside the game's job; flamer hooks only where the method exists |
| 2 | empty SteamVR world | 3,977 board captures uploaded, 0 failures, board submitted at 82-90 fps | captures forced opaque (GDI leaves alpha 0) |
| 3 | same | same | -- |
| 4 (SteamVR closed first) | SteamVR never started | `xrCreateInstance` fails from inside a game launch | SteamVR must already run |
| 5 (theatre setting off) | flat frozen loading frame; refresh flipping 90/144 | viewer crash-looping: a Steam "Properties" window has the game's title | the viewer captures its parent process's window |
| 6 | empty world | readback of the board layer shows the calibration menu, seated 2 m ahead | board seat logging, alpha statistics, opaque switch |
| 7 (board opaque) | empty world, not black | `board_opaque=1`, ~80 fps submitted | **SteamVR is not displaying the viewer's frames at all** |

Findings that hold regardless of the outcome:

- **Steam's Desktop Game Theatre.** Launching a flat Steam game while
  SteamVR runs puts it in a theatre (the frozen loading frame seen in every
  launch is Steam's capture of the game window, not the mod's board). The
  per-game Steam option in old guides is gone; SteamVR has a global one,
  Settings > Dashboard > "Present Non-VR Applications on Theater Screen Upon
  Launch" (`dashboard/autoShowGameTheater`). Turned off for launches 5-7.
- **SteamVR must already be running**: the viewer cannot start it from inside
  a game launch.
- **The viewer inherits the game's Steam identity.** Started by the game it is
  `steam.app.1361210` in `vrserver.txt`, the same key as the flat game, with
  Steam's `gameoverlayrenderer64.dll` injected; started alone (every probe
  that worked) it is `system.generated.openxr...`. Since `2eec2ce` the native
  module strips `SteamAppId`, `SteamGameId`, `SteamOverlayGameId` and
  `SteamClientLaunch` from the viewer's environment. **Untested worn**; it is
  the current explanation for launch 7.
- The settings guard runs inside a game session (its process was found
  waiting on the viewer) but wrote nothing to its log; with Steam's overlay
  injected that may be the overlay, and the environment change may settle it.
- SteamVR left at 90 Hz with smoothing off after launch 7 (SteamVR closed
  before a restore could run); the backup is kept and the next VR launch
  restores it first.

**Next launch:** SteamVR running first, the theatre setting off, `switch vr`,
Play. Read `vrserver.txt` for the viewer's app key (`system.generated...` is
the fix working), and whether the board shows.

## Launch 8 and the cause: the d3d12.dll proxy in the viewer (9 October)

Launch 8 (with `2eec2ce`) separated the identities: the SteamVR overlay
listed "Darktide VR Harness" as its own app beside the flat game, and
"Resume game" gave it focus. The headset still showed the empty world.

**Measuring it without the headset.** The OpenVR compositor reports the
process whose frame it last displayed (`IVRCompositor::GetLastFrameRenderer`)
and can dump what it composites (`CompositorDumpImages`, into
`SteamVR\screenshots`). `tools/stereo/probe-steamvr-compositor.py` polls the
first through SteamVR's own `openvr_api.dll` as a background app. With the
headset unworn a session stays SYNCHRONIZED and submits one frame, but one is
enough: an accepted viewer becomes the last renderer within a second.

The viewer was run with the game's own arguments (from `vrserver.txt`) and a
stand-in window titled "Warhammer 40,000: Darktide" owned by its parent:

| viewer | last renderer |
|---|---|
| installed copy, `mods\darktidevr\bin` | 0 (never) |
| the build's copy, same arguments and every settings flag | the viewer |
| copy of the installed folder | 0 |
| same copy with only `bin\d3d12.dll` removed | the viewer |

**Cause.** The package installs the viewer beside the mod's `d3d12.dll`
proxy (the profile tool copies it from there to `binaries\`). `d3d12.dll` is
not a known DLL, so the viewer's import loaded the proxy from its own
directory, and so did SteamVR's OpenXR runtime inside the viewer when it
shared the swapchains with its compositor. The proxy forwards only the eight
public exports. Every `xrEndFrame` succeeded and SteamVR displayed none of
them, which is launches 2, 3, 6, 7 and 8. Earlier runtimes (Virtual Desktop)
evidently never asked the proxy for more. The probes that worked ran from the
build directory, where there is no proxy.

**Fix (`8be28ac`).** The viewer delay-loads `d3d12.dll` and loads System32's
copy first thing in `wmain`; later loads by name return that module. It logs
`viewer.d3d12 loaded=1 system32=1 proxy_beside=1`. The installed copy beside
the proxy is now the last renderer within a second. The suite passes 285 of
285. Untested worn.

- The compositor dumps a 32x16 placeholder (`-raw.png`) whenever an app owns
  the scene, so the image dump cannot show the board; the last-renderer
  number is the measure. With no scene app it dumps the world (`-Layer.png`).
- A VR launch leaves about 1,700 diagnostic shader dumps in
  `mods\darktidevr\bin\billboard_pixel_shaders` and `blended_pixel_shaders`
  (written by the native module, never read back). The profile tool counts
  them as VR files and removes them on the next switch.

## Launch 9: the first worn play, a crash, and the frame rate (10 October)

The image showed, worn: the owner played the Psykhanium with controllers for
about five minutes at the first VR graphics (`presets/vr-5090-first.json`:
DLSS Balanced, frame generation and ray tracing off).

**Frame rate.** The viewer's live lines over the last minute: 50-65 fresh
stereo pairs a second (`interval_fresh_pair_fps`) against 90 Hz, with no
repeated pairs; SteamVR reprojected the rest. "Decent" worn, but no headroom
for a higher refresh rate yet. Whether the GPU or the CPU limits it is not
measured (the mod's profilers were off). Next settings: DLSS Performance and
LOD multiplier 1.5 (one for each), and an in-headset readout of the game's
rate (`darktidevr_frame_rate_display.lua`, flag `darktidevr_frame_rate_display.flag`).

**Crash** (Crashify `af62c02b-1ccc-4e52-bb45-1820602e4077`, console log
`console-2026-10-10-04.03.26-…`): access violation reading `FFFFFFFFFFFFFFFF`
at `Darktide.exe+0x47e1b6` on a worker thread, error context `ParticleSystem
#ID[fab3f9157509854c]`, during melee, 4 s after a weapon special, right after a
552 ms `RI::wait_for_fence` stall. The instruction loads an array pointer from
an object (`[rsi+0x78]`) and indexes it: the shape of an object freed under a
running update job. The job is `0x47de30` (fragment `0x47e180`), run by the job
system's `0x717960`; the author's particle map
([ENGINE-PARTICLE-STEREO-OWNERSHIP.md](ENGINE-PARTICLE-STEREO-OWNERSHIP.md),
an older build) places the particle update owner at `46b380`, nearby.

- It is the only engine crash in every console log since September; none of
  the owner's 2D sessions has it. One sample cannot say whether rendering two
  eyes per frame is the cause.
- The mod's own particle code (staff projectile, scanner hologram) was not
  active: melee only.
- Nothing upstream reports it. The minidump holds only the crashing thread's
  stack, so the main thread's work at the time is not recoverable.
- If it recurs: compare the RVA and error context, and note the weapon and
  what was on screen.
