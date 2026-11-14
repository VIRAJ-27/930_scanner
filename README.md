# 930 Scanner — SOT SETUP

The weekday GitHub Actions workflow runs SOT SETUP as the primary scanner. It
starts at **09:15 IST**, fetches Angel One data for the current F&O stock
universe, and sends Telegram alerts when a valid setup forms and when its entry
level breaks. It is signal-only: it never places an order. Rules are documented
in `SOT_SETUP_RULES.md`.

The live monitor polls completed 3-minute data until 12:18 IST so it can observe
entry breaks in the final three-candle window. At the end it sends the daily
report through the existing Telegram/email notifier and saves CSV/JSON reports
as a GitHub Actions artifact. `export_sot_universe.py` builds the current F&O
universe from Angel One's instrument master; the live price cap rule excludes
stocks whose session high exceeds ₹9,999.

Running the workflow manually after 12:18 IST downloads the completed candles
for that day and creates the day report without sending old setup or entry
alerts. The backtest CLI can also analyze any saved historical date range.

## Automated option paper mode

`--option-paper` keeps every signal, stop, TP1 and trailing decision on the
underlying stock, while paper-filling the selected current-expiry stock CE at
best ask and exiting at best bid. Quotes that are stale, missing or wider than
the configured maximum spread are rejected rather than estimated.

```powershell
python run_scanner.py --option-paper
```

For unattended Ubuntu VPS installation, Telegram/email delivery, systemd
restart recovery and the weekday 09:10 IST timer, see `deploy/README_VPS.md`.

### Scheduled SOT SETUP workflow

The workflow `.github/workflows/option-paper.yml` starts at 09:15 IST on
weekdays and monitors until 12:18 IST. It uses encrypted repository secrets,
sends setup and entry alerts to Telegram, and never enables live broker orders.
A manual run after 12:18 IST creates that day's report without sending stale
signals. Scheduled workflows run only from the default branch. See
`deploy/README_GITHUB_ACTIONS.md` for secret setup and daily operation.

live audited track record.
