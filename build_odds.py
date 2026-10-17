"""Download DraftKings + FanDuel NFL player prop lines from The Odds API into odds.json.

Usage: ODDS_API_KEY=... python3 build_odds.py [out_dir] [--force]

Credit-aware for the free plan (500 credits/month):
- games are ranked first: a game "with a story" (spread of SPREAD_MIN+ or total of TOTAL_MIN+, from the
  free nflverse schedule in injuries.json) or a team listed in watch.txt is pulled first; other games are
  pulled only when the credits left still cover the story games for the rest of the month.
  watch.txt (repo root, one team per line, e.g. JAX) forces a team's game in; -NYG keeps it out
- the events list is free; each game costs (markets returned) x 1 region
- each game is pulled once on game day (it starts within WINDOW_H hours), so lines are fresh
- a game already pulled is pulled again within PREKICK_H hours of kickoff, at most once every
  REFETCH_H hours, and only if the credits left still cover first pulls for the rest of the month
  (PER_WEEK story games a week); otherwise the earlier lines stay (unless --force)
- milestone (X+) ladders come from the alternate markets in a second, separate pull per game, made only
  when the credits left still cover every remaining first pull this month; otherwise the app says no
  milestone price was pulled and you type it in
- stops before the remaining credits would drop below RESERVE
Previous lines are kept, so a skipped or failed run never blanks the app.
"""
import datetime as dt, json, os, sys, urllib.parse, urllib.request

BASE = os.environ.get("ODDS_API_BASE", "https://api.the-odds-api.com/v4")
SPORT = "americanfootball_nfl"
BOOKS = ["draftkings", "fanduel"]
MARKETS = ["player_reception_yds", "player_receptions", "player_rush_yds", "player_pass_yds",
           "player_pass_attempts"]  # 5 credits a game
# milestone ladders: X+ is the over at X-0.5 (80+ = over 79.5)
ALT_MARKETS = ["player_reception_yds_alternate", "player_receptions_alternate", "player_rush_yds_alternate",
               "player_pass_yds_alternate"]  # 4 more credits a game
WINDOW_H, PREKICK_H, REFETCH_H, RESERVE, PER_WEEK = 15, 4, 3, 10, 9
SPREAD_MIN, TOTAL_MIN = 4, 47
TEAM_AB = {"Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF",
           "Carolina Panthers": "CAR", "Chicago Bears": "CHI", "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE",
           "Dallas Cowboys": "DAL", "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
           "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX", "Kansas City Chiefs": "KC",
           "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC", "Los Angeles Rams": "LA", "Miami Dolphins": "MIA",
           "Minnesota Vikings": "MIN", "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
           "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT", "San Francisco 49ers": "SF",
           "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB", "Tennessee Titans": "TEN", "Washington Commanders": "WAS"}

def load_rank(out_dir):
    """(schedule {(away, home): (spread, total)}, forced-in teams, kept-out teams)."""
    sched, want, skip = {}, set(), set()
    try:
        for g in json.load(open(os.path.join(out_dir, "injuries.json"))).get("games", []):
            sched[(g["a"], g["h"])] = (g.get("sp"), g.get("tot"))
    except Exception:
        pass
    wp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watch.txt")
    if os.path.exists(wp):
        for ln in open(wp):
            t = ln.split("#")[0].strip().upper()
            if t.startswith("-"):
                skip.add(t[1:].strip())
            elif t:
                want.add(t)
    return sched, want, skip

def story(e, rank):
    """'watch', 'spread 7', 'total 51.5', 'skip' (kept out by watch.txt) or '' (no clear story)."""
    sched, want, skip = rank
    a, h = TEAM_AB.get(e["away_team"], ""), TEAM_AB.get(e["home_team"], "")
    if a in skip or h in skip:
        return "skip"
    if a in want or h in want:
        return "watch"
    sp, tot = sched.get((a, h), (None, None))
    if not sched:
        return "no schedule"  # can't rank, so treat every game as a story game
    if sp is not None and abs(sp) >= SPREAD_MIN:
        return f"spread {abs(sp):g}"
    if tot is not None and tot >= TOTAL_MIN:
        return f"total {tot:g}"
    return ""

def month_need(t, cost):
    """Credits to keep for the first pull of every game left this month (credits reset monthly)."""
    nxt = (t.replace(day=1) + dt.timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    weeks = max((nxt - t).total_seconds() / 604800, 0)
    return int(weeks * PER_WEEK * cost) + RESERVE

def now():
    return dt.datetime.now(dt.timezone.utc)

def iso(t):
    return t.strftime("%Y-%m-%dT%H:%MZ")

def parse(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))

def get(path, **params):
    q = urllib.parse.urlencode({"apiKey": os.environ["ODDS_API_KEY"], **params})
    with urllib.request.urlopen(f"{BASE}{path}?{q}", timeout=60) as r:
        rem = r.headers.get("x-requests-remaining")
        used = r.headers.get("x-requests-used")
        return json.loads(r.read().decode("utf-8")), (float(rem) if rem else None), (float(used) if used else None)

def pick(cands):
    """Several lines for one player in a standard market: keep the one priced closest to even."""
    def score(c):
        ps = [p for p in (c.get("over"), c.get("under")) if p is not None]
        return min(abs(abs(p) - 110) for p in ps) if ps else 999
    return min(cands, key=score)

def tidy(markets):
    out = {}
    for m in markets:
        key = m.get("key")
        if key not in MARKETS:
            continue
        per = {}
        for o in m.get("outcomes", []):
            player = o.get("description") or ""
            if not player:
                continue
            name = (o.get("name") or "").lower()
            point = o.get("point")
            if key == "player_anytime_td" and point is None:
                point = 0.5
            c = per.setdefault((player, point), {"line": point})
            if name in ("over", "yes"):
                c["over"] = o.get("price")
            elif name in ("under", "no"):
                c["under"] = o.get("price")
        byp = {}
        for (player, _), c in per.items():
            byp.setdefault(player, []).append(c)
        out[key] = {p: pick(cs) for p, cs in byp.items()}
    return out

def tidy_alt(markets):
    """Every rung each book offers: {market: {player: [[point, over, under], ...]}}, sorted by point."""
    out = {}
    for m in markets:
        key = m.get("key")
        if key not in ALT_MARKETS:
            continue
        per = {}
        for o in m.get("outcomes", []):
            player, point = o.get("description") or "", o.get("point")
            if not player or point is None:
                continue
            r = per.setdefault(player, {}).setdefault(point, [point, None, None])
            name = (o.get("name") or "").lower()
            if name in ("over", "yes"):
                r[1] = o.get("price")
            elif name in ("under", "no"):
                r[2] = o.get("price")
        out[key] = {pl: sorted(v.values()) for pl, v in per.items()}
    return out

def main(out_dir=".", force=False):
    path = os.path.join(out_dir, "odds.json")
    old = {}
    if os.path.exists(path):
        try:
            old = json.load(open(path))
        except Exception:
            old = {}
    games = {g["id"]: g for g in old.get("games", [])}
    t0 = now()
    # forget games that started more than 6 hours ago
    games = {k: g for k, g in games.items() if parse(g["t"]) > t0 - dt.timedelta(hours=6)}
    events, rem, used = get(f"/sports/{SPORT}/events")
    todo = [e for e in events if t0 < parse(e["commence_time"]) <= t0 + dt.timedelta(hours=WINDOW_H)]
    rank = load_rank(out_dir)
    why = {e["id"]: story(e, rank) for e in todo}
    # story games first, then the rest in kickoff order
    todo.sort(key=lambda e: (not why[e["id"]], e["commence_time"]))
    passed = []
    fetched = skipped = alts = 0
    cost, alt_cost = len(MARKETS), len(ALT_MARKETS)

    def pull_alt(e, g):
        """Second pull for the milestone ladders, only if this month's first pulls stay covered."""
        nonlocal rem, used, alts
        if rem is not None and rem - alt_cost < month_need(t0, cost):
            return
        try:
            data, r2, u2 = get(f"/sports/{SPORT}/events/{e['id']}/odds", bookmakers=",".join(BOOKS),
                               markets=",".join(ALT_MARKETS), oddsFormat="american")
        except Exception as err:
            print(f"warning: milestones for {e['away_team']} at {e['home_team']} failed ({err})", file=sys.stderr)
            return
        rem, used = (r2 if r2 is not None else rem), (u2 if u2 is not None else used)
        for b in data.get("bookmakers", []):
            if b.get("key") in g["books"]:
                g["books"][b["key"]]["alt"] = tidy_alt(b.get("markets", []))
        g["alt_fetched"] = iso(now())
        alts += 1

    for e in todo:
        g = games.get(e["id"])
        if why[e["id"]] == "skip" and not force:
            passed.append({"home": e["home_team"], "away": e["away_team"], "t": e["commence_time"], "why": "kept out by watch.txt"})
            continue
        if g and g.get("books") and not force:
            soon = parse(e["commence_time"]) <= t0 + dt.timedelta(hours=PREKICK_H)
            stale = parse(g["fetched"]) <= t0 - dt.timedelta(hours=REFETCH_H)
            budget = rem is None or rem - cost >= month_need(t0, cost)
            if not (soon and stale and budget):
                if not g.get("alt_fetched"):
                    pull_alt(e, g)  # lines came earlier but the milestones didn't fit the budget then
                skipped += 1
                continue
        if not why[e["id"]] and not force and not (g and g.get("books")) and rem is not None and rem - cost < month_need(t0, cost):
            passed.append({"home": e["home_team"], "away": e["away_team"], "t": e["commence_time"],
                           "why": "no clear story (close spread, middling total); saving credits for story games"})
            continue
        if rem is not None and rem < cost + RESERVE:
            print(f"stopping: only {rem:.0f} credits left (keeping a reserve of {RESERVE})")
            break
        try:
            data, r2, u2 = get(f"/sports/{SPORT}/events/{e['id']}/odds", bookmakers=",".join(BOOKS),
                               markets=",".join(MARKETS), oddsFormat="american")
        except Exception as err:
            print(f"warning: {e['away_team']} at {e['home_team']} failed ({err})", file=sys.stderr)
            continue
        rem, used = (r2 if r2 is not None else rem), (u2 if u2 is not None else used)
        books = {}
        for b in data.get("bookmakers", []):
            if b.get("key") in BOOKS:
                books[b["key"]] = {"upd": b.get("last_update"), "m": tidy(b.get("markets", []))}
        games[e["id"]] = {"id": e["id"], "home": e["home_team"], "away": e["away_team"], "t": e["commence_time"],
                          "fetched": iso(now()), "books": books, "story": why[e["id"]]}
        pull_alt(e, games[e["id"]])
        fetched += 1
    res = {"updated": iso(now()) if fetched or alts else old.get("updated"), "checked": iso(now()),
           "remaining": rem, "used": used, "books": BOOKS, "markets": MARKETS, "alt_markets": ALT_MARKETS, "passed": passed,
           "games": sorted(games.values(), key=lambda g: g["t"])}
    with open(path, "w") as fh:
        json.dump(res, fh, separators=(",", ":"))
    print(f"odds: fetched {fetched} game(s), milestone ladders for {alts}, skipped {skipped} already fresh, "
          f"passed on {len(passed)} to save credits; "
          f"{len(res['games'])} game(s) in odds.json; credits left: {rem if rem is not None else 'unknown'}")

if __name__ == "__main__":
    if not os.environ.get("ODDS_API_KEY"):
        sys.exit("ODDS_API_KEY is not set; add it as a repository secret.")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else ".", "--force" in sys.argv)
