-- Particle census (10 October 2026; on while darktidevr_particle_census.flag
-- says "on"). Four Psykhanium melee sessions on the Steam Frame ended in the
-- same engine crash: the particle render job (Darktide.exe 0x47de30) sizes a
-- scratch buffer from a particle system's element counts, and after about
-- five minutes of melee that buffer reached 1.47 GB, with system commit at
-- 78 GB and once 209 GB (docs/STEAMVR-STEAM-FRAME.md, launches 9-12). Some
-- effect is created faster than it is removed. This counts every effect the
-- Lua side creates, stops and destroys, by name, and logs the ones that keep
-- growing, so the leak can be named.
--
-- Effects the engine spawns itself (unit flow) never pass through Lua: if
-- nothing here grows while the crash still comes, that is the answer too.
local Census = {}

Census.FLAG = "./../mods/darktidevr/darktidevr_particle_census.flag"
Census.REPORT_SECONDS = 10
Census.TOP = 8
-- Bounds on what is tracked, so the census cannot itself grow without limit.
Census.MAX_LIVE = 200000

local function flag_on(text)
    if type(text) ~= "string" then return false end
    local value = text:match("^%s*(%a+)%s*$")
    value = value and value:lower()
    return value == "on" or value == "enabled"
end
Census.flag_on = flag_on

-- A fresh tally. Pure.
function Census.new()
    return {by_name = {}, live = {}, live_count = 0, dropped = 0}
end

local function entry(tally, name)
    local record = tally.by_name[name]
    if not record then
        record = {name = name, created = 0, stopped = 0, destroyed = 0, live = 0, interval_created = 0}
        tally.by_name[name] = record
    end
    return record
end

-- One created effect. world_key and id identify it until it is destroyed.
function Census.created(tally, world_key, name, id)
    if type(name) ~= "string" then return end
    local record = entry(tally, name)
    record.created = record.created + 1
    record.interval_created = record.interval_created + 1
    if id == nil then return end
    if tally.live_count >= Census.MAX_LIVE then tally.dropped = tally.dropped + 1; return end
    local live = tally.live[world_key]
    if not live then live = {}; tally.live[world_key] = live end
    if live[id] == nil then
        tally.live_count = tally.live_count + 1
        record.live = record.live + 1
    else
        -- An id handed out again: the earlier one ended without passing through Lua.
        local previous = tally.by_name[live[id]]
        if previous then previous.live = previous.live - 1; record.live = record.live + 1 end
    end
    live[id] = name
end

local function name_of(tally, world_key, id)
    local live = tally.live[world_key]
    return live and live[id], live
end

function Census.stopped(tally, world_key, id)
    local name = name_of(tally, world_key, id)
    if name then entry(tally, name).stopped = entry(tally, name).stopped + 1 end
end

function Census.destroyed(tally, world_key, id)
    local name, live = name_of(tally, world_key, id)
    if not name then return end
    local record = entry(tally, name)
    record.destroyed = record.destroyed + 1
    record.live = record.live - 1
    live[id] = nil
    tally.live_count = tally.live_count - 1
end

-- A world going away takes its effects with it.
function Census.world_destroyed(tally, world_key)
    local live = tally.live[world_key]
    if not live then return end
    for _, name in pairs(live) do
        local record = tally.by_name[name]
        if record then record.live = record.live - 1 end
        tally.live_count = tally.live_count - 1
    end
    tally.live[world_key] = nil
end

-- The report's lines: the names with most effects alive, then the names
-- created most since the last report. Resets the interval counts. Pure.
function Census.report(tally)
    local records = {}
    for _, record in pairs(tally.by_name) do records[#records + 1] = record end
    local function describe(record)
        return string.format("%s live=%d created=%d stopped=%d destroyed=%d recent=%d", record.name,
            record.live, record.created, record.stopped, record.destroyed, record.interval_created)
    end
    table.sort(records, function(a, b)
        if a.live ~= b.live then return a.live > b.live end
        return a.name < b.name
    end)
    local lines = {string.format("live_total=%d names=%d dropped=%d", tally.live_count, #records, tally.dropped)}
    for i = 1, math.min(Census.TOP, #records) do
        if records[i].live <= 0 then break end
        lines[#lines + 1] = "live " .. describe(records[i])
    end
    table.sort(records, function(a, b)
        if a.interval_created ~= b.interval_created then return a.interval_created > b.interval_created end
        return a.name < b.name
    end)
    for i = 1, math.min(Census.TOP, #records) do
        if records[i].interval_created <= 0 then break end
        lines[#lines + 1] = "busy " .. describe(records[i])
    end
    for _, record in ipairs(records) do record.interval_created = 0 end
    return lines
end

function Census.install(mod, presentation)
    local api = {}
    local tally = Census.new()
    local enabled, poll, last_report = false, 0, nil
    local function is_enabled()
        poll = poll - 1
        if poll > 0 then return enabled end
        poll = 600
        local io_api = Mods and Mods.lua and Mods.lua.io
        local file = io_api and io_api.open(Census.FLAG, "r")
        local now_enabled = false
        if file then now_enabled = flag_on(file:read("*all")); file:close() end
        if now_enabled ~= enabled then
            enabled = now_enabled
            mod:info("DARKTIDEVR_PARTICLES census=%s", enabled and "on" or "off")
        end
        return enabled
    end
    local function key(world) return tostring(world) end
    local function guarded(fn, ...)
        local ok, err = pcall(fn, ...)
        if not ok and not api.warned then
            api.warned = true
            mod:warning("DARKTIDEVR_PARTICLES census_error=%s", tostring(err))
        end
    end
    function api.created(world, name, id)
        if is_enabled() then guarded(Census.created, tally, key(world), name, id) end
    end
    mod:hook(World, "stop_spawning_particles", function(func, world, id, ...)
        if is_enabled() then guarded(Census.stopped, tally, key(world), id) end
        return func(world, id, ...)
    end)
    mod:hook(World, "destroy_particles", function(func, world, id, ...)
        if is_enabled() then guarded(Census.destroyed, tally, key(world), id) end
        return func(world, id, ...)
    end)
    -- Called once per gameplay frame (the hand-overlay draw path).
    function api.update(world)
        if not is_enabled() then return end
        local t = Managers.time and Managers.time:time("main")
        if not t then return end
        if not last_report or t < last_report then last_report = t; return end
        if t - last_report < Census.REPORT_SECONDS then return end
        last_report = t
        guarded(function()
            for _, line in ipairs(Census.report(tally)) do mod:info("DARKTIDEVR_PARTICLES %s", line) end
        end)
    end
    -- At every level load: the old world's effects went with it. The totals
    -- by name are kept for the session.
    function api.destroy()
        guarded(function()
            for world_key in pairs(tally.live) do Census.world_destroyed(tally, world_key) end
        end)
    end
    return api
end

return Census
