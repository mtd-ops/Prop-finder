"""Auto-tracking for the Model tab.

Runs in the GitHub refresh after the stats and lines are built: opens the built site in headless Chromium with
last run's tracking loaded, lets the app save its value picks and Auto slips for upcoming games and grade
finished ones (same code as the page), then writes site/tracking.json for every phone and browser to read.
Usage: python3 track.py site
"""
import json, os, subprocess, sys, time
from playwright.sync_api import sync_playwright

site = sys.argv[1] if len(sys.argv) > 1 else "site"
path = os.path.join(site, "tracking.json")
try:
    prior = json.load(open(path))
except Exception:
    prior = {}
srv = subprocess.Popen([sys.executable, "-m", "http.server", "8765"], cwd=site,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.5)
try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        # keep the page from reading tracking.json itself; this script is the one writing it
        pg.route("**/tracking.json*", lambda r: r.fulfill(status=404, body=""))
        pg.goto("http://localhost:8765/index.html")
        pg.evaluate("""(t)=>{localStorage.clear();localStorage.setItem('pf_picks',JSON.stringify(t.picks||{}));
                      localStorage.setItem('pf_slips',JSON.stringify(t.slips||{}))}""", prior)
        pg.reload()
        pg.wait_for_timeout(12000)  # stats, lines, injuries, closing prices load; picks get saved and graded
        out = pg.evaluate("({picks:JSON.parse(localStorage.getItem('pf_picks')||'{}'),slips:JSON.parse(localStorage.getItem('pf_slips')||'{}')})")
        b.close()
    # keep about four months
    cut = (time.time() - 120 * 86400) * 1000
    out["picks"] = {k: v for k, v in out["picks"].items() if v.get("at", 0) >= cut}
    out["slips"] = {k: v for k, v in out["slips"].items() if v.get("at", 0) >= cut}
    out["updated"] = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())
    json.dump(out, open(path, "w"), separators=(",", ":"))
    graded = sum(1 for v in out["picks"].values() if v.get("r")) + sum(1 for v in out["slips"].values() if v.get("r"))
    print(f"tracking: {len(out['picks'])} picks, {len(out['slips'])} slips, {graded} graded; page errors: {errs or 'none'}")
finally:
    srv.terminate()
