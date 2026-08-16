@echo off
rem LiveCaption Windows launcher. Double-click or:
rem   start.bat --source auto --asr hf --hf-model Qwen/Qwen3-ASR-0.6B [--record]
rem   start.bat --source auto --asr hf-stream --hf-model nvidia/nemotron-3.5-asr-streaming-0.6b
cd /d "%~dp0.."
python src\python\win_host.py %*
if errorlevel 1 pause
