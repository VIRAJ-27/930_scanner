# Scanner930 option-paper VPS deployment

This deployment is paper-only. The service starts at 09:10 IST so it can
collect the 09:15–09:29 candles needed by VWAP and EMA calculations. Strategy
entries still follow the confirmed 9:30 rules, and the service exits after
closing positions at 15:15 and delivering reports.

## 1. Create the VPS

Use an Ubuntu 24.04 LTS server in an India region when available:

- 2 vCPU and 4 GB RAM minimum;
- 25 GB or more SSD;
- reserved/static public IPv4;
- SSH-key login (disable password login after verification);
- outbound HTTPS access;
- no public application ports are required.

Keep the reserved IP. Paper mode does not submit orders, but Angel One has
announced a registered-static-IP requirement for API order execution from
1 April 2026, so retaining one avoids a later infrastructure change.

## 2. Upload the project

After this branch is pushed, either clone it or copy the repository to a
temporary directory on the VPS. Do not commit or upload a completed `.env`
through Git.

```bash
git clone --branch vps-option-paper https://github.com/VIRAJ-27/930_scanner.git
cd 930_scanner
sudo bash deploy/install_vps.sh
```

The installer creates the locked `scanner930` service account, installs into
`/opt/scanner930`, creates a private environment file, installs Python
dependencies, and registers the service and timer. Re-running it preserves
`LiveData`, `LiveReports`, logs and the private environment file.

## 3. Add secrets securely

```bash
sudo nano /etc/scanner930/scanner930.env
sudo chmod 640 /etc/scanner930/scanner930.env
sudo chown root:scanner930 /etc/scanner930/scanner930.env
```

Fill Angel One `API_KEY`, `CLIENT_CODE`, `PIN` and `TOTP_SECRET`.

For Telegram, create a private bot through `@BotFather`, message the bot once,
then set `TELEGRAM_BOT_TOKEN` and your numeric `TELEGRAM_CHAT_ID`.

For Gmail, enable two-factor authentication and create an App Password. Set
`SMTP_USER`, `SMTP_APP_PASSWORD` and `REPORT_EMAIL_TO`. Never use the normal
Gmail password.

## 4. Validate before enabling automation

```bash
sudo -u scanner930 bash -c '
  set -a
  source /etc/scanner930/scanner930.env
  set +a
  cd /opt/scanner930
  .venv/bin/python -m unittest discover -s tests -v
  .venv/bin/python option_paper_doctor.py --notify
'
```

The doctor must show a successful Angel One login, mapped equity stocks,
mapped CE contracts, and configured Telegram/email delivery.

## 5. Run one supervised paper session

```bash
sudo systemctl start scanner930-option-paper.service
sudo journalctl -u scanner930-option-paper.service -f
```

Stop only after verifying login, WebSocket connection, current instrument
mapping and report creation:

```bash
sudo systemctl stop scanner930-option-paper.service
```

No real broker orders are sent by `--option-paper`.

## 6. Enable the weekday timer

```bash
sudo systemctl enable --now scanner930-option-paper.timer
systemctl list-timers scanner930-option-paper.timer
```

The timer runs Monday–Friday at 09:10 IST. On an exchange holiday it may start,
receive no ticks, send a health warning and finish with a zero-trade report.
`Persistent=false` prevents a missed morning run from starting unexpectedly
after market hours.

## 7. Security and monitoring

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw enable

systemctl status scanner930-option-paper.service
journalctl -u scanner930-option-paper.service --since today
```

Restrict SSH to your own IP in the cloud firewall when possible. Enable the
provider's automated disk snapshot or backup. The important persistent paths
are `/opt/scanner930/LiveData`, `/opt/scanner930/LiveReports`, and
`/etc/scanner930/scanner930.env`.

## 8. Daily outputs

At shutdown the service sends Telegram and email summaries with:

- `Scanner930_Dashboard.xlsx`;
- `OptionTradeBook.csv`;
- `OptionDailySummary.csv`.

The option trade book records contract, expiry, strike, lot size, underlying
levels, best ask entry, best bid exits, spread, premium P&L, return, status and
quote failures. Any unresolved option exit remains visible instead of being
filled with an invented price.
