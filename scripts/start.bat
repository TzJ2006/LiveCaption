@echo off
rem LiveCaption Windows launcher. Double-click or:
rem   start.bat --source both --asr hf --hf-model Qwen/Qwen3-ASR-0.6B [--record]
cd /d "%~dp0.."
python src\python\win_host.py %*
if errorlevel 1 pause
