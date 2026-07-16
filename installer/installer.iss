; Instalador do Copiloto Dota 2 (Inno Setup 6)
; Build: scripts\build_installer.ps1 (passa /DAppVersion=1.0.N)
;
; O que ele faz:
;  - deixa escolher a pasta de instalacao (upgrade reutiliza a mesma; AppId fixo)
;  - fecha o Copiloto aberto ANTES de atualizar (POST /shutdown; taskkill de garantia)
;  - reabre o app ao final (tambem em instalacao silenciosa)
;  - pagina de pre-requisitos: verifica o CLI do Claude (assinatura) e orienta
;  - opcional: copia o .cfg do GSI pra pasta do Dota 2 (se encontrar a instalacao)

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Copiloto Dota 2"
#define AppExe  "CopilotoDota2.exe"

[Setup]
AppId={{A3E6F2D8-7B41-4C59-9E02-6D8B3C5A1F47}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
AppPublisher=TeteuPower
AppPublisherURL=https://github.com/TeteuPower/Dota2Copiloto
DefaultDirName={autopf}\CopilotoDota2
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
OutputDir=..\dist
OutputBaseFilename=CopilotoDota2-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
CloseApplications=no
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "gsicfg"; Description: "Copiar a configuração do GSI para o Dota 2 (detectado nesta máquina)"; Check: DotaFound
Name: "startup"; Description: "Iniciar o Copiloto junto com o Windows (fica na bandeja)"; GroupDescription: "Opções:"; Flags: unchecked

[Registry]
; Inicio automatico (opcional). --startup: sobe direto pra bandeja, sem abrir o navegador.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "CopilotoDota2"; ValueData: """{app}\{#AppExe}"" --startup"; \
  Tasks: startup; Flags: uninsdeletevalue

[Files]
Source: "..\dist\CopilotoDota2\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\config\gamestate_integration_copiloto.cfg"; DestDir: "{code:GsiDir}"; Tasks: gsicfg; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall runasoriginaluser

[Code]
var
  DotaDir: String;
  DotaSearched: Boolean;

// ---------- deteccao do Dota 2 (pra copiar o .cfg do GSI) ----------
function FindDota(): String;
var
  Steam: String;
  Cands: array[0..3] of String;
  I: Integer;
begin
  Result := '';
  Cands[0] := '';
  if RegQueryStringValue(HKEY_CURRENT_USER, 'Software\Valve\Steam', 'SteamPath', Steam) then
    Cands[0] := Steam + '\steamapps\common\dota 2 beta';
  Cands[1] := 'D:\SteamLibrary\steamapps\common\dota 2 beta';
  Cands[2] := ExpandConstant('{commonpf32}') + '\Steam\steamapps\common\dota 2 beta';
  Cands[3] := 'E:\SteamLibrary\steamapps\common\dota 2 beta';
  for I := 0 to 3 do
    if (Cands[I] <> '') and DirExists(Cands[I]) then
    begin
      Result := Cands[I];
      exit;
    end;
end;

function DotaFound(): Boolean;
begin
  if not DotaSearched then
  begin
    DotaDir := FindDota();
    DotaSearched := True;
  end;
  Result := DotaDir <> '';
end;

function GsiDir(Param: String): String;
begin
  Result := DotaDir + '\game\dota\cfg\gamestate_integration';
end;

// ---------- pre-requisito: CLI do Claude (assinatura) ----------
function ClaudeCliFound(): Boolean;
var
  R: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/C where claude >nul 2>&1', '',
                 SW_HIDE, ewWaitUntilTerminated, R) and (R = 0);
end;

procedure InitializeWizard();
var
  Page: TOutputMsgMemoWizardPage;
  Txt: String;
begin
  Page := CreateOutputMsgMemoPage(wpWelcome, 'Pré-requisitos',
    'Cérebro de IA — Claude pela sua assinatura',
    'O Copiloto usa o CLI do Claude logado na SUA assinatura (nada é copiado; o app usa o login automaticamente).',
    '');
  if ClaudeCliFound() then
    Txt := '[ OK ]  CLI do Claude encontrado neste computador.' + #13#10 + #13#10 +
           'Se você já fez login alguma vez (comando "claude"), o Copiloto conecta sozinho. ' +
           'O indicador na barra lateral do painel mostra o status real da conexão.'
  else
    Txt := '[ FALTA ]  CLI do Claude NÃO foi encontrado.' + #13#10 + #13#10 +
           'A instalação pode continuar (o app funciona em "modo básico"), mas para ligar a IA:' + #13#10 + #13#10 +
           '  1) Instale o CLI:   npm install -g @anthropic-ai/claude-code' + #13#10 +
           '  2) Faça login:      rode "claude" no terminal e siga o login da assinatura' + #13#10 + #13#10 +
           'Depois é só abrir o Copiloto — ele detecta e conecta sozinho.';
  Txt := Txt + #13#10 + #13#10 +
         'Voz do copiloto (opcional): configure sua chave da OpenAI depois, em Configurações no painel.';
  Page.RichEditViewer.Text := Txt;
end;

// ---------- fecha o app aberto antes de instalar/atualizar ----------
procedure KillRunningApp();
var
  R: Integer;
begin
  // 1) fechamento LIMPO pelo proprio app (endpoint /shutdown)
  Exec('powershell.exe',
       '-NoProfile -Command "try { (New-Object Net.WebClient).UploadString(''http://127.0.0.1:49317/shutdown'', '''') } catch {}"',
       '', SW_HIDE, ewWaitUntilTerminated, R);
  Sleep(1500);
  // 2) garantia (se o processo sobreviveu ou era outra instalacao)
  Exec(ExpandConstant('{cmd}'), '/C taskkill /F /IM {#AppExe} >nul 2>&1', '',
       SW_HIDE, ewWaitUntilTerminated, R);
  Sleep(500);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillRunningApp();
  Result := '';
end;

// tambem fecha antes de DESINSTALAR
function InitializeUninstall(): Boolean;
begin
  KillRunningApp();
  Result := True;
end;
