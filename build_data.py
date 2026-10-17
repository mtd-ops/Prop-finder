"""Build data.json for the Prop Finder web app from nflverse weekly player stats.
Usage: python3 build_data.py [out_dir]   (downloads 2025 + current season)"""
import csv, io, json, sys, urllib.request, datetime

SEASONS = [2025, 2026]
URLS = ["https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{s}.csv",
        "https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats_{s}.csv"]
COLS = [("comp", ["completions"]), ("att", ["attempts", "passing_attempts"]), ("pyd", ["passing_yards"]),
        ("ptd", ["passing_tds"]), ("int", ["passing_interceptions", "interceptions"]), ("sk", ["sacks_suffered", "sacks"]),
        ("car", ["carries", "rushing_attempts"]), ("ryd", ["rushing_yards"]), ("rtd", ["rushing_tds"]),
        ("tgt", ["targets"]), ("rec", ["receptions"]), ("cyd", ["receiving_yards"]), ("ctd", ["receiving_tds"]),
        ("air", ["receiving_air_yards"])]
POS = {"QB", "RB", "WR", "TE", "FB"}

def num(r, cands):
    for c in cands:
        v = r.get(c)
        if v not in (None, ""):
            try:
                f = float(v)
                return int(f) if f == int(f) else round(f, 1)
            except ValueError:
                return 0
    return 0

def fetch(season):
    for u in URLS:
        try:
            with urllib.request.urlopen(u.format(s=season), timeout=120) as resp:
                return resp.read().decode("utf-8")
        except Exception as e:
            err = e
    print(f"warning: season {season} unavailable ({err})", file=sys.stderr)
    return ""

def main(out_dir="."):
    players, games = {}, []
    for s in SEASONS:
        text = fetch(s)
        for r in csv.DictReader(io.StringIO(text)):
            r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
            if (r.get("season_type") or "REG").upper() != "REG":
                continue
            team, opp = r.get("team") or r.get("recent_team"), r.get("opponent_team")
            if not team or not opp:
                continue
            pos = r.get("position", "")
            row = [int(float(r["season"])), int(float(r["week"])), team, opp]
            row += [num(r, c) for _, c in COLS]
            games.append(row[:4])  # schedule info, deduped below
            if pos not in POS:
                continue
            pid = r.get("player_id") or r.get("player_display_name")
            p = players.setdefault(pid, {"n": r.get("player_display_name") or r.get("player_name"), "p": pos, "g": []})
            p["p"] = pos
            p["g"].append(row)
    sched = sorted({tuple(g) for g in games})
    plist = []
    for p in players.values():
        p["g"].sort(key=lambda g: (g[0], g[1]))
        if any(any(x for x in g[4:]) for g in p["g"]):
            plist.append(p)
    plist.sort(key=lambda p: p["n"])
    if not plist:
        sys.exit("error: no player stats downloaded; not writing data.json")
    latest = max(((g[0], g[1]) for g in sched), default=(0, 0))
    data = {"updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "latest": {"season": latest[0], "week": latest[1]},
            "cols": ["season", "week", "team", "opp"] + [k for k, _ in COLS],
            "sched": [list(g) for g in sched], "players": plist}
    path = f"{out_dir}/data.json"
    with open(path, "w") as fh:
        json.dump(data, fh, separators=(",", ":"))
    print(f"wrote {path}: {len(plist)} players, latest {latest}")
    try:
        build_injuries(out_dir, plist, latest[0] or SEASONS[-1])
    except Exception as e:
        print(f"warning: injuries.json not written ({e})", file=sys.stderr)

# ---------- injuries: official practice/game reports + snap shares, for the Injuries tab
REL = "https://github.com/nflverse/nflverse-data/releases/download/"
GROUP = {"QB": "QB", "RB": "RB", "FB": "RB", "WR": "WR", "TE": "TE", "T": "OL", "G": "OL", "C": "OL", "OL": "OL",
         "DE": "DL", "DT": "DL", "NT": "DL", "DL": "DL", "LB": "LB", "ILB": "LB", "OLB": "LB", "MLB": "LB",
         "CB": "DB", "S": "DB", "SS": "DB", "FS": "DB", "DB": "DB", "K": "ST", "P": "ST", "LS": "ST"}
GW = {"Out": 1.0, "Doubtful": .9, "Questionable": .55}
PW = {"DNP": .6, "Limited": .35, "Full": .05}

def get_csv(url):
    with urllib.request.urlopen(url, timeout=120) as resp:
        return list(csv.DictReader(io.StringIO(resp.read().decode("utf-8"))))

def norm(n):
    n = (n or "").lower().replace(".", "").replace("'", "").replace("-", " ")
    parts = [p for p in n.split() if p not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return " ".join(parts)

def prac(s):
    s = (s or "").lower()
    return "DNP" if s.startswith("did not") else "Limited" if s.startswith("limited") else "Full" if s.startswith("full") else ""

def build_injuries(out_dir, plist, season):
    inj = [r for r in get_csv(REL + f"injuries/injuries_{season}.csv") if (r.get("game_type") or "REG") == "REG"]
    snaps = [r for r in get_csv(REL + f"snap_counts/snap_counts_{season}.csv") if (r.get("game_type") or "REG") == "REG"]
    sched = [r for r in get_csv(REL + "schedules/games.csv") if r["season"] == str(season) and r["game_type"] == "REG"]
    unplayed = [int(r["week"]) for r in sched if r["away_score"] in ("", "NA")]
    week = min(unplayed) if unplayed else max(int(r["week"]) for r in inj)
    games = [{"a": r["away_team"], "h": r["home_team"], "d": r["gameday"], "t": r["gametime"], "wd": r["weekday"][:3],
              "sp": float(r["spread_line"]) if r["spread_line"] not in ("", "NA") else None,
              "tot": float(r["total_line"]) if r["total_line"] not in ("", "NA") else None}
             for r in sched if int(r["week"]) == week]
    opp = {}
    for g in games:
        opp[g["a"]], opp[g["h"]] = g["h"], g["a"]
    # snap share per player (name+team), most recent games first
    sn = {}
    for r in snaps:
        k = (norm(r["player"]), r["team"])
        o, d = float(r["offense_pct"] or 0), float(r["defense_pct"] or 0)
        sn.setdefault(k, []).append((int(r["week"]), max(o, d), "O" if o >= d else "D", r["position"]))
    for v in sn.values():
        v.sort(reverse=True)
    st = {}
    for p in plist:
        cur = [g for g in p["g"] if g[0] == season]
        if cur:
            st[(norm(p["n"]), cur[-1][2])] = cur
    tw = {}
    for r in snaps:
        tw.setdefault(r["team"], set()).add(int(r["week"]))
    hist = {}
    for r in inj:
        k = (r.get("gsis_id") or r["full_name"], r["team"])
        hist.setdefault(k, []).append(r)
    out = []
    for k, rows in hist.items():
        rows.sort(key=lambda r: int(r["week"]))
        last = rows[-1]
        team, name = last["team"], last["full_name"]
        pos = last["position"]
        grp = GROUP.get(pos, pos)
        key = (norm(name), team)
        if key not in sn:
            nn = norm(name).split()
            alt = [kk for kk in sn if kk[1] == team and kk[0].split()[-1:] == nn[-1:] and kk[0][:1] == norm(name)[:1]]
            if len(alt) == 1:
                key = alt[0]
        sv = sn.get(key, [])
        bywk = {x[0]: x[1] for x in sv}
        team_weeks = sorted(tw.get(team, set()))
        outwk = {int(r["week"]) for r in rows if r["report_status"] in ("Out", "Doubtful")}
        # current role: last 3 team games, counting a DNP as 0 unless he was listed out that week
        inc = [bywk.get(w, 0) for w in team_weeks if w in bywk or w not in outwk][-3:]
        if not any(inc) and sv:
            inc = [x[1] for x in sv[:3]]
        share = round(sum(inc) / len(inc), 2) if inc else 0
        side = (sv[0][2] if sv else ("O" if grp in ("QB", "RB", "WR", "TE", "OL") else "D" if grp in ("DL", "LB", "DB") else "S"))
        missed = [w for w in team_weeks if w not in bywk]
        cur_wk = int(last["week"]) == week
        gs = last["report_status"] if cur_wk else ""
        ps = prac(last["practice_status"]) if cur_wk else ""
        injury = last["report_primary_injury"] or last["practice_primary_injury"] or ""
        if injury.lower().startswith("not injury"):
            injury = "Rest / not injury"
        w = GW.get(gs, PW.get(ps, 0)) if cur_wk else 0
        if injury == "Rest / not injury":
            w = min(w, .1)
        mult = 1.6 if grp == "QB" else 1.25 if grp in ("RB", "WR", "TE") else 1
        e = {"n": name, "t": team, "pos": pos, "grp": grp, "side": side, "inj": injury, "gs": gs, "ps": ps,
             "cur": int(cur_wk), "lw": int(last["week"]), "snap": share, "miss": missed[-4:],
             "sig": round(w * share * mult, 3),
             "h": [[int(r["week"]), r["report_status"], prac(r["practice_status"]),
                    r["report_primary_injury"] or r["practice_primary_injury"] or ""] for r in rows]}
        s = st.get(key)
        if s and grp in ("QB", "RB", "WR", "TE"):
            n = len(s)
            e["st"] = {"g": n, "tgt": round(sum(g[13] for g in s) / n, 1), "car": round(sum(g[10] for g in s) / n, 1),
                       "yds": round(sum(g[6] + g[11] + g[15] for g in s) / n, 1), "td": sum(g[7] + g[12] + g[16] for g in s)}
        out.append(e)
    # next man up: healthy teammates in the same group, by recent snap share
    outset = {(norm(e["n"]), e["t"]) for e in out if e["cur"] and e["gs"] in ("Out", "Doubtful")}
    pool = {}
    for (nm, team), v in sn.items():
        g = GROUP.get(v[0][3], v[0][3])
        if (nm, team) in outset:
            continue
        bw = {x[0]: x[1] for x in v}
        r3 = [bw.get(w, 0) for w in sorted(tw.get(team, set()))][-3:]
        pool.setdefault((team, g), []).append((round(sum(r3) / max(len(r3), 1), 2), nm))
    disp = {}
    for r in snaps:
        disp[(norm(r["player"]), r["team"])] = r["player"]
    for e in out:
        if e["cur"] and e["sig"] >= .2 and e["grp"] in ("QB", "RB", "WR", "TE", "OL", "DL", "LB", "DB"):
            c = sorted([x for x in pool.get((e["t"], e["grp"]), []) if x[1] != norm(e["n"])], reverse=True)
            e["nx"] = [[disp.get((nm, e["t"]), nm), sh] for sh, nm in c[:4]]
    out.sort(key=lambda e: (-e["cur"], -e["sig"], e["n"]))
    data = {"updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "season": season, "week": week, "games": games, "players": out}
    path = f"{out_dir}/injuries.json"
    with open(path, "w") as fh:
        json.dump(data, fh, separators=(",", ":"))
    print(f"wrote {path}: week {week}, {sum(e['cur'] for e in out)} on this week's report")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
