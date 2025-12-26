# BTC Futures Journal Vault

This repo is a zero-cost, automated vault that writes one JSON playbook per day into journal/YYYY/YYYY-MM-DD.json.

- Schedule: GitHub Actions runs every 15 minutes; the script only writes during 06:00–06:10 AM ET.
- Data sources (no keys): Coinbase spot + OKX funding snapshot.

To force-run:
- GitHub → Actions → "BTC Futures Morning Playbook" → Run workflow
