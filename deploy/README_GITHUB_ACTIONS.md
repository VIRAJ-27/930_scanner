# SOT SETUP live signal runner

The scheduled workflow starts at 09:15 IST every weekday and monitors SOT SETUP
through 12:18 IST. It fetches completed Angel One candles and sends Telegram
alerts for valid setups and entry breaks. It is signal-only and never places
orders. Daily CSV/JSON reports are sent through the configured notification
channels and uploaded as a GitHub Actions artifact.

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
2. Select **SOT SETUP live signals**.
3. Select **Run workflow**, keep the default branch selected, and confirm.
4. Open the job and watch the setup/login messages. Credentials and tokens are
   suppressed.
5. A normal weekday run monitors through 12:18 IST. A manual run after that
   time creates the day's report without sending old setup or entry alerts.

## Daily schedule and outputs

GitHub schedules the job at `03:45 UTC`, equal to `09:15 IST`, Monday-Friday.
At completion it sends configured Telegram/email reports and uploads the daily
SOT report folder and generated universe file. Open the workflow run's
**Artifacts** section to download it. Artifacts are retained for 30 days.

An NSE holiday still starts a job, but there should be no signals. GitHub
scheduled jobs can occasionally start late; setup/entry alerts may therefore
arrive after a candle has formed. Check the candle and entry times before acting.
