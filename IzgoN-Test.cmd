@echo off
REM IzgoN - double-click this to prove the thing works.
REM It runs IzgoN-Test.ps1 sitting next to it. Open that file in Notepad first
REM if you want to read what it does before running it.

setlocal
set "HERE=%~dp0"

if not exist "%HERE%IzgoN-Test.ps1" (
  echo.
  echo   IzgoN-Test.ps1 is missing.
  echo   It has to sit in the same folder as this file.
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%IzgoN-Test.ps1"
