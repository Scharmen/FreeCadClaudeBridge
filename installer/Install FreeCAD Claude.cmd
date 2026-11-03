@echo off
rem FreeCAD x Claude setup for Windows: double-click. Opens the setup window.
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Scharmen/FreeCadClaudeBridge/main/install.ps1 | iex"
pause
