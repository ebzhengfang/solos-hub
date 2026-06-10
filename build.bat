@echo off
chcp 65001 >nul
echo ============================================
echo   GPT-Image-2 图片生成器 - 打包构建
echo ============================================
echo.

set PYTHON=C:\Users\28953\.workbuddy\binaries\python\versions\3.13.12\python.exe
set SCRIPT=%~dp0image_generator_app.py
set NAME=GPT图片生成器
set OUTPUT=%~dp0dist

echo [1/2] 清理旧构建...
if exist "%OUTPUT%" rd /s /q "%OUTPUT%"
if exist "build" rd /s /q "build"
if exist "*.spec" del /q "*.spec"

echo [2/2] PyInstaller 打包中...
"%PYTHON%" -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name="%NAME%" ^
    --add-data "%SCRIPT%;." ^
    --hidden-import=tkinter ^
    --hidden-import=requests ^
    --hidden-import=json ^
    --hidden-import=threading ^
    --clean ^
    --noconfirm ^
    "%SCRIPT%"

if %errorlevel% equ 0 (
    echo.
    echo ============================================
    echo   ✅ 打包完成！
    echo   输出: %OUTPUT%\%NAME%.exe
    echo ============================================
    start "" "%OUTPUT%"
) else (
    echo.
    echo ❌ 打包失败，请检查错误信息
)
pause
