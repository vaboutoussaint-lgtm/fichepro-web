@echo off
echo ================================================
echo   FichePro Manager - Compilation en .exe
echo ================================================
echo.

:: Installer les dependances
echo [1/3] Installation des dependances...
pip install -r requirements.txt --quiet

:: Compiler avec PyInstaller
echo [2/3] Compilation en cours...
pyinstaller ^
  --onefile ^
  --noconsole ^
  --name "FichePro Manager" ^
  --icon "static/icon.ico" ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  --add-data "data;data" ^
  --hidden-import flask ^
  --hidden-import openpyxl ^
  --hidden-import pandas ^
  --hidden-import requests ^
  app.py

echo [3/3] Nettoyage...
rmdir /s /q build
del /q "FichePro Manager.spec"

echo.
echo ================================================
echo   TERMINE ! Le fichier .exe est dans : dist/
echo   Fichier : "FichePro Manager.exe"
echo ================================================
pause
