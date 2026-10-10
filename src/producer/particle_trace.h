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

// Whether the second eye's render of a particle system should be skipped
// (10 October 2026, trace launch 16: in frame 12939 the stock
// pass no longer drew system 1ec2444c780, the engine having destroyed it, and
// the second eye's pass drew it from freed, reused memory). A system the first
// pass drew this frame is live; one it drew within the last `window` frames
// but not this one has just been destroyed. Nothing is decided in a frame
// whose first pass has not run. Pure.
inline bool particle_stale_in_second_eye(bool known, std::uint64_t last_first_pass,
                                         std::uint64_t frame, std::uint64_t first_pass_frame,
                                         std::uint64_t window = 8) {
  if (!known || first_pass_frame != frame || last_first_pass == frame) return false;
  return frame > last_first_pass && frame - last_first_pass <= window;
}

// Whether the second eye's render of a system the stock pass drew this same
// frame should be skipped because the system no longer looks as it did then
// (launch 18: torn down by the game between the two passes, within one frame).
// `unchanged`: its counts, arrays, id and [+8] match the stock pass's. Pure.
inline bool particle_changed_since_first_pass(bool known, std::uint64_t last_first_pass,
                                              std::uint64_t frame, bool unchanged) {
  return known && last_first_pass == frame && !unchanged;
}

// Called by the GPU visualizer hook for every render of one visualizer: notes
// a render that overlaps an update of the same visualizer.
void particle_trace_note_render(const void* visualizer);

// For the mod's console log: 0 not asked, 1 installed, 2 off, 3 declined on a
// signature (values[7] = its RVA), 4 MinHook refused. values: owner renders,
// updates, renders overlapping an update of the same visualizer, owner
// renders whose counts changed during the call, garbage owners reaching the
// engine, dumps written, records in the ring, detail, second-eye renders
// skipped as just destroyed, of which with garbage counts, second-eye renders
// skipped as changed since the stock pass drew them this frame.
int particle_trace_state(std::uint64_t values[11]);
}  // namespace darktidevr::producer
