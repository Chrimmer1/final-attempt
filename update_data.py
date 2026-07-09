#!/usr/bin/env python3
"""
update_data.py  —  Seed Breakout Map data generator (runs on a schedule)

WHAT IT DOES
  Once per run (GitHub Actions runs it daily) this script:
    1. Asks Claude to search the web for AI companies that started at seed and
       have since broken out ($100M+ raised) in roughly the last 12 months.
    2. Groups them, records early backers and time-to-breakout.
    3. Compares to yesterday's saved file and computes what changed.
    4. Writes data/market_data.json — the single source of truth the website reads.

  The WEBSITE never calls Claude. It just displays this file. That is why the
  numbers hold still between updates and only move when the market moves.

REQUIRES
  Environment variable ANTHROPIC_API_KEY (stored as a GitHub Actions secret).
  Optional: RADAR_MODEL (defaults to claude-sonnet-5).
"""

import os, re, json, time, urllib.request, urllib.error
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "market_data.json")

GROUPS = ["AI Coding & Dev Tools", "AI Customer Service & Agents", "Cybersecurity",
          "AI Infrastructure", "Vertical AI (Legal/Health/Finance)",
          "Enterprise Search & Ops", "Other AI"]

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def prompt(focus):
    return f"""You are a market-structure analyst for Glasswing Ventures (early-stage AI-native VC). Today is {TODAY}.
Find AI companies that STARTED AT SEED and have since BROKEN OUT, defined as having raised $100M+ in TOTAL funding, crossing that threshold within roughly the last 12 months.
HARD RULES:
- AI-core only (the product is fundamentally AI, not "uses some AI").
- Must have genuinely started with an early/seed round and grown. EXCLUDE companies that launched straight into mega-rounds or were spun out of a large lab with a huge first cheque (e.g. frontier labs).
- US-weighted; notable global companies allowed.
- Only include a company you can tie to a real, reported total-funding figure. If unsure, omit it.
Classify each into EXACTLY ONE group from: {json.dumps(GROUPS)}.
For each, also give its early/seed backers (1-3 names, the investors who got in at seed/Series A), the year of its seed round (seed_year), and the year it crossed $100M (breakout_year).
FOCUS THIS BATCH ON: {focus}.
Return ONLY JSON, no markdown:
{{"companies":[{{"name":"","group":"one from the list","what":"under 8 words","total_raised":"$1.2B","total_m":1200,"valuation":"$3B or n/a","unicorn":true,"seed_backers":["Fund A"],"seed_year":2022,"breakout_year":2026,"source":"publication + year"}}]}}
Give up to 7 companies, most notable first. Keep every string short.
Output ONLY the JSON object. No markdown, no code fences, no text before or after it."""


def parse_companies(text):
    """Very tolerant: handles code fences, extra prose, or a cut-off reply."""
    if not text:
        return []
    # 1) strip markdown code fences if present
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    # 2) try to parse the whole object
    for candidate in (cleaned, text):
        try:
            s = candidate.index("{"); e = candidate.rindex("}")
            o = json.loads(candidate[s:e + 1])
            if isinstance(o, dict) and "companies" in o:
                return o["companies"]
        except Exception:
            pass
    # 3) try to grab just the companies array
    m = re.search(r'"companies"\s*:\s*(\[.*\])', cleaned, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 4) last resort: salvage each complete {...} object that has a "name"
    out = []
    for m in re.finditer(r'\{[^{}]*"name"[^{}]*\}', cleaned, re.S):
        try:
            out.append(json.loads(m.group(0)))
        except Exception:
            pass
    return out


def ask_claude(focus, retries=3):
    key = os.environ["ANTHROPIC_API_KEY"]
    model = os.environ.get("RADAR_MODEL", "claude-sonnet-5")
    payload = {
        "model": model,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt(focus)}],
    }
    # Web search on by default; set RADAR_WEB_SEARCH=0 to disable if the account lacks it.
    if os.environ.get("RADAR_WEB_SEARCH", "1") != "0":
        payload["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]

    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps(payload).encode(),
                headers={"content-type": "application/json", "x-api-key": key,
                         "anthropic-version": "2023-06-01"},
            )
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.load(r)
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            companies = parse_companies(text)
            if not companies:
                # show what came back so an empty result is never a mystery
                print("  got a reply but parsed 0 companies. stop_reason:",
                      data.get("stop_reason"), "| raw text (first 600 chars):")
                print("  " + (text[:600].replace("\n", " ") or "<no text block in response>"))
            return companies
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:500]
            except Exception:
                pass
            last = f"HTTP {e.code}: {body}"
            print("  API error:", last)
            time.sleep(5 * (i + 1))
        except Exception as e:
            last = str(e)
            print("  request error:", last)
            time.sleep(5 * (i + 1))
    print("  batch failed after retries:", last)
    return []


def main():
    print("Generating breakout map…")
    companies = []
    companies += ask_claude("cybersecurity, AI infrastructure, and AI coding/developer tools")
    companies += ask_claude("AI customer service & support agents, vertical AI in legal/health/finance, and enterprise search/ops")

    # dedupe by name
    seen, deduped = set(), []
    for c in companies:
        k = (c.get("name") or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            deduped.append(c)
    companies = deduped
    print(f"  {len(companies)} companies after dedupe")

    # compare to yesterday for real "what changed" deltas
    prev = {}
    if os.path.exists(OUT):
        try:
            prev = json.load(open(OUT))
        except Exception:
            prev = {}
    prev_companies = prev.get("companies", [])
    prev_names = {(c.get("name") or "").strip().lower() for c in prev_companies}
    new_names = [c["name"] for c in companies if (c.get("name") or "").strip().lower() not in prev_names]
    unis_now = sum(1 for c in companies if c.get("unicorn"))
    unis_prev = sum(1 for c in prev_companies if c.get("unicorn"))

    deltas = None
    if prev_companies:
        deltas = {
            "since": prev.get("generated_at"),
            "companies": len(companies) - len(prev_companies),
            "unicorns": unis_now - unis_prev,
            "new_names": new_names[:6],
        }

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "companies": companies,
        "deltas": deltas,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=2)
    print(f"  wrote {OUT}")
    if deltas:
        print(f"  since last update: {deltas['companies']:+d} companies, {deltas['unicorns']:+d} unicorns, new: {new_names}")


if __name__ == "__main__":
    main()
