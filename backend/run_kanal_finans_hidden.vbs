' Kanal Finans TS gorevini tamamen penceresiz calistirir.
' PowerShell'in "-WindowStyle Hidden"i pencereyi once olusturup sonra
' gizliyor -- Interactive logon oturumunda bu kisa bir flash olarak
' goruluyor (kullanici hem powershell hem cmd penceresi flash ettigini
' bildirdi). WScript.Shell.Run stil 0 ile pencereyi hic olusturmuyor,
' flash da olmuyor. Bekleme parametresi True: Task Scheduler'in
' "MultipleInstances=IgnoreNew" ayari duzgun calissin diye (wscript.exe
' powershell bitene kadar "calisiyor" gorunuyor).
Set shell = CreateObject("WScript.Shell")
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & scriptDir & "\run_kanal_finans.ps1"""
shell.Run cmd, 0, True
