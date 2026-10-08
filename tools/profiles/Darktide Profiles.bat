@echo off
setlocal EnableDelayedExpansion
rem Switches the Darktide game folder between profiles: your 2D mods, VR on
rem its own, VR with your mods, and vanilla. Every change is shown first and
rem asks for "yes". Darktide must be closed. See docs\GAME-PROFILES.md.
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
:menu
echo.
echo   Darktide profiles
echo   -----------------
echo   1  Status
echo   2  2D: the mod loader and all your mods
echo   3  VR: the VR mod on its own
echo   4  VR + all your 2D mods
echo   5  VR + chosen mods (vr-custom)
echo   6  vr-custom: add the next mod(s) from 2D
echo   7  vr-custom: show which mods are in it
echo   8  Vanilla: no loader, no mods
echo   9  Plan only: show what a switch would change
echo   R  Restore the first capture (exactly how it was)
echo   C  Take a capture (restore point) now
echo   X  Recover an interrupted switch
echo   Q  Quit
echo.
set "choice="
set /p choice=Choose:
if /i "%choice%"=="1" python -m dtprofiles status
if /i "%choice%"=="2" python -m dtprofiles switch 2d
if /i "%choice%"=="3" python -m dtprofiles switch vr
if /i "%choice%"=="4" python -m dtprofiles switch vr-mods
if /i "%choice%"=="5" python -m dtprofiles switch vr-custom
if /i "%choice%"=="6" (
    set "count=1"
    set /p count=How many? [1]:
    python -m dtprofiles mods next !count!
)
if /i "%choice%"=="7" python -m dtprofiles mods list
if /i "%choice%"=="8" python -m dtprofiles switch vanilla
if /i "%choice%"=="9" (
    set "target="
    set /p target=Profile ^(2d, vr, vr-mods, vr-custom, vanilla^):
    python -m dtprofiles plan !target!
)
if /i "%choice%"=="R" python -m dtprofiles restore-capture first
if /i "%choice%"=="C" python -m dtprofiles capture
if /i "%choice%"=="X" python -m dtprofiles recover
if /i "%choice%"=="Q" goto :eof
goto menu
