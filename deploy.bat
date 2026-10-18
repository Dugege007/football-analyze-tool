@echo off
chcp 65001 >nul
rem 双击发布足球分析工具大陆站。只允许 124.221.56.38，确认时输入 Y。
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" %*
set ERR=%ERRORLEVEL%
echo.
if not "%ERR%"=="0" (
  echo 发布没有完成。
) else (
  echo 可以关闭这个窗口。
)
pause
exit /b %ERR%
