@echo off
set TCL_LIBRARY=C:\Program Files\Python313\tcl\tcl8.6
set TK_LIBRARY=C:\Program Files\Python313\tcl\tk8.6
cd /d "%~dp0lib\parser"
start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0lib\parser\gui.py"
