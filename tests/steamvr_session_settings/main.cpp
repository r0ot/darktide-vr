#include "core/steamvr_session_settings.h"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>

namespace {

using darktidevr::core::SteamVrSessionValues;

void expect(bool condition, const char* message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

// SteamVR's settings in memory. A key can be made to fail its set, to prove
// the rollback; reads of an absent key fail, as an unreachable SteamVR would.
class FakeStore final : public darktidevr::core::SteamVrSettingsStore {
 public:
  std::map<std::string, std::int32_t> ints;
  std::map<std::string, bool> bools;
  std::string fail_set_key;
  int sets{};

  std::optional<std::int32_t> get_int(const char* section,
                                      const char* key) override {
    const auto found = ints.find(name(section, key));
    if (found == ints.end()) return std::nullopt;
    return found->second;
  }
  bool set_int(const char* section, const char* key,
               std::int32_t value) override {
    ++sets;
    if (fail_set_key == key) return false;
    ints[name(section, key)] = value;
    return true;
  }
  std::optional<bool> get_bool(const char* section, const char* key) override {
    const auto found = bools.find(name(section, key));
    if (found == bools.end()) return std::nullopt;
    return found->second;
  }
  bool set_bool(const char* section, const char* key, bool value) override {
    ++sets;
    if (fail_set_key == key) return false;
    bools[name(section, key)] = value;
    return true;
  }

  std::int32_t rate() const { return ints.at("steamvr/preferredRefreshRate"); }
  bool smoothing() const { return bools.at("steamvr/motionSmoothing"); }

 private:
  static std::string name(const char* section, const char* key) {
    return std::string(section) + "/" + key;
  }
};

FakeStore user_store() {
  FakeStore store;
  store.ints["steamvr/preferredRefreshRate"] = 144;
  store.bools["steamvr/motionSmoothing"] = true;
  return store;
}

SteamVrSessionValues play_request() {
  SteamVrSessionValues request;
  request.refresh_rate_hz = 90;
  request.motion_smoothing = false;
  return request;
}

}  // namespace

int main() {
  using namespace darktidevr::core;
  try {
    const auto root = std::filesystem::temp_directory_path() /
                      ("darktidevr-steamvr-session-test-" +
                       std::to_string(std::filesystem::file_time_type::clock::now()
                                          .time_since_epoch()
                                          .count()));
    std::filesystem::create_directories(root);
    const auto backup = root / "steamvr-session-backup.txt";

    // Round trip, and refusal of anything that is not exactly a backup.
    {
      SteamVrSessionValues values;
      values.refresh_rate_hz = 144;
      values.motion_smoothing = true;
      const auto parsed = parse_steamvr_backup(serialize_steamvr_backup(values));
      expect(parsed && parsed->refresh_rate_hz == 144 &&
                 parsed->motion_smoothing == true,
             "backup round trip");
      expect(!parse_steamvr_backup(""), "empty backup accepted");
      expect(!parse_steamvr_backup("refresh_rate_hz=144\n"),
             "headerless backup accepted");
      expect(!parse_steamvr_backup("darktidevr_steamvr_backup=1\nrefresh_rate_hz=5\n"),
             "out-of-range rate accepted");
      expect(!parse_steamvr_backup("darktidevr_steamvr_backup=1\nvolume=3\n"),
             "unknown key accepted");
      expect(!parse_steamvr_backup("darktidevr_steamvr_backup=1\nrefresh_rate_hz=9x\n"),
             "trailing junk accepted");
      expect(parse_steamvr_backup("darktidevr_steamvr_backup=1\r\nrefresh_rate_hz=120\r\n")
                     ->refresh_rate_hz == 120,
             "CRLF backup refused");
    }

    // The session: apply changes and backs up, restore puts back and deletes.
    {
      auto store = user_store();
      const auto applied = apply_steamvr_session_settings(store, play_request(), backup);
      expect(applied.ok && !applied.recovered_stale_backup, "apply failed");
      expect(store.rate() == 90 && !store.smoothing(), "apply did not set");
      expect(applied.previous.refresh_rate_hz == 144 &&
                 applied.previous.motion_smoothing == true,
             "apply did not report the user's values");
      expect(std::filesystem::exists(backup), "apply wrote no backup");
      const auto restored = restore_steamvr_session_settings(store, backup);
      expect(restored.ok, "restore failed");
      expect(store.rate() == 144 && store.smoothing(), "restore did not put back");
      expect(!std::filesystem::exists(backup), "restore left the backup");
      const auto again = restore_steamvr_session_settings(store, backup);
      expect(again.ok && again.applied.empty(), "restore without a backup changed something");
    }

    // A session that died without restoring: the next apply restores the
    // user's values FIRST, so the new backup holds them and not the mod's.
    {
      auto store = user_store();
      expect(apply_steamvr_session_settings(store, play_request(), backup).ok,
             "first apply failed");
      // ... the game is killed here: no restore ...
      const auto next = apply_steamvr_session_settings(store, play_request(), backup);
      expect(next.ok && next.recovered_stale_backup, "stale backup not recovered");
      expect(next.previous.refresh_rate_hz == 144, "stale recovery backed up the mod's rate");
      expect(restore_steamvr_session_settings(store, backup).ok, "restore failed");
      expect(store.rate() == 144 && store.smoothing(), "user's values lost after a crash");
    }

    // A stale backup with no request (the user cleared the flags): restored.
    {
      auto store = user_store();
      expect(apply_steamvr_session_settings(store, play_request(), backup).ok, "apply failed");
      const auto cleared = apply_steamvr_session_settings(store, {}, backup);
      expect(cleared.ok && cleared.recovered_stale_backup, "cleared flags left the mod's values");
      expect(store.rate() == 144 && store.smoothing(), "cleared flags did not restore");
      expect(!std::filesystem::exists(backup), "cleared flags left a backup");
    }

    // Already as asked: nothing set, nothing to put back.
    {
      auto store = user_store();
      store.ints["steamvr/preferredRefreshRate"] = 90;
      store.bools["steamvr/motionSmoothing"] = false;
      store.sets = 0;
      const auto applied = apply_steamvr_session_settings(store, play_request(), backup);
      expect(applied.ok && applied.applied.empty() && store.sets == 0,
             "an unchanged setting was written");
      expect(!std::filesystem::exists(backup), "an unchanged setting was backed up");
    }

    // A set that fails part-way rolls back what changed and leaves no backup.
    {
      auto store = user_store();
      store.fail_set_key = "motionSmoothing";
      const auto applied = apply_steamvr_session_settings(store, play_request(), backup);
      expect(!applied.ok && applied.detail == "set-failed", "a failed set reported success");
      expect(store.rate() == 144, "a failed set left the rate changed");
      expect(!std::filesystem::exists(backup), "a failed set left a backup");
    }

    // SteamVR unreachable for a read: nothing changed, nothing backed up.
    {
      FakeStore store;
      const auto applied = apply_steamvr_session_settings(store, play_request(), backup);
      expect(!applied.ok && store.sets == 0 && !std::filesystem::exists(backup),
             "an unreadable SteamVR was changed");
    }

    // An unreadable backup is kept and reported, never guessed at, and it
    // blocks a new apply rather than being overwritten.
    {
      std::ofstream(backup, std::ios::binary) << "not a backup\n";
      auto store = user_store();
      const auto restored = restore_steamvr_session_settings(store, backup);
      expect(!restored.ok && restored.detail == "backup-unreadable",
             "an unreadable backup was accepted");
      const auto applied = apply_steamvr_session_settings(store, play_request(), backup);
      expect(!applied.ok && store.rate() == 144, "apply overwrote an unreadable backup");
      expect(std::filesystem::exists(backup), "an unreadable backup was deleted");
      std::filesystem::remove(backup);
    }

    // A restore whose set fails keeps the backup for the next attempt.
    {
      auto store = user_store();
      expect(apply_steamvr_session_settings(store, play_request(), backup).ok, "apply failed");
      store.fail_set_key = "preferredRefreshRate";
      expect(!restore_steamvr_session_settings(store, backup).ok, "a failed restore reported success");
      expect(std::filesystem::exists(backup), "a failed restore lost the backup");
      store.fail_set_key.clear();
      expect(restore_steamvr_session_settings(store, backup).ok && store.rate() == 144,
             "the retried restore failed");
    }

    std::filesystem::remove_all(root);
    std::cout << "steamvr_session_settings=pass\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "steamvr_session_settings=fail " << error.what() << '\n';
    return 1;
  }
}
