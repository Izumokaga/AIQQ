' AIQQ hidden launcher: run a batch file with no console window.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base
cmd = ""
For Each arg In WScript.Arguments
    cmd = cmd & " """ & Replace(arg, """", """""") & """"
Next
sh.Run "cmd.exe /c" & cmd, 0, False
