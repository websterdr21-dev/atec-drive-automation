---
slug: stock-update-failed-errno-2-no
status: investigating
trigger: "Stock update failed - [Errno 2] No such file or directory:'service_account.json'"
created: 2026-04-15
updated: 2026-04-15
---

## Symptoms

- **Interface:** Telegram bot
- **Expected:** Stock update (bookout) should succeed — service account authenticates with Google APIs
- **Actual:** `[Errno 2] No such file or directory: 'service_account.json'` — auth fails because the file doesn't exist on the server
- **Error messages:** `[Errno 2] No such file or directory: 'service_account.json'`
- **Timeline:** Started after a redeploy to Railway
- **Reproduction:** Trigger any bookout or stock update via the Telegram bot on the Railway deployment
- **Root context:** `service_account.json` is gitignored so GitHub rejects it; Railway deploys from GitHub so the file never reaches the server. `SERVICE_ACCOUNT_PATH` env var likely points to the filename only (relative path).

## Current Focus

hypothesis: "service_account.json is not deployed to Railway because it is gitignored, and the app is trying to open it by relative path which doesn't exist on the Railway filesystem"
test: "Read utils/auth.py to see how SERVICE_ACCOUNT_PATH is used, and check if there is a way to pass credentials as an environment variable instead"
expecting: "auth.py opens service_account.json by file path from SERVICE_ACCOUNT_PATH env var — need to add support for inline JSON credentials via a SERVICE_ACCOUNT_JSON env var"
next_action: "gather initial evidence"
reasoning_checkpoint: ""

## Evidence

## Eliminated

## Resolution

root_cause:
fix:
verification:
files_changed:
