' Lanzador silencioso de abrir_por_palmada.py (sin ventana de consola).
' Para usar desde un atajo de teclado de Windows o desde G-Helper
' (apuntar la tecla a este .vbs). El .py hace el trabajo real.
Option Explicit
Dim carpeta, raiz, pythonw, script
carpeta = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
raiz = CreateObject("Scripting.FileSystemObject").GetParentFolderName(CreateObject("Scripting.FileSystemObject").GetParentFolderName(carpeta))
pythonw = raiz & "\venv\Scripts\pythonw.exe"
script = carpeta & "\abrir_por_palmada.py"
CreateObject("WScript.Shell").Run """" & pythonw & """ """ & script & """", 0, False
