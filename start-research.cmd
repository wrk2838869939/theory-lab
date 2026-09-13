@echo off
rem Windows convenience entry: forwards to lab.py using any available Python.
setlocal
set "LAB_PYTHON=python"
where python >nul 2>nul || set "LAB_PYTHON=py"
"%LAB_PYTHON%" "%~dp0lab.py" %*
exit /b %errorlevel%
