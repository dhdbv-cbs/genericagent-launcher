#define MyAppName "GenericAgent Launcher"
#define MyAppPublisher "GenericAgent Launcher"
#ifndef MyVersion
  #define MyVersion "0.0.0-local"
#endif

[Setup]
AppId={{B7F9E2A6-0E0C-4A57-AF0D-1A188A26891A}
AppName={#MyAppName}
AppVersion={#MyVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\GenericAgentLauncher
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\release\{#MyVersion}\installer
OutputBaseFilename=GenericAgentLauncher-Setup-{#MyVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\LauncherBootstrap.exe
ChangesAssociations=no
DisableWelcomePage=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\release\{#MyVersion}\install\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\GenericAgent Launcher"; Filename: "{app}\LauncherBootstrap.exe"
Name: "{autodesktop}\GenericAgent Launcher"; Filename: "{app}\LauncherBootstrap.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Run]
Filename: "{app}\LauncherBootstrap.exe"; Description: "启动 GenericAgent Launcher"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\*"
Type: dirifempty; Name: "{app}\app\versions"
Type: dirifempty; Name: "{app}\app"
