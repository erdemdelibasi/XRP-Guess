' Runs trigger_workflow.ps1 with no window at all, forwarding this script's
' arguments (e.g. "-Workflow predict.yml -WindowHours 12") -- see
' run_kanal_finans_hidden.vbs for why WScript.Shell.Run style 0 and not
' powershell.exe -WindowStyle Hidden, and why the third argument (wait) is True.
Set shell = CreateObject("WScript.Shell")
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
args = ""
For Each a In WScript.Arguments
    args = args & " """ & a & """"
Next
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & scriptDir & "\trigger_workflow.ps1""" & args
shell.Run cmd, 0, True
