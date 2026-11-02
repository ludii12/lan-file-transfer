@echo off
chcp 936 >nul
title 局域网文件传输
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 goto nopython

python -c "import sherpa_onnx, av, numpy, qrcode, PIL" >nul 2>nul
if errorlevel 1 goto installdeps
goto checkmodel

:nopython
echo.
echo   没有找到 Python，请先安装 Python 3.10 或更高版本：
echo   https://www.python.org/downloads/
echo   安装时记得勾选 "Add Python to PATH"
echo.
pause
exit /b 1

:installdeps
echo 首次运行：正在安装依赖，约 100MB，请等一会儿...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo 直连失败，改用清华镜像重试...
    python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)
python -c "import sherpa_onnx" >nul 2>nul
if errorlevel 1 goto depfail

:checkmodel
if not exist "模型\sherpa-onnx-paraformer-zh-2023-09-14\model.int8.onnx" goto getmodel
goto run

:getmodel
echo 正在下载语音模型，约 300MB，只需下载这一次...
python 04_下载模型.py

:run
start "" pythonw "01_启动.py"
exit /b 0

:depfail
echo.
echo   依赖没装成功，检查网络后重新双击本文件。
echo.
pause
exit /b 1
