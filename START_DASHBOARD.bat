@echo off
echo ===============================================================================
echo POLYMARKET TRADING DASHBOARD
echo ===============================================================================
echo.
echo Starting dashboard...
echo Open your browser to: http://localhost:8501
echo.
echo Press Ctrl+C to stop the dashboard
echo.
echo ===============================================================================
cd /d "%~dp0"
set PYTHONPATH=%CD%

REM Check if streamlit is installed
python -c "import streamlit" 2>nul
if errorlevel 1 (
    echo ERROR: Streamlit not installed!
    echo Installing streamlit...
    pip install streamlit
)

REM Start dashboard
streamlit run ui\dashboard.py
