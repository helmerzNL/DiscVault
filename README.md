# DiscVault

DiscVault is a self-hosted physical media collection manager for 4K UHD, Blu-ray, and DVD.

DiscVault 26 is the next-generation app experience. It is available as a beta
today and will become the production channel when promoted to stable.

It includes:
- DiscVault 26 library with poster, list, and detail views
- Rich movie, container, box-set, poster, backdrop, video, and metadata pages
- PostgreSQL-backed v26 foundation with a guided migration path
- Plugin runtime for metadata, import sources, receivers, digital sources, API, and MCP
- Plex/Jellyfin digital media matching and MovieVault metadata integration
- Barcode scanning and manual entry
- Metadata lookup through configurable plugins
- Collection filtering, search, and format-aware browsing
- Backup and restore tools
- User management, groups, owner/admin tooling, and RBAC
- MemberGroups — user-created collaboration groups with invite system
- Watchlist & watch history
- Passkey authentication and recovery
- Invite-only registration — admin issues one-time passwords, open sign-up can be disabled
- Multilingual UI
- URL routing and deep links — share direct links to movies or views
- MCP endpoint integration for AI workflows
- PWA — works offline after adding to homescreen
- Push notifications — group members are notified when a new disc is added to a shared group
- User-scoped MCP access — personal API keys connect your AI assistant to your own collection only

## Live Website

- https://discvault.eu

## Docker Images

```
ghcr.io/helmerznl/discvault:latest   # production / stable
ghcr.io/helmerznl/discvault:beta     # DiscVault 26 beta
```

> Docker image references are lowercase. GitHub may show the repository name as
> `helmerzNL/DiscVault`, but Docker pulls should use
> `ghcr.io/helmerznl/discvault`.

## Unraid

- Unraid template repo: https://github.com/helmerzNL/unraid-templates
- Support thread: https://forums.unraid.net/topic/198808-support-discvault-physical-disc-collection-manager/

## Screenshots

### DiscVault 26 desktop preview

| | |
|---|---|
| <img src="screenshots/v26-desktop/DisccVault%2026%20-%2001%20-%20Library%20Posters.png" width="420"> | <img src="screenshots/v26-desktop/DiscVault%2026%20-%2002%20-%20Library%20List.png" width="420"> |
| Library posters | Library list |
| <img src="screenshots/v26-desktop/DiscVault%2026%20-%2003%20-%20Detail.png" width="420"> | <img src="screenshots/v26-desktop/DiscVault%2026%20-%2004%20-%20Detail-%20Backdrops.png" width="420"> |
| Rich detail page | Backdrops |
| <img src="screenshots/v26-desktop/DiscVault%2026%20-%2004%20-%20Watchlist%20-%20Detailed.png" width="420"> | <img src="screenshots/v26-desktop/DiscVault%2026%20-%2004%20-%20Watched%20List.png" width="420"> |
| Watchlist detail | Watched history |
| <img src="screenshots/v26-desktop/DiscVault%2026%20-%2004%20-%20Admin%20-%20RBAC.png" width="420"> | <img src="screenshots/v26-desktop/DiscVault%2026%20-%2004%20-%20Admin%20-%20Plugins.png" width="420"> |
| RBAC admin | Plugin admin |
| <img src="screenshots/v26-desktop/DiscVault%2026%20-%2004%20-%20Admin%20-%20Operations.png" width="420"> | <img src="screenshots/v26-desktop/DiscVault%2026%20-%2004%20-%20Preferences%2001.png" width="420"> |
| Operations | Preferences |

### Migration to DiscVault 26

DiscVault 26 includes a guided migration flow for existing installations. The
wizard checks your current data, confirms what can be imported, runs the
migration, and then opens the new DiscVault 26 app.

| | |
|---|---|
| <img src="screenshots/Migrate%20to%20DiscVault%2026/DiscVault%2026%20-%2001%20-%20Migration%20Start%20.png" width="420"> | <img src="screenshots/Migrate%20to%20DiscVault%2026/DiscVault%2026%20-%2002%20-%20Authenticate%20with%20current%20Passkey.png" width="420"> |
| Start migration | Authenticate with current passkey |
| <img src="screenshots/Migrate%20to%20DiscVault%2026/DiscVault%2026%20-%2003%20-%20Migration%20Wizard%2001.png" width="420"> | <img src="screenshots/Migrate%20to%20DiscVault%2026/DiscVault%2026%20-%2004%20-%20Migration%20Wizard%2002.png" width="420"> |
| Review migration wizard | Confirm migration scope |
| <img src="screenshots/Migrate%20to%20DiscVault%2026/DiscVault%2026%20-%2005%20-%20Ready%20to%20start%20migration.png" width="420"> | <img src="screenshots/Migrate%20to%20DiscVault%2026/DiscVault%2026%20-%2006%20-%20Migration%20in%20progress.png" width="420"> |
| Ready to start | Migration in progress |
| <img src="screenshots/Migrate%20to%20DiscVault%2026/DiscVault%2026%20-%2007%20-%20Migraiton%20finished.png" width="420"> | <img src="screenshots/Migrate%20to%20DiscVault%2026/DiscVault%2026%20-%2008%20-%20Start%20DiscVault%2026.png" width="420"> |
| Migration finished | Start DiscVault 26 |

### Current stable desktop

| | | |
|---|---|---|
| ![Sign in](screenshots/desktop_signin.png) | ![Collection](screenshots/desktop_collection.png) | ![Import](screenshots/desktop_import.png) |
| Sign in | Collection | Import |
| ![Backup & Restore](screenshots/desktop_backup.png) | ![User Management](screenshots/desktop_user_mgmt.png) | ![Logfiles](screenshots/desktop_log.png) |
| Backup & Restore | User Management | Logfiles |
| ![Advanced & MCP](screenshots/desktop_advanced-mcp.png) | | |
| Advanced & MCP | | |

### Mobile

| | | | |
|---|---|---|---|
| <img src="screenshots/Mobile_screenshot%20%282%29.jpeg" width="150"> | <img src="screenshots/Mobile_screenshot%20%281%29.PNG" width="150"> | <img src="screenshots/Mobile_screenshot%20%284%29.PNG" width="150"> | <img src="screenshots/Mobile_screenshot%20%285%29.PNG" width="150"> |
| Sign in | Search | Add movie | Profile |
| <img src="screenshots/Mobile_screenshot%20%286%29.PNG" width="150"> | <img src="screenshots/Mobile_screenshot%20%287%29.PNG" width="150"> | <img src="screenshots/Mobile_screenshot%20%288%29.PNG" width="150"> | <img src="screenshots/Mobile_screenshot%20%289%29.PNG" width="150"> |
| Metadata | Authentication | User management | Backup & Restore |
| <img src="screenshots/Mobile_screenshot%20%2810%29.PNG" width="150"> | <img src="screenshots/Mobile_screenshot%20%2811%29.PNG" width="150"> | <img src="screenshots/mobile_collection.jpeg" width="150"> | |
| Logfiles | Advanced | Collection | |

## Install DiscVault 26 Beta

Use the beta channel to try DiscVault 26 while it is still being finalized.
Create a backup before moving an existing library between stable and beta.

```bash
docker run -d \
  --name discvault \
  -p 6080:80 \
  -p 6090:6090 \
  -e TZ=Europe/Amsterdam \
  -e RP_ID=localhost \
  -e RP_ORIGIN=http://localhost:6080 \
  -v /mnt/user/appdata/discvault:/data \
  ghcr.io/helmerznl/discvault:beta
```

Open: `http://localhost:6080`

For a reverse-proxy production-like beta host, set passkey values to the public
hostname:

```text
RP_ID=discvault.example.com
RP_ORIGIN=https://discvault.example.com
```

## Install DiscVault 26 Production / Stable

Use the production channel for stable deployments. When DiscVault 26 is promoted
from beta to production, this is the channel to run.

```bash
docker run -d \
  --name discvault \
  -p 6080:80 \
  -p 6090:6090 \
  -e TZ=Europe/Amsterdam \
  -e RP_ID=localhost \
  -e RP_ORIGIN=http://localhost:6080 \
  -v /mnt/user/appdata/discvault:/data \
  ghcr.io/helmerznl/discvault:latest
```

Open: `http://localhost:6080`

Before updating production:

- Create a backup from the admin tools or copy the persistent data directory while the container is stopped.
- Keep the same `/data` volume mapping so posters, backdrops, uploads, users, passkeys, and settings remain available.
- Review release notes before moving between beta and production channels.

## Install the standalone MovieVault v2 plugin

DiscVault `26.4.44` and newer provide the local anonymous synchronization
bridge used by the separately released `movievault_v2` plugin. Download the
plugin ZIP and checksum from
[helmerzNL/DiscVault-Plugins](https://github.com/helmerzNL/DiscVault-Plugins),
verify the SHA-256 checksum, and extract its `movievault_v2/` root folder into
`DISCVAULT_PLUGIN_INSTALL_DIR` (normally `/data/plugins` in the persistent
volume).

Restart DiscVault or refresh its plugin registry. The plugin supplies the
standard `https://movies2.vaultstack.eu` origin and safe operational defaults
while remaining disabled. Review or override those settings, then enable the
plugin; DiscVault queues the first synchronization automatically. Normal barcode,
title, release, and box-set queries then use the derived PostgreSQL index. The
existing `movievault_26` plugin remains independently available for MovieVault
Next. Its attributed contribution connection is not used for MovieVault v2
anonymous reads.

## Repository Structure

- `app/` - Main application code (backend, frontend, mcp-server, deployment files)
- `.github/workflows/` - CI/CD workflows

The marketing website (https://discvault.eu) lives in its own repository:
[helmerzNL/DiscVault.EU](https://github.com/helmerzNL/DiscVault.EU).

## License

MIT
