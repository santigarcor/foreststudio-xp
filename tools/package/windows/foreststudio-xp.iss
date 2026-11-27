; ForestStudio XP - Windows installer (Inno Setup 6). Built by tools/package/build.py, which passes:
;   /DAppVersion=0.1.0  /DSourceDir=<packed tree: python\ + app\>  /DOutputDir=<dist>
;   /DRepoDir=<repository>  /DAppId=ForestStudio.XP (Windows AppUserModelID, = forest/app/main.py)
;
; Per-user install without admin rights into %LOCALAPPDATA%\Programs\ForestStudio XP.
; The shortcuts run the bundled pythonw.exe (no console window, rule 4) with -I (isolated from any
; PYTHONPATH / user site-packages) and -m forest.app.main.
; Uninstall removes only the program folder: never the data folder (~\.foreststudio or the one
; chosen in Settings) and never the forest_studio_xp link in X-Plane's Custom Scenery.

#ifndef AppVersion
  #error AppVersion is required (/DAppVersion=x.y.z)
#endif
#define AppName "ForestStudio XP"
#define Publisher "ForestStudio XP contributors"
#define Url "https://github.com/santigarcor/foreststudio-xp"
#define Pyw "{app}\python\pythonw.exe"
#define AppArgs "-I -m forest.app.main"
#define Ico "{app}\app\forest\app\icons\foreststudio-xp.ico"

[Setup]
; The AppId identifies this program for upgrades and uninstall. NEVER change it.
AppId={{E88FB503-2636-4976-BFF1-267D46B48FA0}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#Publisher}
AppPublisherURL={#Url}
AppSupportURL={#Url}/issues
AppUpdatesURL={#Url}/releases
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
DefaultDirName={userpf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile={#RepoDir}\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=ForestStudio-XP-{#AppVersion}-windows-x64-setup
SetupIconFile={#RepoDir}\forest\app\icons\foreststudio-xp.ico
UninstallDisplayIcon={#Ico}
UninstallDisplayName={#AppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; lzma2/ultra64 with block threads ran the compiler out of memory on ~385 MB of input (CI,
; 2026-11-26); max + a separate compressor process fits.
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Messages]
english.ConfirmUninstall=Remove %1 from this computer?%n%nOnly the program is removed. Your ForestStudio data folder (downloads, built tiles, settings) and the forest pack link in X-Plane's Custom Scenery are kept. To remove the pack from X-Plane, use "Uninstall pack" in the app first.
spanish.ConfirmUninstall=¿Desinstalar %1 de este equipo?%n%nSolo se elimina el programa. La carpeta de datos de ForestStudio (descargas, tiles generados, ajustes) y el enlace del pack en Custom Scenery de X-Plane se conservan. Para quitar el pack de X-Plane, use primero "Uninstall pack" en la aplicación.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; An upgrade starts from a clean program folder: no modules left over from the previous version.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app"

[Files]
Source: "{#SourceDir}\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#RepoDir}\tools\package\windows\forest.cmd"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{#Pyw}"; Parameters: "{#AppArgs}"; WorkingDir: "{app}\app"; IconFilename: "{#Ico}"; AppUserModelID: "{#AppId}"; Comment: "X-Plane 12 forest scenery builder"
Name: "{autodesktop}\{#AppName}"; Filename: "{#Pyw}"; Parameters: "{#AppArgs}"; WorkingDir: "{app}\app"; IconFilename: "{#Ico}"; AppUserModelID: "{#AppId}"; Tasks: desktopicon

[Run]
Filename: "{#Pyw}"; Parameters: "{#AppArgs}"; WorkingDir: "{app}\app"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Files the app made inside the program folder (Python's __pycache__). Only these two folders,
; never anything outside {app}.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app"
Type: dirifempty; Name: "{app}"
