@echo off
chcp 65001 >nul
echo ============================================
echo   GPT图片生成器 - PyInstaller 打包构建
echo ============================================
echo.

set PYTHON=C:\Users\28953\.workbuddy\binaries\python\versions\3.13.12\python.exe
set SPEC=%~dp0GPT图片生成器.spec
set OUTPUT=%~dp0dist

echo [1/2] 清理旧构建...
if exist "%OUTPUT%" rd /s /q "%OUTPUT%"
if exist "build" rd /s /q "build"

echo [2/2] PyInstaller 打包中（使用 GPT图片生成器.spec）...
"%PYTHON%" -m PyInstaller --clean --noconfirm "%SPEC%"

if %errorlevel% equ 0 (
    echo.
    echo ============================================
    echo   打包完成！
    echo   输出: %OUTPUT%\GPT图片生成器.exe
    echo ============================================
    start "" "%OUTPUT%"
) else (
    echo.
    echo 打包失败，请检查错误信息
)
pause
