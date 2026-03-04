@echo off
echo Opening VPS Dashboard tunnel on http://localhost:8502 ...
echo (Keep this window open. Close it to disconnect.)
echo.
"C:\Program Files\Git\usr\bin\ssh.exe" -i C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem -L 8502:localhost:8501 -N ubuntu@3.249.183.5
pause
