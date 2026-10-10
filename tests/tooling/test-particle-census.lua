local Census=dofile(assert(arg[1]))
assert(Census.flag_on('on') and Census.flag_on(' enabled\n') and not Census.flag_on('off') and not Census.flag_on(nil))
local t=Census.new()
-- Three blood effects created, one destroyed; one stopped (still alive until destroyed).
for id=1,3 do Census.created(t,'w','content/fx/particles/blood',id) end
Census.created(t,'w','content/fx/particles/spark',10)
Census.destroyed(t,'w',1)
Census.stopped(t,'w',2)
local blood=t.by_name['content/fx/particles/blood']
assert(blood.created==3 and blood.destroyed==1 and blood.stopped==1 and blood.live==2)
assert(t.live_count==3,'three alive: '..t.live_count)
-- Unknown ids and other worlds are ignored.
Census.destroyed(t,'w',999); Census.destroyed(t,'other',2); Census.stopped(t,'w',999)
assert(t.live_count==3)
-- An id handed out again moves the count to the new name: the old effect ended outside Lua.
Census.created(t,'w','content/fx/particles/smoke',3)
assert(blood.live==1 and t.by_name['content/fx/particles/smoke'].live==1 and t.live_count==3)
-- Effects created without an id are counted but not tracked.
Census.created(t,'w','content/fx/particles/no_id',nil)
assert(t.by_name['content/fx/particles/no_id'].created==1 and t.live_count==3)
Census.created(t,'w',nil,77)
assert(t.live_count==3,'a nameless effect is ignored')
-- The report: most alive first, then busiest; interval counts reset.
local lines=Census.report(t)
assert(lines[1]:find('live_total=3',1,true),lines[1])
assert(lines[2]:find('^live content/fx/particles/blood live=1') or lines[2]:find('^live content/fx/particles/s'),lines[2])
local busy
for _,line in ipairs(lines) do if line:find('^busy content/fx/particles/blood') then busy=line end end
assert(busy and busy:find('recent=3',1,true),'busiest name with its recent count')
for _,line in ipairs(Census.report(t)) do assert(not line:find('^busy'),'interval counts reset: '..line) end
-- A world going away takes its effects.
Census.world_destroyed(t,'w')
assert(t.live_count==0 and blood.live==0 and blood.created==3,'totals kept, live cleared')
-- Bounded.
local small=Census.new()
local saved=Census.MAX_LIVE; Census.MAX_LIVE=2
for id=1,5 do Census.created(small,'w','x',id) end
assert(small.live_count==2 and small.dropped==3 and small.by_name.x.created==5)
Census.MAX_LIVE=saved
print('particle_census=pass counts reused_ids report reset world bounded')
