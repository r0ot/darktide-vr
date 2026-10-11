#pragma once

#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>

namespace darktidevr::core {

// The SteamVR settings the mod changes for its own session and puts back
// afterwards (8 October, Steam Frame). SteamVR offers an OpenXR application
// only its current refresh rate (XR_FB_display_refresh_rate lists one), so
// the rate is changed where SteamVR's own settings window changes it, and
// SteamVR applies that live: the Frame switched 144 -> 90 -> 144 Hz in about
// 0.2 s each way. The user's values are written to a backup BEFORE anything
// is changed, so a session that dies without restoring is undone by the next
// one.
struct SteamVrSessionValues {
  std::optional<std::int32_t> refresh_rate_hz;   // steamvr/preferredRefreshRate
  std::optional<bool> motion_smoothing;          // steamvr/motionSmoothing
  // The viewer's own per-application framesToThrottle (10 October, Steam
  // Frame): SteamVR's automatic throttling halves an application that keeps
  // missing frames, and the viewer -- which submits one frame per game pair
  // -- was held at 45 for whole missions, with the game behind it. A fixed
  // throttle of 0 keeps it at the display rate and lets the runtime reproject
  // the misses instead. kSettingUnset (a backup only): the key was absent and
  // a restore removes it, leaving SteamVR's automatic behaviour.
  std::optional<std::int32_t> frames_to_throttle;

  bool empty() const {
    return !refresh_rate_hz && !motion_smoothing && !frames_to_throttle;
  }
};

inline constexpr const char* kSteamVrSection = "steamvr";
inline constexpr const char* kSteamVrRefreshRateKey = "preferredRefreshRate";
inline constexpr const char* kSteamVrMotionSmoothingKey = "motionSmoothing";
// SteamVR's application key for the viewer's OpenXR session (its
// XrApplicationInfo name), as vrserver logs it.
inline constexpr const char* kViewerAppSection = "system.generated.openxr.darktidevr";
inline constexpr const char* kFramesToThrottleKey = "framesToThrottle";
inline constexpr std::int32_t kSettingUnset = -1;
inline constexpr std::int32_t kFramesToThrottleMaximum = 10;

// SteamVR's settings, or a fake of them in a test.
class SteamVrSettingsStore {
 public:
  virtual ~SteamVrSettingsStore() = default;
  virtual std::optional<std::int32_t> get_int(const char* section,
                                              const char* key) = 0;
  virtual bool set_int(const char* section, const char* key,
                       std::int32_t value) = 0;
  virtual std::optional<bool> get_bool(const char* section,
                                       const char* key) = 0;
  virtual bool set_bool(const char* section, const char* key, bool value) = 0;
  // A per-application key, which is normally absent: its value,
  // kSettingUnset when SteamVR holds none and has no default, nothing when it
  // cannot be read. A store that cannot tell absence apart says nothing.
  virtual std::optional<std::int32_t> get_int_or_unset(const char* section,
                                                       const char* key) {
    return get_int(section, key);
  }
  virtual bool remove_key(const char* /*section*/, const char* /*key*/) {
    return false;
  }
};

std::string serialize_steamvr_backup(const SteamVrSessionValues& values);
// Nothing on a missing header, an unknown key or a value out of range: a
// backup that cannot be read is kept on disk and reported, never guessed at.
std::optional<SteamVrSessionValues> parse_steamvr_backup(
    const std::string& text);

struct SteamVrSessionResult {
  bool ok{};
  // A backup was left by a session that never restored, and was restored
  // before anything else happened.
  bool recovered_stale_backup{};
  SteamVrSessionValues previous;  // the values read before a change (apply)
  SteamVrSessionValues applied;   // what this call set (apply: the request;
                                  // restore: the user's values put back)
  std::string detail;             // one word on failure, for the log
};

// Restore any stale backup, read the user's current values for the keys the
// request names, back up those that differ, then set them. A failed set puts
// back what was already changed and removes the backup.
SteamVrSessionResult apply_steamvr_session_settings(
    SteamVrSettingsStore& store, const SteamVrSessionValues& request,
    const std::filesystem::path& backup_path);

// Put back what the backup holds and delete it. No backup is success.
SteamVrSessionResult restore_steamvr_session_settings(
    SteamVrSettingsStore& store, const std::filesystem::path& backup_path);

}  // namespace darktidevr::core
