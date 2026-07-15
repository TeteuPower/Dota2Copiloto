"""
Baixa e cacheia os dados de herois e counters da OpenDota.
============================================================

Roda 1x (ou quando sair um patch novo). Gera 3 arquivos em cache/:
  - heroes.json   : lista canonica enriquecida (id, name, class, localized_name,
                    primary_attr, attack_type, roles, img_url, winrate_global)
  - matchups.json : matriz de counters {id: {enemy_id: {games, wins}}}
  - meta.json     : {downloaded_at, source, n_heroes}

Em partida o app NAO faz chamadas de rede: le esses caches -> sugestoes instantaneas.
Usa so a biblioteca padrao (urllib). Free tier da OpenDota: 60 req/min, 50k/mes.
"""

import json
import os
import time
import urllib.request

API = "https://api.opendota.com/api"
CDN = "https://cdn.cloudflare.steamstatic.com"
# cache/ fica na RAIZ do repo (este script mora em scripts/)
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
UA = "CopilotoDota2/0.1 (local tool)"  # Cloudflare bloqueia sem User-Agent


def get(path):
    """GET JSON da OpenDota com retry simples."""
    url = API + path
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if attempt == 2:
                raise
            print(f"  retry {path} ({e})")
            time.sleep(2)


def hero_class(npc_name):
    return (npc_name or "").replace("npc_dota_hero_", "")


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)

    print("[1/3] Baixando /heroStats (metadados + winrate global)...")
    stats = get("/heroStats")
    print(f"      {len(stats)} herois.")

    heroes = []
    for h in stats:
        pick = h.get("pub_pick") or 0
        win = h.get("pub_win") or 0
        wr = round(win / pick, 4) if pick else 0.5
        cls = hero_class(h.get("name"))
        img = h.get("img") or f"/apps/dota2/images/dota_react/heroes/{cls}.png"
        heroes.append({
            "id": h["id"],
            "name": h.get("name"),
            "class": cls,
            "localized_name": h.get("localized_name"),
            "primary_attr": h.get("primary_attr"),
            "attack_type": h.get("attack_type"),
            "roles": h.get("roles") or [],
            "img_url": CDN + img,
            "winrate_global": wr,
        })

    with open(os.path.join(CACHE_DIR, "heroes.json"), "w", encoding="utf-8") as f:
        json.dump(heroes, f, ensure_ascii=False, indent=1)
    print(f"      heroes.json salvo ({len(heroes)} herois).")

    print(f"[2/3] Baixando matchups de {len(heroes)} herois (~{len(heroes)}s, respeitando 60/min)...")
    matchups = {}
    for i, h in enumerate(heroes, 1):
        hid = h["id"]
        try:
            rows = get(f"/heroes/{hid}/matchups")
            matchups[str(hid)] = {
                str(r["hero_id"]): {"games": r.get("games_played", 0), "wins": r.get("wins", 0)}
                for r in rows if r.get("games_played")
            }
        except Exception as e:
            print(f"      FALHOU hero {hid} ({h['localized_name']}): {e}")
            matchups[str(hid)] = {}
        if i % 10 == 0 or i == len(heroes):
            print(f"      {i}/{len(heroes)}...")
        time.sleep(1.1)  # 60/min com folga

    with open(os.path.join(CACHE_DIR, "matchups.json"), "w", encoding="utf-8") as f:
        json.dump(matchups, f, ensure_ascii=False)
    print("      matchups.json salvo.")

    print("[3/3] Salvando meta.json...")
    meta = {
        "downloaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "opendota",
        "n_heroes": len(heroes),
    }
    with open(os.path.join(CACHE_DIR, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    print("\nOK! Cache pronto em", CACHE_DIR)


if __name__ == "__main__":
    main()
