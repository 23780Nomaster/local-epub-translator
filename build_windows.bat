@echo off
REM ============================================================
REM  EPUB Translator — Windows Build Script
REM  Downloads all dependencies + builds exe
REM  Output: release\EPUB-Translator\  (ready to zip/distribute)
REM ============================================================

setlocal enabledelayedexpansion
echo.
echo ============================================
echo   EPUB Translator — Windows Build
echo ============================================
echo.

REM 1. Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

REM 2. Create dirs
if not exist dist mkdir dist
if not exist release\EPUB-Translator mkdir release\EPUB-Translator
if not exist models mkdir models
if not exist tesseract\tessdata mkdir tesseract\tessdata

REM 3. Install Python deps
echo [1/6] Installing Python dependencies...
pip install -r requirements_windows.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

REM 4. Download translation model (~3.5GB)
echo.
echo [2/6] Translation model (Hy-MT2)...
set MODEL_PATH=models\Hy-MT2-1.8B-Q8_0.gguf
if not exist "%MODEL_PATH%" (
    echo Downloading Hy-MT2-1.8B-Q8_0.gguf ^(~3.5GB^)...
    echo This will take a while depending on your internet speed.
    powershell -Command ^
        "$url='https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF/resolve/main/Hy-MT2-1.8B-Q8_0.gguf';" ^
        "Invoke-WebRequest -Uri $url -OutFile '%MODEL_PATH%' -TimeoutSec 3600"
    if %errorlevel% neq 0 (
        echo [WARN] Model download failed. Download manually and save to:
        echo        %CD%\%MODEL_PATH%
        pause
    ) else (
        echo Model downloaded successfully.
    )
) else (
    echo Model already exists: %MODEL_PATH%
)

REM 5. Download llama-server.exe
echo.
echo [3/6] llama-server.exe (translation engine)...
if not exist llama-server.exe (
    powershell -Command ^
        "$repo='ggerganov/llama.cpp';" ^
        "$r=Invoke-RestMethod 'https://api.github.com/repos/'+$repo+'/releases/latest';" ^
        "$a=$r.assets.Where({$_.name -match 'win-x64.*\.zip'})[0];" ^
        "if(-not $a){Write-Error 'No win-x64 zip found';exit 1};" ^
        "Write-Host 'Downloading:'$a.name;" ^
        "Invoke-WebRequest -Uri $a.browser_download_url -OutFile llama-cpp.zip;" ^
        "Expand-Archive llama-cpp.zip -DestinationPath llama-cpp;" ^
        "$s=Get-ChildItem llama-cpp -Recurse -Name 'llama-server.exe'[0];" ^
        "Copy-Item llama-cpp/$s llama-server.exe;" ^
        "Remove-Item -Recurse llama-cpp,llama-cpp.zip;" ^
        "Write-Host 'llama-server.exe ready'"
    if %errorlevel% neq 0 (
        echo [WARN] Download failed. Get from https://github.com/ggerganov/llama.cpp/releases
        echo        and place llama-server.exe in this directory.
        pause
    )
) else (
    echo llama-server.exe already exists, skipping.
)

REM 6. Download Tesseract + tessdata (OCR engine + language models)
echo.
echo [4/6] Tesseract + tessdata (OCR engine)...
if not exist tesseract\tesseract.exe (
    echo Downloading tesseract.exe ^(portable^)...
    powershell -Command ^
        "$url='https://github.com/nicdumz/ghostscript-mupdf-tesseract/releases/download/tesseract-5.5.0/tesseract-5.5.0-w64.zip';" ^
        "try {" ^
        "  Invoke-WebRequest -Uri $url -OutFile te.zip -TimeoutSec 120;" ^
        "  Expand-Archive te.zip -DestinationPath te_ext;" ^
        "  $exe=Get-ChildItem te_ext -Recurse -Name 'tesseract.exe'[0];" ^
        "  Copy-Item te_ext/$exe tesseract/tesseract.exe;" ^
        "  Remove-Item -Recurse te_ext,te.zip;" ^
        "  Write-Host 'tesseract.exe ready'" ^
        "} catch { Write-Host 'WARNING: tesseract.exe download failed: ' + $_ }"
)

echo Downloading tessdata language files (chi_sim + eng)...
set TESSDATA_BASE=https://raw.githubusercontent.com/tesseract-ocr/tessdata/main
for %%L in (chi_sim eng) do (
    if not exist "tesseract\tessdata\%%L.traineddata" (
        powershell -Command ^
            "try {" ^
            "  Invoke-WebRequest -Uri '%TESSDATA_BASE%/%%L.traineddata' -OutFile 'tesseract\tessdata\%%L.traineddata' -TimeoutSec 60;" ^
            "  Write-Host '  %%L.traineddata OK'" ^
            "} catch { Write-Host '  WARNING: %%L.traineddata download failed' }"
    ) else (
        echo   %%L.traineddata already exists
    )
)
echo OCR engine ready.

REM 7. PyInstaller build
echo.
echo [5/6] Building exe with PyInstaller...
pyinstaller --clean --noconfirm epub_translator.spec
if %errorlevel% neq 0 (
    echo [ERROR] PyInstaller build failed.
    pause
    exit /b 1
)

REM 8. Package
echo.
echo [6/6] Creating release package...
copy /Y dist\EPUB-Translator.exe release\EPUB-Translator\ >nul
if exist llama-server.exe copy /Y llama-server.exe release\EPUB-Translator\ >nul
if exist tesseract\tesseract.exe (
    xcopy /E /I /Y tesseract release\EPUB-Translator\tesseract\ >nul
    for %%L in (chi_sim eng) do (
        if exist "tesseract\tessdata\%%L.traineddata" (
            echo   OCR model: %%L.traineddata bundled
        )
    )
)
if exist "%MODEL_PATH%" (
    if not exist release\EPUB-Translator\models mkdir release\EPUB-Translator\models
    echo Copying translation model ^(~3.5GB^)...
    copy /Y "%MODEL_PATH%" release\EPUB-Translator\models\ >nul
)

REM Write README
(
echo EPUB Translator — 本地离线 EPUB 翻译工具
echo ==========================================
echo.
echo 开箱即用，无需安装任何依赖！
echo.
echo 包含的模型:
echo   models/Hy-MT2-1.8B-Q8_0.gguf — 翻译模型 ^(3.5GB^)
echo   tesseract/tessdata/          — OCR 语言包 ^(chi_sim + eng^)
echo.
echo 使用方法:
echo   1. 双击 EPUB-Translator.exe 启动
echo   2. 选择 EPUB 文件、目标语言
echo   3. 点击"开始"翻译
echo.
echo 翻译中可随时暂停/继续，关闭后自动保存断点。
echo 输出: ^<原名^>.translated.epub
) > release\EPUB-Translator\使用说明.txt

echo.
echo ============================================
echo   Build complete!
echo   Release folder: release\EPUB-Translator\
echo   Distribute this folder as a zip archive.
echo ============================================
echo.
pause
