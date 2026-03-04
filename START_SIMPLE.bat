@echo off
echo ===============================================================================
echo POLYMARKET AI - SIMPLE START
echo ===============================================================================
echo.
echo This will start the main trading system (not the dashboard)
echo.
echo Press Ctrl+C to stop
echo.
cd /d "%~dp0"
set PYTHONPATH=%CD%
python main.py
