# The game-folder profile manager (tools/profiles), against a fake Steam
# install and game folder. Never touches a real game.
find_package(Python3 REQUIRED COMPONENTS Interpreter)
add_test(NAME game_profiles COMMAND "${Python3_EXECUTABLE}" -B
  "${CMAKE_CURRENT_SOURCE_DIR}/test_dtprofiles.py")
set_tests_properties(game_profiles PROPERTIES
  PASS_REGULAR_EXPRESSION "dtprofiles=pass"
  FAIL_REGULAR_EXPRESSION "dtprofiles=fail"
  TIMEOUT 120
  LABELS "profiles;offline")
