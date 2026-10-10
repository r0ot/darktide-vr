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

// Whether the second eye draws a particle system (10 October 2026). The second
// eye's pass meets systems the engine has destroyed: ones the stock pass
// stopped drawing frames ago (launch 16 one frame, launch 19 twelve) and ones
// torn down between the two passes of one frame (launch 18), all from freed,
// reused memory. So it draws only a system the stock pass drew this same frame
// and that still looks as it did then (`unchanged`: counts, arrays, id and
// [+8]); in the traces that is 99.2% of its draws. The rest are skipped:
// systems the stock pass drew in an earlier frame, ones it never drew (seen
// by the second eye alone, at the edge of its view), and changed ones. Pure.
enum class SecondEyeDraw { draw, skip_earlier, skip_unseen, skip_changed };
inline SecondEyeDraw particle_second_eye_draw(bool known, std::uint64_t last_first_pass,
                                              std::uint64_t frame, bool unchanged) {
  if (!known) return SecondEyeDraw::skip_unseen;
  if (last_first_pass != frame) return SecondEyeDraw::skip_earlier;
  return unchanged ? SecondEyeDraw::draw : SecondEyeDraw::skip_changed;
}

// Called by the GPU visualizer hook for every render of one visualizer: notes
// a render that overlaps an update of the same visualizer.
void particle_trace_note_render(const void* visualizer);

// For the mod's console log: 0 not asked, 1 installed, 2 off, 3 declined on a
// signature (values[7] = its RVA), 4 MinHook refused. values: owner renders,
// updates, renders overlapping an update of the same visualizer, owner
// renders whose counts changed during the call, garbage owners reaching the
// engine, dumps written, records in the ring, detail, and second-eye renders
// skipped (particle_second_eye_draw) as drawn by the stock pass only in an
// earlier frame, as never drawn by it, and as changed since it drew them.
int particle_trace_state(std::uint64_t values[11]);
}  // namespace darktidevr::producer
