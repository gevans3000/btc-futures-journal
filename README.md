# BTC Futures Journal Vault

This repo is an automated vault that writes one JSON playbook per day into journal/YYYY/YYYY-MM-DD.json.

## Quick links
- Journal dashboard: journal/INDEX.md
- Latest summary: journal/LATEST.md
- Latest JSON: journal/LATEST.json

## How it runs
- GitHub Actions runs on schedule; the generator writes only during 06:00–06:10 AM ET.
- Manual workflow runs will FORCE_WRITE and generate immediately.

## Data sources (no keys)
- Coinbase spot + OKX funding snapshot
