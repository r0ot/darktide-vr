-- Frame-rate readout (10 October 2026; on while darktidevr_frame_rate_display.flag
-- says "on"): the game's frame rate beside the headset's refresh rate, low and
-- to the left in the view, so a graphics change can be judged in the headset.
-- Every game frame is one stereo pair for the viewer; the headset shows its
-- refresh rate regardless, SteamVR reprojecting the frames the game misses, so
-- the game's rate is the number that says whether a setting is affordable
-- (Steam Frame, 9 October: 50-65 pairs a second at 90 Hz in the Psykhanium).
--
-- Anchored to the eye rather than a hand, so it shows with keyboard and mouse
-- as with controllers. Drawn on the hand overlay's panel, in front of the
-- scene, like the wrist display's front test mode.
local Rate = {}

Rate.FLAG = "./../mods/darktidevr/darktidevr_frame_rate_display.flag"
Rate.REFRESH_FLAG = "./../mods/darktidevr/darktidevr_refresh_rate.flag"
-- Seconds per reading: long enough to be steady, short enough to follow a
-- fight starting.
Rate.WINDOW = 1.0
-- Where it sits, in metres along the eye's own axes: nearer than the HUD
-- panel (2 m by default) so it never hides behind it, and below and left of
-- the centre so it stays out of the aim.
Rate.AHEAD, Rate.DOWN, Rate.LEFT = 1.0, 0.30, 0.32
Rate.TEXT_SIZE = 0.028
-- Half the line's width at TEXT_SIZE ("120 fps / 144 Hz"), for the panel
-- scale (as Wrist.LAYOUT_HALF_WIDTH).
Rate.LAYOUT_HALF_WIDTH = 0.16
Rate.PIXEL_METRES = 0.0005
-- At or above GOOD of the refresh rate the game keeps up (green); below FAIR
-- SteamVR is filling most frames (red); between, amber.
Rate.GOOD, Rate.FAIR = 0.97, 0.75
Rate.GREEN, Rate.AMBER, Rate.RED, Rate.WHITE = {120, 230, 120}, {240, 200, 90}, {240, 110, 100},
    {235, 235, 235}

local function finite(x) return type(x) == "number" and x == x and math.abs(x) < math.huge end

-- Whether a flag's text turns the readout on. Pure.
function Rate.flag_on(text)
    if type(text) ~= "string" then return false end
    local value = text:match("^%s*(%a+)%s*$")
    value = value and value:lower()
    return value == "on" or value == "enabled"
end

-- The refresh rate from darktidevr_refresh_rate.flag's text, or nil. Pure.
function Rate.parse_hz(text)
    local hz = type(text) == "string" and tonumber(text:match("^%s*([%d%.]+)%s*$")) or nil
    return finite(hz) and hz >= 30 and hz <= 360 and hz or nil
end

-- A frame counter over WINDOW-second readings. Pure.
function Rate.counter() return {frames = 0} end

-- Count the frame at time t (repeated calls at one t are one frame). Returns
-- the frame rate when a reading completes, else nil.
function Rate.observe(counter, t)
    if not finite(t) then return nil end
    if counter.last_t ~= nil and t < counter.last_t then
        counter.start, counter.frames, counter.last_t = nil, 0, nil  -- clock reset
    end
    if t == counter.last_t then return nil end
    counter.last_t = t
    if counter.start == nil then counter.start, counter.frames = t, 0; return nil end
    counter.frames = counter.frames + 1
    local elapsed = t - counter.start
    if elapsed < Rate.WINDOW then return nil end
    local fps = counter.frames / elapsed
    counter.start, counter.frames = t, 0
    return fps
end

-- Colour for a frame rate against the refresh rate. Pure.
function Rate.color(fps, hz)
    if not finite(fps) or not finite(hz) or hz <= 0 then return Rate.WHITE end
    local share = fps / hz
    if share >= Rate.GOOD then return Rate.GREEN end
    if share >= Rate.FAIR then return Rate.AMBER end
    return Rate.RED
end

-- The line shown. Pure.
function Rate.text(fps, hz)
    if not finite(fps) then return nil end
    local shown = string.format("%d fps", math.floor(fps + 0.5))
    if finite(hz) then shown = shown .. string.format(" / %d Hz", math.floor(hz + 0.5)) end
    return shown
end

-- Metres per panel pixel for an overlay cell this many pixels wide: the
-- line has to fit in one cell (darktidevr_hand_overlay). Pure.
function Rate.pixel_metres(cell_width)
    cell_width = tonumber(cell_width)
    if not cell_width or not (cell_width > 32) then return Rate.PIXEL_METRES end
    return math.max(Rate.PIXEL_METRES, Rate.LAYOUT_HALF_WIDTH / (cell_width * 0.5 - 10))
end

local function read_file(path)
    local io_api = Mods and Mods.lua and Mods.lua.io
    local file = io_api and io_api.open(path, "r")
    if not file then return nil end
    local text = file:read("*all")
    file:close()
    return text
end

function Rate.install(mod, presentation)
    local api = {visible = false}
    local counter = Rate.counter()
    local fps, hz, failed, logged = nil, nil, false, false
    local enabled, poll = false, 0
    -- Every 300 calls (about five seconds): an open per frame on the main
    -- thread is where these modules' spikes came from
    -- (docs/LUA-FRAME-PROFILE-2026-09-16.md). The flag can be changed while
    -- playing.
    local function refresh_flags()
        poll = poll - 1
        if poll > 0 then return enabled end
        poll = 300
        enabled = Rate.flag_on(read_file(Rate.FLAG))
        hz = Rate.parse_hz(read_file(Rate.REFRESH_FLAG))
        return enabled
    end
    -- Wall time, not game time: the game clock can be scaled.
    local function now()
        if Application and Application.time_since_launch then
            local ok, value = pcall(Application.time_since_launch)
            if ok and finite(value) then return value end
        end
        return Managers.time and Managers.time:time("main") or nil
    end
    function api.destroy()
        counter, fps, failed = Rate.counter(), nil, false
        api.visible = false
    end
    local function draw(game_world, unit)
        if not refresh_flags() then api.visible = false; return end
        local reading = Rate.observe(counter, now())
        if reading then fps = reading end
        if not fps or presentation.mode ~= 1 then api.visible = false; return end
        local eye, rotation
        if presentation.eye_pose then eye, rotation = presentation.eye_pose(unit) end
        if not eye or not rotation then api.visible = false; return end
        local anchor = eye + Quaternion.forward(rotation) * Rate.AHEAD -
            Quaternion.up(rotation) * Rate.DOWN - Quaternion.right(rotation) * Rate.LEFT
        local overlay = presentation.hand_overlay
        local pixel_metres = Rate.pixel_metres(overlay and overlay.cell_width and overlay.cell_width())
        local canvas = overlay and overlay.canvas(game_world, "frame_rate_display", anchor, pixel_metres)
        if not canvas then api.visible = false; return end
        local c = Rate.color(fps, hz)
        canvas.text(Rate.text(fps, hz), Rate.TEXT_SIZE / pixel_metres, 0, 0, {235, c[1], c[2], c[3]})
        api.visible = true
        if not logged then
            logged = true
            mod:info("DARKTIDEVR_FRAME_RATE first_draw fps=%.1f hz=%s", fps, tostring(hz))
        end
    end
    function api.draw(game_world, unit)
        if failed then return end
        local ok, err = pcall(draw, game_world, unit)
        if not ok then
            failed = true
            mod:warning("DARKTIDEVR_FRAME_RATE error=%s stopped=true", tostring(err))
        end
    end
    return api
end

return Rate
