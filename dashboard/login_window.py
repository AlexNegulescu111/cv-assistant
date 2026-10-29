"""Opens a career site in a visible window with the workspace browser profile, so the user signs in once by hand
and later `apply` runs reuse that session. Nothing is typed or clicked here: the user does it all.

    python -I login_window.py --profile <browser profile dir> <url> [--wait-minutes 30]
Ends when the user closes the window (or after the wait).
"""
import argparse
import sys
import time
from urllib.parse import urlparse

BLOCKED_DOMAINS = ("linkedin.com", "lnkd.in")   # same rule as jobpilot.page: the user's LinkedIn stays out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--wait-minutes", type=int, default=30)
    ap.add_argument("url")
    a = ap.parse_args(argv)
    host = (urlparse(a.url).hostname or "").lower()
    if urlparse(a.url).scheme not in ("http", "https") or not host:
        sys.exit("REFUSED: not a web address")
    if any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS):
        sys.exit("REFUSED: LinkedIn is never opened")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx, errors = None, []
        for channel in ("msedge", "chrome", None):   # as jobpilot.page: a dedicated profile, never the everyday one
            try:
                ctx = pw.chromium.launch_persistent_context(a.profile, channel=channel, headless=False,
                                                            viewport={"width": 1280, "height": 900})
                break
            except Exception as e:
                errors.append(f"{channel or 'chromium'}: {str(e).splitlines()[0][:100]}")
        if ctx is None:
            sys.exit("could not start a browser: " + " | ".join(errors))
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(a.url, wait_until="domcontentloaded", timeout=45000)
        print(f"OPEN {host} - sign in in the window, then close it", flush=True)
        closed = []
        ctx.on("close", lambda _: closed.append(1))
        deadline = time.time() + a.wait_minutes * 60
        while not closed and time.time() < deadline and ctx.pages:
            try:
                ctx.pages[0].wait_for_timeout(1000)   # keeps Playwright's events flowing while the user works
            except Exception:  # the page (or the whole window) was closed meanwhile
                break
        if not closed:
            try:
                ctx.close()
            except Exception:
                pass
        print(f"CLOSED {host} - the session stays in the browser profile", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
