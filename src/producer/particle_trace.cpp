// Particle crash trace (10 October 2026); see particle_trace.h.
//
// Every Psykhanium melee session on the Steam Frame ends in the particle
// rendering owner (0x47de30) reading garbage element counts from its system
// object (array at +0x78 = -1, or counts that size 1.47 GB to 65 GB of
// scratch), although the object's resource id at +0x240 stays valid (it is the
// crash report's error context). Simulating GPU particles once per frame
// (particle_simulation_once) did not stop it (docs/STEAMVR-STEAM-FRAME.md,
// launches 9-15). The working explanation is the object's arrays being changed
// by another thread while a render reads them -- the update or a destruction
// timed for one render a frame meeting the second eye's render. This records
// what each thread was doing to which particle object, in order, and writes
// it out at the moment it goes wrong.
#include "producer/particle_trace.h"
#include "producer/guarded_copy.h"

#include <MinHook.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace darktidevr::producer {
namespace {

constexpr std::uintptr_t kOwner = 0x47de30, kUpdate = 0x573f40, kCrash = 0x47e1b6;

struct Site {
  std::uintptr_t rva;
  const unsigned char* bytes;
  std::size_t size;
};
// The rendering owner's prologue through `mov rbx,[rcx+0x240]` and `mov rsi,rcx`.
constexpr unsigned char kOwnerEntry[] = {
    0x48, 0x8b, 0xc4, 0x4c, 0x89, 0x48, 0x20, 0x4c, 0x89, 0x40, 0x18, 0x48, 0x89, 0x50, 0x10, 0x55,
    0x48, 0x8d, 0xa8, 0xc8, 0xfc, 0xff, 0xff, 0x48, 0x81, 0xec, 0x40, 0x04, 0x00, 0x00, 0x48, 0x89,
    0x58, 0xf0, 0x48, 0x8b, 0x99, 0x40, 0x02, 0x00, 0x00, 0x48, 0x89, 0x70, 0xe8, 0x48, 0x8b, 0xf1};
constexpr unsigned char kOwnerCall[] = {0xe8, 0x6c, 0x59, 0x0f, 0x00};
// The update's prologue (`movss [rax+0x20], xmm3`: the fourth argument is a
// float) through `mov byte [rcx+0x4ed], 0; mov r13, rcx`.
constexpr unsigned char kUpdateEntry[] = {
    0x48, 0x8b, 0xc4, 0x48, 0x89, 0x58, 0x18, 0xf3, 0x0f, 0x11, 0x58, 0x20, 0x48, 0x89, 0x50, 0x10,
    0x48, 0x89, 0x48, 0x08, 0x55, 0x56, 0x57, 0x41, 0x54, 0x41, 0x55, 0x41, 0x56, 0x41, 0x57, 0x48,
    0x8d, 0xa8, 0x18, 0xf5, 0xff, 0xff, 0x48, 0x81, 0xec, 0xb0, 0x0b, 0x00, 0x00, 0xc6, 0x81, 0xed,
    0x04, 0x00, 0x00, 0x00, 0x4c, 0x8b, 0xe9};
constexpr unsigned char kUpdateCall[] = {0xe8, 0xe1, 0x71, 0x0f, 0x00};
// The crashing load: `mov rax,[rsi+0x78]; mov edx,ecx; mov r8,[rax+rdx*8]`.
constexpr unsigned char kCrashSite[] = {0x48, 0x8b, 0x46, 0x78, 0x8b, 0xd1, 0x4c, 0x8b, 0x04, 0xd0};
constexpr std::array<Site, 5> kSites{{
    {kOwner, kOwnerEntry, sizeof(kOwnerEntry)},
    {0x3884bf, kOwnerCall, sizeof(kOwnerCall)},
    {kUpdate, kUpdateEntry, sizeof(kUpdateEntry)},
    {0x47cd5a, kUpdateCall, sizeof(kUpdateCall)},
    {0x47e1b0, kCrashSite, sizeof(kCrashSite)},
}};

// The owner's arrays and counts, as the crashing code reads them.
struct OwnerView {
  std::uint64_t id{}, array{};
  std::uint32_t count{}, a0{}, b8{}, d0{};
  bool readable{};
};
OwnerView read_owner(const void* owner) {
  OwnerView view{};
  const auto* bytes = static_cast<const std::uint8_t*>(owner);
  view.readable = bytes && safe_copy_bytes(&view.id, bytes + 0x240, 8) &&
                  safe_copy_bytes(&view.array, bytes + 0x78, 8) &&
                  safe_copy_bytes(&view.count, bytes + 0x70, 4) &&
                  safe_copy_bytes(&view.a0, bytes + 0xa0, 4) &&
                  safe_copy_bytes(&view.b8, bytes + 0xb8, 4) &&
                  safe_copy_bytes(&view.d0, bytes + 0xd0, 4);
  return view;
}
bool garbage(const OwnerView& v) {
  const bool canonical = v.array == 0 || (v.array >> 47) == 0;
  return !v.readable || !canonical || v.count > 100000 || v.a0 > 10000000 ||
         v.b8 > 10000000 || v.d0 > 10000000;
}
bool same(const OwnerView& a, const OwnerView& b) {
  return a.array == b.array && a.count == b.count && a.a0 == b.a0 && a.b8 == b.b8 &&
         a.d0 == b.d0 && a.id == b.id;
}

enum Kind : std::uint32_t {
  owner_enter = 1, owner_exit, update_enter, update_exit, render_during_update,
  owner_changed, owner_garbage, owner_stand_in
};
const char* kind_name(std::uint32_t kind) {
  switch (kind) {
    case owner_enter: return "owner_enter";
    case owner_exit: return "owner_exit";
    case update_enter: return "update_enter";
    case update_exit: return "update_exit";
    case render_during_update: return "render_during_update";
    case owner_changed: return "owner_changed";
    case owner_garbage: return "owner_garbage";
    case owner_stand_in: return "owner_stand_in";
  }
  return "?";
}
struct Record {
  std::uint64_t qpc{}, object{}, frame{}, id{}, array{};
  std::uint32_t kind{}, thread{}, count{}, a0{}, b8{}, d0{};
  std::int32_t eye{-1};
  std::uint32_t sequence{};
};
constexpr std::size_t kRing = 1 << 16;
std::vector<Record> ring(kRing);
std::atomic<std::uint64_t> next_record{};

using Owner = std::uint64_t (*)(void*, std::uint64_t, std::uint64_t, std::uint64_t, std::uint64_t,
                                std::uint64_t, std::uint64_t, std::uint64_t, std::uint64_t,
                                std::uint64_t, std::uint64_t, std::uint64_t, std::uint64_t,
                                std::uint64_t, std::uint64_t, std::uint64_t, std::uint64_t,
                                std::uint64_t, std::uint64_t, std::uint64_t);
using Update = std::uint64_t (*)(void*, std::uint64_t, std::uint64_t, float, std::uint64_t,
                                 std::uint64_t, std::uint64_t, std::uint64_t, std::uint64_t,
                                 std::uint64_t, std::uint64_t, std::uint64_t, std::uint64_t,
                                 std::uint64_t, std::uint64_t, std::uint64_t, std::uint64_t,
                                 std::uint64_t, std::uint64_t, std::uint64_t);
Owner original_owner{};
Update original_update{};
ParticleEyeReader eye_reader{};
std::uintptr_t image_base{}, image_end{};

std::atomic<int> install_state{};
std::atomic<std::uint64_t> install_detail{}, owners{}, updates{}, overlaps{}, changes{},
    garbage_seen{}, dumps{};
std::atomic<bool> dumped{};
std::atomic<std::uint64_t> stand_ins{}, stand_in_garbage{};
bool stand_in_enabled = true;

// The frame each owner was last drawn by a first (stock) pass, and the last
// frame any first pass ran.
struct FirstPass {
  std::mutex mutex;
  std::unordered_map<const void*, std::uint64_t> last;
  std::uint64_t pruned{};
};
std::array<FirstPass, 64> first_pass{};
std::atomic<std::uint64_t> first_pass_frame{~0ull};

std::wstring log_directory;

// Visualizers being updated right now, by which thread.
constexpr std::size_t kStripes = 64;
struct Stripe {
  std::mutex mutex;
  std::unordered_map<const void*, DWORD> updating;
};
std::array<Stripe, kStripes> stripes{};
Stripe& stripe_of(const void* p) {
  return stripes[(reinterpret_cast<std::uintptr_t>(p) >> 6) % kStripes];
}

// The last frame and eye an owner render saw: update records reuse it rather
// than take the capture lock thousands of times a second.
std::atomic<std::uint64_t> last_frame{};
std::atomic<int> last_eye{-1};

void record(Kind kind, const void* object, const OwnerView* view = nullptr) {
  const auto index = next_record.fetch_add(1, std::memory_order_relaxed);
  auto& r = ring[index % kRing];
  LARGE_INTEGER now{};
  QueryPerformanceCounter(&now);
  r.sequence = static_cast<std::uint32_t>(index);
  r.qpc = static_cast<std::uint64_t>(now.QuadPart);
  r.kind = kind;
  r.thread = GetCurrentThreadId();
  r.object = reinterpret_cast<std::uintptr_t>(object);
  if (kind == update_enter || kind == update_exit || !eye_reader) {
    r.frame = last_frame.load(std::memory_order_relaxed);
    r.eye = last_eye.load(std::memory_order_relaxed);
  } else {
    const auto eye = eye_reader();
    r.frame = eye.present;
    r.eye = eye.eye;
    last_frame.store(eye.present, std::memory_order_relaxed);
    last_eye.store(eye.eye, std::memory_order_relaxed);
  }
  if (view) {
    r.id = view->id; r.array = view->array; r.count = view->count;
    r.a0 = view->a0; r.b8 = view->b8; r.d0 = view->d0;
  } else {
    r.id = r.array = 0; r.count = r.a0 = r.b8 = r.d0 = 0;
  }
}

// The ring, oldest first, and the counters. Once per process: the first
// moment something goes wrong is the one worth keeping.
void dump(const char* reason) {
  if (dumped.exchange(true) || log_directory.empty()) return;
  dumps.fetch_add(1);
  const auto path = log_directory + L"\\particle-trace-" + std::to_wstring(GetCurrentProcessId()) + L".log";
  const auto file = CreateFileW(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_ALWAYS,
                                FILE_ATTRIBUTE_NORMAL, nullptr);
  if (file == INVALID_HANDLE_VALUE) return;
  LARGE_INTEGER frequency{};
  QueryPerformanceFrequency(&frequency);
  std::string out;
  char line[512]{};
  std::snprintf(line, sizeof(line),
                "PARTICLE_TRACE reason=%s pid=%lu thread=%lu qpc_frequency=%lld owners=%llu updates=%llu "
                "render_during_update=%llu owner_changed=%llu garbage=%llu\r\n",
                reason, GetCurrentProcessId(), GetCurrentThreadId(), frequency.QuadPart,
                static_cast<unsigned long long>(owners.load()), static_cast<unsigned long long>(updates.load()),
                static_cast<unsigned long long>(overlaps.load()), static_cast<unsigned long long>(changes.load()),
                static_cast<unsigned long long>(garbage_seen.load()));
  out += line;
  const auto last = next_record.load();
  const auto first = last > kRing ? last - kRing : 0;
  for (auto i = first; i < last; ++i) {
    const auto& r = ring[i % kRing];
    std::snprintf(line, sizeof(line),
                  "%u qpc=%llu %s thread=%lu object=%llx frame=%llu eye=%d id=%016llx array=%llx count=%u "
                  "a0=%u b8=%u d0=%u\r\n",
                  r.sequence, static_cast<unsigned long long>(r.qpc), kind_name(r.kind), r.thread,
                  static_cast<unsigned long long>(r.object), static_cast<unsigned long long>(r.frame), r.eye,
                  static_cast<unsigned long long>(r.id), static_cast<unsigned long long>(r.array), r.count,
                  r.a0, r.b8, r.d0);
    out += line;
  }
  DWORD written{};
  WriteFile(file, out.data(), static_cast<DWORD>(out.size()), &written, nullptr);
  FlushFileBuffers(file);
  CloseHandle(file);
}

std::uint64_t owner_hook(void* owner, std::uint64_t a2, std::uint64_t a3, std::uint64_t a4,
                         std::uint64_t a5, std::uint64_t a6, std::uint64_t a7, std::uint64_t a8,
                         std::uint64_t a9, std::uint64_t a10, std::uint64_t a11, std::uint64_t a12,
                         std::uint64_t a13, std::uint64_t a14, std::uint64_t a15, std::uint64_t a16,
                         std::uint64_t a17, std::uint64_t a18, std::uint64_t a19, std::uint64_t a20) {
  owners.fetch_add(1, std::memory_order_relaxed);
  const auto before = read_owner(owner);
  record(owner_enter, owner, &before);
  bool stale = false;
  if (stand_in_enabled && eye_reader) {
    const auto eye = eye_reader();
    auto& pass = first_pass[(reinterpret_cast<std::uintptr_t>(owner) >> 6) % first_pass.size()];
    std::scoped_lock lock(pass.mutex);
    if (eye.eye != 1) {
      pass.last[owner] = eye.present;
      first_pass_frame.store(eye.present, std::memory_order_relaxed);
      if (eye.present > pass.pruned + 600) {
        for (auto it = pass.last.begin(); it != pass.last.end();) {
          it = it->second + 600 < eye.present ? pass.last.erase(it) : std::next(it);
        }
        pass.pruned = eye.present;
      }
    } else {
      const auto it = pass.last.find(owner);
      const bool known = it != pass.last.end();
      stale = particle_stale_in_second_eye(known, known ? it->second : 0, eye.present,
                                           first_pass_frame.load(std::memory_order_relaxed));
    }
  }
  if (stale) {
    stand_ins.fetch_add(1, std::memory_order_relaxed);
    if (garbage(before)) stand_in_garbage.fetch_add(1, std::memory_order_relaxed);
    record(owner_stand_in, owner, &before);
    // Not drawn, and never read again: it may already belong to something
    // else. The caller (0x3883cd..0x3884f0) allocates its scratch block before
    // every call and frees it after while its capacity is non-zero, which a
    // call that never happened leaves as the caller set it; the return value
    // is unused. (A zeroed stand-in object, the first version, crashed: the
    // owner reads [rsi+8]->+0x24 with no emitters at 0x47e6cb, launch 17.)
    return 0;
  }
  if (garbage(before)) {
    garbage_seen.fetch_add(1);
    record(owner_garbage, owner, &before);
    dump("owner_garbage_on_entry");
  }
  const auto result = original_owner(owner, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11, a12, a13, a14,
                                     a15, a16, a17, a18, a19, a20);
  const auto after = read_owner(owner);
  record(owner_exit, owner, &after);
  if (!same(before, after)) {
    changes.fetch_add(1, std::memory_order_relaxed);
    record(owner_changed, owner, &after);
    if (garbage(after)) {
      garbage_seen.fetch_add(1);
      dump("owner_garbage_on_exit");
    }
  }
  return result;
}

std::uint64_t update_hook(void* visualizer, std::uint64_t a2, std::uint64_t a3, float a4,
                          std::uint64_t a5, std::uint64_t a6, std::uint64_t a7, std::uint64_t a8,
                          std::uint64_t a9, std::uint64_t a10, std::uint64_t a11, std::uint64_t a12,
                          std::uint64_t a13, std::uint64_t a14, std::uint64_t a15, std::uint64_t a16,
                          std::uint64_t a17, std::uint64_t a18, std::uint64_t a19, std::uint64_t a20) {
  updates.fetch_add(1, std::memory_order_relaxed);
  record(update_enter, visualizer);
  {
    auto& stripe = stripe_of(visualizer);
    std::scoped_lock lock(stripe.mutex);
    stripe.updating[visualizer] = GetCurrentThreadId();
  }
  const auto result = original_update(visualizer, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11, a12, a13,
                                      a14, a15, a16, a17, a18, a19, a20);
  {
    auto& stripe = stripe_of(visualizer);
    std::scoped_lock lock(stripe.mutex);
    stripe.updating.erase(visualizer);
  }
  record(update_exit, visualizer);
  return result;
}

LONG CALLBACK on_exception(EXCEPTION_POINTERS* info) {
  if (!info || !info->ExceptionRecord) return EXCEPTION_CONTINUE_SEARCH;
  const auto code = info->ExceptionRecord->ExceptionCode;
  const auto at = reinterpret_cast<std::uintptr_t>(info->ExceptionRecord->ExceptionAddress);
  if (code == EXCEPTION_ACCESS_VIOLATION && at >= image_base && at < image_end) {
    char reason[96]{};
    std::snprintf(reason, sizeof(reason), "access_violation_at_%llx%s",
                  static_cast<unsigned long long>(at - image_base),
                  at - image_base == kCrash ? "_known_crash" : "");
    dump(reason);
  }
  return EXCEPTION_CONTINUE_SEARCH;
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
  std::transform(value.begin(), value.end(), value.begin(),
                 [](char c) { return static_cast<char>(c >= 'A' && c <= 'Z' ? c - 'A' + 'a' : c); });
  return value.find("off") != std::string::npos;
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

}  // namespace

void particle_trace_note_render(const void* visualizer) {
  if (install_state.load(std::memory_order_relaxed) != 1 || !visualizer) return;
  auto& stripe = stripe_of(visualizer);
  bool overlapping = false;
  {
    std::scoped_lock lock(stripe.mutex);
    overlapping = stripe.updating.find(visualizer) != stripe.updating.end();
  }
  if (overlapping) {
    overlaps.fetch_add(1, std::memory_order_relaxed);
    record(render_during_update, visualizer);
  }
}

bool install_particle_trace(HMODULE module, ParticleEyeReader eye) {
  std::array<wchar_t, MAX_PATH> local{};
  const auto local_size = GetEnvironmentVariableW(L"LOCALAPPDATA", local.data(),
                                                  static_cast<DWORD>(local.size()));
  if (local_size && local_size < local.size()) {
    log_directory = std::wstring(local.data(), local_size) + L"\\DarktideVR";
    CreateDirectoryW(log_directory.c_str(), nullptr);
  }
  stand_in_enabled = !switched_off(beside(module, L"darktidevr_particle_stand_in.flag"));
  if (switched_off(beside(module, L"darktidevr_particle_trace.flag"))) {
    install_state.store(2);
    return true;
  }
  auto* base = reinterpret_cast<std::uint8_t*>(GetModuleHandleW(nullptr));
  for (const auto& site : kSites) {
    std::array<unsigned char, 64> actual{};
    if (site.size > actual.size() || !safe_copy_bytes(actual.data(), base + site.rva, site.size) ||
        std::memcmp(actual.data(), site.bytes, site.size) != 0) {
      install_detail.store(site.rva);
      install_state.store(3);
      return true;
    }
  }
  image_base = reinterpret_cast<std::uintptr_t>(base);
  const auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(base);
  const auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS*>(base + dos->e_lfanew);
  image_end = image_base + nt->OptionalHeader.SizeOfImage;
  eye_reader = eye;
  if (MH_CreateHook(base + kOwner, reinterpret_cast<void*>(&owner_hook),
                    reinterpret_cast<void**>(&original_owner)) != MH_OK ||
      MH_CreateHook(base + kUpdate, reinterpret_cast<void*>(&update_hook),
                    reinterpret_cast<void**>(&original_update)) != MH_OK) {
    install_state.store(4);
    return false;
  }
  AddVectoredExceptionHandler(1, on_exception);
  install_state.store(1);
  return true;
}

int particle_trace_state(std::uint64_t values[10]) {
  if (values) {
    values[0] = owners.load(std::memory_order_relaxed);
    values[1] = updates.load(std::memory_order_relaxed);
    values[2] = overlaps.load(std::memory_order_relaxed);
    values[3] = changes.load(std::memory_order_relaxed);
    values[4] = garbage_seen.load(std::memory_order_relaxed);
    values[5] = dumps.load(std::memory_order_relaxed);
    values[6] = next_record.load(std::memory_order_relaxed);
    values[7] = install_detail.load(std::memory_order_relaxed);
    values[8] = stand_ins.load(std::memory_order_relaxed);
    values[9] = stand_in_garbage.load(std::memory_order_relaxed);
  }
  return install_state.load();
}

}  // namespace darktidevr::producer
