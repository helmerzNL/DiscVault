# DiscVault

DiscVault is a self-hosted physical media collection manager for 4K UHD, Blu-ray, and DVD.

It includes:
- Barcode scanning and manual entry
- Metadata lookup (OMDb + TMDb fallback)
- Collection filtering and search
- Backup and restore tools
- User management and groups
- MemberGroups — user-created collaboration groups with invite system
- Watchlist & watch history
- Passkey authentication and recovery
- Invite-only registration — admin issues one-time passwords, open sign-up can be disabled
- Multilingual UI (English, Dutch, French, German, Spanish)
- URL routing and deep links — share direct links to movies or views
- MCP endpoint integration for AI workflows
- PWA — works offline after adding to homescreen

## Live Website

- https://discvault.eu

## Docker Image

```
ghcr.io/helmerzNL/DiscVault:latest   # stable
ghcr.io/helmerzNL/DiscVault:beta     # beta (new features)
```

## Unraid

- Unraid template repo: https://github.com/helmerzNL/unraid-templates
- Support thread: https://forums.unraid.net/topic/198808-support-discvault-physical-disc-collection-manager/

## Screenshots

### Desktop

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

## Quick Start (Docker)

```bash
docker run -d \
  --name discvault \
  -p 6080:80 \
  -p 6090:6090 \
  -e TZ=Europe/Amsterdam \
  -e RP_ID=localhost \
  -e RP_ORIGIN=http://localhost:6080 \
  -v /mnt/user/appdata/discvault:/data \
  ghcr.io/helmerzNL/DiscVault:latest
```

Open: `http://localhost:6080`

## Repository Structure

- `app/` - Main application code (backend, frontend, mcp-server, deployment files)
- `website/` - GitHub Pages marketing site
- `.github/workflows/` - CI/CD workflows

## License

MIT
