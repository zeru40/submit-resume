@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo 安装完成。已安装 Edge 时无需另外下载浏览器；双击 start.bat 启动。
pause
exit /b 0
:fail
echo 安装失败，请检查上方错误信息。
pause
exit /b 1
