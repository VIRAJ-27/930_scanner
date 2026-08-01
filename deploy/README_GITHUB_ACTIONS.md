# Free GitHub Actions option-paper runner

This runner is intentionally paper-only. It starts at 09:25 IST every weekday,
uses the Angel One WebSocket for underlying-stock ticks, and force-closes every
remaining position at 11:00 IST with `TIME_EXIT_11_00` in the trade report.

## Requirements

- The repository must be public for unlimited standard GitHub-hosted runner
  minutes. Private GitHub Free repositories receive only 2,000 minutes/month.
- The workflow must exist on the repository's default branch.
- Never commit a `.env` file or paste a credential into workflow YAML.

## Add encrypted secrets

Open the repository on GitHub, then select **Settings > Secrets and variables >
Actions > New repository secret**. Add these required secrets one at a time:

- `API_KEY`
- `CLIENT_CODE`
- `PIN`
- `TOTP_SECRET`

For Telegram delivery, also add:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

For email delivery, also add:

- `SMTP_HOST` (normally `smtp.gmail.com`)
- `SMTP_PORT` (normally `587`)
- `SMTP_USER`
- `SMTP_APP_PASSWORD`
- `REPORT_EMAIL_TO`

The workflow never prints these values. Keep repository write access restricted,
because writers can modify a workflow that uses repository secrets.

## Test manually

After the workflow is merged into the default branch:

1. Open **Actions** in the GitHub repository.
2. Select **Option paper scanner**.
3. Select **Run workflow**, keep the default branch selected, and confirm.
4. Open the job and watch the setup/login messages. Credentials and tokens are
   suppressed.
5. Stop the test if it is outside market hours. A normal weekday run stops by
   itself at 11:00 IST.

## Daily schedule and outputs

GitHub schedules the job at `03:55 UTC`, equal to `09:25 IST`, Monday-Friday.
At completion it sends configured Telegram/email reports and uploads a private
workflow artifact containing `LiveReports` and the SQLite audit database. Open
the workflow run's **Artifacts** section to download it. Artifacts are retained
for 30 days.

An NSE holiday still starts a job, but there should be no market trades. GitHub
scheduled jobs can occasionally start late. This is acceptable for observation
and paper testing, but the workflow must never be treated as live-order
infrastructure.
