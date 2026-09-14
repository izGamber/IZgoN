@echo off
REM IzgoN - double-click this file.
REM It just runs install-windows.ps1 sitting next to it. Open that file in
REM Notepad first if you want to read what it does before running it.

setlocal
set "HERE=%~dp0"

if not exist "%HERE%install-windows.ps1" (
  echo.
  echo   install-windows.ps1 is missing.
  echo   It has to sit in the same folder as this file.
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%install-windows.ps1"
