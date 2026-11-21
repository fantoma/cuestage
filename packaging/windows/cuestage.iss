; Installatore Windows di Cuevivo (Inno Setup 6).
;   iscc /DAppVersion=1.0.0 packaging\windows\cuestage.iss
; Prende la cartella creata da PyInstaller (dist\Cuevivo) e produce dist\Cuevivo-<versione>-windows-setup.exe

#ifndef AppVersion
  #define AppVersion "1.0.31"
#endif
#define Root "..\.."

[Setup]
; AppId è quello di CueStage (nome precedente): il nuovo installatore aggiorna l'installazione esistente
AppId={{6E2B7C1A-6C55-4F9B-9A51-2F3C0D2B7E41}
AppName=Cuevivo
AppVersion={#AppVersion}
AppVerName=Cuevivo {#AppVersion}
AppPublisher=Cuevivo
DefaultDirName={autopf}\Cuevivo
DefaultGroupName=Cuevivo
UninstallDisplayIcon={app}\Cuevivo.exe
OutputDir={#Root}\dist
OutputBaseFilename=Cuevivo-{#AppVersion}-windows-setup
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
Name: "assoc"; Description: "Apri i file .cueshow con Cuevivo"; GroupDescription: "File:"
Name: "firewall"; Description: "Consenti Cuevivo nel firewall (mixer, OSC, web, istanze secondarie)"; GroupDescription: "Rete:"; Check: IsAdminInstallMode

[InstallDelete]
; residui del nome precedente (CueStage) in un'installazione aggiornata
Type: files; Name: "{app}\CueStage.exe"
Type: files; Name: "{group}\CueStage.lnk"
Type: files; Name: "{autodesktop}\CueStage.lnk"

[Files]
Source: "{#Root}\dist\Cuevivo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Cuevivo"; Filename: "{app}\Cuevivo.exe"
Name: "{group}\{cm:UninstallProgram,Cuevivo}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Cuevivo"; Filename: "{app}\Cuevivo.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\CueStage.Show"; ValueType: none; Flags: deletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\.cueshow"; ValueType: string; ValueName: ""; ValueData: "Cuevivo.Show"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\Cuevivo.Show"; ValueType: string; ValueName: ""; ValueData: "Show Cuevivo"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\Cuevivo.Show\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\Cuevivo.exe,0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\Cuevivo.Show\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\Cuevivo.exe"" ""%1"""; Tasks: assoc

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""CueStage"""; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""Cuevivo"" dir=in action=allow program=""{app}\Cuevivo.exe"" enable=yes"; Flags: runhidden; Tasks: firewall
Filename: "{app}\Cuevivo.exe"; Description: "{cm:LaunchProgram,Cuevivo}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Cuevivo"""; Flags: runhidden; RunOnceId: "fw"
