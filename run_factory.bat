@echo off
chcp 65001 >nul
cd /d E:\1workspace\duanju-car
if not exist logs mkdir logs
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set REST_EVERY_MIN=60
set REST_MIN=10
echo ==== started %date% %time% ==== >> logs\factory.log
"E:\1workspace\local-video-gen\venv\Scripts\python.exe" make_shots.py next >> logs\factory.log 2>&1
echo ==== finished %date% %time% exit=%errorlevel% ==== >> logs\factory.log
