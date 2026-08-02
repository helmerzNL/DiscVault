# Troubleshooting — the library shows too few movies, or the app freezes

Follow this if you see either of these:

- **The library counter stops short.** It reads something like `200 / 228` and
  stays there, so part of your collection never appears.
- **The app becomes unresponsive** after a few minutes of use, or after running a
  PDF export. Reloading helps for a while, then it happens again.

These two symptoms have **more than one possible cause**, and the fix is
different for each. Work through the steps in order — each one either fixes the
problem or tells you which branch to take next. Steps 1 and 2 alone resolve most
reports.

Nothing here touches your collection. The worst case is that you sign in again.

---

## Step 1 — Check which version you are actually running

Everything below assumes **26.7.24 or newer** on the **beta** channel. Earlier
builds still contain the fault, and no amount of clearing your browser will help.

In the app:

> **Profile** → **About** → *App version*

That value is rendered by the server, so it is accurate even if the page itself
is stale.

Or from any machine that can reach the server, without signing in:

```bash
curl -s http://<host>:<port>/api/next/health
```

The response contains `version` and `sha`.

| Version shown | What to do |
| --- | --- |
| 26.7.24 or newer | Go to **step 3**. |
| Anything older | Go to **step 2**. Do not skip it — the rest of this guide will not help you. |
| `unknown` or empty | Your container is not passing a build version through. Go to step 2 anyway, then report the value you see. |

---

## Step 2 — Update the container

DiscVault never updates itself. It ships as a Docker stack and cannot safely
recreate its own container from the inside, so the in-app check only *reports*
that an update exists and prints the command for you to run on the Docker host.

For a Compose install:

```bash
docker compose pull && docker compose up -d
```

For a plain `docker run` install, pull the image and recreate the container:

```bash
docker pull ghcr.io/helmerznl/discvault:beta
# then stop, remove and re-run the container with your usual options
```

Notes:

- The beta image is `ghcr.io/helmerznl/discvault:beta`. The in-app update check
  reports the same image as `:v26-beta`. **Both names are the same build** — the
  strings differing is not a fault.
- Admins can confirm what is available under **Profile** → **About** →
  *Check for update*, with the update channel set to **beta**. That setting only
  controls which channel is *checked*; it does not change the image you run. To
  actually switch channels you change the tag and recreate the container.
- Take a backup before moving an existing library between the stable and beta
  channels — see the *Before updating production* section in the main
  [`README.md`](../../README.md).

**Then go back to step 1 and confirm the version really changed.** A pull that
did not recreate the container is the single most common reason an update appears
to have no effect.

---

## Step 3 — Find out whether a service worker is involved

DiscVault only installs its offline service worker on secure origins. This
decides whether step 4 applies to you at all. Look at your browser's address bar:

| How you open DiscVault | Service worker | Next step |
| --- | --- | --- |
| `https://…` | Yes | **Step 4** |
| `http://localhost…` or `http://127.0.0.1…` | Yes | **Step 4** |
| `http://192.168.…`, `http://tower.local`, or any other plain `http://` address | **No** | **Skip to step 5** |

If you are on a plain `http://` LAN address, no service worker was ever
installed on that origin, so there is nothing cached to clear and step 4 would be
wasted effort. Updating to 26.7.24+ in step 2 is the whole fix available to you;
if the problem survives that, go straight to step 5.

---

## Step 4 — Clear the old service worker (secure origins only)

**Why this is needed once.** The update 26.7.24 fixes the mechanism that tells
your browser a new version has arrived — but it cannot announce *itself*, because
the part that listens for that announcement only exists from this version onward.
So for this one upgrade the browser may keep running the previous version of the
app until you clear it by hand. From the next update onward you will get a
"A new version of DiscVault is available — Reload" banner instead.

### Firefox

1. Open a new tab and go to `about:debugging#/runtime/this-firefox`.
2. Find **Service Workers**, locate the DiscVault entry, and click **Unregister**.
3. Go to **Settings** → **Privacy & Security** → **Cookies and Site Data** →
   **Manage Data…**, select the DiscVault site, click **Remove Selected**, then
   **Save Changes**.
4. Close **every** DiscVault tab, then open the app again.

### Chrome (and Edge)

1. Open DiscVault, press **F12** to open DevTools.
2. Go to the **Application** tab → **Storage** in the left sidebar.
3. Make sure **Unregister service workers** is ticked, then click
   **Clear site data**.
4. Close DevTools and press **Ctrl+Shift+R** (macOS: **Cmd+Shift+R**).

> If the entry will not go away, `chrome://serviceworker-internals` lists every
> registration with an **Unregister** button.

You may have to sign in again afterwards. This clears offline data only — your
collection lives on the server and is untouched.

---

## Step 5 — Verify

Open the library and check all four:

1. **The counter reaches your real total** — `228 / 228` rather than stopping at
   `200 / 228`. It may take a few seconds to climb; that is the library loading
   in the background.
2. **The paging module loads as a script.** In DevTools → **Network**, reload and
   find `library-paging.js`. It must be **200** with
   `Content-Type: application/javascript`. If it says `application/json`, or
   fails with 503, the original fault is still present — capture that row.
3. **The caches carry the new version.** DevTools → **Application** →
   **Cache Storage**. The names should read `discvault-sw-26.7.24…` (or newer).
   If you still see `discvault-sw-v153`, the old service worker is in control —
   repeat step 4.
4. **It survives use.** Run a PDF export, then keep browsing for about five
   minutes.

---

## Step 6 — If it still happens, send this back

Two different causes remain possible at this point, and they need different
evidence. Please include all of it:

- The exact **App version** and **sha** from Profile → About.
- The **form** of the address you use — `https://`, or `http://` with an IP or
  hostname. The address itself is not needed, just which of the two.
- Browser and version, and whether **both** browsers behave identically.
- Any **console errors**: DevTools → **Console**, especially messages naming
  `library-paging.js` or `library-export.js`.
- The **Network** row for `library-paging.js` — status code and `Content-Type`.
- The **Cache Storage** names from step 5.
- What the **counter reads** at the moment it stops climbing.
- **The decisive one.** At the moment the app "stops working", run this from
  another machine or a terminal:

  ```bash
  curl -s http://<host>:<port>/api/next/health
  ```

  - **It answers normally** → the browser tab is stuck; the server is fine.
  - **It does not answer, or reports `degraded`** → the server is the problem.
    Also send the log from around that moment:

    ```bash
    docker compose logs --tail=200 next-api
    ```

That one check tells us which half of the system to look at, and saves a round
trip.

---

## What each symptom means

| Observation | Cause | Fix |
| --- | --- | --- |
| The counter stops at exactly 200 and never moves | The background loader never ran, or gave up. Before 26.7.24 a single failed request stopped it permanently and said nothing | Update (step 2), then clear the service worker (step 4). From 26.7.24 the app retries and shows a warning instead of failing silently |
| A feature (export, paging) does nothing at all, with no error on screen | Its script was served from cache as the wrong content type, so the browser refused to run it | Step 4. Confirm with the `Content-Type` check in step 5 |
| Works right after clearing, then degrades again over minutes | The browser's storage quota for the site filled up, and the old version treated a full cache as "you are offline" | Update to 26.7.24+, which bounds what is stored and never lets a storage failure break a working page |
| Firefox breaks first, Chrome later | Same storage-quota cause — Firefox allows a smaller amount per site, so it hits the limit sooner. Not a Firefox bug | As above |
| "It started working after I updated" | You reloaded the page as part of updating. Nothing prompted you to, because the version you were on had no update banner | Expected for this one upgrade. From the next release a banner offers a reload |
| The whole tab is frozen, but `/api/next/health` still answers | The problem is in the browser, not the server | Update to 26.7.24+, which removes a re-render loop and loads posters lazily |
| The app is unreachable and `/api/next/health` does not answer | The server is not responding | Send the `next-api` log from step 6 |

---

## Related

- [`docs/BRANCHING.md`](../BRANCHING.md) — release channels and which image each one builds.
- [`app/deploy/next/README.md`](../../app/deploy/next/README.md) — stack-level
  troubleshooting (PostgreSQL health, preflight checks, log commands).
- FAQ: https://discvault.eu/faq
