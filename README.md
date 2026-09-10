# Market sentiment reports

Railway starts a short cron process every five minutes on weekdays from 13:00 to
22:55 UTC. The process exits immediately when no report is due. Expensive report
generation runs in a child process with an eight-minute timeout, so plotting and
historical price data do not occupy RAM between reports.

The report times remain in `America/New_York`: CNN Fear & Greed at 09:45, 11:45,
13:45, and 15:45; market breadth at 16:30; Reddit at 16:42. The runner accounts for
daylight saving time and NYSE holidays. It can briefly wait inside its current
five-minute window for 16:42 and accepts starts up to fifteen minutes late.
Railway cron may itself start a few minutes late.

## Deployment

- Keep the existing `WEBHOOK_URL` secret.
- Attach a persistent Railway volume at `/data`. Railway automatically sets
  `RAILWAY_VOLUME_MOUNT_PATH`; the application refuses to use ephemeral state in
  Railway. The SQLite journal stores run IDs and the previous confirmed fear value.
- Deploy this repository with `railway.json`. Restart policy is `NEVER`: subsequent
  cron invocations provide bounded retries for failures before delivery.
- A normal idle execution logs `CRON_FINISHED failed=False` and exits with code 0.
  A stopped/completed cron container is expected between runs.

There are no startup test messages. A successful Discord response commits both the
delivery record and the comparison value. Confirmed deliveries are never repeated.
If a connection drops after sending starts, the journal marks the result uncertain
and suppresses automatic resending, because Discord webhooks do not provide an
idempotency key. Inspect logs and the channel before manually recovering that slot.
The first run after migrating from the old in-memory process starts without a prior
comparison value; later successful reports retain it across restarts.

## Validation

```sh
python -m unittest discover -s tests -v
python main.py --dry-run
python main.py --dry-run --job fear
python main.py --dry-run --job reddit
```

Dry runs may read public data but never post to Discord or update delivery state.
The Docker build runs the offline tests against the actual Linux Python runtime.
No browser is required by any report, so the image no longer installs Chromium.

The pre-migration source commit is `a325dbd6c2b53beb44dc60ac780bcbdf11df1283`.
If reverting, remove the cron schedule, restore the former start/restart settings,
and deploy that revision. Its old startup self-test sends three messages; perform
any rollback with awareness of that behavior. Keep the state volume for recovery.

References: [Railway cron](https://docs.railway.com/cron-jobs),
[configuration precedence](https://docs.railway.com/config-as-code/reference),
[persistent volumes](https://docs.railway.com/volumes).
