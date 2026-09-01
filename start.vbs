' isaflow-bot — 콘솔 창 없이 트레이 모드로 실행한다.
' 이 파일의 "바로가기"를 shell:startup 폴더에 넣어 두면 로그온 시 자동 실행된다.
Option Explicit

Dim sh, fso, base, pyw
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base

pyw = base & "\.venv\Scripts\pythonw.exe"

If fso.FileExists(pyw) Then
    ' uv sync로 만들어진 가상환경을 직접 호출 (중간 cmd 창이 아예 안 생김)
    sh.Run """" & pyw & """ """ & base & "\main.py""", 0, False
Else
    ' 가상환경이 없으면 uv에 맡긴다 (창은 0으로 숨김)
    sh.Run "cmd /c uv run pythonw main.py", 0, False
End If