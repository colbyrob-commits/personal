#!/usr/bin/env python3
"""Find cheap tonight-only hotel deals in coastal North County San Diego.

Searches Booking.com for each town (Carlsbad -> Del Mar) with 2 rooms / 4 adults,
keeps only properties in the target towns, flags ones whose recommended rooms
have two queen beds, and prints them cheapest first.

Setup (once):
    pip install playwright && playwright install chromium

Usage:
    python hotel_deals.py                      # tonight, 2 rooms, 4 adults
    python hotel_deals.py --max-price 300      # total for both rooms
    python hotel_deals.py --queens-only        # only two-queen matches
    python hotel_deals.py --watch 15           # re-check every 15 min, flag new lows
    python hotel_deals.py --show               # watch the browser work
"""
import argparse
import csv
import datetime as dt
import re
import sys
import time
import urllib.parse

TOWNS = ["Carlsbad", "Leucadia", "Encinitas", "Cardiff-by-the-Sea", "Solana Beach", "Del Mar"]
# Booking addresses use these names; Leucadia/Cardiff listings usually say Encinitas.
ALLOWED = re.compile(r"carlsbad|la costa|leucadia|encinitas|cardiff|solana beach|del mar", re.I)
TWO_QUEENS = re.compile(r"(two|2)\s+queen|queen\s+room\s+with\s+two|double\s+queen|2\s*x?\s*queen beds|queen beds", re.I)


def search_url(town, checkin, checkout, rooms, adults):
    q = {
        "ss": f"{town}, California, United States",
        "checkin": checkin.isoformat(),
        "checkout": checkout.isoformat(),
        "group_adults": adults,
        "no_rooms": rooms,
        "group_children": 0,
        "order": "price",
        "selected_currency": "USD",
        "lang": "en-us",
    }
    return "https://www.booking.com/searchresults.html?" + urllib.parse.urlencode(q)


def parse_price(text):
    # Card shows the discounted price last, e.g. "$289 $214" -> 214
    nums = re.findall(r"\$\s?([\d,]+)", text or "")
    return int(nums[-1].replace(",", "")) if nums else None


def parse_cards(cards):
    """cards: list of dicts of raw text pulled from the page."""
    out = []
    for c in cards:
        if not c.get("name") or not ALLOWED.search(c.get("address", "")):
            continue
        price = parse_price(c.get("price"))
        if price is None:
            continue
        units = " ".join((c.get("units") or "").split())
        out.append({
            "name": c["name"].strip(),
            "address": c.get("address", "").strip(),
            "total_price": price,
            "two_queens": bool(TWO_QUEENS.search(units)),
            "rooms": units[:120],
            "rating": (re.findall(r"\d+(?:\.\d)?", c.get("rating") or "") or [""])[0],
            "url": (c.get("url") or "").split("?")[0],
        })
    return out


EXTRACT_JS = """
() => [...document.querySelectorAll('[data-testid="property-card"]')].map(card => {
  const t = s => card.querySelector(s)?.innerText || '';
  return {
    name: t('[data-testid="title"]'),
    address: t('[data-testid="address"]'),
    price: t('[data-testid="price-and-discounted-price"]'),
    units: t('[data-testid="recommended-units"]'),
    rating: t('[data-testid="review-score"]'),
    url: card.querySelector('a[data-testid="title-link"]')?.href || '',
  };
})
"""


def scrape(args):
    from playwright.sync_api import sync_playwright

    checkin = args.date
    checkout = checkin + dt.timedelta(days=args.nights)
    found = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.show)
        page = browser.new_page(
            locale="en-US",
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"),
        )
        for town in TOWNS:
            try:
                page.goto(search_url(town, checkin, checkout, args.rooms, args.adults),
                          wait_until="domcontentloaded", timeout=45000)
                # Dismiss sign-in / cookie popups if present.
                for sel in ['button[aria-label*="Dismiss"]', "#onetrust-accept-btn-handler"]:
                    if page.locator(sel).count():
                        page.locator(sel).first.click(timeout=2000)
                page.wait_for_selector('[data-testid="property-card"]', timeout=20000)
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(1500)
                rows = parse_cards(page.evaluate(EXTRACT_JS))
            except Exception as e:  # one town failing shouldn't kill the run
                print(f"  ! {town}: {type(e).__name__}: {str(e).splitlines()[0]}", file=sys.stderr)
                continue
            print(f"  {town}: {len(rows)} properties", file=sys.stderr)
            for r in rows:
                key = r["url"] or r["name"]
                if key not in found or r["total_price"] < found[key]["total_price"]:
                    found[key] = r
            time.sleep(2)
        browser.close()

    results = sorted(found.values(), key=lambda r: r["total_price"])
    if args.queens_only:
        results = [r for r in results if r["two_queens"]]
    if args.max_price:
        results = [r for r in results if r["total_price"] <= args.max_price]
    return results


def show(results, limit):
    if not results:
        print("No matches. Try raising --max-price or dropping --queens-only.")
        return
    print(f"\n{'TOTAL':>6}  {'2Q':2}  {'RATING':6}  HOTEL / ROOMS")
    for r in results[:limit]:
        print(f"${r['total_price']:>5}  {'Y' if r['two_queens'] else '-':2}  {r['rating'][:6]:6}  "
              f"{r['name']} — {r['address']}")
        if r["rooms"]:
            print(f"{'':18}{r['rooms']}")
        print(f"{'':18}{r['url']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", type=dt.date.fromisoformat, default=dt.date.today(), help="check-in YYYY-MM-DD (default today)")
    ap.add_argument("--nights", type=int, default=1)
    ap.add_argument("--rooms", type=int, default=2)
    ap.add_argument("--adults", type=int, default=4)
    ap.add_argument("--max-price", type=int, help="max total price for all rooms, USD")
    ap.add_argument("--queens-only", action="store_true", help="only rooms listed with two queen beds")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--csv", help="also write results to this CSV file")
    ap.add_argument("--watch", type=int, metavar="MIN", help="re-run every MIN minutes and flag new low prices")
    ap.add_argument("--show", action="store_true", help="show the browser window (helps if blocked)")
    args = ap.parse_args()

    best = None
    while True:
        print(f"Searching {args.date} for {args.rooms} rooms / {args.adults} adults...", file=sys.stderr)
        results = scrape(args)
        show(results, args.limit)
        if args.csv and results:
            with open(args.csv, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=results[0].keys())
                w.writeheader()
                w.writerows(results)
        if not args.watch:
            break
        if results and (best is None or results[0]["total_price"] < best):
            if best is not None:
                print(f"\a*** NEW LOW: ${results[0]['total_price']} at {results[0]['name']} (was ${best}) ***")
            best = results[0]["total_price"]
        time.sleep(args.watch * 60)


if __name__ == "__main__":
    main()
