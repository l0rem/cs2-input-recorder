
Set objShell = WScript.CreateObject("WScript.Shell")
' Recorder with default args: auto-attach to cs2.exe, sessions dir = exe folder
objShell.Run """C:\Users\lorem\Desktop\strafes\target\release\cs2-input-recorder.exe""", 0, False
