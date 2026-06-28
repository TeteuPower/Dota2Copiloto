@echo off
title Copiloto Dota 2
cd /d "c:\Trabalho\dota2"
echo ============================================================
echo   Copiloto Dota 2 - iniciando...
echo   Painel: http://localhost:3000
echo   (feche esta janela para desligar)
echo ============================================================
python server.py
echo.
echo Servidor encerrado.
pause
