# 9:30 Scanner

- `STRATEGY_SPEC.md` is the confirmed rule source of truth.
- `scanner930/strategy.py` owns Trigger/C1/C2 and setup-count state.
- `scanner930/runtime.py` owns tick ordering, position management and candle
  callbacks.
- `scanner930/broker.py` owns paper/live cash-equity order submission.
- `scanner930/storage.py` and `scanner930/reports.py` own audit persistence and
  reporting.
- Never lower an active buy stop.
- Never retry an uncertain live order automatically.
- Keep paper mode as the default and preserve the two-part live approval lock.
- Add or update tests for every rule change.

Run:

```powershell
python -m unittest discover -s tests -v
```
