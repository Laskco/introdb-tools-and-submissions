@echo off
setlocal EnableDelayedExpansion
title Blind OP/ED detector
set "tool_dir=%~dp0"
set "venv=%tool_dir%.venv"
set "venv_python=%venv%\Scripts\python.exe"

if not exist "%venv_python%" (
  echo First run setup: creating local Python environment...
  python -m venv "%venv%"
  if errorlevel 1 goto setup_failed
)

"%venv_python%" -c "import numpy" >nul 2>nul
if errorlevel 1 (
  echo First run setup: installing numpy into local environment...
  "%venv_python%" -m pip install --upgrade pip numpy
  if errorlevel 1 goto setup_failed
)

if "%~1"=="" (
  echo Drag the episode folder onto this .bat file, or paste its path below.
  set /p "folder=Folder: "
) else (
  set "folder=%~1"
)
echo.
echo Choose mode:
echo   1 = Detect/create OP/ED chapters
echo   2 = Edit existing chapters only
echo   3 = Export existing OP/ED chapters to IntroDB JSON, with review/edit prompts
echo       Asks show name, IMDb, optional TVDB, and season per show folder.
echo       Mode 3 uses release-wide OP/Opening and ED/Ending/Credits priority.
echo       Detection applies a valid op_ed_overrides.json in the release folder.
set /p "mode=Mode [1]: "
if "%mode%"=="" set "mode=1"
echo.
if "%mode%"=="2" (
  echo Edit existing chapters only.
  echo Examples:
  echo   12        removes both OP and ED from episode 12
  echo   12:op     removes only OP from episode 12
  echo   12:ed     removes only ED from episode 12
  echo   1-3:ed    removes ED from episodes 1 through 3
  set /p "remove_spec=Remove: "
  echo.
  if "!remove_spec!"=="" (
    echo Edit-existing mode needs something in Remove, like 12 or 12:ed.
  ) else (
    "%venv_python%" "%tool_dir%detect_op_ed.py" "%folder%" --edit-existing --remove "!remove_spec!"
  )
) else if "%mode%"=="3" (
  "%venv_python%" "%tool_dir%detect_op_ed.py" "%folder%" --export-introdb
) else (
  echo Optional: remove detected OP/ED from specific episodes before writing chapters.
  echo Examples:
  echo   12        removes both OP and ED from episode 12
  echo   12:op     removes only OP from episode 12
  echo   12:ed     removes only ED from episode 12
  echo   1-3:ed    removes ED from episodes 1 through 3
  echo Leave blank to keep all detected OP/ED chapters.
  set /p "remove_spec=Remove: "
  echo.
  if "!remove_spec!"=="" (
    "%venv_python%" "%tool_dir%detect_op_ed.py" "%folder%"
  ) else (
    "%venv_python%" "%tool_dir%detect_op_ed.py" "%folder%" --remove "!remove_spec!"
  )
)
echo.
pause
exit /b

:setup_failed
echo.
echo Setup failed. Make sure Python can create a venv and that internet access is available for numpy.
echo.
pause
