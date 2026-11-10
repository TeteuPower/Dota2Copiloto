; Instalador do Copiloto Dota 2 (Inno Setup 6)
; Build: scripts\build_installer.ps1 (passa /DAppVersion=1.0.N)
;
; Auto-suficiente: depois dele o app funciona sem nenhum passo manual.
;  - instala POR USUARIO (%LOCALAPPDATA%\Programs) -> as atualizacoes automaticas
;    do proprio app rodam silenciosas, sem pedir administrador
;  - remove a versao antiga instalada em Arquivos de Programas (1 UAC, uma vez so)
;  - fecha o Copiloto aberto ANTES de atualizar (POST /shutdown; taskkill de garantia)
;  - configura o Dota 2: acha o jogo em qualquer biblioteca da Steam, grava o .cfg
;    do GSI e a opcao -gamestateintegration (CopilotoDota2.exe --provision)
;  - o Claude ja vem dentro do app (nao precisa de Node/npm); o login do Claude e
;    do Codex e feito pelo painel, em Configuracoes
;  - reabre o app ao final (instalacao silenciosa = atualizacao -> abre na bandeja)

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Copiloto Dota 2"
#define AppExe  "CopilotoDota2.exe"
#define AppGuid "{A3E6F2D8-7B41-4C59-9E02-6D8B3C5A1F47}"

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
DisableDirPage=auto
OutputDir=..\dist
OutputBaseFilename=CopilotoDota2-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Por usuario: sem UAC (necessario pra atualizacao silenciosa por dentro do app)
PrivilegesRequired=lowest
UsedUserAreasWarning=no
CloseApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "dotasetup"; Description: "Configurar o Dota 2 automaticamente (GSI + opção de inicialização — se a Steam estiver aberta, ela fecha e reabre sozinha)"; GroupDescription: "Opções:"
Name: "startup"; Description: "Iniciar o Copiloto junto com o Windows (fica na bandeja)"; GroupDescription: "Opções:"; Flags: unchecked

[Registry]
; Inicio automatico (opcional). --startup: sobe direto pra bandeja, sem abrir o navegador.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "CopilotoDota2"; ValueData: """{app}\{#AppExe}"" --startup"; \
  Tasks: startup; Flags: uninsdeletevalue

[Files]
Source: "..\dist\CopilotoDota2\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; versoes antigas: arquivos que mudaram de nome nao ficam sobrando
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; Configura o Dota (o que antes era manual). Na atualizacao silenciosa nao fecha a Steam.
Filename: "{app}\{#AppExe}"; Parameters: "--provision {code:ProvisionArgs}"; \
  StatusMsg: "Configurando o Dota 2..."; Flags: runhidden waituntilterminated; Tasks: dotasetup
Filename: "{app}\{#AppExe}"; Parameters: "{code:LaunchArgs}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
  Flags: nowait postinstall

[Code]
// ---------- argumentos ----------
function ProvisionArgs(Param: String): String;
begin
  if WizardSilent then Result := '' else Result := '--fechar-steam';
end;

function LaunchArgs(Param: String): String;
begin
  // atualizacao automatica (silenciosa): volta direto pra bandeja, sem abrir o navegador
  if WizardSilent then Result := '--startup' else Result := '';
end;

// ---------- pagina de informacao ----------
procedure InitializeWizard();
var
  Page: TOutputMsgMemoWizardPage;
begin
  Page := CreateOutputMsgMemoPage(wpWelcome, 'Tudo pronto pra funcionar',
    'Nada pra instalar à parte',
    'O Copiloto já traz tudo o que precisa:', '');
  Page.RichEditViewer.Text :=
    '- Claude: já vem dentro do app (não precisa de Node, npm nem terminal).' + #13#10 +
    '- Dota 2: o instalador acha o jogo em qualquer biblioteca da Steam e configura o' + #13#10 +
    '  GSI e a opção de inicialização (-gamestateintegration) sozinho.' + #13#10 +
    '- Atualizações: o próprio app baixa e instala as versões novas.' + #13#10 + #13#10 +
    'Depois de instalar, abra Configurações no painel e conecte a sua IA:' + #13#10 +
    '  • Claude — cole o token da sua assinatura (o painel tem o botão "Gerar token")' + #13#10 +
    '  • Codex — entre com a sua conta do ChatGPT (botão "Entrar com ChatGPT")' + #13#10 +
    'Qualquer um dos dois já liga o chat, a leitura do placar e o draft.';
end;

// ---------- fecha o app aberto antes de instalar/atualizar ----------
procedure KillRunningApp();
var
  R: Integer;
begin
  // 1) fechamento LIMPO pelo proprio app (endpoint /shutdown)
  Exec('powershell.exe',
       '-NoProfile -Command "try { (New-Object Net.WebClient).UploadString(''http://127.0.0.1:39317/shutdown'', '''') } catch {}"',
       '', SW_HIDE, ewWaitUntilTerminated, R);
  Sleep(1500);
  // 2) garantia (se o processo sobreviveu ou era outra instalacao)
  Exec(ExpandConstant('{cmd}'), '/C taskkill /F /IM {#AppExe} >nul 2>&1', '',
       SW_HIDE, ewWaitUntilTerminated, R);
  Sleep(500);
end;

// ---------- versao antiga (instalada para todos, em Arquivos de Programas) ----------
const
  OldUninstKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppGuid}_is1';

function OldAdminUninstaller(var Cmd: String): Boolean;
begin
  Result := False;
  if IsWin64 and RegQueryStringValue(HKLM64, OldUninstKey, 'UninstallString', Cmd) then
    Result := True
  else if RegQueryStringValue(HKLM32, OldUninstKey, 'UninstallString', Cmd) then
    Result := True;
end;

procedure RemoveOldAdminInstall();
var
  Cmd: String;
  R: Integer;
begin
  if not OldAdminUninstaller(Cmd) then exit;
  Cmd := RemoveQuotes(Cmd);
  if not WizardSilent then
    MsgBox('Encontrei uma versão antiga do Copiloto instalada em Arquivos de Programas.' + #13#10 + #13#10 +
           'Ela será removida agora (o Windows pode pedir permissão de administrador, só desta vez).' + #13#10 +
           'Suas configurações, chaves e histórico são mantidos.', mbInformation, MB_OK);
  if not ShellExec('runas', Cmd, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE,
                   ewWaitUntilTerminated, R) then
    Log('Nao consegui remover a versao antiga: ' + SysErrorMessage(R));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillRunningApp();
  RemoveOldAdminInstall();
  Result := '';
end;

// tambem fecha antes de DESINSTALAR
function InitializeUninstall(): Boolean;
begin
  KillRunningApp();
  Result := True;
end;
