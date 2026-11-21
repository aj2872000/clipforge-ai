@echo off
title ClipForge AI - Opus Shorts Creator
cd /d "%~dp0"

echo ===================================================
echo     ClipForge AI - Viral YouTube Shorts Creator
echo           (Opus Clip Alternative Engine)
echo ===================================================
echo.
echo Starting Web Server on http://localhost:8000 ...
echo Press Ctrl+C anytime to stop.
echo.

start "" "http://localhost:8000"
C:\Python313\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
