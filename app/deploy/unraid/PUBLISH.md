# DiscVault — Unraid Community Apps Publish Checklist

Complete these steps once before submitting to Community Applications.

---

## 1. Rotate secrets (CRITICAL before any public push)

Your `.env` has been visible in development. Rotate these before going public:

- [ ] OMDb API key → https://www.omdbapi.com/apikey.aspx
- [ ] TMDb API key → https://www.themoviedb.org/settings/api
- [ ] Generate new `JWT_SECRET`: `openssl rand -base64 48`
- [ ] Generate new `MCP_API_KEY`: `openssl rand -base64 32`
- [ ] Update production `.env` only — never commit `.env`

---

## 2. GitHub repository

- [ ] Verify `.gitignore` includes `.env` and `.env.*` (already done)
- [ ] Enable 2FA on your GitHub account (required by Unraid CA)
- [ ] Create repo / ensure it's public

```bash
cd C:\Git\DiscVault
git init           # skip if already a git repo
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/helmerzNL/DiscVault.git
git push -u origin main
```

---

## 3. Org name

Already set to `helmerzNL/DiscVault` in:

| File | Field |
|------|-------|
| `app/deploy/unraid/discvault.xml` | `<Repository>`, `<Project>`, `<Icon>` |
| `.github/workflows/docker-publish.yml` | `IMAGE_NAME` env var |

---

## 4. Create a separate template repository

Unraid CA requires templates in their own repo:

```bash
# Create a new repo: github.com/helmerzNL/unraid-templates
mkdir unraid-templates
cd unraid-templates
git init
mkdir DiscVault
cp C:\Git\DiscVault\app\deploy\unraid\discvault.xml DiscVault/
git add .
git commit -m "Add DiscVault template"
git remote add origin https://github.com/helmerzNL/unraid-templates.git
git push -u origin main
```

Templates must be in the `main` or `master` branch.

---

## 5. Host the icon

The `<Icon>` URL must be a stable HTTPS URL.
Already set to GitHub raw content in the template:

```
https://raw.githubusercontent.com/helmerzNL/DiscVault/main/app/frontend/favicon-192.png
```

Update `discvault.xml` if you change the repo name or path.

---

## 6. Publish your first release / trigger image build

```bash
cd C:\Git\DiscVault
git tag v1.0.0
git push origin v1.0.0
```

The GitHub Actions workflow will then:
- Build multi-arch image (amd64 + arm64)
- Push to `ghcr.io/helmerzNL/DiscVault:1.0.0`, `:latest`, `:stable`

Wait for the action to complete, then verify:
```
docker pull ghcr.io/helmerzNL/DiscVault:latest
docker run --rm ghcr.io/helmerzNL/DiscVault:latest curl -s http://localhost/api/health
```

---

## 7. Create a support thread on Unraid forums

- Post in **Docker Containers** section or ask a moderator to move it
- URL format: `https://forums.unraid.net/topic/XXXXX-discvault/`
- Update `<Support>` in `discvault.xml` with the real URL
- Push the change to your template repo

---

## 8. Submit to Community Applications

Fill out the CA submission form (official Unraid process):
https://form.asana.com/?k=qtIUrf5ydiXvXzPI57BiJw&d=714739274360802

You'll need:
- GitHub URL of your **template repository** (not the app repo)
- Your support thread URL

After approval, CA refreshes every 2 hours and your app will be visible.

---

## 9. (Optional) Make the image package public on GHCR

After first push, go to:
`https://github.com/users/YOUR_ORG/packages/container/discvault/settings`

Set visibility to **Public** so Unraid users can pull without authentication.

---

## Quick reference: update flow after this

```bash
# After making code changes:
git tag v1.0.1
git push origin v1.0.1
# GitHub Actions builds and pushes the new image automatically.
# Unraid users will see the update via the "Check for Updates" button.
```
