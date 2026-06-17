#!/usr/bin/env python3
"""Agora Bounty Monitor — Cloud-Variante (GitHub Actions, 24/7).

Pollt die GitHub-Such-API nach frischen Algora-Bounties (Label "💎 Bounty"),
filtert nach Sprache / $-Range / Qualität, dedupliziert gegen seen.json und
alarmiert per Email. Nur Python-Stdlib.

Email-Adresse + App-Passwort kommen aus Umgebungsvariablen (GitHub Secrets),
nicht aus dem Code -> sicher fuer ein oeffentliches Repo:
  GMAIL_USER, GMAIL_APP_PASSWORD
GITHUB_TOKEN (von Actions bereitgestellt) hebt die API-Limits.
"""

import json
import os
import re
import smtplib
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text())
SEEN_PATH = HERE / "seen.json"
FEED_PATH = HERE / "bounty-feed.md"
LOG_PATH = HERE / "monitor.log"

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GMAIL_USER = os.environ.get("GMAIL_USER", "").strip()
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "").strip()

BOUNTY_LABEL = "\U0001f48e Bounty"  # 💎 Bounty
API = "https://api.github.com"


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}Z] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def gh_get(url):
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "agora-bounty-monitor")
    if GITHUB_TOKEN:
        req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def parse_amount(labels):
    best = None
    for lab in labels:
        m = re.fullmatch(r"\$(\d+(?:\.\d+)?)(k)?", lab.strip(), re.IGNORECASE)
        if m:
            val = float(m.group(1)) * (1000 if m.group(2) else 1)
            best = val if best is None else max(best, val)
    return best


def search_bounties(language):
    q = f'label:"{BOUNTY_LABEL}" state:open is:issue no:assignee language:{language}'
    params = urllib.parse.urlencode(
        {"q": q, "sort": "created", "order": "desc", "per_page": 30}
    )
    try:
        return gh_get(f"{API}/search/issues?{params}").get("items", [])
    except Exception as e:
        log(f"search failed ({language}): {e}")
        return []


def has_open_pr(owner, repo, number):
    url = f"{API}/repos/{owner}/{repo}/issues/{number}/timeline?per_page=100"
    try:
        events = gh_get(url)
    except Exception as e:
        log(f"timeline check failed {owner}/{repo}#{number}: {e}")
        return False
    for ev in events:
        if ev.get("event") == "cross-referenced":
            iss = ev.get("source", {}).get("issue", {})
            if iss.get("pull_request") and iss.get("state") == "open":
                return True
    return False


def blocked(repo_full):
    rl = repo_full.lower()
    if repo_full in CONFIG["blocklist_repos"]:
        return True
    return any(p.lower() in rl for p in CONFIG["blocklist_patterns"])


def load_seen():
    if SEEN_PATH.exists():
        try:
            return json.loads(SEEN_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_seen(seen):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    SEEN_PATH.write_text(json.dumps(seen, indent=2))


def age_str(created_iso):
    created = datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
    h = int((datetime.now(timezone.utc) - created).total_seconds() // 3600)
    return f"{h}h alt" if h < 24 else f"{h // 24}d alt"


def send_email(finds):
    cfg = CONFIG.get("email", {})
    if not cfg.get("enabled"):
        return
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        log("email: GMAIL_USER/GMAIL_APP_PASSWORD fehlt -> skip")
        return
    to = os.environ.get("EMAIL_TO", "").strip() or GMAIL_USER
    blocks = [
        f"{f['amount_str']}  {f['repo']}\n  {f['title']}\n  {f['url']}\n"
        f"  ({f['language']}, {f['age']})\n"
        for f in finds
    ]
    msg = EmailMessage()
    msg["Subject"] = f"\U0001f7e2 {len(finds)} neue Bounty(s) gefunden"
    msg["From"] = GMAIL_USER
    msg["To"] = to
    msg.set_content("Neue Algora-Bounties:\n\n" + "\n".join(blocks))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.send_message(msg)
        log(f"email sent to {to}")
    except Exception as e:
        log(f"email failed: {e}")


def write_feed(finds):
    if not finds:
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    block = "\n".join(
        f"## {f['amount_str']} — [{f['repo']}]({f['url']})\n"
        f"- **{f['title']}**\n"
        f"- {f['language']} · {f['age']} · gefunden {now}Z\n"
        for f in finds
    ) + "\n"
    existing = FEED_PATH.read_text() if FEED_PATH.exists() else ""
    if not existing:
        header = ("# Agora — Bounty Feed\n\n"
                  "Automatisch befuellt vom Bounty-Monitor. Neueste oben.\n\n---\n\n")
        FEED_PATH.write_text(header + block)
        return
    marker = "---\n\n"
    idx = existing.find(marker)
    if idx != -1:
        cut = idx + len(marker)
        FEED_PATH.write_text(existing[:cut] + block + existing[cut:])
    else:
        FEED_PATH.write_text(block + existing)


def main():
    seen = load_seen()
    candidates = []
    for lang in CONFIG["languages"]:
        for item in search_bounties(lang):
            url = item["html_url"]
            if url in seen:
                continue
            repo_full = item["repository_url"].split("/repos/")[1]
            if blocked(repo_full):
                continue
            labels = [l["name"] for l in item.get("labels", [])]
            amount = parse_amount(labels)
            if amount is None:
                if not CONFIG.get("include_unknown_amount"):
                    continue
                amount_str = "$?"
            elif amount < CONFIG["min_amount"] or amount > CONFIG["max_amount"]:
                continue
            else:
                amount_str = f"${int(amount)}" if amount == int(amount) else f"${amount}"
            candidates.append({
                "url": url, "repo": repo_full, "title": item["title"],
                "language": lang, "amount": amount, "amount_str": amount_str,
                "age": age_str(item["created_at"]), "number": item["number"],
            })

    finds = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for c in candidates:
        owner, repo = c["repo"].split("/", 1)
        seen[c["url"]] = now_iso
        if has_open_pr(owner, repo, c["number"]):
            continue
        finds.append(c)

    finds.sort(key=lambda x: (x["amount"] is not None, x["amount"] or 0), reverse=True)

    if finds:
        log(f"{len(finds)} new bounties")
        write_feed(finds)
        send_email(finds)
    else:
        log("no new bounties")
    save_seen(seen)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL: {e}")
        sys.exit(1)
