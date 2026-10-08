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

  bool empty() const { return !refresh_rate_hz && !motion_smoothing; }
};

inline constexpr const char* kSteamVrSection = "steamvr";
inline constexpr const char* kSteamVrRefreshRateKey = "preferredRefreshRate";
inline constexpr const char* kSteamVrMotionSmoothingKey = "motionSmoothing";

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
