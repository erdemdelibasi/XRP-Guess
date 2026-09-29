' Runs trigger_predict.ps1 completely windowless.
' PowerShell's "-WindowStyle Hidden" creates the window first and hides it
' after -- in an interactive logon session this is a brief visible flash.
' WScript.Shell.Run with style 0 never creates the window at all, so there
' is no flash. Wait parameter True so Task Scheduler's
' "MultipleInstances=IgnoreNew" setting works correctly (wscript.exe shows
' as "running" until the powershell process actually exits).
Set shell = CreateObject("WScript.Shell")
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & scriptDir & "\trigger_predict.ps1"""
shell.Run cmd, 0, True
