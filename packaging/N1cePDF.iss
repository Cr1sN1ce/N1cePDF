; ===========================================================================
;  N1cePDF installer script - Inno Setup 6.3 or newer (needs x64compatible support)
;
;  Compile (after the PyInstaller build produced ..\dist\N1cePDF\):
;      ISCC.exe packaging\N1cePDF.iss
;  Optional switches:
;      /DAppPublisher="Your Name or Company"   publisher shown in Settings > Installed apps
;      /DWithLicensePage                        show a licence page (use once LICENSE is final text)
;
;  Output: ..\release\N1cePDF-Setup-1.1.0-x64.exe
; ===========================================================================

#define AppName      "N1cePDF"
#define AppVersion   "1.1.0"
#define AppExeName   "N1cePDF.exe"
#define AppGuid      "{{E2E2DFF2-7F7E-450D-A602-53E414E1F1EA}"
; Must equal n1cepdf.runtime.APP_USER_MODEL_ID so shortcuts and the running window share one taskbar button.
#define AppUserModelId "N1cePDF.Desktop"
#ifndef AppPublisher
  #define AppPublisher "N1cePDF"
#endif

[Setup]
AppId={#AppGuid}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}.0
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
VersionInfoCompany={#AppPublisher}

; --- install location: per-user by default (no administrator rights), "all users" offered on request ---
; {autopf} resolves to %LOCALAPPDATA%\Programs for a per-user install and to Program Files for an
; all-users (administrator) install. The destination page is always shown; the user may browse or type a path.
DefaultDirName={autopf}\{#AppName}
DisableDirPage=no
UsePreviousAppDir=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

; --- wizard pages: Welcome, [License], Select Destination, Select Additional Tasks, Ready, Progress, Finish ---
DisableWelcomePage=no
DisableProgramGroupPage=yes
DisableReadyPage=no
DisableFinishedPage=no
WizardStyle=modern
ShowLanguageDialog=no
#ifdef WithLicensePage
LicenseFile=..\LICENSE
#endif

; --- branding / Settings > Installed apps ---
SetupIconFile=..\assets\n1cepdf.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
OutputDir=..\release
OutputBaseFilename=N1cePDF-Setup-{#AppVersion}-x64

; --- packaging ---
Compression=lzma2/ultra64
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Shortcuts: independent, both ticked by default. Nothing is created unless its task is selected.
Name: "desktopicon";   Description: "Create a &desktop shortcut";    GroupDescription: "Shortcuts:"
Name: "startmenuicon"; Description: "Create a &Start Menu shortcut"; GroupDescription: "Shortcuts:"
; Optional and OFF by default. Registers N1cePDF as a PDF handler ("Open with" list, Settings > Default apps).
; It never sets the default PDF application - Windows leaves that choice to the user.
Name: "fileassoc"; Description: "Offer N1cePDF in the ""Open with"" list for .pdf files (does not change your default PDF app)"; GroupDescription: "File types:"; Flags: unchecked

[Files]
; Everything under dist\N1cePDF comes from the PyInstaller build (no .venv, caches or test files).
Source: "..\dist\N1cePDF\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Created only when the matching task is ticked. Both use the icon embedded in the installed executable.
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"; AppUserModelID: "{#AppUserModelId}"; Tasks: desktopicon
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"; AppUserModelID: "{#AppUserModelId}"; Tasks: startmenuicon

[Registry]
; All entries exist only with the "fileassoc" task and are removed on uninstall. HKA = HKCU for a per-user
; install, HKLM for an all-users install. The default value of .pdf is NOT touched.
Root: HKA; Subkey: "Software\Classes\N1cePDF.Document"; ValueType: string; ValueName: ""; ValueData: "PDF document"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\N1cePDF.Document\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExeName},0"; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\N1cePDF.Document\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExeName}"" ""%1"""; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: none; ValueName: "N1cePDF.Document"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExeName}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExeName}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExeName}"" ""%1"""; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExeName}\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""; Tasks: fileassoc
Root: HKA; Subkey: "Software\{#AppName}\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#AppName}"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\{#AppName}\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Offline PDF viewer and editor"; Tasks: fileassoc
Root: HKA; Subkey: "Software\{#AppName}\Capabilities\FileAssociations"; ValueType: string; ValueName: ".pdf"; ValueData: "N1cePDF.Document"; Tasks: fileassoc
Root: HKA; Subkey: "Software\{#AppName}"; Flags: uninsdeletekeyifempty; Tasks: fileassoc
Root: HKA; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "{#AppName}"; ValueData: "Software\{#AppName}\Capabilities"; Flags: uninsdeletevalue; Tasks: fileassoc

[Run]
; Final page checkbox "Launch N1cePDF" - ticked by default (no "unchecked" flag), skipped in silent installs.
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
// Uninstall removes the program files, shortcuts and registry entries it created. It never touches the
// user's PDF documents. Personal data (recent-files list, settings, logs) is kept unless the user says yes.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if not UninstallSilent then
    begin
      if MsgBox('Also delete your N1cePDF settings, recent-files list and log files?' + #13#10 + #13#10 +
                'Your PDF documents are never deleted.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(ExpandConstant('{userappdata}\{#AppName}'), True, True, True);
        DelTree(ExpandConstant('{localappdata}\{#AppName}'), True, True, True);
      end;
    end;
  end;
end;
