"""Download DraftKings + FanDuel NFL player prop lines from The Odds API into odds.json.

Usage: ODDS_API_KEY=... python3 build_odds.py [out_dir] [--force]

Credit-aware for the free plan (500 credits/month):
- the events list is free; each game costs (markets returned) x 1 region
- each game is pulled once on game day (it starts within WINDOW_H hours), so lines are fresh
- a game already pulled is pulled again within PREKICK_H hours of kickoff, at most once every
  REFETCH_H hours, and only if the credits left still cover first pulls for the rest of the month
  (PER_WEEK games a week); otherwise the earlier lines stay (unless --force)
- stops before the remaining credits would drop below RESERVE
Previous lines are kept, so a skipped or failed run never blanks the app.
"""
import datetime as dt, json, os, sys, urllib.parse, urllib.request

BASE = os.environ.get("ODDS_API_BASE", "https://api.the-odds-api.com/v4")
SPORT = "americanfootball_nfl"
BOOKS = ["draftkings", "fanduel"]
MARKETS = ["player_reception_yds", "player_receptions", "player_rush_yds", "player_pass_yds",
           "player_pass_attempts", "player_anytime_td", "player_rush_reception_yds"]
WINDOW_H, PREKICK_H, REFETCH_H, RESERVE, PER_WEEK = 15, 4, 3, 10, 16

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
    fetched = skipped = 0
    cost = len(MARKETS)
    for e in sorted(todo, key=lambda e: e["commence_time"]):
        g = games.get(e["id"])
        if g and g.get("books") and not force:
            soon = parse(e["commence_time"]) <= t0 + dt.timedelta(hours=PREKICK_H)
            stale = parse(g["fetched"]) <= t0 - dt.timedelta(hours=REFETCH_H)
            budget = rem is None or rem - cost >= month_need(t0, cost)
            if not (soon and stale and budget):
                skipped += 1
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
                          "fetched": iso(now()), "books": books}
        fetched += 1
    res = {"updated": iso(now()) if fetched else old.get("updated"), "checked": iso(now()),
           "remaining": rem, "used": used, "books": BOOKS, "markets": MARKETS,
           "games": sorted(games.values(), key=lambda g: g["t"])}
    with open(path, "w") as fh:
        json.dump(res, fh, separators=(",", ":"))
    print(f"odds: fetched {fetched} game(s), skipped {skipped} already fresh; "
          f"{len(res['games'])} game(s) in odds.json; credits left: {rem if rem is not None else 'unknown'}")

if __name__ == "__main__":
    if not os.environ.get("ODDS_API_KEY"):
        sys.exit("ODDS_API_KEY is not set; add it as a repository secret.")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else ".", "--force" in sys.argv)
