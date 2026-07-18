"""
Paginas HTML do painel (servidas por web/server.py).
======================================================

Frontend inline (HTML+CSS+JS) em duas paginas:
  - DASHBOARD_HTML: o painel principal (/) - faz polling em /state a cada 1s
  - MINIMAP_HTML:   a 2a janela do minimapa ao vivo (/minimap)

Proximo passo natural: quebrar em arquivos estaticos (static/) servidos do disco.
"""

DASHBOARD_HTML = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Copiloto Dota 2</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;600;700;900&family=Rajdhani:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  /* ============ Tema Dota 2 ============ */
  :root{
    --bg:#070910; --panel:#10151f; --panel2:#0b101a; --panel-hi:#161e2b;
    --line:#212a39; --line-soft:#19212e;
    --gold:#c8aa6e; --gold-hi:#f1d191; --gold-dim:#7c6838;
    --red:#c0392b; --red-hi:#e85a45; --red-deep:#7c1f17;
    --rad:#7ec94f; --rad-dim:#3f6f29;
    --dire:#e24a3b; --dire-dim:#7a241c;
    --tx:#e9eef6; --tx2:#93a0b4; --tx3:#586272;
    --ok:#48c569; --warn:#d9a534;
    --r:5px;
    color-scheme:dark;
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{
    margin:0; background:var(--bg); color:var(--tx);
    font-family:'Rajdhani',system-ui,'Segoe UI',sans-serif; font-size:15px;
    overflow:hidden; -webkit-font-smoothing:antialiased;
  }
  body::before{
    content:''; position:fixed; inset:0; z-index:0; pointer-events:none;
    background:
      radial-gradient(1100px 480px at 50% -8%, rgba(192,57,43,.12), transparent 62%),
      radial-gradient(900px 700px at 112% 118%, rgba(40,64,100,.14), transparent 60%),
      linear-gradient(180deg,#0a0e15 0%,#06080d 100%);
  }
  ::-webkit-scrollbar{width:10px;height:10px}
  ::-webkit-scrollbar-track{background:#0a0e15}
  ::-webkit-scrollbar-thumb{background:#222c3b;border-radius:6px;border:2px solid #0a0e15}
  ::-webkit-scrollbar-thumb:hover{background:#33415a}

  .display{font-family:'Cinzel','Trajan Pro',serif}

  /* ============ Estrutura ============ */
  .app{position:relative;z-index:1;height:100vh;display:flex;flex-direction:column}
  .layout{flex:1;display:grid;grid-template-columns:214px 1fr;min-height:0}
  .sidebar{background:linear-gradient(180deg,#0c111a,#080b12);border-right:1px solid var(--line);
           display:flex;flex-direction:column;overflow-y:auto;padding:14px 0 12px}
  .content{overflow-y:auto;min-width:0;padding:20px 22px 40px}

  /* ============ Topbar ============ */
  .topbar{flex:none;height:66px;display:flex;align-items:center;gap:18px;padding:0 20px;
          background:linear-gradient(180deg,#11161f,#0b0f17);
          border-bottom:1px solid var(--line);
          box-shadow:0 2px 14px rgba(0,0,0,.45);position:relative}
  .topbar::after{content:'';position:absolute;left:0;right:0;bottom:-1px;height:1px;
                 background:linear-gradient(90deg,transparent,rgba(200,170,110,.45),transparent)}
  .brand{display:flex;align-items:center;gap:12px;min-width:200px}
  .brand .logo{width:40px;height:40px;flex:none;display:grid;place-items:center;
               background:radial-gradient(circle at 50% 35%,#3a0f0a,#170707);
               border:1px solid #5a1b13;border-radius:7px;box-shadow:0 0 14px rgba(192,57,43,.35),inset 0 0 10px rgba(232,90,69,.25)}
  .brand .logo svg{width:24px;height:24px}
  .brand .bt{line-height:1}
  .brand .bt b{font-family:'Cinzel',serif;font-weight:900;font-size:18px;letter-spacing:1.5px;
               background:linear-gradient(180deg,#f3d9a6,#c0392b);-webkit-background-clip:text;background-clip:text;color:transparent}
  .brand .bt b span{color:var(--tx);-webkit-text-fill-color:var(--tx)}
  .brand .bt small{display:block;font-size:9.5px;letter-spacing:3px;color:var(--gold-dim);margin-top:2px}

  .livematch{flex:1;display:flex;align-items:center;justify-content:center;gap:16px}
  .lm-mode{text-align:right;min-width:140px}
  .lm-mode .live{display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:700;letter-spacing:1.5px;color:var(--red-hi)}
  .lm-mode .live i{width:8px;height:8px;border-radius:50%;background:#555;display:inline-block}
  .lm-mode.on .live i{background:var(--red-hi);box-shadow:0 0 9px var(--red-hi);animation:pulse 1.6s infinite}
  @keyframes pulse{50%{opacity:.35}}
  .lm-mode .phase{font-size:12px;color:var(--tx2);letter-spacing:.5px;text-transform:uppercase}
  .lm-core{display:flex;align-items:center;gap:12px}
  .ports{display:flex;gap:4px}
  .ports.dire{flex-direction:row-reverse}
  .ports .port{width:42px;height:30px}
  .lm-score{display:flex;align-items:center;gap:14px;padding:0 4px}
  .lm-score .sc{font-family:'Rajdhani';font-weight:700;font-size:30px;line-height:1;min-width:38px;text-align:center}
  .lm-score .sc.rad{color:var(--rad);text-shadow:0 0 16px rgba(126,201,79,.4)}
  .lm-score .sc.dire{color:var(--dire);text-shadow:0 0 16px rgba(226,74,59,.4)}
  .lm-score .sc small{display:block;font-size:9px;letter-spacing:2px;color:var(--tx3);font-weight:600;margin-top:3px}
  .lm-clock{display:grid;place-items:center;width:62px;height:62px;border-radius:50%;
            border:2px solid var(--gold-dim);background:radial-gradient(circle,#11161f,#0a0e15);
            font-weight:700;font-size:17px;color:var(--gold-hi);box-shadow:inset 0 0 12px rgba(0,0,0,.6),0 0 10px rgba(200,170,110,.12)}
  .topstat{min-width:150px;display:flex;justify-content:flex-end;align-items:center;gap:10px}
  .conn{display:flex;align-items:center;gap:8px;font-size:11px;font-weight:700;letter-spacing:1px;color:var(--tx2);
        text-transform:uppercase;padding:6px 11px;border:1px solid var(--line);border-radius:20px;background:#0c1119}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--red);transition:.3s;flex:none}
  .dot.live{background:var(--ok);box-shadow:0 0 9px var(--ok)}
  .voicebtn{padding:7px 13px;border-radius:20px;font-size:12px;letter-spacing:.5px;display:flex;align-items:center;gap:6px}
  .voicebtn.rec{background:linear-gradient(180deg,#c0392b,#8a2018);border-color:#9a2a1f;color:#fff;
                box-shadow:0 0 12px rgba(226,74,59,.5);animation:pulse 1.3s infinite}
  .voicebtn.busy{opacity:.7;cursor:default}
  .topbtn{padding:7px 12px;border-radius:20px;font-size:12px;letter-spacing:.5px;
          display:flex;align-items:center;gap:6px;white-space:nowrap}
  .topbtn.danger{border-color:#7c1f17;color:#e8a99f}
  .topbtn.danger:hover{background:linear-gradient(180deg,#c0392b,#8a2018);border-color:#9a2a1f;color:#fff}
  /* tela cheia mostrada quando a aplicacao e desligada pelo painel */
  .killscreen{position:fixed;inset:0;z-index:99;display:none;flex-direction:column;gap:14px;
              align-items:center;justify-content:center;text-align:center;padding:24px;
              background:rgba(5,7,12,.94);backdrop-filter:blur(4px)}
  .killscreen.on{display:flex}
  .killscreen .kc-ico{font-size:48px;color:var(--red-hi);filter:drop-shadow(0 0 14px rgba(226,74,59,.5))}
  .killscreen h2{font-family:'Cinzel',serif;letter-spacing:2px;margin:0;color:var(--tx)}
  .killscreen p{color:var(--tx2);margin:0;max-width:440px;line-height:1.5}
  .killscreen code{background:#141b27;border:1px solid #2b3647;border-radius:5px;padding:2px 8px;color:var(--gold-hi)}
  .cfg{display:flex;flex-direction:column;gap:15px}
  .cfg-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .cfg-row > label{font-size:12px;color:var(--tx2);letter-spacing:.5px;min-width:118px}
  .cfg-input{flex:1;min-width:220px;background:#0a0e15;border:1px solid #2b3647;border-radius:var(--r);
             color:var(--tx);padding:9px 12px;font-size:13px;font-family:inherit}
  .cfg-input:focus{outline:none;border-color:var(--gold-dim)}
  .cfg-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
  .cfg-cell{display:flex;flex-direction:column;gap:6px}
  .cfg-cell label{font-size:10.5px;color:var(--tx3);text-transform:uppercase;letter-spacing:1px}
  .cfg-cell select{width:100%}
  .cfg-group{margin-top:6px;padding-top:12px;border-top:1px solid var(--line-soft);
             font-size:12px;font-weight:700;letter-spacing:.8px;color:var(--gold);
             display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
  .cfg-group small{font-weight:500;letter-spacing:.2px;color:var(--tx3);font-size:11.5px}
  .cfg-note{font-size:11.5px;color:var(--tx3);line-height:1.5;margin-top:-6px}
  .meter{flex:1;min-width:160px;height:16px;border-radius:8px;background:#0a0e15;border:1px solid var(--line);overflow:hidden;position:relative}
  .meter i{display:block;height:100%;width:0%;border-radius:8px;background:linear-gradient(90deg,var(--rad-dim),var(--rad) 70%,var(--gold));transition:width .07s linear}
  .meter.live{border-color:var(--rad-dim)}
  .voicehint{font-size:12.5px;line-height:1.5;color:var(--tx2);background:linear-gradient(180deg,rgba(192,57,43,.10),rgba(11,16,26,.5));
             border:1px solid #3a2420;border-radius:var(--r);padding:9px 12px;margin-bottom:10px}
  .voicehint b{color:var(--gold-hi)}

  /* ============ Aviso global de captura do placar ============ */
  .scanflash{position:fixed;inset:0;z-index:90;pointer-events:none;opacity:0;
             box-shadow:inset 0 0 0 3px rgba(232,90,69,.7), inset 0 0 120px rgba(226,74,59,.35)}
  .scanflash.go{animation:scanflash .6s ease-out}
  @keyframes scanflash{0%{opacity:1}100%{opacity:0}}
  .scantoast{position:fixed;top:80px;left:50%;transform:translate(-50%,-18px);z-index:95;
             display:none;align-items:center;gap:14px;min-width:330px;max-width:480px;
             padding:14px 18px;border-radius:10px;cursor:pointer;
             background:linear-gradient(180deg,#1b232f,#10151f);border:1px solid var(--gold-dim);
             box-shadow:0 16px 44px rgba(0,0,0,.6),0 0 24px rgba(200,170,110,.18);
             opacity:0;transition:opacity .25s ease, transform .25s ease}
  .scantoast.show{display:flex;opacity:1;transform:translate(-50%,0)}
  .scantoast.ok{border-color:#2f6b3a;box-shadow:0 16px 44px rgba(0,0,0,.6),0 0 24px rgba(72,197,105,.22)}
  .scantoast.err{border-color:#7a2a22;box-shadow:0 16px 44px rgba(0,0,0,.6),0 0 24px rgba(226,74,59,.28)}
  .st-ic{width:42px;height:42px;flex:none;display:grid;place-items:center;border-radius:8px;
         background:#0b101a;border:1px solid var(--line);font-size:21px;position:relative}
  .st-ic .st-spin{position:absolute;width:36px;height:36px;border:3px solid rgba(200,170,110,.22);
                  border-top-color:var(--gold);border-radius:50%;animation:sp .8s linear infinite;display:none}
  .scantoast.busy .st-ic .st-spin{display:block}
  .scantoast.busy .st-emoji{display:none}
  .st-title{font-weight:700;font-size:15.5px;color:var(--tx);letter-spacing:.3px}
  .scantoast.ok .st-title{color:var(--rad)} .scantoast.err .st-title{color:var(--dire)}
  .st-sub{font-size:12px;color:var(--tx2);margin-top:1px}
  .st-thumb{height:44px;border-radius:5px;border:1px solid var(--line);margin-left:auto;display:none}
  .scantoast.has-thumb .st-thumb{display:block}

  /* ============ Sidebar nav ============ */
  .nav{display:flex;flex-direction:column;gap:2px;padding:0 10px}
  .nav-item{display:flex;align-items:center;gap:12px;padding:11px 12px;border-radius:var(--r);
            color:var(--tx2);cursor:pointer;font-weight:600;font-size:13.5px;letter-spacing:.6px;
            text-transform:uppercase;border:1px solid transparent;position:relative;transition:.15s}
  .nav-item svg{width:19px;height:19px;flex:none;opacity:.8}
  .nav-item:hover{background:#121a26;color:var(--tx)}
  .nav-item.active{color:var(--gold-hi);background:linear-gradient(90deg,rgba(192,57,43,.18),rgba(192,57,43,.02));
                   border-color:#3a2420}
  .nav-item.active::before{content:'';position:absolute;left:0;top:6px;bottom:6px;width:3px;border-radius:2px;
                           background:linear-gradient(180deg,var(--red-hi),var(--red));box-shadow:0 0 10px var(--red-hi)}
  .nav-item.active svg{opacity:1;color:var(--gold)}
  .nav-soon{margin-left:auto;font-size:8.5px;letter-spacing:1px;color:var(--tx3);border:1px solid var(--line);
            border-radius:4px;padding:1px 5px}

  .side-foot{margin-top:auto;padding:12px 12px 0;display:flex;flex-direction:column;gap:10px}
  .agentcard{display:flex;align-items:center;gap:10px;padding:10px;border:1px solid #2a1c1a;border-radius:var(--r);
             background:linear-gradient(180deg,rgba(192,57,43,.12),rgba(192,57,43,.03))}
  .agentcard .av{width:34px;height:34px;border-radius:6px;flex:none;display:grid;place-items:center;
                 background:radial-gradient(circle,#3a0f0a,#170707);border:1px solid #5a1b13;
                 box-shadow:0 0 12px rgba(192,57,43,.4)}
  .agentcard .av svg{width:18px;height:18px}
  .agentcard b{font-size:13px;color:var(--tx);display:block;line-height:1.2}
  .agentcard .av{cursor:default}
  /* status da conexao com a IA (cor dinamica pelo health-check real) */
  .astat{font-size:11px;color:var(--tx2);display:flex;align-items:center;gap:5px;cursor:pointer}
  .astat i{width:6px;height:6px;border-radius:50%;background:var(--tx3);flex:none;transition:background .3s,box-shadow .3s}
  .astat #agent-prov{color:inherit}
  .astat.ok{color:var(--ok)}     .astat.ok i{background:var(--ok);box-shadow:0 0 6px var(--ok)}
  .astat.bad{color:var(--red-hi)} .astat.bad i{background:var(--red-hi);box-shadow:0 0 7px var(--red-hi)}
  .astat.warn{color:var(--gold-hi)} .astat.warn i{background:var(--gold-hi);box-shadow:0 0 6px var(--gold-hi)}
  .astat.checking i{animation:pulse 1.3s infinite}
  .sidenote{font-size:11.5px;line-height:1.5;color:var(--tx3);border:1px dashed var(--line);border-radius:var(--r);padding:10px}

  /* ============ Panels ============ */
  .panel{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);
         border-radius:var(--r);padding:15px;position:relative;overflow:hidden}
  .panel::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;
                 background:linear-gradient(90deg,transparent,rgba(200,170,110,.3),transparent)}
  .ptitle{font-size:12px;font-weight:700;letter-spacing:1.6px;text-transform:uppercase;color:var(--gold);
          margin:0 0 13px;display:flex;align-items:center;gap:9px}
  .ptitle::before{content:'';width:3px;height:14px;background:linear-gradient(180deg,var(--red-hi),var(--red));border-radius:2px;flex:none}
  .ptitle .grow{flex:1;height:1px;background:linear-gradient(90deg,var(--line),transparent)}
  .ptitle .acc{color:var(--tx3);font-size:10px;letter-spacing:1px}
  .empty{color:var(--tx3);font-style:italic;font-size:13px}

  /* portraits / icons */
  .port{border-radius:4px;overflow:hidden;background:#0a0e15;border:1px solid var(--line);position:relative;flex:none}
  .port img{width:100%;height:100%;object-fit:cover;display:block}
  .port.ally{border-color:var(--rad-dim);box-shadow:inset 0 0 0 1px rgba(126,201,79,.18)}
  .port.enemy{border-color:var(--dire-dim);box-shadow:inset 0 0 0 1px rgba(226,74,59,.2)}

  /* ============ Dashboard grid ============ */
  .dash{display:grid;grid-template-columns:300px minmax(0,1fr) 318px;gap:16px;align-items:start}
  .col{display:flex;flex-direction:column;gap:16px;min-width:0}

  /* hero panel */
  .hero-portrait{height:168px;border-radius:var(--r);overflow:hidden;position:relative;background:#0a0e15;border:1px solid var(--line)}
  .hero-portrait img{width:100%;height:100%;object-fit:cover;object-position:50% 22%}
  .hero-portrait .ov{position:absolute;inset:0;background:linear-gradient(180deg,rgba(8,10,16,.05) 40%,rgba(8,10,16,.92))}
  .hero-portrait .nm{position:absolute;left:14px;right:14px;bottom:10px}
  .hero-portrait .nm b{font-family:'Cinzel',serif;font-weight:700;font-size:21px;color:var(--gold-hi);
                       text-shadow:0 2px 8px #000;letter-spacing:.5px;display:block;line-height:1.05}
  .hero-portrait .nm small{color:var(--tx2);font-size:12px;letter-spacing:1.5px;text-transform:uppercase}
  .hero-portrait .lvl{position:absolute;top:10px;right:10px;width:34px;height:34px;border-radius:50%;
                      display:grid;place-items:center;font-weight:700;font-size:15px;color:#1a1206;
                      background:radial-gradient(circle,var(--gold-hi),var(--gold));border:2px solid #4a3a18;box-shadow:0 0 10px rgba(200,170,110,.5)}
  .bars{display:flex;flex-direction:column;gap:5px;margin:11px 0 4px}
  .bar{height:9px;border-radius:5px;background:#0a0e15;border:1px solid var(--line-soft);overflow:hidden;position:relative}
  .bar i{position:absolute;left:0;top:0;bottom:0;border-radius:5px;transition:width .4s}
  .bar.hp i{background:linear-gradient(90deg,#3f7a2a,#7ec94f)}
  .bar.mp i{background:linear-gradient(90deg,#1f5a8a,#46a6e8)}
  .statgrid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}
  .stat{background:#0b101a;border:1px solid var(--line-soft);border-radius:var(--r);padding:9px 11px}
  .stat .l{font-size:10px;letter-spacing:1px;color:var(--tx3);text-transform:uppercase}
  .stat .v{font-size:18px;font-weight:700;color:var(--tx);margin-top:1px}
  .stat .v b{color:var(--rad)} .stat .v i{color:var(--dire);font-style:normal} .stat .v u{color:#5aaee8;text-decoration:none}
  .stat .v.gold{color:var(--gold-hi)}
  .subh{font-size:10.5px;letter-spacing:1.4px;color:var(--tx3);text-transform:uppercase;margin:15px 0 8px;
        display:flex;align-items:center;gap:8px}
  .subh::after{content:'';flex:1;height:1px;background:var(--line-soft)}

  .abilities{display:flex;gap:7px}
  .ab{width:42px;height:42px;border-radius:5px;background:#0a0e15;border:1px solid var(--line);position:relative;overflow:hidden}
  .ab img{width:100%;height:100%;object-fit:cover}
  .ab.ult{border-color:var(--gold-dim);box-shadow:0 0 8px rgba(200,170,110,.25)}
  .ab .lvl{position:absolute;bottom:0;right:0;font-size:9px;font-weight:700;padding:0 3px;color:var(--gold-hi);
           background:rgba(0,0,0,.8);border-top-left-radius:4px}
  .items{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
  .islot{aspect-ratio:1.35;border-radius:4px;background:#0a0e15;border:1px solid var(--line-soft);position:relative;overflow:hidden}
  .islot img{width:100%;height:100%;object-fit:cover}
  .islot.empty{background:repeating-linear-gradient(45deg,#0a0e15,#0a0e15 6px,#0c111a 6px,#0c111a 12px)}
  .islot .chg{position:absolute;bottom:0;right:1px;font-size:9px;font-weight:700;color:var(--gold-hi);text-shadow:0 0 3px #000}

  /* itens sugeridos (chips com icone) */
  .isugg{margin:12px 0}
  .isugg-h{font-size:10.5px;letter-spacing:1.4px;color:var(--gold-hi);text-transform:uppercase;margin:0 0 9px;
           display:flex;align-items:center;gap:8px}
  .isugg-h::after{content:'';flex:1;height:1px;background:linear-gradient(90deg,var(--gold-dim),transparent)}
  .ichips{display:flex;flex-wrap:wrap;gap:8px}
  .ichip{display:flex;align-items:center;gap:8px;padding:4px 11px 4px 4px;border-radius:8px;background:#0b101a;
         border:1px solid var(--gold-dim);box-shadow:0 0 8px rgba(200,170,110,.12)}
  .ichip img{width:42px;height:31px;object-fit:cover;border-radius:4px;flex:none;background:#0a0e15}
  .ichip span{font-size:12.5px;font-weight:600;color:var(--tx);white-space:nowrap}
  /* icone inline, ao lado do nome do item no texto do relatorio */
  .inl-item{height:20px;width:27px;object-fit:cover;border-radius:3px;vertical-align:-6px;margin:0 5px 0 0;
            border:1px solid var(--gold-dim);background:#0a0e15}

  /* insights */
  .section-h{font-family:'Cinzel',serif;font-weight:700;font-size:15px;letter-spacing:1.5px;color:var(--tx);
             text-transform:uppercase;margin:2px 0 4px;display:flex;align-items:center;gap:10px}
  .section-h .grow{flex:1;height:1px;background:linear-gradient(90deg,rgba(200,170,110,.3),transparent)}
  .priority{border:1px solid #4a2a22;background:linear-gradient(180deg,rgba(192,57,43,.10),rgba(16,21,31,.7))}
  .priority .tag{display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:700;letter-spacing:1.4px;
                 color:var(--red-hi);text-transform:uppercase;margin-bottom:10px}
  .priority .rep{font-size:14.5px;line-height:1.6;color:#dde6f1;white-space:pre-wrap}
  .priority .rep strong{color:var(--gold-hi)}
  .threats{display:flex;gap:8px;flex-wrap:wrap}
  .threats .port{width:62px;height:38px}
  .threats .port .tnm{position:absolute;left:0;right:0;bottom:0;font-size:9px;text-align:center;color:var(--tx);
                      background:linear-gradient(transparent,rgba(0,0,0,.85));padding:6px 1px 1px;line-height:1}
  .sit{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
  .sit .c{background:#0b101a;border:1px solid var(--line-soft);border-radius:var(--r);padding:11px}
  .sit .c .l{font-size:10px;letter-spacing:1px;color:var(--tx3);text-transform:uppercase}
  .sit .c .v{font-size:16px;font-weight:700;margin-top:3px;color:var(--tx)}
  .sit .c .v.good{color:var(--rad)} .sit .c .v.bad{color:var(--dire)} .sit .c .v.g{color:var(--gold-hi)}
  .sit .c .mini{font-size:11px;color:var(--tx2);margin-top:2px}
  .advbar{height:7px;border-radius:4px;background:#3a1714;overflow:hidden;margin-top:7px}
  .advbar i{display:block;height:100%;background:linear-gradient(90deg,var(--rad-dim),var(--rad));border-radius:4px;transition:width .4s}
  .quicktip{border:1px solid var(--line);background:linear-gradient(180deg,rgba(40,30,15,.35),rgba(11,16,26,.6))}
  .quicktip .ptitle{color:var(--gold-hi)}
  .quicktip .body{font-size:13.5px;line-height:1.55;color:var(--tx2)}

  /* right rail enemies */
  .enemy-row{display:flex;align-items:center;gap:11px;padding:9px;border-radius:var(--r);background:#0b101a;
             border:1px solid var(--line-soft);margin-bottom:8px;border-left:3px solid var(--dire-dim)}
  .enemy-row .port{width:54px;height:32px}
  .enemy-row .nm{font-size:13.5px;font-weight:600;color:var(--tx);line-height:1.1}
  .enemy-row .pl{font-size:11px;color:var(--tx3)}
  .enemy-row .einfo{min-width:0;flex:1}
  .enemy-row .eright{margin-left:auto;display:flex;flex-direction:column;align-items:flex-end;gap:3px}
  .enemy-row .kda{text-align:right;font-size:13px;font-weight:600;white-space:nowrap}
  .enemy-row .kda b{color:var(--rad)} .enemy-row .kda i{color:var(--dire);font-style:normal} .enemy-row .kda u{color:#5aaee8;text-decoration:none}
  .erank{flex:none;width:18px;text-align:center;font-weight:700;font-size:13px;color:var(--gold);font-family:'Rajdhani'}
  .advbadge{font-size:11px;font-weight:700;padding:1px 7px;border-radius:10px;white-space:nowrap;letter-spacing:.3px}
  .advbadge.adv-good{color:var(--rad);background:rgba(126,201,79,.13);border:1px solid var(--rad-dim)}
  .advbadge.adv-bad{color:var(--dire);background:rgba(226,74,59,.13);border:1px solid var(--dire-dim)}
  .advbadge.adv-neu{color:var(--tx3);background:#10151f;border:1px solid var(--line)}

  .donut-wrap{display:flex;align-items:center;gap:14px}
  .donut{width:118px;height:118px;flex:none}
  .donut .d-num{font:700 19px 'Rajdhani';fill:var(--tx)}
  .donut .d-lbl{font:600 9px 'Rajdhani';fill:var(--tx3);letter-spacing:2px}
  .legend{display:flex;flex-direction:column;gap:8px;font-size:13px}
  .legend .li{display:flex;align-items:center;gap:8px;color:var(--tx2)}
  .legend .li b{margin-left:auto;color:var(--tx);font-weight:700}
  .legend .sw{width:11px;height:11px;border-radius:3px;flex:none}

  .map-soon{height:150px;border-radius:var(--r);border:1px solid var(--line);display:grid;place-items:center;
            text-align:center;background:
              radial-gradient(circle at 30% 70%,rgba(126,201,79,.06),transparent 40%),
              radial-gradient(circle at 70% 30%,rgba(226,74,59,.06),transparent 40%),
              repeating-linear-gradient(45deg,#0a0e15,#0a0e15 12px,#0b101a 12px,#0b101a 24px)}
  .map-soon span{font-size:12px;color:var(--tx3);letter-spacing:1px}
  .map-soon b{display:block;color:var(--tx2);font-size:13px;margin-bottom:3px;letter-spacing:1.5px}

  /* minimapa ao vivo (thumbnail no dashboard) */
  .mini-map{position:relative;aspect-ratio:1/1;border-radius:var(--r);overflow:hidden;border:1px solid var(--line);
            background:repeating-linear-gradient(45deg,#0a0e15,#0a0e15 12px,#0b101a 12px,#0b101a 24px)}
  .mini-map img{width:100%;height:100%;object-fit:contain;display:none;background:#05070b}
  .mini-map.live img{display:block}
  .mm-hint{position:absolute;inset:0;display:grid;place-items:center;text-align:center;gap:3px;pointer-events:none}
  .mini-map.live .mm-hint{display:none}
  .mm-hint b{display:block;color:var(--tx2);font-size:13px;letter-spacing:1.5px}
  .mm-hint span{font-size:11px;color:var(--tx3);letter-spacing:.5px}
  .btn.mm-open{width:100%;margin-top:10px;text-align:center}

  /* ============ Toolbar / chips / buttons ============ */
  .toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-bottom:14px}
  .toolbar label{font-size:12px;color:var(--tx2);letter-spacing:.5px}
  select,.btn{background:#141b27;border:1px solid #2b3647;color:var(--tx);border-radius:var(--r);
              padding:9px 13px;font-size:13px;font-family:inherit;font-weight:600;cursor:pointer;letter-spacing:.5px}
  select:hover,.btn:hover{border-color:#3f4f68}
  .btn.primary{background:linear-gradient(180deg,#c0392b,#8a2018);border-color:#9a2a1f;color:#fff;
               box-shadow:0 2px 10px rgba(192,57,43,.3)}
  .btn.primary:hover{background:linear-gradient(180deg,#d8463a,#a02b20)}
  .btn:disabled{opacity:.5;cursor:default}
  .kbd{background:#0a0e15;border:1px solid #2b3647;border-radius:4px;padding:3px 9px;font-size:12px;font-weight:700;color:var(--gold-hi)}
  .chip{display:inline-flex;align-items:center;gap:7px;font-size:12.5px;padding:7px 13px;border-radius:20px;
        background:#121a26;border:1px solid var(--line);color:var(--tx2);font-weight:600;cursor:default}
  .chip.click{cursor:pointer}
  .chip.go{color:var(--ok);border-color:#27502f;background:#0f2417}
  .chip.work{color:var(--warn);border-color:#5a4a16;background:#241d0c}
  .chip.err{color:var(--red-hi);border-color:#5a2222;background:#240f0f}
  .spin{width:13px;height:13px;border:2px solid #d2992255;border-top-color:var(--warn);border-radius:50%;animation:sp .7s linear infinite}
  @keyframes sp{to{transform:rotate(360deg)}}
  #thumb{height:54px;border-radius:5px;border:1px solid var(--line);display:none}

  .teams{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  .team h3{font-size:12px;letter-spacing:1.2px;text-transform:uppercase;margin:0 0 9px;display:flex;align-items:center;gap:8px}
  .team.ally h3{color:var(--rad)} .team.enemy h3{color:var(--dire)}
  .team h3 i{width:8px;height:8px;border-radius:2px;display:inline-block}
  .team.ally h3 i{background:var(--rad)} .team.enemy h3 i{background:var(--dire)}
  .hero{display:flex;align-items:center;gap:10px;padding:8px;border-radius:var(--r);background:#0b101a;
        border:1px solid var(--line-soft);margin-bottom:7px}
  .team.enemy .hero{border-left:3px solid var(--dire-dim)} .team.ally .hero{border-left:3px solid var(--rad-dim)}
  .hero .port{width:54px;height:32px}
  .hero .nm{font-size:13.5px;font-weight:600;line-height:1.1}
  .hero .pl{font-size:11px;color:var(--tx3)}
  .hero .kda{margin-left:auto;font-size:13px;font-weight:600;white-space:nowrap}
  .hero .kda b{color:var(--rad)} .hero .kda i{color:var(--dire);font-style:normal} .hero .kda u{color:#5aaee8;text-decoration:none}

  .report{background:#0a0f18;border:1px solid #243049;border-radius:var(--r);padding:15px;font-size:14.5px;
          line-height:1.6;color:#dde6f1;white-space:pre-wrap}
  .report.empty2{color:var(--tx3);font-style:italic;border-style:dashed}
  .report strong{color:var(--gold-hi)}
  .scan-err-box{margin-top:10px;border:1px solid #4a2a22;background:rgba(192,57,43,.08);
                border-radius:var(--r);padding:9px 11px}
  .scan-err-h{font-size:11px;font-weight:700;letter-spacing:.5px;color:var(--red-hi);
              text-transform:uppercase;margin-bottom:5px}
  .scan-err-row{font-size:12.5px;color:#e7d0cc;padding:2px 0;line-height:1.4}
  .scan-err-when{color:var(--tx3);font-size:11px;margin-right:4px}

  /* live fields (game status) */
  .fields{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:11px}
  .field{background:#0b101a;border:1px solid var(--line-soft);border-radius:var(--r);padding:12px 14px}
  .field .l{font-size:10px;letter-spacing:1px;color:var(--tx3);text-transform:uppercase}
  .field .v{font-size:20px;font-weight:700;margin-top:3px}
  pre{background:#06090f;border:1px solid var(--line);border-radius:var(--r);padding:12px;overflow:auto;
      font-size:11.5px;max-height:340px;color:#8fa3bd;font-family:ui-monospace,Consolas,monospace}

  /* chat */
  #chat{display:flex;flex-direction:column;gap:12px;height:100%}
  #log{display:flex;flex-direction:column;gap:9px;flex:1;overflow-y:auto;padding:4px;min-height:300px}
  .msg{padding:10px 13px;border-radius:11px;font-size:14px;line-height:1.5;white-space:pre-wrap;max-width:86%}
  .msg.user{align-self:flex-end;background:linear-gradient(180deg,rgba(192,57,43,.22),rgba(192,57,43,.08));border:1px solid #5a2a22;color:#fbe9e4}
  .msg.bot{align-self:flex-start;background:#0e1521;border:1px solid var(--line)}
  .msg strong{color:var(--gold-hi)}
  #chatform{display:flex;gap:9px}
  #chatinput{flex:1;background:#0a0e15;border:1px solid #2b3647;border-radius:9px;color:var(--tx);
             padding:11px 13px;font-size:14px;font-family:inherit;resize:none}
  #chatinput:focus{outline:none;border-color:var(--gold-dim)}
  #chatform button{border:none;border-radius:9px;padding:0 16px;font-weight:700;cursor:pointer;font-family:inherit;font-size:14px}
  #chatsend{background:linear-gradient(180deg,#c0392b,#8a2018);color:#fff}
  #micbtn{background:#141b27;border:1px solid #2b3647!important;font-size:17px;color:var(--tx)}
  #micbtn.rec{background:var(--red);border-color:var(--red)!important}

  /* views */
  .view{display:none;animation:fade .25s ease}
  .view.active{display:block}
  @keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
  .stack{display:flex;flex-direction:column;gap:16px}
  .soon-big{display:grid;place-items:center;min-height:340px;text-align:center;gap:8px}
  .soon-big .ic{width:60px;height:60px;opacity:.4}
  .soon-big b{font-family:'Cinzel',serif;font-size:18px;letter-spacing:1px;color:var(--tx2)}
  .soon-big p{color:var(--tx3);max-width:380px;line-height:1.6;font-size:13.5px}

  /* responsive */
  @media (max-width:1280px){
    .dash{grid-template-columns:280px minmax(0,1fr)}
    .dash .rail{grid-column:1 / -1}
    .rail-inner{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;align-items:start}
  }
  @media (max-width:920px){
    .layout{grid-template-columns:1fr}
    .sidebar{display:none}
    .dash{grid-template-columns:1fr}
    .rail-inner{grid-template-columns:1fr}
    .livematch{display:none}
  }

  /* ============ Aba Draft (grid de picks + counters ao vivo) ============ */
  .nav-live{margin-left:auto;font-size:8.5px;font-weight:700;letter-spacing:1px;color:#fff;
            border-radius:4px;padding:1px 6px;background:linear-gradient(180deg,var(--red-hi),var(--red));
            box-shadow:0 0 9px rgba(226,74,59,.6);animation:pulse 1.5s infinite;display:none}
  .nav-live.on{display:inline-block}
  .draft-wrap{display:grid;grid-template-columns:minmax(0,1fr) 318px;gap:16px;align-items:start}
  @media (max-width:1100px){.draft-wrap{grid-template-columns:1fr}}
  .dmode{display:inline-flex;gap:4px;background:#0a0e15;border:1px solid var(--line);border-radius:7px;padding:3px}
  .dmode button{background:transparent;border:1px solid transparent;color:var(--tx2);border-radius:5px;
                padding:6px 12px;font-size:12.5px;font-weight:700;letter-spacing:.4px;cursor:pointer;font-family:inherit}
  .dmode button.on.enemy{background:linear-gradient(180deg,#c0392b,#8a2018);color:#fff;border-color:#9a2a1f}
  .dmode button.on.ally{background:linear-gradient(180deg,#3f7a2a,#2a5a1c);color:#fff;border-color:#3f6f29}
  .dmode button.on.ban{background:linear-gradient(180deg,#3a4254,#272e3c);color:#fff;border-color:#4a5468}
  .dsearch{background:#0a0e15;border:1px solid #2b3647;border-radius:var(--r);color:var(--tx);
           padding:8px 12px;font-size:13px;font-family:inherit;min-width:170px}
  .dsearch:focus{outline:none;border-color:var(--gold-dim)}
  .dcounts{display:flex;gap:14px;font-size:12px;color:var(--tx2);align-items:center}
  .dcounts b{color:var(--tx);font-weight:700}
  .dcounts .ce{color:var(--dire)} .dcounts .ca{color:var(--rad)}
  .dgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(84px,1fr));gap:7px;margin-top:4px}
  .dh{position:relative;border-radius:5px;overflow:hidden;border:1px solid var(--line);background:#0a0e15;
      cursor:pointer;aspect-ratio:16/10;transition:transform .1s,border-color .1s}
  .dh img{width:100%;height:100%;object-fit:cover;display:block}
  .dh:hover{transform:translateY(-2px)}
  .dh .nm{position:absolute;left:0;right:0;bottom:0;font-size:9px;text-align:center;color:#fff;
          background:linear-gradient(transparent,rgba(0,0,0,.9));padding:9px 2px 2px;line-height:1.05;
          white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .dh .adv{position:absolute;top:3px;left:3px;font-size:10px;font-weight:700;padding:1px 4px;border-radius:3px;line-height:1.25}
  .dh .adv.good{background:rgba(46,160,67,.9);color:#fff} .dh .adv.bad{background:rgba(192,57,43,.9);color:#fff}
  /* gradacao de vantagem (counter) / desvantagem (counterado) */
  .dh.g1{border-color:#3a6f2c} .dh.g2{border-color:#5fae4a;box-shadow:0 0 0 1px rgba(126,201,79,.35)}
  .dh.g3{border-color:#7ec94f;box-shadow:0 0 0 2px rgba(126,201,79,.45)}
  .dh.b1{border-color:#7a3a2f} .dh.b2{border-color:#c0392b;box-shadow:0 0 0 1px rgba(226,74,59,.35)}
  /* marcacao (inimigo/aliado/ban) */
  .dh.mk-enemy{outline:2px solid var(--dire);outline-offset:-2px}
  .dh.mk-ally{outline:2px solid var(--rad);outline-offset:-2px}
  .dh.mk-ban{outline:2px solid #5a647a;outline-offset:-2px;filter:grayscale(1) brightness(.55)}
  .dh .mk{position:absolute;top:3px;right:3px;width:17px;height:17px;border-radius:4px;display:grid;
          place-items:center;font-size:10px;font-weight:800;color:#fff}
  .dh .mk.enemy{background:var(--dire)} .dh .mk.ally{background:var(--rad);color:#0a1606} .dh .mk.ban{background:#5a647a}
  .dsugg{display:flex;flex-direction:column;gap:8px}
  .dsugg .row{display:flex;align-items:center;gap:10px;padding:8px;border-radius:var(--r);background:#0b101a;
              border:1px solid var(--line-soft);border-left:3px solid var(--rad-dim)}
  .dsugg .row .port{width:48px;height:30px}
  .dsugg .row .nm{font-size:13px;font-weight:700;color:var(--tx);line-height:1.15}
  .dsugg .row .rs{font-size:10.5px;color:var(--tx3);line-height:1.25;margin-top:1px}
  .dsugg .row .pc{margin-left:auto;font-size:14px;font-weight:800;color:var(--rad);white-space:nowrap}
  .dhint{font-size:11.5px;color:var(--tx3);line-height:1.5;margin-top:10px}
</style>
</head>
<body>

<!-- Flash + aviso GLOBAL de captura do placar (aparece em qualquer aba) -->
<div id="scanflash" class="scanflash"></div>
<div id="scantoast" class="scantoast" title="Ver no Team Analysis" onclick="showView('teamanalysis')">
  <div class="st-ic"><span class="st-spin"></span><span class="st-emoji" id="st-emoji">📸</span></div>
  <div class="st-body"><div class="st-title" id="st-title">Capturando…</div><div class="st-sub" id="st-sub">lendo o placar</div></div>
  <img class="st-thumb" id="st-thumb" alt="" onerror="this.style.display='none'">
</div>

<div class="app">

  <header class="topbar">
    <div class="brand">
      <div class="logo">
        <svg viewBox="0 0 24 24" fill="none"><path d="M12 2l8 5v10l-8 5-8-5V7l8-5z" stroke="#e85a45" stroke-width="1.6"/><path d="M12 6.5l4.5 2.8v5.4L12 17.5l-4.5-2.8V9.3L12 6.5z" fill="#c0392b"/></svg>
      </div>
      <div class="bt"><b>DOTA 2 <span>COPILOT</span></b><small>POWERED BY TETEUPOWER</small></div>
    </div>

    <div class="livematch">
      <div class="lm-mode" id="lm-mode"><span class="live"><i></i> PARTIDA AO VIVO</span><div class="phase" id="lm-phase">aguardando</div></div>
      <div class="lm-core">
        <div class="ports" id="lm-allies"></div>
        <div class="lm-score">
          <div class="sc rad"><span id="lm-rad">–</span><small>ALIADOS</small></div>
          <div class="lm-clock" id="lm-clock">--:--</div>
          <div class="sc dire"><span id="lm-dire">–</span><small>INIMIGOS</small></div>
        </div>
        <div class="ports dire" id="lm-enemies"></div>
      </div>
    </div>

    <div class="topstat">
      <button class="btn voicebtn" id="voicebtn" title="Falar com o copiloto (atalho global configurável em Settings)">🎤 <span id="voicebtn-lbl">Falar</span></button>
      <button class="btn topbtn" id="ctxbtn" title="Limpar o contexto da partida (chat, draft, placar e relatórios) para começar um jogo novo do zero">🧹 <span>Novo jogo</span></button>
      <button class="btn topbtn danger" id="killbtn" title="Desligar a aplicação — encerra o servidor por completo (sem ficar fantasma no PC)">⏻ <span>Desligar</span></button>
      <div class="conn"><span class="dot" id="conn-dot"></span><span id="conn-text">conectando...</span></div>
    </div>
  </header>

  <!-- mostrado quando o usuário desliga a aplicação pelo botão -->
  <div class="killscreen" id="killscreen">
    <div class="kc-ico">⏻</div>
    <h2>APLICAÇÃO DESLIGADA</h2>
    <p>O servidor do copiloto foi encerrado. Pode fechar esta aba.<br>
       Para usar de novo, abra o <code>iniciar.bat</code>.</p>
  </div>

  <div class="layout">
    <aside class="sidebar">
      <nav class="nav" id="nav">
        <div class="nav-item active" data-view="dashboard"><svg viewBox="0 0 24 24" fill="currentColor"><rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/></svg>Painel</div>
        <div class="nav-item" data-view="draft"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4l5.5 1-1 5.5"/><path d="M20 5L9 16"/><path d="M4 14.5l3.5 3.5"/><path d="M9.5 4L4 5l1 5.5"/><path d="M4 5l7 7"/><path d="M16 14.5L12.5 18"/></svg>Seleção<span class="nav-live" id="draft-live">PICK</span></div>
        <div class="nav-item" data-view="gamestatus"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.5" fill="currentColor"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/></svg>Status do Jogo</div>
        <div class="nav-item" data-view="heroinsights"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2c1.5 3.5 5 4.8 5 8.8a5 5 0 0 1-10 0c0-1.8 .8-2.9 1.8-3.9-.2 2 .9 3.1 2.7 3.3-2-2.8-1.4-5.4 .5-8.2z"/></svg>Análise do Herói</div>
        <div class="nav-item" data-view="itemadvisor"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M5 8h14l-1 12H6L5 8z"/><path d="M9 8a3 3 0 0 1 6 0"/></svg>Guia de Itens</div>
        <div class="nav-item" data-view="teamanalysis"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.3"/><path d="M3.5 19c.5-3 2.8-4.5 5.5-4.5s5 1.5 5.5 4.5"/><path d="M16 14.6c2 .3 3.6 1.6 4 4.4"/></svg>Análise de Time</div>
        <div class="nav-item" data-view="strategy"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 21V4"/><path d="M6 4h11l-2.5 3.5L17 11H6"/></svg>Estratégia</div>
        <div class="nav-item" data-view="replay"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M11 6L4 12l7 6V6z"/><path d="M20 6l-7 6 7 6V6z"/></svg>Análise de Replay<span class="nav-soon">EM BREVE</span></div>
        <div class="nav-item" data-view="settings"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.3l2-1.5-2-3.4-2.3 1a7 7 0 0 0-2.2-1.3L14 2h-4l-.4 2.2a7 7 0 0 0-2.2 1.3l-2.3-1-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .9.1 1.3l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 2.2 1.3L10 22h4l.4-2.2a7 7 0 0 0 2.2-1.3l2.3 1 2-3.4-2-1.5c.1-.4.1-.9.1-1.3z"/></svg>Configurações</div>
      </nav>

      <div class="side-foot">
        <div class="agentcard">
          <div class="av"><svg viewBox="0 0 24 24" fill="none"><path d="M12 2l8 5v10l-8 5-8-5V7l8-5z" stroke="#e85a45" stroke-width="1.4"/><circle cx="12" cy="11" r="3" fill="#e85a45"/></svg></div>
          <div><b id="agent-name">Copiloto</b><span id="agent-status" class="astat checking" title="verificando conexão com a IA"><i></i> <span id="agent-prov">verificando conexão…</span></span></div>
        </div>
        <div class="sidenote">Analisando a partida em tempo real (GSI) para te dar os melhores insights. Pressione <b>Tab</b> + tecla para ler o placar.</div>
      </div>
    </aside>

    <main class="content">

      <!-- ============ DASHBOARD ============ -->
      <section class="view active" data-view="dashboard">
        <div class="dash">

          <!-- coluna herói -->
          <div class="col">
            <div class="panel">
              <h2 class="ptitle">Seu Herói<span class="grow"></span></h2>
              <div id="hero-card"><span class="empty">aguardando o jogo (GSI)...</span></div>
            </div>
          </div>

          <!-- coluna insights -->
          <div class="col">
            <div class="section-h">Insights do Copilot<span class="grow"></span></div>
            <div class="panel priority" id="insight-card">
              <div class="tag" id="insight-tag">⚠ análise tática</div>
              <div class="rep" id="insight-report"></div>
              <div id="insight-suggest"></div>
            </div>
            <div class="panel">
              <h2 class="ptitle">Ameaças Principais<span class="grow"></span></h2>
              <div class="threats" id="threats"><span class="empty">escaneie o placar para detectar os inimigos.</span></div>
            </div>
            <div class="panel">
              <h2 class="ptitle">Situação da Partida<span class="grow"></span></h2>
              <div class="sit" id="situation"></div>
            </div>
            <div class="panel quicktip">
              <h2 class="ptitle">Sugestão Rápida<span class="grow"></span></h2>
              <div class="body" id="quicktip">Conecte o GSI e escaneie o placar (Tab + tecla) — o copiloto monta a leitura tática da partida aqui.</div>
            </div>
          </div>

          <!-- coluna direita -->
          <div class="col rail">
           <div class="rail-inner">
            <div class="panel">
              <h2 class="ptitle">Inimigos<span class="grow"></span><span class="acc">fáceis no topo ↑</span></h2>
              <div id="enemy-list"><span class="empty">sem leitura do placar ainda.</span></div>
            </div>
            <div class="panel">
              <h2 class="ptitle">Placar de Abates<span class="grow"></span></h2>
              <div id="donut-box"><span class="empty">sem dados de abates.</span></div>
            </div>
            <div class="panel">
              <h2 class="ptitle">Minimapa<span class="grow"></span><span class="acc" id="mm-acc">ao vivo</span></h2>
              <div class="mini-map" id="mini-map">
                <img id="mini-thumb" alt="minimapa ao vivo">
                <div class="mm-hint" id="mm-hint"><b>MINIMAPA</b><span>entre numa partida pra ver ao vivo</span></div>
              </div>
              <button class="btn mm-open" onclick="openMinimap()">⛶ Abrir minimapa grande (2ª janela)</button>
            </div>
           </div>
          </div>

        </div>
      </section>

      <!-- ============ DRAFT (grid de picks + counters ao vivo) ============ -->
      <section class="view" data-view="draft">
        <div class="panel">
          <h2 class="ptitle">Assistente de Draft<span class="grow"></span><span class="acc">marque os picks · veja os counters</span></h2>
          <div class="toolbar">
            <label>Um toque marca:</label>
            <div class="dmode" id="dmode">
              <button data-role="enemy" class="on enemy">Inimigo</button>
              <button data-role="ally" class="ally">Aliado</button>
              <button data-role="ban" class="ban">Ban</button>
            </div>
            <input id="dsearch" class="dsearch" placeholder="🔎 buscar herói..." autocomplete="off">
            <span style="flex:1"></span>
            <button class="btn primary" id="dscan">📷 Copiar tela de picks</button>
            <button class="btn" id="dclear">limpar</button>
          </div>
          <div class="toolbar" style="margin-bottom:0">
            <span class="chip" id="dchip">marque os inimigos ou copie a tela</span>
            <div class="dcounts">
              <span class="ce">Inimigos <b id="dc-enemy">0</b></span>
              <span class="ca">Aliados <b id="dc-ally">0</b></span>
              <span>Bans <b id="dc-ban">0</b></span>
            </div>
            <img id="dthumb" alt="" style="height:46px;border-radius:5px;border:1px solid var(--line);display:none" onerror="this.style.display='none'">
          </div>
        </div>
        <div class="draft-wrap">
          <div class="panel">
            <h2 class="ptitle">Heróis<span class="grow"></span><span class="acc" id="dgrid-acc">ordenado por vantagem</span></h2>
            <div class="dgrid" id="dgrid"><span class="empty">carregando heróis...</span></div>
            <div class="dhint">Verde = você tem vantagem natural contra os inimigos marcados · Vermelho = você é counterado. Quanto mais marca inimigos, mais o grid se reordena pelos melhores picks.</div>
          </div>
          <div class="col">
            <div class="panel">
              <h2 class="ptitle">Melhores Picks<span class="grow"></span></h2>
              <div class="dsugg" id="dsugg"><span class="empty">marque ao menos um inimigo para ver os counters.</span></div>
            </div>
          </div>
        </div>
      </section>

      <!-- ============ GAME STATUS ============ -->
      <section class="view" data-view="gamestatus">
        <div class="stack">
          <div class="panel">
            <h2 class="ptitle">Partida ao Vivo (GSI)<span class="grow"></span><span class="acc" id="gs-acc"></span></h2>
            <div class="fields" id="gs-fields"><span class="empty">aguardando dados do Dota...</span></div>
          </div>
          <div class="panel">
            <h2 class="ptitle">JSON cru do GSI<span class="grow"></span></h2>
            <pre id="raw">-</pre>
          </div>
        </div>
      </section>

      <!-- ============ HERO INSIGHTS ============ -->
      <section class="view" data-view="heroinsights">
        <div class="grid2">
          <div class="panel">
            <h2 class="ptitle">Habilidades<span class="grow"></span></h2>
            <div id="hi-abilities"><span class="empty">aguardando o herói (GSI)...</span></div>
          </div>
          <div class="panel">
            <h2 class="ptitle">Itens Atuais<span class="grow"></span></h2>
            <div id="hi-items"><span class="empty">aguardando inventário (GSI)...</span></div>
          </div>
        </div>
      </section>

      <!-- ============ ITEM ADVISOR ============ -->
      <section class="view" data-view="itemadvisor">
        <div class="stack">
          <div class="panel">
            <h2 class="ptitle">Seus Itens Atuais<span class="grow"></span></h2>
            <div id="ia-items"><span class="empty">aguardando inventário (GSI)...</span></div>
          </div>
          <div class="panel">
            <h2 class="ptitle">⚡ Relatório rápido de itens<span class="grow"></span>
              <span class="acc">só itens · usa os inimigos do último placar</span></h2>
            <div class="toolbar">
              <label>Atalho:</label> <span class="kbd">Tab</span> +
              <select id="ir-hk">
                <option>f5</option><option>f7</option><option>f9</option>
                <option>f10</option><option>f11</option><option>f12</option>
              </select>
              <button class="btn primary" id="ir-run">⚡ Gerar agora</button>
              <span style="flex:1"></span>
              <span class="chip" id="ir-chip">pronto</span>
            </div>
            <div id="ir-suggest"></div>
            <div class="report empty2" id="ir-report">clique em <b>Gerar</b> (ou <b>Tab+F5</b> no jogo) pra uma lista rápida dos próximos itens contra o time inimigo. Escaneie o placar (Tab+F7) uma vez pra eu saber quem são os inimigos.</div>
          </div>
          <div class="panel">
            <h2 class="ptitle">Recomendação do Copiloto<span class="grow"></span></h2>
            <div id="ia-suggest"></div>
            <div class="report empty2" id="ia-report">escaneie o placar (Team Analysis) — o copiloto avalia seus itens e sugere os próximos contra o time inimigo.</div>
          </div>
        </div>
      </section>

      <!-- ============ TEAM ANALYSIS ============ -->
      <section class="view" data-view="teamanalysis">
        <div class="stack">
          <div class="panel">
            <h2 class="ptitle">Leitura do Placar por IA<span class="grow"></span></h2>
            <div class="toolbar">
              <label>Atalho:</label> <span class="kbd">Tab</span> +
              <select id="hksel">
                <option>f5</option><option>f7</option>
                <option>f9</option><option>f10</option><option>f11</option><option>f12</option>
              </select>
              <button class="btn primary js-scan">📷 Escanear agora</button>
              <span style="flex:1"></span>
              <span class="chip click js-voice">🔊 voz: off</span>
            </div>
            <div class="toolbar" style="margin-bottom:0">
              <span class="chip" id="sbchip">pronto pra escanear</span>
              <img id="thumb" alt="" onerror="this.style.display='none'">
            </div>
            <div id="scan-errors"></div>
          </div>
          <div class="panel">
            <h2 class="ptitle">Times<span class="grow"></span></h2>
            <div class="teams" id="teams"><span class="empty">escaneie o placar para listar os times.</span></div>
          </div>
          <div class="panel">
            <h2 class="ptitle">Relatório Tático do Agente<span class="grow"></span></h2>
            <div class="report empty2" id="report">escaneie o placar (Tab + tecla) para o agente analisar a partida.</div>
          </div>
        </div>
      </section>

      <!-- ============ STRATEGY (chat) ============ -->
      <section class="view" data-view="strategy">
        <div class="panel" style="display:flex;flex-direction:column;min-height:calc(100vh - 150px)">
          <h2 class="ptitle">Conversar com o Copiloto<span class="grow"></span>
            <span class="chip click" id="clearchat" style="font-size:11px;padding:4px 11px">limpar</span></h2>
          <div class="voicehint" id="voice-hint">🎤 Aperte <b id="voice-hint-key">F6</b> a qualquer momento (inclusive dentro do jogo) para <b>falar por voz</b> — sua fala vira mensagem aqui e a resposta sai falada. Você também pode digitar abaixo.</div>
          <div id="chat">
            <div id="log"></div>
            <form id="chatform">
              <textarea id="chatinput" rows="1" placeholder="ex: o que compro agora contra esse time?"></textarea>
              <button type="button" id="micbtn" title="Falar">🎤</button>
              <button type="submit" id="chatsend">Enviar</button>
            </form>
          </div>
        </div>
      </section>

      <!-- ============ REPLAY (em breve) ============ -->
      <section class="view" data-view="replay">
        <div class="panel">
          <div class="soon-big">
            <svg class="ic" viewBox="0 0 24 24" fill="var(--tx3)"><path d="M11 6L4 12l7 6V6z"/><path d="M20 6l-7 6 7 6V6z"/></svg>
            <b>ANÁLISE DE REPLAY</b>
            <p>Em breve: revisão de partidas gravadas com timings, erros de posicionamento e sugestões do copiloto.</p>
          </div>
        </div>
      </section>

      <!-- ============ SETTINGS ============ -->
      <section class="view" data-view="settings">
        <div class="stack">
          <div class="grid2">
            <div class="panel">
              <h2 class="ptitle">Cérebro de IA<span class="grow"></span></h2>
              <div class="fields">
                <div class="field"><div class="l">Provedor ativo</div><div class="v" id="set-prov" style="font-size:15px">...</div></div>
                <div class="field"><div class="l">Conexão GSI</div><div class="v" id="set-conn" style="font-size:15px">...</div></div>
                <div class="field"><div class="l">Match ID</div><div class="v" id="set-match" style="font-size:15px">–</div></div>
                <div class="field"><div class="l">Versão</div><div class="v" id="set-version" style="font-size:15px">–</div></div>
              </div>
              <div class="sidenote" id="update-note" style="display:none;border-style:solid;margin-top:10px">
                ⬆ <b>Nova versão disponível:</b> <span id="update-latest"></span> —
                <a id="update-link" href="#" target="_blank" style="color:var(--gold-hi)">baixar o instalador</a>
                (feche e abra de novo depois de instalar… ele faz isso sozinho 😉)
              </div>
            </div>
            <div class="panel">
              <h2 class="ptitle">Voz do navegador<span class="grow"></span></h2>
              <div class="toolbar"><span class="chip click js-voice">🔊 voz: off</span></div>
              <div class="sidenote" style="border-style:solid">
                <b>Leitura do placar:</b> em <b>Team Analysis</b>, escolha a tecla e use <b>Tab + tecla</b> no jogo.<br><br>
                <b>Voz do navegador (grátis):</b> 🔊 lê as respostas · 🎤 (no chat) captura por voz via Web Speech (Chrome/Edge).
              </div>
            </div>
          </div>

          <div class="panel">
            <h2 class="ptitle">Cérebro de IA — provedor e chaves<span class="grow"></span>
              <span class="acc">fallback pra quando o SDK cair</span></h2>
            <div class="cfg-row">
              <label>Usar</label>
              <select id="ai-provider" style="max-width:440px"></select>
              <span class="acc">no “Automático” usa sua assinatura do Claude e cai numa chave se ela parar</span>
            </div>
            <div class="cfg-group">🔑 CHAVES DE API <small>— guardadas só no seu PC (nunca aparecem aqui); deixe em branco pra manter a atual</small></div>
            <div class="cfg-row">
              <label>Claude (Anthropic)</label>
              <input type="password" id="ai-key-anthropic" class="cfg-input" placeholder="sk-ant-..." autocomplete="off">
              <span class="chip" id="ai-has-anthropic">—</span>
              <button class="btn" data-clear="anthropic">Limpar</button>
            </div>
            <div class="cfg-row">
              <label>OpenAI</label>
              <input type="password" id="ai-key-openai" class="cfg-input" placeholder="sk-..." autocomplete="off">
              <span class="chip" id="ai-has-openai">—</span>
              <button class="btn" data-clear="openai">Limpar</button>
            </div>
            <div class="cfg-row">
              <label>Gemini (Google)</label>
              <input type="password" id="ai-key-gemini" class="cfg-input" placeholder="AIza..." autocomplete="off">
              <span class="chip" id="ai-has-gemini">—</span>
              <button class="btn" data-clear="gemini">Limpar</button>
            </div>
            <div class="cfg-row">
              <span style="flex:1"></span>
              <button class="btn primary" id="ai-save">Salvar chaves</button>
            </div>
            <div class="cfg-note">Onde pegar: <b>OpenAI</b> platform.openai.com/api-keys · <b>Gemini</b> aistudio.google.com/apikey · <b>Anthropic</b> console.anthropic.com. A chave da <b>voz</b> (OpenAI) é separada, na seção de voz.</div>
          </div>

          <div class="panel">
            <h2 class="ptitle">Atalhos no jogo<span class="grow"></span>
              <span class="acc">funcionam com o Dota em foco · ficam salvos</span></h2>
            <div class="cfg-row">
              <label>Ler o placar (visão)</label>
              <span class="kbd">Tab</span> +
              <select id="hk-scan" style="max-width:110px">
                <option>f5</option><option>f7</option><option>f9</option><option>f10</option><option>f11</option><option>f12</option>
              </select>
              <span class="acc">captura o placar → relatório tático completo</span>
            </div>
            <div class="cfg-row">
              <label>Relatório rápido de itens</label>
              <span class="kbd">Tab</span> +
              <select id="hk-items" style="max-width:110px">
                <option>f5</option><option>f7</option><option>f9</option><option>f10</option><option>f11</option><option>f12</option>
              </select>
              <span class="acc">lista curta dos próximos itens (usa o último placar)</span>
            </div>
            <div class="sidenote" style="border-style:solid">
              Não use a mesma tecla nos dois atalhos. A tecla pra <b>falar por voz</b> fica na seção de voz mais abaixo.
            </div>
          </div>

          <div class="panel">
            <h2 class="ptitle">Overlay do minimapa — fantasmas<span class="grow"></span>
              <span class="acc">Tab+F6 liga/desliga</span></h2>
            <div class="cfg-row">
              <label>Expirar fantasma após</label>
              <select id="ov-ttl" style="max-width:300px">
                <option value="30">30 segundos</option>
                <option value="60">1 minuto</option>
                <option value="120">2 minutos (padrão)</option>
                <option value="180">3 minutos</option>
                <option value="300">5 minutos</option>
                <option value="0">Nunca — mantém até reaparecer</option>
              </select>
              <span class="acc">tempo que a última posição do inimigo fica marcada</span>
            </div>
            <div class="cfg-row">
              <label>Estilo do fantasma</label>
              <select id="ov-portrait" style="max-width:300px">
                <option value="0">Bolinha na cor do herói (padrão)</option>
                <option value="1">Retrato do herói</option>
              </select>
              <span class="acc">o retrato precisa do placar lido (Tab+F7)</span>
            </div>
            <div class="sidenote" style="border-style:solid">
              Desenha a <b>última posição</b> de um inimigo que sumiu na fog (na cor do herói + cronômetro), por cima do minimapa do jogo — sem alterar o minimapa. Requer o Dota em <b>"Tela cheia em janela"</b> (borderless).
            </div>
          </div>

          <div class="panel">
            <h2 class="ptitle">Voz do Copiloto — OpenAI (atalho “me ouvir”)<span class="grow"></span>
              <span class="acc" id="voice-status-acc">—</span></h2>
            <div class="cfg">
              <!-- Chave: uma só, vale pra ouvir E pra falar -->
              <div class="cfg-row">
                <label>Chave da OpenAI</label>
                <input type="password" id="vk-key" class="cfg-input" placeholder="sk-... (fica só no servidor, nunca aparece aqui)" autocomplete="off">
                <button class="btn primary" id="vk-save">Salvar chave</button>
                <button class="btn" id="vk-clear">Limpar</button>
                <span class="chip" id="vk-status">verificando...</span>
              </div>
              <div class="cfg-note">Uma única chave, usada tanto pra <b>ouvir</b> quanto pra <b>falar</b>. Pegue em <b>platform.openai.com/api-keys</b>.</div>

              <div class="cfg-group">⚡ MOTOR DO RELATÓRIO <small>— quem lê o placar e escreve a análise tática</small></div>
              <div class="cfg-row">
                <label>Motor</label>
                <select id="vc-report-engine" style="max-width:400px">
                  <option value="claude">Claude — preciso (lê certo), porém lento ~2 min · padrão</option>
                  <option value="openai">OpenAI — rápido ~10s, mas pode errar a leitura</option>
                </select>
                <span class="acc">vale pra ler o placar (Tab) e escrever o relatório</span>
              </div>

              <!-- ───────── GRUPO 1: SAÍDA (o que VOCÊ OUVE) ───────── -->
              <div class="cfg-group">🔊 O QUE VOCÊ OUVE <small>— a voz do copiloto te respondendo (sai no volume cheio do PC)</small></div>
              <div class="cfg-grid">
                <div class="cfg-cell"><label>O copiloto fala comigo?</label>
                  <select id="vc-engine"><option value="openai">Sim — voz da OpenAI</option><option value="off">Não, só texto</option></select></div>
                <div class="cfg-cell"><label>Voz do copiloto</label><select id="vc-voice"></select></div>
                <div class="cfg-cell"><label>Ler a análise tática em voz alta</label>
                  <select id="vc-speakreport"><option value="on">Sim, quando ficar pronta</option><option value="off">Não</option></select></div>
              </div>
              <div class="cfg-row">
                <label>Estilo da fala</label>
                <input type="text" id="vc-inst" class="cfg-input" placeholder="ex.: fale como um treinador empolgado e direto (opcional)">
                <button class="btn" id="vc-test">🔊 Testar voz agora</button>
              </div>

              <!-- ───────── GRUPO 2: ENTRADA (quando VOCÊ FALA) ───────── -->
              <div class="cfg-group">🎤 QUANDO VOCÊ FALA <small>— captar sua voz pelo MICROFONE DO PC (escolha abaixo), com um atalho que funciona a qualquer momento</small></div>
              <div class="cfg-grid">
                <div class="cfg-cell"><label>Microfone</label>
                  <select id="vc-mic"><option value="">Padrão do Windows</option></select></div>
                <div class="cfg-cell"><label>Tecla pra falar (a qualquer hora)</label>
                  <select id="vc-hotkey"><option>f5</option><option>f6</option><option>f7</option><option>f8</option><option>f9</option><option>f10</option><option>f11</option><option>f12</option></select></div>
                <div class="cfg-cell"><label>Bip ao começar a ouvir</label>
                  <select id="vc-beep"><option value="on">Sim</option><option value="off">Não</option></select></div>
                <div class="cfg-cell"><label>Abaixar os OUTROS apps enquanto ouve/fala</label>
                  <select id="vc-duck"><option value="off">Manter</option><option value="0.1">Abaixar p/ 10%</option><option value="0.2">Abaixar p/ 20%</option><option value="0.35">Abaixar p/ 35%</option><option value="0.5">Abaixar p/ 50%</option></select></div>
              </div>
              <div class="cfg-row">
                <label>Testar microfone</label>
                <button class="btn" id="vc-mictest">🎙️ Testar (fale e veja a barra)</button>
                <div class="meter" id="vc-meter"><i id="vc-meter-bar"></i></div>
                <span class="acc" id="vc-meter-txt" style="min-width:120px"></span>
                <span style="flex:1"></span>
                <button class="btn primary" id="vc-save">Salvar configurações</button>
              </div>

              <div class="sidenote" id="voice-help" style="border-style:solid">
                <b>Como funciona:</b> aperte a tecla <b id="voice-help-key">F8</b> <b>a qualquer momento</b> (inclusive dentro do jogo) → o copiloto usa o <b>microfone do PC</b> escolhido acima (por isso o navegador NÃO pede permissão), abaixa só os outros apps, <b>te ouve</b> e <b>responde falando</b>. Use <b>“Testar microfone”</b> e fale: se a barra subir, o mic está certo. (O <b>“Testar voz agora”</b> lá em cima só FALA uma frase.) Evite usar a mesma tecla do placar (Tab+F7).
              </div>
            </div>
          </div>
        </div>
      </section>

    </main>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
const $$ = sel => Array.from(document.querySelectorAll(sel));
const esc = t => (t||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
function fmt(t){ return esc(t).replace(/\\*\\*(.+?)\\*\\*/g,'<strong>$1</strong>'); }
const sum = (arr,k) => (arr||[]).reduce((n,r)=>n+(Number(r[k])||0),0);
const nv = x => (x===null||x===undefined||x==='') ? '–' : x;
const num = x => (x===null||x===undefined||x==='') ? '–' : (typeof x==='number' ? x.toLocaleString('pt-BR') : x);

// estado global compartilhado pelos dois pollers
let G={connected:false}, GAGE=null, RAW=null, S={};

// ---------- navegação (com rota por hash, deep-link + refresh) ----------
const VIEWS=['dashboard','draft','gamestatus','heroinsights','itemadvisor','teamanalysis','strategy','replay','settings'];
let curView='dashboard';
function showView(name){
  if(!VIEWS.includes(name)) name='dashboard';
  curView=name;
  $$('.view').forEach(v=>v.classList.toggle('active', v.dataset.view===name));
  $$('.nav-item').forEach(n=>n.classList.toggle('active', n.dataset.view===name));
  if(('#'+name)!==location.hash) history.replaceState(null,'','#'+name);
  if(name==='strategy'){ const i=$('chatinput'); if(i) setTimeout(()=>i.focus(),50); $('log')&&($('log').scrollTop=$('log').scrollHeight); }
  if(name==='draft') draftInit();
}
$$('.nav-item').forEach(n=>n.addEventListener('click',()=>showView(n.dataset.view)));
window.addEventListener('hashchange',()=>showView(location.hash.slice(1)));
showView(location.hash.slice(1)||'dashboard');

// ---------- helpers de render ----------
function port(img,name,cls){
  return `<div class="port ${cls||''}" title="${esc(name||'')}">`+
    (img?`<img src="${img}" onerror="this.style.display='none'">`:'')+`</div>`;
}
function abIcon(a){
  return `<div class="ab${a.ultimate?' ult':''}" title="${esc(a.name||'')}">`+
    (a.img?`<img src="${a.img}" onerror="this.style.display='none'">`:'')+
    (a.level?`<span class="lvl">${a.level}</span>`:'')+`</div>`;
}
function itemSlot(it){
  if(!it) return `<div class="islot empty"></div>`;
  return `<div class="islot" title="${esc(it.name||'')}">`+
    (it.img?`<img src="${it.img}" onerror="this.parentNode.classList.add('empty');this.remove()">`:'')+
    (it.charges?`<span class="chg">${it.charges}</span>`:'')+`</div>`;
}
function itemsGrid(items,slots){
  const arr=(items||[]).slice(); slots=slots||9;
  let out=''; for(let i=0;i<slots;i++) out+=itemSlot(arr[i]); return out;
}
function itemChips(list){
  if(!list||!list.length) return '';
  return `<div class="isugg-h">Itens sugeridos</div><div class="ichips">`+list.map(it=>
    `<div class="ichip" title="${esc(it.name||'')}">`+
    (it.img?`<img src="${it.img}" onerror="this.style.display='none'">`:'')+
    `<span>${esc(it.name||'')}</span></div>`).join('')+`</div>`;
}
// insere o icone do item ao lado do nome dele no texto ja formatado (1a ocorrencia)
function withItemIcons(html, list){
  if(!list||!list.length) return html;
  list.forEach(it=>{
    if(!it.name||!it.img) return;
    const rx = new RegExp('('+it.name.replace(/[.*+?^${}()|[\\]\\\\]/g,'\\\\$&')+')','i');
    html = html.replace(rx, `<img class="inl-item" src="${it.img}" onerror="this.style.display='none'">$1`);
  });
  return html;
}

// ---------- render principal ----------
function paint(){
  const fresh = GAGE!==null && GAGE<8;

  // topbar conexão
  $('conn-dot').className = 'dot'+(fresh?' live':'');
  $('conn-text').textContent = GAGE===null ? 'OFFLINE' : (fresh?'AO VIVO':'PARADO '+GAGE+'s');
  $('lm-mode').classList.toggle('on', fresh);
  $('lm-phase').textContent = (G.connected && G.game_state) ? G.game_state : (fresh?'em partida':'aguardando');
  $('lm-clock').textContent = G.clock || '--:--';

  // selo "PICK" no menu Draft durante a selecao de herois
  const inDraft = G.game_state_raw==='DOTA_GAMERULES_STATE_HERO_SELECTION'
               || G.game_state_raw==='DOTA_GAMERULES_STATE_STRATEGY_TIME';
  const dl=$('draft-live'); if(dl) dl.classList.toggle('on', !!(inDraft && fresh));

  // placar topo (vem do scoreboard)
  const allies=S.allies||[], enemies=S.enemies||[];
  $('lm-allies').innerHTML = allies.slice(0,5).map(r=>port(r.img,r.hero,'ally')).join('');
  $('lm-enemies').innerHTML = enemies.slice(0,5).map(r=>port(r.img,r.hero,'enemy')).join('');
  const ak=sum(allies,'k'), ek=sum(enemies,'k');
  $('lm-rad').textContent = (allies.length?ak:'–');
  $('lm-dire').textContent = (enemies.length?ek:'–');

  paintHero(); paintInsights(); paintEnemies(); paintGameStatus(); paintMinimap();
}

// ---------- minimapa ao vivo (thumbnail no dashboard) ----------
let mmStreaming=false;
function paintMinimap(){
  const fresh = GAGE!==null && GAGE<8;
  const box=$('mini-map'), img=$('mini-thumb');
  if(!box||!img) return;
  if(fresh && !mmStreaming){
    img.src='/minimap/stream'; mmStreaming=true; box.classList.add('live');
    img.onerror=()=>{ box.classList.remove('live'); mmStreaming=false; };
  } else if(!fresh && mmStreaming){
    img.src=''; mmStreaming=false; box.classList.remove('live');
  }
  $('mm-acc').textContent = fresh ? 'ao vivo' : 'aguardando';
}
function openMinimap(){
  window.open('/minimap','copiloto_minimapa',
    'width=820,height=900,menubar=no,toolbar=no,location=no,status=no');
}

function paintHero(){
  const box=$('hero-card');
  if(!G.connected){ box.innerHTML='<span class="empty">aguardando o jogo (GSI)...</span>'; }
  else{
    const kda=G.kda||[];
    box.innerHTML = `
      <div class="hero-portrait">
        ${G.hero_img?`<img src="${G.hero_img}" onerror="this.style.display='none'">`:''}
        <div class="ov"></div>
        <div class="lvl">${nv(G.level)}</div>
        <div class="nm"><b>${esc((G.hero||'Herói').toUpperCase())}</b><small>${G.alive===false?'morto':'em jogo'}</small></div>
      </div>
      <div class="bars">
        <div class="bar hp"><i style="width:${G.health_pct||0}%"></i></div>
        <div class="bar mp"><i style="width:${G.mana_pct||0}%"></i></div>
      </div>
      <div class="statgrid">
        <div class="stat"><div class="l">K / D / A</div><div class="v"><b>${nv(kda[0])}</b> / <i>${nv(kda[1])}</i> / <u>${nv(kda[2])}</u></div></div>
        <div class="stat"><div class="l">LH / DN</div><div class="v">${nv(G.last_hits)} / ${nv(G.denies)}</div></div>
        <div class="stat"><div class="l">Ouro</div><div class="v gold">${num(G.gold)}</div></div>
        <div class="stat"><div class="l">Net Worth</div><div class="v gold">${num(G.net_worth!=null?G.net_worth:G.gold)}</div></div>
      </div>
      <div class="subh">Habilidades</div>
      <div class="abilities">${(G.abilities&&G.abilities.length)?G.abilities.map(abIcon).join(''):'<span class="empty">—</span>'}</div>
      <div class="subh">Itens Atuais</div>
      <div class="items">${itemsGrid(G.items,9)}</div>`;
  }
  // hero insights view (espelho expandido)
  if(G.abilities) $('hi-abilities').innerHTML = G.abilities.length
    ? `<div class="abilities" style="flex-wrap:wrap;gap:9px">${G.abilities.map(abIcon).join('')}</div>` : '<span class="empty">sem habilidades no estado atual.</span>';
  $('hi-items').innerHTML = `<div class="items" style="grid-template-columns:repeat(3,52px);gap:8px">${itemsGrid(G.items,9)}</div>`;
  $('ia-items').innerHTML = (G.items&&G.items.length)
    ? `<div class="items" style="grid-template-columns:repeat(6,46px);gap:8px">${itemsGrid(G.items,Math.max(6,G.items.length))}</div>`
    : '<span class="empty">aguardando inventário (GSI)...</span>';
}

function paintInsights(){
  // relatório / prioridade
  const rep = S.report;
  const sugg = S.suggested_items||[];
  const repHtml = rep ? withItemIcons(fmt(rep), sugg) : '';
  if(rep){
    $('insight-tag').innerHTML = '⚠ ALTA PRIORIDADE';
    $('insight-report').innerHTML = repHtml;
  } else {
    $('insight-tag').innerHTML = '⚠ análise tática';
    $('insight-report').innerHTML = '<span class="empty">escaneie o placar (Tab + tecla) — a análise tática do copiloto aparece aqui.</span>';
  }
  $('ia-report').className = rep?'report':'report empty2';
  $('ia-report').innerHTML = rep?repHtml:'escaneie o placar (Team Analysis) — o copiloto avalia seus itens e sugere os próximos contra o time inimigo.';
  const chips = (rep && sugg.length)?`<div class="isugg">${itemChips(sugg)}</div>`:'';
  $('ia-suggest').innerHTML = chips;
  $('insight-suggest').innerHTML = chips;

  // ameaças = inimigos
  const enemies=S.enemies||[];
  $('threats').innerHTML = enemies.length
    ? enemies.map(r=>`<div class="port enemy" style="width:62px;height:38px" title="${esc(r.hero||'')}">${r.img?`<img src="${r.img}" onerror="this.style.display='none'">`:''}<span class="tnm">${esc(r.hero||'?')}</span></div>`).join('')
    : '<span class="empty">escaneie o placar para detectar os inimigos.</span>';

  // situação da partida
  const allies=S.allies||[];
  const ak=sum(allies,'k'), ek=sum(enemies,'k'), tot=ak+ek;
  const adv = tot? Math.round(ak/tot*100) : 50;
  const advCls = adv>55?'good':(adv<45?'bad':'');
  const nw = G.net_worth!=null?G.net_worth:G.gold;
  $('situation').innerHTML = `
    <div class="c"><div class="l">Fase</div><div class="v" style="font-size:13px">${G.connected?esc(G.game_state||'–'):'–'}</div></div>
    <div class="c"><div class="l">Relógio</div><div class="v g">${G.clock||'--:--'}</div><div class="mini">${G.connected?esc(G.daytime||''):''}</div></div>
    <div class="c"><div class="l">Vantagem em abates</div><div class="v ${advCls}">${tot?adv+'%':'–'}</div><div class="advbar"><i style="width:${tot?adv:50}%"></i></div></div>
    <div class="c"><div class="l">Patrimônio</div><div class="v g">${num(nw)}</div><div class="mini">GPM ${nv(G.gpm)}</div></div>`;

  // sugestão rápida
  if(rep){
    const first = rep.split(/(?<=[.!?])\\s+/).slice(0,2).join(' ');
    $('quicktip').innerHTML = fmt(first);
  }
}

function paintEnemies(){
  // ordena por FACILIDADE de matar agora (vantagem natural + forma atual): mais facil no topo
  const enemies=(S.enemies||[]).slice().sort((a,b)=>((b.ease??-9)-(a.ease??-9)));
  $('enemy-list').innerHTML = enemies.length ? enemies.map((r,i)=>{
    const pct=Math.round((r.adv||0)*100);
    const cls = pct>3?'adv-good':(pct<-3?'adv-bad':'adv-neu');
    const arrow = pct>3?'▲':(pct<-3?'▼':'▬');
    const badge=`<span class="advbadge ${cls}" title="vantagem natural do seu herói contra ele">${arrow} ${pct>0?'+':''}${pct}%</span>`;
    return `
    <div class="enemy-row">
      <span class="erank">${i+1}</span>
      ${port(r.img,r.hero,'enemy')}
      <div class="einfo"><div class="nm">${esc(r.hero||'?')}</div><div class="pl">${esc(r.player||'')}</div></div>
      <div class="eright">${badge}<div class="kda"><b>${nv(r.k)}</b>/<i>${nv(r.d)}</i>/<u>${nv(r.a)}</u></div></div>
    </div>`;}).join('') : '<span class="empty">sem leitura do placar ainda.</span>';

  // donut placar de abates
  const allies=S.allies||[];
  const ak=sum(allies,'k'), ek=sum(enemies,'k'), tot=ak+ek;
  const box=$('donut-box');
  if(!tot){ box.innerHTML='<span class="empty">sem dados de abates.</span>'; return; }
  const C=2*Math.PI*42, radLen=C*(ak/tot);
  box.innerHTML = `<div class="donut-wrap">
    <svg viewBox="0 0 110 110" class="donut">
      <circle cx="55" cy="55" r="42" fill="none" stroke="#1a2230" stroke-width="14"/>
      <circle cx="55" cy="55" r="42" fill="none" stroke="var(--dire)" stroke-width="14"/>
      <circle cx="55" cy="55" r="42" fill="none" stroke="var(--rad)" stroke-width="14" stroke-dasharray="${radLen} ${C}" transform="rotate(-90 55 55)"/>
      <text x="55" y="52" text-anchor="middle" class="d-num">${ak}–${ek}</text>
      <text x="55" y="67" text-anchor="middle" class="d-lbl">ABATES</text>
    </svg>
    <div class="legend">
      <div class="li"><span class="sw" style="background:var(--rad)"></span> Aliados <b>${ak}</b></div>
      <div class="li"><span class="sw" style="background:var(--dire)"></span> Inimigos <b>${ek}</b></div>
    </div></div>`;
}

function paintGameStatus(){
  $('gs-acc').textContent = GAGE===null?'sem dados':(GAGE<8?'ao vivo':'parado '+GAGE+'s');
  if(!G.connected){ $('gs-fields').innerHTML='<span class="empty">aguardando dados do Dota...</span>'; }
  else{
    const kda=G.kda||[];
    const f=(l,v)=>`<div class="field"><div class="l">${l}</div><div class="v">${nv(v)}</div></div>`;
    $('gs-fields').innerHTML =
      f('Fase',G.game_state)+f('Relógio',G.clock)+f('Período',G.daytime)+f('Herói',G.hero)+
      f('Nível',G.level)+f('Ouro',num(G.gold))+f('Net Worth',num(G.net_worth))+f('GPM',G.gpm)+f('XPM',G.xpm)+
      f('K/D/A',kda.some(x=>x!=null)?kda.map(x=>nv(x)).join(' / '):'–')+f('Last Hits',G.last_hits)+f('Denies',G.denies)+
      f('Dano herói',num(G.hero_damage))+f('Vida %',G.health_pct)+f('Mana %',G.mana_pct);
  }
  $('raw').textContent = RAW ? JSON.stringify(RAW,null,2) : '(nada ainda)';
  $('set-prov').textContent = PROVIDER_NAME || '...';
  $('set-conn').textContent = GAGE===null?'offline':(GAGE<8?'ao vivo':'parado '+GAGE+'s');
  $('set-match').textContent = (G.connected&&G.match_id)?G.match_id:'–';
}

// ---------- poll GSI ----------
async function tick(){
  try{
    const d = await (await fetch('/state')).json();
    G = d.summary || {connected:false};
    GAGE = d.seconds_since_update;
    RAW = d.raw;
  }catch(e){ G={connected:false}; GAGE=null; }
  paint();
}
setInterval(tick,1000); tick();

// ---------- poll Scoreboard ----------
let sbLastScan=0;
const CHIP={ idle:['','pronto pra escanear'], capturando:['work','capturando a tela...'],
  recebido:['go','📸 print recebido'], analisando:['work','🧠 Claude analisando o placar...'],
  pronto:['go','✅ leitura concluída'], erro:['err','erro ao ler'] };
function teamHtml(title,cls,rows){
  const body=(rows||[]).map(r=>`
    <div class="hero">${port(r.img,r.hero,cls)}
      <div><div class="nm">${esc(r.hero||'?')}</div><div class="pl">${esc(r.player||'')}</div></div>
      <div class="kda"><b>${nv(r.k)}</b>/<i>${nv(r.d)}</i>/<u>${nv(r.a)}</u></div>
    </div>`).join('');
  return `<div class="team ${cls}"><h3><i></i>${title}</h3>${body||'<span class="empty">—</span>'}</div>`;
}

// ---------- aviso GLOBAL de captura do placar (toast + flash, em qualquer aba) ----------
let prevScanStatus='idle', firstSB=true, toastHideTimer=null;
const ST_VIEW={
  capturando:{cls:'busy', emoji:'📸', t:'Capturando a tela…', s:'lendo o seu placar'},
  recebido:  {cls:'busy', emoji:'📸', t:'Print capturado', s:'enviando pra IA…'},
  analisando:{cls:'busy', emoji:'🧠', t:'Analisando o placar', s:'Claude lendo heróis e KDA…'},
  pronto:    {cls:'ok',   emoji:'✅', t:'Leitura concluída', s:'toque para ver o relatório'},
  erro:      {cls:'err',  emoji:'⚠️', t:'Erro ao ler o placar', s:'tente de novo (abra o Tab)'},
};
function flashScan(){ const f=$('scanflash'); if(!f) return; f.classList.remove('go'); void f.offsetWidth; f.classList.add('go'); }
function updateScanToast(d){
  const toast=$('scantoast'); if(!toast) return;
  const st=d.status||'idle';
  if(firstSB){ firstSB=false; prevScanStatus=st; return; } // nao mostra um toast "velho" no carregamento
  const active=['capturando','recebido','analisando'].includes(st);
  const wasActive=['capturando','recebido','analisando'].includes(prevScanStatus);
  if(active && !wasActive) flashScan(); // novo scan detectado: pisca a tela
  if(active || st==='pronto' || st==='erro'){
    const v=ST_VIEW[st]||ST_VIEW.capturando;
    toast.className='scantoast show '+v.cls;
    $('st-emoji').textContent=v.emoji;
    $('st-title').textContent=v.t;
    $('st-sub').textContent=(st==='erro'&&d.error)?('erro: '+d.error):v.s;
    const th=$('st-thumb');
    if(['recebido','analisando','pronto'].includes(st) && d.scanned_at){
      th.src='/scoreboard/image?t='+d.scanned_at; th.style.display='block'; toast.classList.add('has-thumb');
    } else toast.classList.remove('has-thumb');
    clearTimeout(toastHideTimer);
    if(st==='pronto'||st==='erro') toastHideTimer=setTimeout(()=>toast.classList.remove('show'), st==='erro'?5000:3800);
  }
  prevScanStatus=st;
}

function renderScanErrors(errs){
  const el=$('scan-errors'); if(!el) return;
  if(!errs.length){ el.innerHTML=''; return; }
  const rows=errs.slice().reverse().map(e=>{
    const t=e.at?new Date(e.at*1000).toLocaleTimeString('pt-BR'):'';
    const clk=e.clock?(' · '+e.clock):'';
    return `<div class="scan-err-row"><span class="scan-err-when">${t}${clk}</span> ${esc(e.reason||'erro')}</div>`;
  }).join('');
  el.innerHTML=`<div class="scan-err-box"><div class="scan-err-h">⚠ Últimas falhas de leitura do placar</div>${rows}</div>`;
}
async function pollSB(){
  try{
    const d = await (await fetch('/scoreboard/state')).json();
    if(!hkReady && d.hotkey){ $('hksel').value=d.hotkey; hkReady=true; }
    S = d;
    updateScanToast(d);
    const [c,txt]=CHIP[d.status]||CHIP.idle;
    $('sbchip').className='chip '+c;
    $('sbchip').innerHTML=(d.status==='capturando'||d.status==='analisando'?'<span class="spin"></span>':'')+(d.error?('erro: '+d.error):txt);
    renderScanErrors(d.errors||[]);
    const th=$('thumb');
    if(['recebido','analisando','pronto'].includes(d.status)){ th.src='/scoreboard/image?t='+d.scanned_at; th.style.display='block'; }
    if((d.allies&&d.allies.length)||(d.enemies&&d.enemies.length))
      $('teams').innerHTML=teamHtml('Seu time','ally',d.allies)+teamHtml('Inimigos','enemy',d.enemies);
    if(d.report){ $('report').className='report'; $('report').innerHTML=withItemIcons(fmt(d.report), d.suggested_items||[]); }
    if(d.scanned_at && d.scanned_at!==sbLastScan){
      sbLastScan=d.scanned_at;
      // o servidor ja fala a analise com a voz da OpenAI? entao o navegador NAO repete.
      const serverSpeaks = voiceCfg && voiceCfg.configured && voiceCfg.engine==='openai' && voiceCfg.speak_report;
      if(d.report && voiceOn && !serverSpeaks) speak(d.report);
    }
    paint();
  }catch(e){}
}
let hkReady=false;
setInterval(pollSB,800); pollSB();

// ---------- scan / hotkey (delegados por classe) ----------
async function doScan(btn){
  firstSB=false; updateScanToast({status:'capturando'}); // feedback visual imediato no clique
  const btns=$$('.js-scan'); btns.forEach(b=>b.disabled=true);
  try{ await fetch('/scoreboard/scan',{method:'POST'}); }catch(e){}
  btns.forEach(b=>b.disabled=false); pollSB();
}
$$('.js-scan').forEach(b=>b.addEventListener('click',()=>doScan(b)));
$('hksel').addEventListener('change', async ()=>{
  await fetch('/scoreboard/hotkey',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:$('hksel').value})});
});

// ---------- Relatório rápido só de itens (Tab+F5) ----------
const IR_CHIP={ idle:['','pronto'], gerando:['work','gerando...'], pronto:['go','pronto ✓'], erro:['err','erro'] };
let irHkReady=false, irLastAt=0;
async function pollItems(){
  try{
    const d=await (await fetch('/items/state')).json();
    if(!irHkReady && d.hotkey){ $('ir-hk').value=d.hotkey; irHkReady=true; }
    const [c,txt]=IR_CHIP[d.status]||IR_CHIP.idle;
    $('ir-chip').className='chip '+c;
    $('ir-chip').innerHTML=(d.status==='gerando'?'<span class="spin"></span>':'')+(d.error?('erro: '+d.error):txt);
    const rep=d.report, sugg=d.suggested_items||[];
    if(d.status==='erro' && d.error){
      $('ir-report').className='report empty2';
      $('ir-report').innerHTML='<span class="empty">'+esc(d.error)+'</span>';
      $('ir-suggest').innerHTML='';
    } else if(rep){
      $('ir-report').className='report';
      $('ir-report').innerHTML=withItemIcons(fmt(rep), sugg);
      $('ir-suggest').innerHTML=(sugg.length)?`<div class="isugg">${itemChips(sugg)}</div>`:'';
    }
    if(d.at && d.at!==irLastAt){ irLastAt=d.at;
      const serverSpeaks = voiceCfg && voiceCfg.configured && voiceCfg.engine==='openai' && voiceCfg.speak_report;
      if(rep && voiceOn && !serverSpeaks) speak(rep);
    }
  }catch(e){}
}
setInterval(pollItems,1000); pollItems();
$('ir-run').addEventListener('click', async ()=>{
  $('ir-run').disabled=true; $('ir-chip').className='chip work'; $('ir-chip').innerHTML='<span class="spin"></span>gerando...';
  try{ await fetch('/items/report',{method:'POST'}); }catch(e){}
  $('ir-run').disabled=false; pollItems();
});
$('ir-hk').addEventListener('change', async ()=>{
  await fetch('/items/hotkey',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:$('ir-hk').value})});
});

// ---------- Aba Draft (grid de picks + counters ao vivo) ----------
let DHEROES=null;                          // lista estatica de /heroes
let DSTATE={enemy:[],allies:[],bans:[]};   // marcacoes (servidor = fonte da verdade)
let DGRID={};                              // hero_id -> {counter_score, reasons, ...}
let DSUGG=[];                              // top counters (com motivos)
let dMode='enemy', dSearch='', dEditing=false, dPollTimer=null, dInited=false;

async function draftInit(){
  if(!dInited){
    dInited=true;
    try{ const d=await (await fetch('/heroes')).json(); DHEROES=d.heroes||[]; }
    catch(e){ DHEROES=[]; }
  }
  draftRefresh();
  if(!dPollTimer) dPollTimer=setInterval(()=>{ if(curView==='draft') draftRefresh(); }, 1400);
}

function applyGrid(gr){
  DGRID={}; (gr.suggestions||[]).forEach(s=>{ DGRID[s.hero_id]=s; });
  DSUGG = DSTATE.enemy.length ? (gr.suggestions||[]).filter(s=>s.counter_score>0).slice(0,8) : [];
}

async function draftRefresh(){
  try{
    const [st,gr,sc]=await Promise.all([
      fetch('/draft/state').then(r=>r.json()),
      fetch('/draft/grid').then(r=>r.json()),
      fetch('/draft/scan/state').then(r=>r.json()),
    ]);
    if(!dEditing){ DSTATE={enemy:st.enemy||[],allies:st.allies||[],bans:st.bans||[]}; }
    applyGrid(gr);
    updateDScan(sc);
    paintDraft();
  }catch(e){}
}

function updateDScan(sc){
  const chip=$('dchip'); if(!chip) return;
  const M={ idle:['','marque os inimigos ou copie a tela'],
            capturando:['work','capturando a tela...'],
            analisando:['work','Claude lendo os picks...'],
            pronto:['go','✅ picks preenchidos'],
            erro:['err','⚠️ '+(sc.error||'erro ao ler')] };
  const [c,txt]=M[sc.status]||M.idle;
  chip.className='chip '+c;
  chip.innerHTML=(sc.status==='capturando'||sc.status==='analisando'?'<span class="spin"></span> ':'')+esc(txt);
  const th=$('dthumb');
  if(th && ['analisando','pronto'].includes(sc.status) && sc.scanned_at){
    th.src='/draft/scan/image?t='+sc.scanned_at; th.style.display='block';
  }
}

function advClass(v){
  if(v>=4) return 'g3'; if(v>=2) return 'g2'; if(v>=0.6) return 'g1';
  if(v<=-4) return 'b2'; if(v<=-1) return 'b1'; return '';
}
function roleOf(id){
  if(DSTATE.enemy.includes(id)) return 'enemy';
  if(DSTATE.allies.includes(id)) return 'ally';
  if(DSTATE.bans.includes(id)) return 'ban';
  return null;
}

function paintDraft(){
  if(!$('dgrid')) return;
  $('dc-enemy').textContent=DSTATE.enemy.length;
  $('dc-ally').textContent=DSTATE.allies.length;
  $('dc-ban').textContent=DSTATE.bans.length;
  const hasEnemy=DSTATE.enemy.length>0;
  $('dgrid-acc').textContent=hasEnemy?'ordenado por vantagem':'ordenado por atributo';

  const box=$('dgrid');
  if(!DHEROES||!DHEROES.length){
    box.innerHTML='<span class="empty">cache de heróis indisponível (rode build_cache.py).</span>';
  } else {
    const q=dSearch.trim().toLowerCase();
    const rank={enemy:0,ally:1,ban:2};
    const list=DHEROES.filter(h=>!q || (h.name||'').toLowerCase().includes(q)).slice();
    list.sort((a,b)=>{
      const ra=roleOf(a.id), rb=roleOf(b.id);
      if(ra&&rb) return rank[ra]-rank[rb];
      if(ra&&!rb) return -1;
      if(rb&&!ra) return 1;
      if(hasEnemy){
        const va=DGRID[a.id]?DGRID[a.id].counter_score:0, vb=DGRID[b.id]?DGRID[b.id].counter_score:0;
        return vb-va;
      }
      return (a.name||'').localeCompare(b.name||'');
    });
    box.innerHTML=list.map(h=>{
      const role=roleOf(h.id), sc=DGRID[h.id];
      const v=(sc&&hasEnemy)?sc.counter_score:null;
      const cls=role?('mk-'+role):(v!=null?advClass(v):'');
      const badge=(v!=null&&Math.abs(v)>=0.6)?`<span class="adv ${v>0?'good':'bad'}">${v>0?'+':''}${Math.round(v)}</span>`:'';
      const mk=role?`<span class="mk ${role}">${role==='enemy'?'I':role==='ally'?'A':'B'}</span>`:'';
      return `<div class="dh ${cls}" data-id="${h.id}" title="${esc(h.name||'')}">`+
        (h.img?`<img src="${h.img}" onerror="this.style.display='none'">`:'')+
        badge+mk+`<span class="nm">${esc(h.name||'')}</span></div>`;
    }).join('');
  }

  const sg=$('dsugg');
  if(!hasEnemy){ sg.innerHTML='<span class="empty">marque ao menos um inimigo para ver os counters.</span>'; }
  else if(!DSUGG.length){ sg.innerHTML='<span class="empty">sem counter claro contra esse time ainda.</span>'; }
  else{
    sg.innerHTML=DSUGG.map(s=>`
      <div class="row">
        ${port(s.img,s.name,'ally')}
        <div><div class="nm">${esc(s.name||'')}</div><div class="rs">${esc((s.reasons||[]).slice(0,2).join(' · ')||'bom contra o time inimigo')}</div></div>
        <div class="pc">+${Math.round(s.counter_score)}</div>
      </div>`).join('');
  }
}

async function draftTap(id){
  const key=dMode==='enemy'?'enemy':(dMode==='ally'?'allies':'bans');
  const had=DSTATE[key].includes(id);
  ['enemy','allies','bans'].forEach(k=>{ DSTATE[k]=DSTATE[k].filter(x=>x!==id); });
  if(!had) DSTATE[key].push(id);   // re-tocar no mesmo papel desmarca
  dEditing=true; paintDraft();
  try{
    await fetch('/draft/state',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({enemy:DSTATE.enemy,allies:DSTATE.allies,bans:DSTATE.bans})});
    const gr=await (await fetch('/draft/grid')).json();
    applyGrid(gr); paintDraft();
  }catch(e){}
  dEditing=false;
}

$('dgrid').addEventListener('click',e=>{ const c=e.target.closest('.dh'); if(c) draftTap(+c.dataset.id); });
$('dmode').addEventListener('click',e=>{ const b=e.target.closest('button'); if(!b) return;
  dMode=b.dataset.role;
  $$('#dmode button').forEach(x=>x.classList.toggle('on', x.dataset.role===dMode));
});
$('dsearch').addEventListener('input',e=>{ dSearch=e.target.value; paintDraft(); });
$('dscan').addEventListener('click',async()=>{
  const b=$('dscan'); b.disabled=true; updateDScan({status:'capturando'});
  try{ await fetch('/draft/scan',{method:'POST'}); }catch(e){}
  b.disabled=false; draftRefresh();
});
$('dclear').addEventListener('click',async()=>{
  DSTATE={enemy:[],allies:[],bans:[]}; DSUGG=[]; paintDraft();
  try{ await fetch('/draft/clear',{method:'POST'}); }catch(e){}
  draftRefresh();
});

// ---------- chat ----------
let PROVIDER_NAME='';
const log=$('log'), input=$('chatinput'), form=$('chatform'), sendBtn=$('chatsend');
function addMsg(role,text){
  const div=document.createElement('div');
  div.className='msg '+(role==='user'?'user':'bot');
  div.innerHTML=fmt(text); log.appendChild(div); log.scrollTop=log.scrollHeight; return div;
}
async function loadHistory(){
  try{
    const d=await (await fetch('/chat/history')).json();
    PROVIDER_NAME=d.provider||'?';
    // o texto/cor do #agent-prov quem cuida e o pollAiHealth (status real da conexao)
    if($('set-prov')) $('set-prov').textContent=PROVIDER_NAME;
    log.innerHTML='';
    (d.history||[]).forEach(m=>addMsg(m.role,m.content));
    if(!(d.history||[]).length) addMsg('bot','Escaneie o placar e me pergunte o que fazer. Posso falar sobre itens, ameaças e jogadas.');
  }catch(e){}
}
form.addEventListener('submit', async (e)=>{
  e.preventDefault();
  const msg=input.value.trim(); if(!msg) return;
  addMsg('user',msg); input.value=''; input.style.height='auto'; sendBtn.disabled=true;
  const thinking=addMsg('bot','...');
  try{
    const d=await (await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg})})).json();
    thinking.innerHTML=fmt(d.reply||d.error||'(sem resposta)'); speak(d.reply||'');
  }catch(e){ thinking.textContent='erro de conexão.'; }
  log.scrollTop=log.scrollHeight; sendBtn.disabled=false; input.focus();
});
input.addEventListener('input',()=>{ input.style.height='auto'; input.style.height=Math.min(input.scrollHeight,120)+'px'; });
input.addEventListener('keydown',(e)=>{ if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); form.requestSubmit(); } });
$('clearchat').addEventListener('click', async ()=>{ await fetch('/chat/reset',{method:'POST'}); loadHistory(); });
loadHistory();

// ---------- status REAL da conexao com a IA (health-check) ----------
async function pollAiHealth(force){
  try{
    const d = await (await fetch('/ai/health'+(force?'?force=1':''))).json();
    const st = $('agent-status'); if(!st) return;
    const lvl = d.level || 'checking';
    st.className = 'astat '+lvl;
    const prov = d.provider || 'IA';
    const short = prov.replace(/\\s*\\(.*\\)/,'');   // "Claude (assinatura...)" -> "Claude"
    st.title = prov + ' — ' + (d.detail||'');
    $('agent-prov').textContent =
        lvl==='ok'       ? short+' · conectado' :
        lvl==='warn'     ? (d.detail||'modo básico (sem IA)') :
        lvl==='checking' ? 'verificando conexão…' :
                           short+' · sem conexão';
    if($('set-prov')) $('set-prov').textContent =
        prov + (lvl==='ok'?' · conectado':lvl==='bad'?' · sem conexão':'');
  }catch(e){}
}
// clicar no indicador re-testa na hora
(function(){ const st=$('agent-status'); if(st) st.addEventListener('click',()=>{
  st.className='astat checking'; $('agent-prov').textContent='verificando conexão…'; pollAiHealth(true);
}); })();
setInterval(()=>pollAiHealth(false), 20000); pollAiHealth(false);

// ---------- voz ----------
const ttsOk='speechSynthesis' in window;
const RecCtor=window.SpeechRecognition||window.webkitSpeechRecognition;
let voiceOn=false;
function ptVoice(){ const vs=speechSynthesis.getVoices(); return vs.find(v=>v.lang&&v.lang.toLowerCase().startsWith('pt'))||vs.find(v=>v.default)||vs[0]; }
function speak(text){
  if(!voiceOn||!ttsOk||!text) return;
  speechSynthesis.cancel();
  const clean=text.replace(/\\*\\*/g,'').replace(/[#>*_`]/g,'').replace(/\\s+/g,' ').trim();
  (clean.match(/[^.!?\\n]+[.!?]?/g)||[clean]).forEach(p=>{
    const u=new SpeechSynthesisUtterance(p.trim()); const v=ptVoice(); if(v) u.voice=v;
    u.lang='pt-BR'; u.rate=1.05; speechSynthesis.speak(u);
  });
}
function setVoiceLabels(){ $$('.js-voice').forEach(el=>el.textContent=voiceOn?'🔊 voz: on':'🔊 voz: off'); }
$$('.js-voice').forEach(el=>el.addEventListener('click',()=>{
  if(!ttsOk){ el.textContent='voz indisponível'; return; }
  voiceOn=!voiceOn; setVoiceLabels();
  if(voiceOn){ speechSynthesis.getVoices(); speak('Voz ligada.'); } else speechSynthesis.cancel();
}));
const micbtn=$('micbtn'); let recording=false, rec=null;
if(RecCtor && micbtn){
  rec=new RecCtor(); rec.lang='pt-BR'; rec.interimResults=false; rec.maxAlternatives=1;
  rec.onresult=(e)=>{ input.value=e.results[0][0].transcript; form.requestSubmit(); };
  rec.onend=()=>{ recording=false; micbtn.classList.remove('rec'); };
  rec.onerror=()=>{ recording=false; micbtn.classList.remove('rec'); };
  micbtn.addEventListener('click',()=>{ if(recording){ try{rec.stop();}catch(e){} return; } try{rec.start(); recording=true; micbtn.classList.add('rec'); }catch(e){} });
} else if(micbtn) micbtn.style.display='none';

// ---------- voz OpenAI (atalho "me ouvir": ouve -> transcreve -> responde -> fala) ----------
const VKEY=$('vk-key'), VSTATUS=$('vk-status'), VACC=$('voice-status-acc'), VBTN=$('voicebtn');
let voiceCfg=null;
async function loadVoiceConfig(){
  try{
    const c=await (await fetch('/voice/config')).json(); voiceCfg=c;
    if(VSTATUS){ VSTATUS.className='chip '+(c.configured?'go':'err'); VSTATUS.textContent=c.configured?'✓ chave configurada':'⚠ sem chave'; }
    const vs=$('vc-voice'); if(vs){ if(!vs.options.length) vs.innerHTML=(c.voices||[]).map(v=>`<option value="${v}">${v}</option>`).join(''); vs.value=c.voice||'coral'; }
    if($('vc-engine')) $('vc-engine').value=c.engine||'openai';
    if($('vc-hotkey')) $('vc-hotkey').value=c.hotkey||'f8';
    if($('vc-inst')) $('vc-inst').value=c.instructions||'';
    if($('vc-duck')) $('vc-duck').value=c.duck?String(c.duck_level):'off';
    if($('vc-beep')) $('vc-beep').value=c.beep?'on':'off';
    if($('vc-speakreport')) $('vc-speakreport').value=c.speak_report?'on':'off';
    if($('vc-report-engine')) $('vc-report-engine').value=c.report_engine||'claude';
    const md=$('vc-mic');
    if(md){
      md.innerHTML='<option value="">Padrão do Windows</option>'+(c.devices||[]).map(d=>`<option value="${d.index}">${esc(d.name)}</option>`).join('');
      md.value=(c.mic_index===null||c.mic_index===undefined)?'':String(c.mic_index);
    }
    const hk=(c.hotkey||'f8').toUpperCase();
    if($('voice-help-key')) $('voice-help-key').textContent=hk;
    if($('voice-hint-key')) $('voice-hint-key').textContent=hk;   // aviso no Strategy
    if(VBTN) VBTN.title='Falar com o copiloto — atalho global '+hk;
    let warn='';
    if(!c.audio_ok) warn+=' Microfone/gravação indisponível (rode: pip install sounddevice).';
    if(!c.volume_ok) warn+=' Controle de volume indisponível (rode: pip install pycaw comtypes).';
    const h=$('voice-help'); if(h && warn && !h.dataset.warned){ h.dataset.warned='1'; h.innerHTML+='<br><b style="color:var(--warn)">Atenção:</b>'+warn; }
  }catch(e){}
}
async function saveVoiceCfg(extra){
  const duck=$('vc-duck')?$('vc-duck').value:'0.2';
  const body={engine:$('vc-engine')?$('vc-engine').value:'openai', voice:$('vc-voice')?$('vc-voice').value:'coral',
    hotkey:$('vc-hotkey')?$('vc-hotkey').value:'f8', instructions:$('vc-inst')?$('vc-inst').value:'',
    duck: duck!=='off', duck_level: duck==='off'?0.2:parseFloat(duck),
    beep: $('vc-beep') ? $('vc-beep').value==='on' : true,
    speak_report: $('vc-speakreport') ? $('vc-speakreport').value==='on' : true,
    report_engine: $('vc-report-engine') ? $('vc-report-engine').value : 'claude'};
  const micEl=$('vc-mic');
  if(micEl){
    const idx = micEl.value!=='' ? parseInt(micEl.value) : null;
    body.mic_index = idx;
    body.mic_name = (idx===null || micEl.selectedIndex<0) ? '' : micEl.options[micEl.selectedIndex].text;
  }
  if(extra) Object.assign(body, extra);
  try{ await fetch('/voice/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); }catch(e){}
  loadVoiceConfig();
}
if($('vk-save')) $('vk-save').addEventListener('click', async ()=>{ const k=VKEY.value.trim(); if(!k) return; VSTATUS.textContent='salvando...'; await saveVoiceCfg({key:k}); VKEY.value=''; });
if($('vk-clear')) $('vk-clear').addEventListener('click', async ()=>{ await saveVoiceCfg({clear:true}); if(VKEY) VKEY.value=''; });
if($('vc-save')) $('vc-save').addEventListener('click', ()=>saveVoiceCfg());
if($('vc-test')) $('vc-test').addEventListener('click', testVoice);
async function testVoice(){
  if(voiceCfg && !voiceCfg.configured){ showView('settings'); if(VSTATUS) VSTATUS.textContent='configure a chave primeiro'; return; }
  try{ await fetch('/voice/test',{method:'POST'}); }catch(e){}
}
async function triggerListen(){
  if(voiceCfg && !voiceCfg.configured){ showView('settings'); if(VSTATUS) VSTATUS.textContent='configure a chave primeiro'; return; }
  try{ await fetch('/voice/listen',{method:'POST'}); }catch(e){}
}
if(VBTN) VBTN.addEventListener('click', triggerListen);

// ---------- medidor do microfone (Settings): fale e veja a barra subir ----------
let micTestTimer=null, micPolling=false, micTestStart=0;
function stopMicTest(){
  if(micTestTimer){ clearInterval(micTestTimer); micTestTimer=null; }
  const m=$('vc-meter'), b=$('vc-meter-bar'), t=$('vc-mictest');
  if(m) m.classList.remove('live'); if(b) b.style.width='0%';
  if(t) t.textContent='🎙️ Testar (fale e veja a barra)';
}
if($('vc-mictest')) $('vc-mictest').addEventListener('click', ()=>{
  if(micTestTimer){ stopMicTest(); if($('vc-meter-txt')) $('vc-meter-txt').textContent=''; return; }
  const dev = $('vc-mic') ? $('vc-mic').value : '';
  $('vc-mictest').textContent='⏹️ Parar';
  $('vc-meter').classList.add('live');
  micTestStart=tnow();
  micTestTimer=setInterval(async ()=>{
    if(tnow()-micTestStart>15000){ stopMicTest(); if($('vc-meter-txt')) $('vc-meter-txt').textContent='(teste encerrou)'; return; }
    if(micPolling) return; micPolling=true;
    try{
      const q = dev!=='' ? ('?device='+encodeURIComponent(dev)) : '';
      const r = await (await fetch('/voice/miclevel'+q)).json();
      const lvl = r.level;
      if(lvl<0){ $('vc-meter-bar').style.width='0%'; $('vc-meter-txt').textContent='✕ esse mic não funciona — escolha outro'; }
      else { $('vc-meter-bar').style.width=Math.min(100,lvl)+'%'; $('vc-meter-txt').textContent = lvl>8 ? ('✓ captando ('+lvl+')') : 'fale algo... ('+lvl+')'; }
    }catch(e){}
    micPolling=false;
  }, 180);
});
function tnow(){ return (window.performance && performance.now) ? performance.now() : (+new Date()); }
// sair do Settings para o teste do mic
$$('.nav-item').forEach(n=>n.addEventListener('click', ()=>{ if(n.dataset.view!=='settings') stopMicTest(); }));

const VOICE_LBL={ouvindo:'Ouvindo…', transcrevendo:'Transcrevendo…', pensando:'Pensando…', falando:'Falando…', erro:'Erro', idle:'Falar'};
let voiceLastAt=0, prevVoiceStatus='idle';
async function pollVoice(){
  try{
    const s=await (await fetch('/voice/state')).json();
    const st=s.status||'idle';
    // ao COMECAR a falar (atalho/botao), abre o Strategy pra ver o texto + resposta lá
    if(st==='ouvindo' && prevVoiceStatus!=='ouvindo') showView('strategy');
    prevVoiceStatus=st;
    if(VBTN){
      $('voicebtn-lbl').textContent=VOICE_LBL[st]||'Falar';
      VBTN.classList.toggle('rec', st==='ouvindo');
      VBTN.classList.toggle('busy', ['transcrevendo','pensando','falando'].includes(st));
    }
    if(VACC) VACC.textContent = s.error ? ('erro: '+s.error) : (st==='idle' ? 'pronto' : st);
    // a fala transcrita e a resposta aparecem no chat do Strategy (mesma conversa do teclado)
    if(s.at && s.at!==voiceLastAt){ voiceLastAt=s.at; if(s.reply || s.transcript) loadHistory(); }
  }catch(e){}
}
setInterval(pollVoice, 1000); pollVoice();
loadVoiceConfig();

// ---------- overlay do minimapa: TTL do fantasma (editavel aqui) ----------
async function loadOverlayCfg(){
  try{
    const c=await (await fetch('/overlay/config')).json();
    const sel=$('ov-ttl');
    if(sel){ const v=String(Math.round(Number(c.ghost_ttl)||0));
      if([...sel.options].some(o=>o.value===v)) sel.value=v; }
    if($('ov-portrait')) $('ov-portrait').value = c.portrait ? '1' : '0';
  }catch(e){}
}
async function saveOverlayCfg(body){
  try{ await fetch('/overlay/config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)}); }catch(e){}
}
if($('ov-ttl')) $('ov-ttl').addEventListener('change', ()=>saveOverlayCfg({ghost_ttl:Number($('ov-ttl').value)}));
if($('ov-portrait')) $('ov-portrait').addEventListener('change', ()=>saveOverlayCfg({portrait: $('ov-portrait').value==='1'}));
loadOverlayCfg();

// ---------- atalhos no jogo (persistidos) ----------
function _setSel(id,v){ const el=$(id); if(el && v && [...el.options].some(o=>o.value===v)) el.value=v; }
async function loadAppCfg(){
  try{
    const c=await (await fetch('/app/config')).json();
    _setSel('hk-scan', c.scan_hotkey); _setSel('hksel', c.scan_hotkey);
    _setSel('hk-items', c.items_hotkey); _setSel('ir-hk', c.items_hotkey);
  }catch(e){}
}
async function setHotkey(kind, key){
  const url = kind==='scan' ? '/scoreboard/hotkey' : '/items/hotkey';
  try{ await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})}); }catch(e){}
}
if($('hk-scan')) $('hk-scan').addEventListener('change', ()=>{ const v=$('hk-scan').value; _setSel('hksel',v); setHotkey('scan',v); });
if($('hk-items')) $('hk-items').addEventListener('change', ()=>{ const v=$('hk-items').value; _setSel('ir-hk',v); setHotkey('items',v); });
loadAppCfg();

// ---------- cérebro de IA: provedor + chaves de API ----------
function _hasKeyChip(prov, has){
  const el=$('ai-has-'+prov); if(!el) return;
  el.className='chip '+(has?'go':''); el.textContent=has?'✓ configurada':'sem chave';
}
async function loadAiConfig(){
  try{
    const c=await (await fetch('/ai/config')).json();
    const sel=$('ai-provider');
    if(sel && !sel.options.length && c.providers)
      sel.innerHTML=c.providers.map(p=>`<option value="${p[0]}">${esc(p[1])}</option>`).join('');
    if(sel) sel.value=c.provider||'auto';
    for(const k of ['anthropic','openai','gemini']) _hasKeyChip(k, (c.has_key||{})[k]);
  }catch(e){}
}
async function postAiConfig(body){
  try{
    const c=await (await fetch('/ai/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
    const sel=$('ai-provider'); if(sel) sel.value=c.provider||'auto';
    for(const k of ['anthropic','openai','gemini']) _hasKeyChip(k, (c.has_key||{})[k]);
    if(typeof pollAiHealth==='function') pollAiHealth(true);  // re-checa o cérebro na hora
  }catch(e){}
}
if($('ai-provider')) $('ai-provider').addEventListener('change', ()=>postAiConfig({provider:$('ai-provider').value}));
if($('ai-save')) $('ai-save').addEventListener('click', ()=>{
  const keys={};
  for(const k of ['anthropic','openai','gemini']){ const v=$('ai-key-'+k).value.trim(); if(v) keys[k]=v; }
  postAiConfig({provider:$('ai-provider').value, keys});
  for(const k of ['anthropic','openai','gemini']) $('ai-key-'+k).value='';  // não deixa a chave na tela
});
$$('[data-clear]').forEach(b=>b.addEventListener('click', ()=>{
  const k=b.dataset.clear; postAiConfig({keys:{[k]:null}}); $('ai-key-'+k).value='';
}));
loadAiConfig();

// ---------- versão instalada + aviso de atualização ----------
async function loadVersion(){
  try{
    const v=await (await fetch('/version')).json();
    if($('set-version')) $('set-version').textContent = v.version==='dev' ? 'dev (repositório)' : 'v'+v.version;
    if(v.update_available && $('update-note')){
      $('update-latest').textContent='v'+v.latest;
      $('update-link').href=v.update_url||'#';
      $('update-note').style.display='block';
    }
  }catch(e){}
}
loadVersion(); setInterval(loadVersion, 60*60*1000);

// ---------- limpar contexto (novo jogo) / desligar aplicação ----------
$('ctxbtn').addEventListener('click', async ()=>{
  if(!confirm('Limpar o contexto da partida?\\n\\nIsso apaga a conversa, o draft, o placar lido e os relatórios para começar um jogo novo do zero. O servidor continua ligado.')) return;
  const btn=$('ctxbtn'); btn.disabled=true;
  try{ await fetch('/context/clear',{method:'POST'}); }catch(e){}
  // limpa o que está na tela agora (os pollers re-populam sozinhos na próxima partida)
  DSTATE={enemy:[],allies:[],bans:[]}; DSUGG=[];
  if(typeof paintDraft==='function') paintDraft();
  S={};
  const tm=$('teams'); if(tm) tm.innerHTML='<span class="empty">escaneie o placar para listar os times.</span>';
  const rp=$('report'); if(rp){ rp.className='report empty2'; rp.textContent='escaneie o placar (Tab + tecla) para o agente analisar a partida.'; }
  if(typeof loadHistory==='function') loadHistory();
  if(typeof draftRefresh==='function') draftRefresh();
  btn.disabled=false;
});

$('killbtn').addEventListener('click', async ()=>{
  if(!confirm('Desligar a aplicação?\\n\\nO servidor do copiloto será encerrado por completo. Para usar de novo, abra o iniciar.bat.')) return;
  try{ await fetch('/shutdown',{method:'POST'}); }catch(e){}
  $('killscreen').classList.add('on');   // o servidor cai logo após responder
});
</script>
</body>
</html>
"""


MINIMAP_HTML = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Minimapa ao vivo - Copiloto Dota 2</title>
<style>
  :root{--bg:#05070b;--line:#212a39;--gold:#c8aa6e;--gold-hi:#f1d191;--tx:#e9eef6;
        --tx2:#93a0b4;--tx3:#586272;--ok:#48c569;--red:#e85a45;--r:6px;color-scheme:dark}
  *{box-sizing:border-box}
  html,body{height:100%;margin:0}
  body{background:var(--bg);color:var(--tx);font-family:Rajdhani,system-ui,'Segoe UI',sans-serif;
       overflow:hidden;display:flex;flex-direction:column}
  .bar{flex:none;display:flex;align-items:center;gap:12px;padding:8px 12px;
       background:linear-gradient(180deg,#11161f,#0b0f17);border-bottom:1px solid var(--line)}
  .bar .t{font-weight:700;letter-spacing:1.5px;text-transform:uppercase;font-size:12px;color:var(--gold)}
  .bar .live{display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:700;letter-spacing:1px;color:var(--tx2)}
  .bar .live i{width:8px;height:8px;border-radius:50%;background:#555;display:inline-block}
  .bar .live.on i{background:var(--ok);box-shadow:0 0 8px var(--ok);animation:pulse 1.6s infinite}
  @keyframes pulse{50%{opacity:.4}}
  .bar .clock{font-weight:700;font-size:15px;color:var(--gold-hi);min-width:54px;text-align:center}
  .bar .sp{flex:1}
  .gbtn{background:#141b27;border:1px solid #2b3647;color:var(--tx);border-radius:var(--r);
        padding:6px 11px;font-size:14px;cursor:pointer;font-family:inherit}
  .gbtn:hover{border-color:#3f4f68}
  .stage{flex:1;position:relative;display:grid;place-items:center;min-height:0;padding:10px}
  .wrap{position:relative;width:min(96vmin,96vh);max-width:100%;max-height:100%;aspect-ratio:1/1}
  #map{width:100%;height:100%;object-fit:contain;display:block;border-radius:var(--r);
       border:1px solid var(--line);background:#05070b;box-shadow:0 0 40px rgba(0,0,0,.6)}
  .hint{position:absolute;inset:0;display:grid;place-items:center;text-align:center;gap:6px;pointer-events:none}
  .hint b{font-size:16px;letter-spacing:2px;color:var(--tx2)}
  .hint span{font-size:12px;color:var(--tx3)}
  body.live .hint{display:none}
  /* painel de calibracao */
  .cal{position:absolute;top:10px;right:10px;width:230px;background:rgba(11,16,26,.96);
       border:1px solid var(--line);border-radius:var(--r);padding:12px;display:none;font-size:12px}
  .cal.open{display:block}
  .cal h4{margin:0 0 8px;font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--gold)}
  .cal .row{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:6px}
  .cal label{display:flex;flex-direction:column;gap:2px;color:var(--tx3);font-size:10px;text-transform:uppercase}
  .cal input{background:#0a0e15;border:1px solid #2b3647;color:var(--tx);border-radius:4px;padding:5px;font-size:12px;font-family:inherit}
  .pad{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin:8px 0}
  .pad button,.zoom button{background:#141b27;border:1px solid #2b3647;color:var(--tx);border-radius:4px;
                           padding:7px;cursor:pointer;font-size:13px;font-family:inherit}
  .pad button:hover,.zoom button:hover{border-color:#3f4f68}
  .pad .e{visibility:hidden}
  .zoom{display:grid;grid-template-columns:1fr 1fr;gap:5px}
  .cal .note{color:var(--tx3);font-size:10.5px;line-height:1.4;margin-top:8px}
</style>
</head>
<body>
  <div class="bar">
    <span class="t">Minimapa</span>
    <span class="live" id="live"><i></i> <span id="livetxt">aguardando</span></span>
    <span class="sp"></span>
    <span class="clock" id="clock">--:--</span>
    <button class="gbtn" id="reload" title="Recarregar">↻</button>
    <button class="gbtn" id="gear" title="Ajustar recorte">⚙</button>
  </div>
  <div class="stage">
    <div class="wrap">
      <img id="map" alt="minimapa ao vivo">
      <div class="hint"><b>MINIMAPA</b><span>abra o Dota e entre numa partida</span></div>
      <div class="cal" id="cal">
        <h4>Ajustar recorte (px)</h4>
        <div class="row">
          <label>Esquerda<input type="number" id="i-left"></label>
          <label>Topo<input type="number" id="i-top"></label>
          <label>Direita<input type="number" id="i-right"></label>
          <label>Baixo<input type="number" id="i-bottom"></label>
        </div>
        <div class="pad">
          <span class="e"></span><button data-mv="0,-4">▲</button><span class="e"></span>
          <button data-mv="-4,0">◀</button><button data-mv="0,4">▼</button><button data-mv="4,0">▶</button>
        </div>
        <div class="zoom">
          <button data-zoom="-4">− menor</button><button data-zoom="4">+ maior</button>
        </div>
        <div class="note">Mova/ajuste até o quadro mostrar só o minimapa. Salva automático. Setas do teclado também movem.</div>
      </div>
    </div>
  </div>
<script>
const $=id=>document.getElementById(id);
const map=$('map');

// ---- stream com auto-reconexao ----
function startStream(){ map.src='/minimap/stream?t='+Date.now(); }
map.onerror=()=>{ document.body.classList.remove('live'); setTimeout(startStream,1200); };
$('reload').onclick=startStream;
startStream();

// ---- status (clock + ao vivo) ----
async function tick(){
  try{
    const d=await (await fetch('/state')).json();
    const age=d.seconds_since_update, fresh=age!==null&&age<8;
    const s=d.summary||{};
    $('clock').textContent=s.clock||'--:--';
    $('live').classList.toggle('on',fresh);
    $('livetxt').textContent=fresh?'ao vivo':(age===null?'offline':'parado');
    document.body.classList.toggle('live',fresh);
  }catch(e){ $('live').classList.remove('on'); $('livetxt').textContent='offline'; }
}
setInterval(tick,1000); tick();

// ---- calibracao do recorte ----
let box={left:0,top:0,right:0,bottom:0};
const ins={left:$('i-left'),top:$('i-top'),right:$('i-right'),bottom:$('i-bottom')};
function fill(){ for(const k in ins) ins[k].value=box[k]; }
async function loadBox(){ try{ box=await (await fetch('/minimap/box')).json(); fill(); }catch(e){} }
async function saveBox(){
  try{
    const r=await (await fetch('/minimap/box',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(box)})).json();
    if(r&&r.left!==undefined){ box={left:r.left,top:r.top,right:r.right,bottom:r.bottom}; fill(); }
  }catch(e){}
}
function move(dx,dy){ box.left+=dx;box.right+=dx;box.top+=dy;box.bottom+=dy; fill(); saveBox(); }
function zoom(d){ box.left-=d;box.top-=d;box.right+=d;box.bottom+=d; fill(); saveBox(); }
for(const k in ins) ins[k].addEventListener('change',()=>{ box[k]=parseInt(ins[k].value)||0; saveBox(); });
document.querySelectorAll('.pad button[data-mv]').forEach(b=>b.onclick=()=>{
  const [dx,dy]=b.dataset.mv.split(',').map(Number); move(dx,dy); });
document.querySelectorAll('.zoom button[data-zoom]').forEach(b=>b.onclick=()=>zoom(Number(b.dataset.zoom)));
$('gear').onclick=()=>{ $('cal').classList.toggle('open'); };
window.addEventListener('keydown',e=>{
  if(!$('cal').classList.contains('open')) return;
  if(e.target.tagName==='INPUT') return;
  const m={ArrowUp:[0,-4],ArrowDown:[0,4],ArrowLeft:[-4,0],ArrowRight:[4,0]};
  if(m[e.key]){ e.preventDefault(); move(...m[e.key]); }
  else if(e.key==='+'||e.key==='='){ zoom(4); } else if(e.key==='-'){ zoom(-4); }
});
loadBox();
</script>
</body>
</html>
"""
