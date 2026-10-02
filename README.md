# Market sentiment reports

Railway starts a short cron process every five minutes on weekdays from 13:00 to
22:55 UTC. The process exits immediately when no report is due. Expensive report
generation runs in a child process with an eight-minute timeout, so report work
does not occupy RAM between reports.

The remaining report runs at 16:42 in `America/New_York`: Reddit Top 30.
The runner accounts for daylight saving time and NYSE holidays. It can briefly wait inside its current
five-minute window for 16:42 and accepts starts up to fifteen minutes late.
Railway cron may itself start a few minutes late.

The S&P 500 market breadth / 20-day and 50-day participation report has been
removed, including its 16:30 slot, manual job options, chart generation and image
upload path. The change takes effect only after deploying this revision; pushing
an unmerged development branch does not stop an older production revision.

The CNN market sentiment / Fear & Greed report has also been removed, including
its four daily slots, data collection, comparison calculation, Discord card,
manual job options, bot configuration and dependency. Only Reddit is scheduled.
The existing `python -u main.py` start command and Railway cron are unchanged.

## Deployment

- Keep the existing `WEBHOOK_URL` secret.
- Attach a persistent Railway volume at `/data`. Railway automatically sets
  `RAILWAY_VOLUME_MOUNT_PATH`; the application refuses to use ephemeral state in
  Railway. The SQLite journal stores delivery run IDs and retains historical state.
- Deploy this repository with `railway.json`. Restart policy is `NEVER`: subsequent
  cron invocations provide bounded retries for failures before delivery.
- A normal idle execution logs `CRON_FINISHED failed=False` and exits with code 0.
  A stopped/completed cron container is expected between runs.

There are no startup test messages. A successful Discord response commits the
delivery record. Confirmed deliveries are never repeated.
If a connection drops after sending starts, the journal marks the result uncertain
and suppresses automatic resending, because Discord webhooks do not provide an
idempotency key. Inspect logs and the channel before manually recovering that slot.

## Validation

```sh
python -m unittest discover -s tests -v
python main.py --dry-run
python main.py --dry-run --job reddit
```

Dry runs may read public data but never post to Discord or update delivery state.
The Docker build runs the offline tests against the actual Linux Python runtime.
No browser is required by any report, so the image no longer installs Chromium.

The pre-migration source commit is `a325dbd6c2b53beb44dc60ac780bcbdf11df1283`.
If reverting, remove the cron schedule, restore the former start/restart settings,
and deploy that revision. Its old startup self-test sends three messages; perform
any rollback with awareness of that behavior. Keep the state volume for recovery.

## Retired report data

CNN delivery rows ending in `:fear` and historical `previous_fear_value` state
remain untouched. They are not read to generate reports or retried by the runner.
No database migration or permanent deletion is performed; deleting historical
data, Discord messages, the service or its volume requires separate approval.

No production cleanup or database migration runs as part of this change. No
tracked price datasets or chart files exist in this revision: the former report
calculated prices in memory and uploaded a PNG from an in-memory buffer.

- The shared delivery journal is `sentiment.sqlite3` under
  `MARKET_SENTIMENT_STATE_DIR`, otherwise `RAILWAY_VOLUME_MOUNT_PATH`, otherwise
  `.state` in the working directory. With the documented `/data` mount and no
  directory override, this is `/data/sentiment.sqlite3`.
- Only rows in `runs` whose `id` ends in `:breadth` belong to the retired report
  (normally `YYYY-MM-DD:16:30:breadth`). A read-only inventory is
  `SELECT id, status, updated FROM runs WHERE id LIKE '%:breadth';`.
  Leave the database, all other run rows, and the `state` table intact.
  Breadth wrote no entries to the `state` table. Existing
  retention removes run records older than 35 days when the journal is opened;
  this policy is unchanged. Deleting delivery records loses the original audit
  and deduplication history unless a backup exists, and can allow an old revision
  to resend a recently deleted slot if rolled back.
- Potential regenerable caches from the retired libraries are the old working
  directory's `yfinance.cache`, the runtime user's cache directory's
  `py-yfinance/`, and Matplotlib's dedicated cache directory. On a default Linux
  root-user container these are `/app/yfinance.cache`,
  `/root/.cache/py-yfinance/` and `/root/.cache/matplotlib/`; environment overrides
  (`XDG_CACHE_HOME`, `MPLCONFIGDIR`) or a different runtime user change the paths.
  Presence and exclusive ownership must be checked before any later cleanup.
  Do not delete a shared cache root or inspect Yahoo cookie-cache contents.
  These paths are candidates inferred from code/library defaults, not a verified
  inventory of production storage. Removed dependencies disappear from new
  images when rebuilt; do not uninstall packages in a running production service.
- Existing Discord messages and attachments are outside this cleanup scope.
  They are not deleted by the code change. No webhook or shared volume should be
  removed: Reddit still uses them.

Source removal is reversible through Git history. No irreversible production
data deletion is included in this change.

References: [Railway cron](https://docs.railway.com/cron-jobs),
[configuration precedence](https://docs.railway.com/config-as-code/reference),
[persistent volumes](https://docs.railway.com/volumes).
