#!/usr/bin/env python3
"""
update_data.py  —  Seed Breakout Map data generator (runs on a schedule)

WHAT IT DOES
  Once per run (GitHub Actions runs it daily) this script:
    1. Asks Claude to find AI companies that started at seed and have since
       raised $10M+ total in roughly the last 12 months.
    2. Sorts them into 4 categories.
    3. Records early backers, seed year, and the year they crossed $10M.
    4. Guarantees 4 Glasswing portfolio companies appear (one per category).
    5. Compares to yesterday's saved file and computes what changed.
    6. Writes data/market_data.json — the single source of truth the site reads.

  The WEBSITE never calls Claude. It just displays this file. That is why the
  numbers hold still between updates and only move when the market moves.

REQUIRES
  Environment variable ANTHROPIC_API_KEY (stored as a GitHub Actions secret).
  Optional: RADAR_MODEL (defaults to claude-sonnet-5).
  Optional: RADAR_WEB_SEARCH (defaults to "1" = ON; set "0" to disable).
"""

import os, re, json, time, urllib.request, urllib.error
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "market_data.json")

# ---- FOUR CATEGORIES ------------------------------------------------------
GROUPS = [
    "Healthcare & Life Sciences AI",
    "Cybersecurity & Fraud Defense",
    "Industrial & Operations AI",
    "Data & Enterprise Intelligence AI",
]

# ---- GUARANTEED GLASSWING PORTFOLIO COMPANIES -----------------------------
# One per category, always shown regardless of what the search returns.
# total_m 0 / "n/a" where a verified public figure isn't on hand — edit freely.
GLASSWING_INCLUDE = [
    {"name": "Asepha", "group": "Healthcare & Life Sciences AI",
     "what": "agentic pharmacy workflow automation", "total_raised": "n/a", "total_m": 0,
     "valuation": "n/a", "unicorn": False, "seed_backers": ["Glasswing Ventures"],
     "seed_year": 2024, "breakout_year": 2026, "source": "Glasswing portfolio, 2026", "glasswing": True},
    {"name": "Allure Security", "group": "Cybersecurity & Fraud Defense",
     "what": "AI brand-impersonation and fraud defense", "total_raised": "n/a", "total_m": 0,
     "valuation": "n/a", "unicorn": False, "seed_backers": ["Glasswing Ventures"],
     "seed_year": 2020, "breakout_year": 2026, "source": "Glasswing portfolio, 2026", "glasswing": True},
    {"name": "Basetwo AI", "group": "Industrial & Operations AI",
     "what": "AI for manufacturing process optimization", "total_raised": "n/a", "total_m": 0,
     "valuation": "n/a", "unicorn": False, "seed_backers": ["Glasswing Ventures"],
     "seed_year": 2022, "breakout_year": 2026, "source": "Glasswing portfolio, 2026", "glasswing": True},
    {"name": "DTwo", "group": "Data & Enterprise Intelligence AI",
     "what": "enterprise data intelligence platform", "total_raised": "n/a", "total_m": 0,
     "valuation": "n/a", "unicorn": False, "seed_backers": ["Glasswing Ventures"],
     "seed_year": 2023, "breakout_year": 2026, "source": "Glasswing portfolio, 2026", "glasswing": True},
]

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def prompt(group, focus, examples):
    return f"""You are a market-structure analyst for Glasswing Ventures (early-stage AI-native VC). Today is {TODAY}.
Find AI companies that STARTED AT SEED and have since raised $10M+ in TOTAL funding, crossing that $10M threshold within roughly the last 12 months.
HARD RULES:
- AI-core only (the product is fundamentally AI, not "uses some AI").
- Must have genuinely started with an early/seed round and grown. EXCLUDE only companies that launched straight into $50M+ mega-rounds or were spun out of a large lab with a huge first cheque (e.g. frontier labs).
- US-weighted; notable global companies allowed.
- Only include a company you can tie to a real, reported total-funding figure of $10M or more. If unsure, omit it.
- Every company you return MUST clearly fit THIS category: "{group}" ({focus}). If it does not obviously fit, DROP it.
Companies of the RIGHT TYPE and funding bar for this category include, for calibration: {examples}. Do NOT just return these; find current, real companies that fit, and you may include these if they still qualify.
For each, give its early/seed backers (1-3 names), the year of its seed round (seed_year), and the year it crossed $10M (breakout_year). If it is a Glasswing Ventures portfolio company, set "glasswing": true.
Return ONLY JSON, no markdown:
{{"companies":[{{"name":"","group":"{group}","what":"under 8 words","total_raised":"$45M","total_m":45,"valuation":"$200M or n/a","unicorn":false,"seed_backers":["Fund A"],"seed_year":2023,"breakout_year":2026,"glasswing":false,"source":"publication + year"}}]}}
Give up to 6 companies, most notable first. Keep every string short.
Use web search to verify funding figures where you can.
Output ONLY the JSON object. No markdown, no code fences, no text before or after it."""


def parse_companies(text):
    """Very tolerant: handles code fences, extra prose, or a cut-off reply."""
    if not text:
        return []
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    for candidate in (cleaned, text):
        try:
            s = candidate.index("{"); e = candidate.rindex("}")
            o = json.loads(candidate[s:e + 1])
            if isinstance(o, dict) and "companies" in o:
                return o["companies"]
        except Exception:
            pass
    m = re.search(r'"companies"\s*:\s*(\[.*\])', cleaned, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    out = []
    for m in re.finditer(r'\{[^{}]*"name"[^{}]*\}', cleaned, re.S):
        try:
            out.append(json.loads(m.group(0)))
        except Exception:
            pass
    return out


def ask_claude(group, focus, examples, retries=3):
    key = os.environ["ANTHROPIC_API_KEY"]
    model = os.environ.get("RADAR_MODEL", "claude-sonnet-5")
    payload = {
        "model": model,
        "max_tokens": 12000,
        "messages": [{"role": "user", "content": prompt(group, focus, examples)}],
    }
    if os.environ.get("RADAR_WEB_SEARCH", "1") == "1":
        payload["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]

    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps(payload).encode(),
                headers={"content-type": "application/json", "x-api-key": key,
                         "anthropic-version": "2023-06-01"},
            )
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.load(r)
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            companies = parse_companies(text)
            if not companies:
                print(f"  [{group}] parsed 0 companies. stop_reason:",
                      data.get("stop_reason"), "| raw (first 400):", (text[:400].replace("\n", " ") or "<none>"))
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
    print(f"  [{group}] batch failed after retries:", last)
    return []


def main():
    print("Generating breakout map…")
    companies = []
    companies += ask_claude(
        "Healthcare & Life Sciences AI",
        "clinical workflow, pharmacy, revenue-cycle, life-sciences operations",
        "Amperos Health, Enzo Health, Cohere Health")
    companies += ask_claude(
        "Cybersecurity & Fraud Defense",
        "threat detection, identity, fraud prevention, incident response",
        "A Security, JetStream Security, Ray Security")
    companies += ask_claude(
        "Industrial & Operations AI",
        "manufacturing, supply chain, logistics, physical-world operations",
        "Limitless Labs, Rebar, Roadrunner")
    companies += ask_claude(
        "Data & Enterprise Intelligence AI",
        "data platforms, analytics, decision intelligence, enterprise search",
        "Taktile, Peregrine Technologies, Quantifind")

    # Pin the guaranteed Glasswing companies first so they survive dedupe.
    companies = GLASSWING_INCLUDE + companies

    # Drop anything that didn't land in one of our categories.
    valid = set(GROUPS)
    companies = [c for c in companies if (c.get("group") or "") in valid]

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
