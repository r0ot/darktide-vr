#pragma once
#include <Windows.h>
#include <cstdint>

namespace darktidevr::producer {
// The present count: equal for every render of one frame, different between
// frames.
using ParticleFrameReader = std::uint64_t (*)();

// What the last simulating render of one particle system saw.
struct ParticleSimulationSeen {
  std::uint64_t frame{};
  std::uint32_t serial{}, parity{};
  bool valid{};
};
// Whether a render that finds the update-needed byte set should draw without
// simulating: only when this frame already simulated this very update (same
// update counter and parity). Otherwise it records this render as the one
// that simulates. Pure.
inline bool particle_simulated_already(ParticleSimulationSeen& seen, std::uint64_t frame,
                                       std::uint32_t serial, std::uint32_t parity) {
  if (seen.valid && seen.frame == frame && seen.serial == serial && seen.parity == parity) {
    return true;
  }
  seen = {frame, serial, parity, true};
  return false;
}
// GPU particles simulated once per update in stereo (10 October 2026). Register
// after MH_Initialize, before MH_EnableHook. Declines, returning true, unless
// every verified site of build 25681127 matches or when
// darktidevr_particle_simulation_once.flag beside the module says "off";
// false only when MinHook refuses a hook it was given.
bool install_particle_simulation_once(HMODULE module, ParticleFrameReader frame);

// For the mod's own log, which a game launch can always write: 0 not yet
// asked, 1 installed, 2 turned off by the flag, 3 declined on a signature
// (detail = its RVA), 4 MinHook refused, 5 no frame reader. values receives
// renders, renders that simulated, renders that were suppressed, detail.
int particle_simulation_once_state(std::uint64_t values[4]);
}  // namespace darktidevr::producer
