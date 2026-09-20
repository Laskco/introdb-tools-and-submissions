@echo off
setlocal
title Relabel existing chapters as OP/ED
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
"%venv_python%" "%tool_dir%relabel_chapters.py" "%folder%"
echo.
pause
exit /b

:setup_failed
echo.
echo Setup failed. Make sure Python can create a venv and that internet access is available for numpy.
echo.
pause
