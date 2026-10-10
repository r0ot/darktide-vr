// The rule behind the stereo particle fix (src/producer/particle_simulation_once):
// the first render of an update simulates, every further render of that frame
// and update draws only, and nothing else is ever suppressed.
#include "producer/particle_simulation_once.h"
#include "producer/particle_trace.h"

#include <cstdio>
#include <cstdlib>

namespace {
int failures = 0;
void expect(bool condition, const char* what) {
  if (!condition) {
    std::printf("FAIL %s\n", what);
    ++failures;
  }
}
}  // namespace

int main() {
  using darktidevr::producer::ParticleSimulationSeen;
  using darktidevr::producer::particle_simulated_already;

  ParticleSimulationSeen seen{};
  // Frame 10, update 3 (parity 3): the left eye simulates, the right draws.
  expect(!particle_simulated_already(seen, 10, 3, 3), "first render of an update simulates");
  expect(particle_simulated_already(seen, 10, 3, 3), "second render of the same update draws only");
  expect(particle_simulated_already(seen, 10, 3, 3), "a third render of it too");
  // The next frame's update advanced the counters: simulate again, once.
  expect(!particle_simulated_already(seen, 11, 4, 4), "next update simulates");
  expect(particle_simulated_already(seen, 11, 4, 4), "and its second eye draws only");
  // A frame whose update did not visit the system leaves the byte set with the
  // same counters: stock simulates again on that frame's render, and so does this.
  expect(!particle_simulated_already(seen, 12, 4, 4), "an unvisited frame keeps stock behaviour");
  // Two updates within one frame (no Present between): the counters differ.
  expect(!particle_simulated_already(seen, 12, 5, 5), "a second update in one frame simulates");
  // A record left by a freed system at a reused address, from an earlier frame,
  // never suppresses the new system's first emission even with equal counters.
  ParticleSimulationSeen stale{};
  expect(!particle_simulated_already(stale, 7, 1, 1), "an old system simulated");
  expect(!particle_simulated_already(stale, 9, 1, 1), "a new system at that address simulates its first update");
  // A fresh record never suppresses, whatever its zero-initialised fields.
  ParticleSimulationSeen fresh{};
  expect(!particle_simulated_already(fresh, 0, 0, 0), "an empty record never suppresses");

  // The second eye's rule (particle_trace.h): draw only what the stock pass
  // drew this frame, unchanged.
  using darktidevr::producer::particle_second_eye_draw;
  using darktidevr::producer::SecondEyeDraw;
  expect(particle_second_eye_draw(true, 12939, 12939, true) == SecondEyeDraw::draw,
         "drawn by the stock pass this frame, unchanged: drawn");
  expect(particle_second_eye_draw(true, 11075, 11075, false) == SecondEyeDraw::skip_changed,
         "launch 18: torn down since this frame's stock draw");
  expect(particle_second_eye_draw(true, 12938, 12939, true) == SecondEyeDraw::skip_earlier,
         "launch 16: gone from the stock pass one frame");
  expect(particle_second_eye_draw(true, 12233, 12245, true) == SecondEyeDraw::skip_earlier,
         "launch 19: gone from the stock pass twelve frames");
  expect(particle_second_eye_draw(false, 0, 12939, true) == SecondEyeDraw::skip_unseen,
         "never drawn by the stock pass");

  if (failures != 0) return EXIT_FAILURE;
  std::printf("particle_simulation_once=pass first_simulates second_draws next_update unvisited_frame "
              "stale_address fresh_record\n");
  return EXIT_SUCCESS;
}
