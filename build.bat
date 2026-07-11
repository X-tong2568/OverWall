@echo off
chcp 65001 >nul
echo ============================================
echo   OverWall 打包脚本
echo ============================================
echo.

cd /d "%~dp0"

echo [1/4] 激活虚拟环境...
call .venv\Scripts\activate.bat

echo [2/4] 安装 PyInstaller（如未安装）...
pip install pyinstaller -q

echo [3/4] 确保 Playwright 浏览器已安装（打包不包含浏览器，首次运行自动下载）...
python -m playwright install chromium

echo [4/4] PyInstaller 打包中...
python -m PyInstaller --clean --noconfirm OverWall.spec

echo.
echo ============================================
echo   打包完成！产物在 dist\OverWall.exe
echo   分发说明：exe 放到新电脑上首次运行时会自动下载 Chromium 浏览器
echo   （约145MB，仅需下载一次，之后持久化到 exe 同目录）
echo ============================================
pause
