# agora-bounty-monitor (Cloud)

24/7-Monitor für frische, ungeclaimte Algora-Bounties (Python/TypeScript, $50–350).
Läuft als GitHub-Actions-Cronjob alle ~15 Min, alarmiert per Email.

## Wie es läuft
- `.github/workflows/monitor.yml` triggert `monitor.py` per Cron.
- `monitor.py` sucht via GitHub-API, filtert Junk/Scam, dedupliziert gegen `seen.json`.
- Neue Treffer → Email + Eintrag in `bounty-feed.md` (im Repo).
- `seen.json` wird nach jedem Lauf zurück-committet (State-Persistenz).

## Secrets (Repo → Settings → Secrets → Actions)
- `GMAIL_USER` — Gmail-Adresse (Absender + Empfänger)
- `GMAIL_APP_PASSWORD` — Gmail App-Passwort (2FA nötig)

## Konfiguration
`config.json`: Sprachen, $-Range, Blocklists.

## Manuell starten
Actions-Tab → "bounty-monitor" → "Run workflow". Oder:
`gh workflow run bounty-monitor.yml`
