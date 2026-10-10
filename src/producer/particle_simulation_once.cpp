// GPU particles simulated once per update in stereo (10 October 2026).
//
// Darktide's GPU particle visualizer (GPUVisualizer::render, 0x5758b0 in build
// 25681127; the author's 563770 on the earlier build, see
// docs/ENGINE-PARTICLE-STEREO-OWNERSHIP.md) reads the system's update-needed
// byte at +0x514 on entry and, when it is set, runs the emit and simulate
// kernels and a later step gated on the same byte. It never clears the byte:
// the update (0x573f40) recomputes it once per world update and, only when
// set, advances the update counter +0x510 and the ping-pong parity +0x4e4 that
// selects which buffer the simulation reads and which it writes.
//
// The VR mod renders the world twice a frame, once per eye, so every GPU
// particle system emitted and simulated twice per update into the same
// buffers. Four Psykhanium melee sessions on the Steam Frame ended with the
// particle render job sizing scratch from garbage element counts (a -1 array,
// or 1.47 GB and then 75 GB of commit), the weak-spot blood splatter twice
// among them (docs/STEAMVR-STEAM-FRAME.md, launches 9-13).
//
// This lets the first render of an update simulate and has every further
// render of the same frame and update draw from that result: the byte is
// cleared for the call and restored after it. "Same" needs the same present
// count, parity and update counter, so a record left by a freed system at a
// reused address can never take a new system's first emission. Rendering is
// never skipped, and a system the update did not visit keeps stock behaviour.
#include "producer/particle_simulation_once.h"
#include "producer/guarded_copy.h"

#include <MinHook.h>

#include <array>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <unordered_map>

namespace darktidevr::producer {
namespace {

constexpr std::uintptr_t kRender = 0x5758b0;
constexpr std::size_t kFlag = 0x514, kSerial = 0x510, kParity = 0x4e4;

// Exact bytes of build 25681127 at every site this relies on. Any difference
// (a game update) and nothing is installed.
struct Site {
  std::uintptr_t rva;
  const unsigned char* bytes;
  std::size_t size;
};
// GPUVisualizer::render's prologue through `movzx r15d, byte [rcx+0x514]`.
constexpr unsigned char kRenderEntry[] = {
    0x48, 0x8b, 0xc4, 0x4c, 0x89, 0x48, 0x20, 0x4c, 0x89, 0x40, 0x18, 0x48, 0x89, 0x50,
    0x10, 0x48, 0x89, 0x48, 0x08, 0x55, 0x53, 0x56, 0x57, 0x41, 0x54, 0x41, 0x55, 0x41,
    0x56, 0x41, 0x57, 0x48, 0x8d, 0xa8, 0xa8, 0xfb, 0xff, 0xff, 0x48, 0x81, 0xec, 0x18,
    0x05, 0x00, 0x00, 0x44, 0x0f, 0xb6, 0xb9, 0x14, 0x05, 0x00, 0x00};
// The update: the counters advance only when the byte is set.
constexpr unsigned char kUpdateAdvance[] = {
    0x45, 0x38, 0xbd, 0x14, 0x05, 0x00, 0x00, 0x74, 0x15, 0x41, 0xff, 0x85, 0x10, 0x05, 0x00,
    0x00, 0x41, 0xff, 0x85, 0xe4, 0x04, 0x00, 0x00, 0x41, 0xff, 0x85, 0xe8, 0x04, 0x00, 0x00};
// The update recomputes the byte every update (`mov [r13+0x514], al`).
constexpr unsigned char kUpdateFlag[] = {0x41, 0x88, 0x85, 0x14, 0x05, 0x00, 0x00};
// The only caller (the particle rendering owner): `call 0x5758b0`.
constexpr unsigned char kCallSite[] = {0xe8, 0xb0, 0x6a, 0x0f, 0x00};
constexpr std::array<Site, 4> kSites{{
    {kRender, kRenderEntry, sizeof(kRenderEntry)},
    {0x5755ba, kUpdateAdvance, sizeof(kUpdateAdvance)},
    {0x57483c, kUpdateFlag, sizeof(kUpdateFlag)},
    {0x47edfb, kCallSite, sizeof(kCallSite)},
}};

// Verified at the only caller and the callee's stack loads: four integer
// registers and stack slots up to the sixteenth argument (two of them floats,
// which travel in their stack slots unchanged); the return is unused. Twenty
// slots forward every argument and read only the caller's own frame.
using Render = void (*)(void*, std::uint64_t, std::uint64_t, std::uint64_t, std::uint64_t,
                        std::uint64_t, std::uint64_t, std::uint64_t, std::uint64_t,
                        std::uint64_t, std::uint64_t, std::uint64_t, std::uint64_t,
                        std::uint64_t, std::uint64_t, std::uint64_t, std::uint64_t,
                        std::uint64_t, std::uint64_t, std::uint64_t);
Render original{};
ParticleFrameReader frame_reader{};


constexpr std::size_t kStripes = 64;
struct Stripe {
  std::mutex mutex;
  std::unordered_map<const void*, ParticleSimulationSeen> seen;
  std::uint64_t pruned_frame{};
};
std::array<Stripe, kStripes> stripes{};

std::atomic<std::uint64_t> simulated{}, suppressed{}, renders{};
std::atomic<int> install_state{};
std::atomic<std::uint64_t> install_detail{};
std::atomic<ULONGLONG> next_report{};
std::wstring log_path;

void log_line(const char* text) {
  if (log_path.empty()) return;
  const auto file = CreateFileW(log_path.c_str(), FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
  if (file == INVALID_HANDLE_VALUE) return;
  SYSTEMTIME now{};
  GetLocalTime(&now);
  char line[512]{};
  const int n = std::snprintf(line, sizeof(line), "%04u-%02u-%02u %02u:%02u:%02u pid=%lu %s\r\n",
                              now.wYear, now.wMonth, now.wDay, now.wHour, now.wMinute,
                              now.wSecond, GetCurrentProcessId(), text);
  DWORD written{};
  if (n > 0) WriteFile(file, line, static_cast<DWORD>(n), &written, nullptr);
  CloseHandle(file);
}

void report_now_and_then() {
  const auto now = GetTickCount64();
  auto due = next_report.load(std::memory_order_relaxed);
  if (now < due || !next_report.compare_exchange_strong(due, now + 60000)) return;
  char text[200]{};
  std::snprintf(text, sizeof(text), "particle_simulation_once renders=%llu simulated=%llu suppressed=%llu",
                static_cast<unsigned long long>(renders.load()),
                static_cast<unsigned long long>(simulated.load()),
                static_cast<unsigned long long>(suppressed.load()));
  log_line(text);
}

void render_hook(void* system, std::uint64_t a2, std::uint64_t a3, std::uint64_t a4,
                 std::uint64_t a5, std::uint64_t a6, std::uint64_t a7, std::uint64_t a8,
                 std::uint64_t a9, std::uint64_t a10, std::uint64_t a11, std::uint64_t a12,
                 std::uint64_t a13, std::uint64_t a14, std::uint64_t a15, std::uint64_t a16,
                 std::uint64_t a17, std::uint64_t a18, std::uint64_t a19, std::uint64_t a20) {
  renders.fetch_add(1, std::memory_order_relaxed);
  auto* bytes = static_cast<std::uint8_t*>(system);
  const std::uint8_t flag = bytes ? bytes[kFlag] : 0;
  bool suppress = false;
  if (flag != 0 && frame_reader) {
    std::uint32_t serial{}, parity{};
    std::memcpy(&serial, bytes + kSerial, sizeof(serial));
    std::memcpy(&parity, bytes + kParity, sizeof(parity));
    const auto frame = frame_reader();
    auto& stripe = stripes[(reinterpret_cast<std::uintptr_t>(system) >> 6) % kStripes];
    std::scoped_lock lock(stripe.mutex);
    // Records older than this frame can never suppress again; drop them now
    // and then so freed systems do not accumulate.
    if (frame > stripe.pruned_frame + 600) {
      for (auto it = stripe.seen.begin(); it != stripe.seen.end();) {
        it = it->second.frame + 1 < frame ? stripe.seen.erase(it) : std::next(it);
      }
      stripe.pruned_frame = frame;
    }
    suppress = particle_simulated_already(stripe.seen[system], frame, serial, parity);
  }
  if (suppress) {
    bytes[kFlag] = 0;
    suppressed.fetch_add(1, std::memory_order_relaxed);
  } else if (flag != 0) {
    simulated.fetch_add(1, std::memory_order_relaxed);
  }
  original(system, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11, a12, a13, a14, a15, a16, a17,
           a18, a19, a20);
  if (suppress) bytes[kFlag] = flag;
  report_now_and_then();
}

std::wstring beside(HMODULE module, const wchar_t* name) {
  std::array<wchar_t, 32768> path{};
  const auto size = GetModuleFileNameW(module, path.data(), static_cast<DWORD>(path.size()));
  if (!size || size >= path.size()) return {};
  std::wstring result(path.data(), size);
  const auto separator = result.find_last_of(L"\\/");
  if (separator == std::wstring::npos) return {};
  result.resize(separator + 1);
  return result + name;
}

bool switched_off(const std::wstring& flag) {
  const auto file = CreateFileW(flag.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
  if (file == INVALID_HANDLE_VALUE) return false;
  char text[33]{};
  DWORD read{};
  ReadFile(file, text, 32, &read, nullptr);
  CloseHandle(file);
  std::string value(text, read);
  for (auto& c : value) c = static_cast<char>(c >= 'A' && c <= 'Z' ? c - 'A' + 'a' : c);
  return value.find("off") != std::string::npos;
}

}  // namespace

bool install_particle_simulation_once(HMODULE module, ParticleFrameReader frame) {
  std::array<wchar_t, MAX_PATH> local{};
  const auto local_size = GetEnvironmentVariableW(L"LOCALAPPDATA", local.data(),
                                                  static_cast<DWORD>(local.size()));
  if (local_size && local_size < local.size()) {
    log_path = std::wstring(local.data(), local_size) + L"\\DarktideVR";
    CreateDirectoryW(log_path.c_str(), nullptr);
    log_path += L"\\particle-simulation-once.log";
  }
  if (switched_off(beside(module, L"darktidevr_particle_simulation_once.flag"))) {
    install_state.store(2);
    log_line("particle_simulation_once=off reason=flag");
    return true;
  }
  if (!frame) {
    install_state.store(5);
    log_line("particle_simulation_once=declined reason=no_frame_reader");
    return true;
  }
  auto* base = reinterpret_cast<std::uint8_t*>(GetModuleHandleW(nullptr));
  for (const auto& site : kSites) {
    std::array<unsigned char, 64> actual{};
    if (site.size > actual.size() || !safe_copy_bytes(actual.data(), base + site.rva, site.size) ||
        std::memcmp(actual.data(), site.bytes, site.size) != 0) {
      char text[128]{};
      std::snprintf(text, sizeof(text),
                    "particle_simulation_once=declined reason=signature rva=%llx",
                    static_cast<unsigned long long>(site.rva));
      install_detail.store(site.rva);
      install_state.store(3);
      log_line(text);
      return true;
    }
  }
  frame_reader = frame;
  if (MH_CreateHook(base + kRender, reinterpret_cast<void*>(&render_hook),
                    reinterpret_cast<void**>(&original)) != MH_OK) {
    install_state.store(4);
    log_line("particle_simulation_once=failed reason=create_hook");
    return false;
  }
  install_state.store(1);
  log_line("particle_simulation_once=installed build=25681127 rva=5758b0");
  return true;
}

int particle_simulation_once_state(std::uint64_t values[4]) {
  if (values) {
    values[0] = renders.load(std::memory_order_relaxed);
    values[1] = simulated.load(std::memory_order_relaxed);
    values[2] = suppressed.load(std::memory_order_relaxed);
    values[3] = install_detail.load(std::memory_order_relaxed);
  }
  return install_state.load();
}

}  // namespace darktidevr::producer
