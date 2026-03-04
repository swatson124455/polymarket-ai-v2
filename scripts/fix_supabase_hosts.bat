@echo off
:: Add Supabase IPv6 to hosts file - fixes "getaddrinfo failed" on Windows
:: Run as Administrator: Right-click this file -> Run as administrator

set HOSTS=%SystemRoot%\System32\drivers\etc\hosts
set LINE=2600:1f16:1cd0:332c:ad0c:8b3e:f46:bf70 db.tfwevnuxmjqskmawvnfx.supabase.co

echo Checking for admin rights...
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo ERROR: This script must be run as Administrator.
    echo Right-click fix_supabase_hosts.bat and select "Run as administrator"
    echo.
    pause
    exit /b 1
)

findstr /C:"db.tfwevnuxmjqskmawvnfx.supabase.co" "%HOSTS%" >nul 2>&1
if %errorLevel% equ 0 (
    echo Entry already exists in hosts file.
    goto :done
)

echo Adding Supabase host entry...
echo.>> "%HOSTS%"
echo # Supabase DB - added by polymarket-ai-v2 fix>> "%HOSTS%"
echo %LINE%>> "%HOSTS%"
echo.
echo Done. Added: %LINE%
echo.
echo Run: python verify_system.py
:done
pause
