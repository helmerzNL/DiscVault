# The anonymous installation count

The detail behind the *Privacy and security* section of the
[README](../README.md#privacy-and-security).

Nobody could say how many DiscVault servers are in use. So a running server
reports, at most once a day, that it is running, and it does so without saying
which server it is.

## What is sent, and when

Once per UTC day the background worker (`next-worker`) sends one request:

```http
POST https://api.discvault.eu/v2/presence
Content-Type: application/json

{"protocol":"presence-1","platform":"server","version":"26.11.0","sinceDays":3}
```

| Field | Meaning |
|---|---|
| `protocol` | Always `presence-1` |
| `platform` | Always `server` |
| `version` | The released DiscVault version this server runs |
| `sinceDays` | Whole UTC days since this server's previous accepted report, or `null` when there was none |

That is the whole report. `sinceDays` is what makes counting possible without
an identifier: the service can tell a server that is new in a period from one
that reported before, without knowing who either is.

- Only an accepted report (a `2xx` answer) counts for the day. After a failed
  attempt the server waits at least an hour and tries at most six times a day.
- Only a released image reports. The version is the one the published image
  was built with (`DISCVAULT_IMAGE_VERSION`); a source checkout or a development
  build has none and sends nothing.
- The date of the last accepted report and the retry bookkeeping are kept in
  the server's own database (`app_settings`, keys starting with
  `installation_count_`), so a restore from a backup continues the same
  interval instead of looking like a new installation.

## What is never sent

No instance id, no hostname, no user count, no account, no cookie, no
credential, no MovieVault contribution identity, and nothing about the
collection. The request uses its own connection, separate from the catalogue
and contribution calls, and follows no redirect.

The service keeps daily totals only. Like any request, a report arrives from an
address; the service uses it for rate limiting only, as a keyed hash that
expires within 24 hours, does not store it with the report, and keeps no
request log for this route.

## The owner is told first

Before the first report leaves, the owner gets one notice in the notification
inbox: the anonymous installation count is on, what it sends, and where to turn
it off. Opening the notice goes straight to the setting.

- It is created once and never again, whether or not it was read.
- It goes to the owner only, and it is not pushed to any device.
- A server that has no owner account yet has nobody to tell, so it sends
  nothing until there is one.
- While the count is off, no notice is created. It is created if the count is
  ever turned on.

## Turning it off

Either of these is enough. With the count off, the server makes no request to
the endpoint at all.

- **In the app.** The owner turns off **Count this server** under
  *Preferences > Collectors > Anonymous installation count*.
- **In the environment.** `DISCVAULT_INSTALLATION_COUNT=off` (also `false`, `0`
  or `no`) turns it off whatever the owner setting says, and before the first
  start. The switch in the app then shows that the operator turned it off.

  The variable has to reach the container that runs the worker. The shipped
  Compose files map it on `next-worker`, which sends the report, and on
  `next-api`, which only shows the owner that it is off. A Compose override of
  your own needs the same line on both:

  ```yaml
  DISCVAULT_INSTALLATION_COUNT: ${DISCVAULT_INSTALLATION_COUNT:-on}
  ```

The stored date of the last accepted report is kept while the count is off, so
turning it back on reports the true interval.

## Egress firewalls

For a server to be counted, outbound HTTPS to `api.discvault.eu` on port 443
has to be allowed. A server that cannot reach it is simply not counted: nothing
else depends on the report, no job waits for it, and the worker logs at most
one warning line a day about it.

## Specification

The rules above are specified, for the server and for the iOS and Android apps,
in App-Guidance:
[`projects/discvault/specs/active-installation-count.md`](https://github.com/Flux76HQ/App-Guidance/blob/main/projects/discvault/specs/active-installation-count.md).
