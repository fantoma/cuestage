; Installatore Windows di CueStage (Inno Setup 6).
;   iscc /DAppVersion=1.0.0 packaging\windows\cuestage.iss
; Prende la cartella creata da PyInstaller (dist\CueStage) e produce dist\CueStage-<versione>-windows-setup.exe

#ifndef AppVersion
  #define AppVersion "1.0.4"
#endif
#define Root "..\.."

[Setup]
AppId={{6E2B7C1A-6C55-4F9B-9A51-2F3C0D2B7E41}
AppName=CueStage
AppVersion={#AppVersion}
AppVerName=CueStage {#AppVersion}
AppPublisher=CueStage
DefaultDirName={autopf}\CueStage
DefaultGroupName=CueStage
UninstallDisplayIcon={app}\CueStage.exe
OutputDir={#Root}\dist
OutputBaseFilename=CueStage-{#AppVersion}-windows-setup
SetupIconFile={#Root}\packaging\cuestage.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; installazione per l'utente corrente senza diritti di amministratore, se si preferisce
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "assoc"; Description: "Apri i file .cueshow con CueStage"; GroupDescription: "File:"
Name: "firewall"; Description: "Consenti CueStage nel firewall (mixer, OSC, web, istanze secondarie)"; GroupDescription: "Rete:"; Check: IsAdminInstallMode

[Files]
Source: "{#Root}\dist\CueStage\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\CueStage"; Filename: "{app}\CueStage.exe"
Name: "{group}\{cm:UninstallProgram,CueStage}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\CueStage"; Filename: "{app}\CueStage.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.cueshow"; ValueType: string; ValueName: ""; ValueData: "CueStage.Show"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\CueStage.Show"; ValueType: string; ValueName: ""; ValueData: "Show CueStage"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\CueStage.Show\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\CueStage.exe,0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\CueStage.Show\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\CueStage.exe"" ""%1"""; Tasks: assoc

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""CueStage"" dir=in action=allow program=""{app}\CueStage.exe"" enable=yes"; Flags: runhidden; Tasks: firewall
Filename: "{app}\CueStage.exe"; Description: "{cm:LaunchProgram,CueStage}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""CueStage"""; Flags: runhidden; RunOnceId: "fw"
