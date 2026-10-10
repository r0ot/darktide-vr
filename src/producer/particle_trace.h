#pragma once
#include <Windows.h>
#include <cstdint>

#include "producer/particle_submission_probe.h"

namespace darktidevr::producer {
// Particle crash trace (10 October 2026). Observes, never changes: the particle
// rendering owner (0x47de30) and the GPU particle update (0x573f40) of build
// 25681127, recording each call in a ring with its thread, frame and eye, and
// the owner's counts on entry and exit. Writes the ring to
// %LOCALAPPDATA%\DarktideVR\particle-trace-<pid>.log when the owner meets
// garbage counts, when they change under a running render, or when the known
// crash fires. Register after MH_Initialize, before MH_EnableHook. Declines,
// returning true, unless every verified site matches or when
// darktidevr_particle_trace.flag beside the module says "off".
bool install_particle_trace(HMODULE module, ParticleEyeReader eye);

// Called by the GPU visualizer hook for every render of one visualizer: notes
// a render that overlaps an update of the same visualizer.
void particle_trace_note_render(const void* visualizer);

// For the mod's console log: 0 not asked, 1 installed, 2 off, 3 declined on a
// signature (values[7] = its RVA), 4 MinHook refused. values: owner renders,
// updates, renders overlapping an update of the same visualizer, owner
// renders whose counts changed during the call, garbage owners seen, dumps
// written, records in the ring, detail.
int particle_trace_state(std::uint64_t values[8]);
}  // namespace darktidevr::producer
