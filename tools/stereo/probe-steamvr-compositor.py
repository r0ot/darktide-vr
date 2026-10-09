"""Ask the SteamVR compositor which process it is showing.

Usage: python probe-steamvr-compositor.py SECONDS [dump]
Prints one line per change: scene focus pid, last frame renderer pid,
CanRenderScene, IsCurrentSceneFocusAppLoading. `dump` then asks the
compositor to write what it composites into SteamVR\\screenshots.

A viewer whose frames SteamVR accepts becomes `last_renderer` within a second
of its first submitted frame, worn or not; one that stays at 0 is submitting
frames SteamVR never shows (see docs/STEAMVR-STEAM-FRAME.md, 9 October).
Connects as a background app through SteamVR's own openvr_api.dll, using the
IVRCompositor_029 function table (slots from openvr_capi.h).
"""
import ctypes, sys, time, os

RUNTIME = r"C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin\win64\openvr_api.dll"
vr = ctypes.WinDLL(RUNTIME)
err = ctypes.c_int(0)
vr.VR_InitInternal2.restype = ctypes.c_uint32
vr.VR_InitInternal2.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int, ctypes.c_char_p]
vr.VR_GetGenericInterface.restype = ctypes.c_void_p
vr.VR_GetGenericInterface.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
vr.VR_ShutdownInternal.restype = None
vr.VR_InitInternal2(ctypes.byref(err), 3, None)  # VRApplication_Background
if err.value:
    sys.exit(f"init error {err.value}")
table = vr.VR_GetGenericInterface(b"FnTable:IVRCompositor_029", ctypes.byref(err))
if not table or err.value:
    sys.exit(f"compositor interface error {err.value}")
slots = (ctypes.c_void_p * 53).from_address(table)
fn = lambda i, res, *a: ctypes.CFUNCTYPE(res, *a)(slots[i])
focus = fn(24, ctypes.c_uint32)
renderer = fn(25, ctypes.c_uint32)
can_render = fn(26, ctypes.c_bool)
dump = fn(30, None)
loading = fn(47, ctypes.c_bool)

seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 5
last = None
end = time.time() + seconds
while time.time() < end:
    state = (focus(), renderer(), can_render(), loading())
    if state != last:
        print(time.strftime("%H:%M:%S"), "focus=%d last_renderer=%d can_render=%d focus_loading=%d" % state, flush=True)
        last = state
    time.sleep(0.25)
if len(sys.argv) > 2 and sys.argv[2] == "dump":
    dump()
    print("dumped", flush=True)
vr.VR_ShutdownInternal()
