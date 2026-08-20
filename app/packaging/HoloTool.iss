; HoloTool 安裝程式（Inno Setup 6）
;
; 不要直接用 Inno Setup 開這個檔按編譯 —— 請在專案根目錄執行：
;     .venv\Scripts\python.exe app\packaging\build_installer.py
; 那個腳本會先打包 exe、產生圖示，再帶著正確的參數呼叫 ISCC。
;
; 產出：dist\HoloToolSetup.exe（單一檔案，拿給別人雙擊就能裝）

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

#define MyAppName "HoloTool"
#define MyAppExeName "HoloTool.exe"
#define MyAppPublisher "WANIN INTERNATIONAL"
#define SrcDir "..\dist\HoloTool"
; 裝好之後 exe 旁邊只留這一個資料夾，程式內容與所有資料都塞在裡面。
; 名稱必須和 src/paths.py 的 BUNDLE_SUBDIR、build_exe.py 的 --contents-directory 一致。
#define Bundle "app"

[Setup]
; AppId 決定「這是不是同一個程式」。改了它，舊版就不會被視為同一套而無法覆蓋升級，
; 所以之後改版**不要動這一行**。
AppId={{63638D29-E4A0-4872-AA6B-8B2792C0EE66}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}

; 預設裝在使用者自己的 AppData 底下，理由很重要：
; 這個程式會把校準檔、卡牌樣板、log 寫在**自己旁邊**。裝到 Program Files 的話
; 一般權限寫不進去，程式一開就壞。裝在這裡不需要系統管理員權限，也不會被擋。
; 使用者仍然可以在安裝畫面自己改路徑（挑一個自己有寫入權限的資料夾即可）。
DefaultDirName={localappdata}\HoloTool
DisableDirPage=no
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

OutputDir=..\dist
OutputBaseFilename=HoloToolSetup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

#ifdef HaveIcon
SetupIconFile=icon.ico
#endif
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
; 連解除安裝程式（unins000.exe / .dat）也收進子資料夾，
; 這樣安裝後的資料夾裡真的就只剩一個 HoloTool.exe。
UninstallFilesDir={app}\{#Bundle}

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
#ifdef HaveChinese
Name: "zhtw"; MessagesFile: "compiler:Languages\ChineseTraditional.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "在桌面建立捷徑 (Create a desktop shortcut)"; GroupDescription: "額外工作 (Additional shortcuts):"

[Dirs]
; 程式執行時要寫入的資料夾，先建好免得第一次開啟出錯
Name: "{app}\{#Bundle}\data"
Name: "{app}\{#Bundle}\logs"
Name: "{app}\{#Bundle}\debug_captures"
Name: "{app}\{#Bundle}\card_templates\parts"

[Files]
; ---- 程式本體：每次安裝都覆蓋成新的 ----
Source: "{#SrcDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; PyInstaller 的執行檔內容（--contents-directory app），連同 defaults\ 一起。
; config / card_templates 排除掉，改用下面「已存在就不覆蓋」的規則；
; data / logs / debug_captures 是你自己的紀錄，不該打包給別人。
Source: "{#SrcDir}\{#Bundle}\*"; DestDir: "{app}\{#Bundle}"; \
    Excludes: "config\*,card_templates\*,data\*,logs\*,debug_captures\*"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\README.md"; DestDir: "{app}\{#Bundle}"; Flags: ignoreversion isreadme

; ---- 使用者資料：只在「還沒有」時才放進去，解除安裝時也不刪 ----
; 對方裝完就有一份可用的校準與樣板；但如果他自己重新校準過，
; 之後再裝新版**不會**把他辛苦調的東西蓋掉。
Source: "{#SrcDir}\{#Bundle}\config\*"; DestDir: "{app}\{#Bundle}\config"; \
    Flags: onlyifdoesntexist uninsneveruninstall recursesubdirs createallsubdirs
Source: "{#SrcDir}\{#Bundle}\card_templates\*"; DestDir: "{app}\{#Bundle}\card_templates"; \
    Flags: onlyifdoesntexist uninsneveruninstall recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即開啟 {#MyAppName}"; \
    WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; PyInstaller 產生的快取與程式自己寫的暫存檔，解除安裝時一併清掉
Type: filesandordirs; Name: "{app}\{#Bundle}\logs"
Type: filesandordirs; Name: "{app}\{#Bundle}\debug_captures"
Type: filesandordirs; Name: "{app}\{#Bundle}\__pycache__"

[Code]
// 裝到沒有寫入權限的地方（例如 Program Files）程式一開就壞，
// 所以在按下「安裝」之前先實際寫一個檔試試看。
function NextButtonClick(CurPageID: Integer): Boolean;
var
  Probe: string;
begin
  Result := True;
  if CurPageID <> wpSelectDir then
    Exit;

  Probe := AddBackslash(WizardDirValue) + 'holotool_write_test.tmp';
  ForceDirectories(WizardDirValue);
  if SaveStringToFile(Probe, 'ok', False) then
  begin
    DeleteFile(Probe);
    Exit;
  end;

  Result := False;
  MsgBox(
    '這個資料夾沒有寫入權限：' + #13#10 + #13#10 +
    WizardDirValue + #13#10 + #13#10 +
    'HoloTool 會把校準檔、卡牌樣板與紀錄寫在自己旁邊，裝在唯讀的位置' +
    '（例如 C:\Program Files）程式一開啟就會出錯。' + #13#10 + #13#10 +
    '請改成你自己有權限的資料夾，例如：' + #13#10 +
    ExpandConstant('{localappdata}') + '\HoloTool' + #13#10 +
    ExpandConstant('{userdocs}') + '\HoloTool',
    mbError, MB_OK);
end;
