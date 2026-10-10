local Rate=dofile(assert(arg[1]))
-- The flag: "on" or "enabled", any case and surrounding space; anything else off.
assert(Rate.flag_on('on') and Rate.flag_on(' ON\r\n') and Rate.flag_on('enabled'))
assert(not Rate.flag_on('off') and not Rate.flag_on('') and not Rate.flag_on(nil) and not Rate.flag_on('on please'))
-- The refresh rate flag's number, within what a headset runs.
assert(Rate.parse_hz('90')==90 and Rate.parse_hz(' 144\n')==144 and Rate.parse_hz('72.5')==72.5)
assert(Rate.parse_hz('abc')==nil and Rate.parse_hz('5')==nil and Rate.parse_hz(nil)==nil)
-- Counting: one reading per WINDOW seconds, the frames since the last one.
local c=Rate.counter()
assert(Rate.observe(c,10)==nil,'the first call starts the window')
local reading
for i=1,60 do reading=Rate.observe(c,10+i/60) or reading end
assert(reading and math.abs(reading-60)<1e-9,'60 frames in a second: '..tostring(reading))
assert(Rate.observe(c,11)==nil,'the same time is the same frame')
for i=1,45 do reading=Rate.observe(c,11+i/45) end
assert(math.abs(reading-45)<1e-9,'the next second reads on its own: '..tostring(reading))
assert(Rate.observe(c,0/0)==nil,'NaN ignored')
assert(Rate.observe(c,3)==nil and c.start==3 and c.frames==0,'a clock going backwards starts again')
-- Colours against the refresh rate.
assert(Rate.color(90,90)==Rate.GREEN and Rate.color(88,90)==Rate.GREEN,'within 3 per cent is keeping up')
assert(Rate.color(70,90)==Rate.AMBER and Rate.color(60,90)==Rate.RED)
assert(Rate.color(60,nil)==Rate.WHITE,'no refresh rate known')
-- The line.
assert(Rate.text(61.6,90)=='62 fps / 90 Hz' and Rate.text(61.4,nil)=='61 fps' and Rate.text(nil,90)==nil)
-- The panel scale fits the widest line in an overlay cell.
assert(Rate.pixel_metres(nil)==Rate.PIXEL_METRES)
for _,cell in ipairs({477,528,960}) do
  local mpp=Rate.pixel_metres(cell)
  assert(Rate.LAYOUT_HALF_WIDTH/mpp<cell*.5-9,'fits a '..cell..' px cell')
end
print('frame_rate_display=pass flag hz counter colours text pixel_metres')
