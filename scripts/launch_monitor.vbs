' Lance le live monitor en arrière-plan sans console
' Utilisation: cscript //nologo launch_monitor.vbs

Set shell = CreateObject("WScript.Shell")
' 0 = hidden window, False = don't wait
shell.Run "cmd /c set PYTHONPATH=" & chr(34) & "C:\Users\saint\Documents\MT5_FTMO_IA.7" & chr(34) & " && python.exe scripts\live_monitor.py --interval 45", 0, False
