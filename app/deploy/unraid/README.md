# DiscVault Unraid Community Apps Template

This folder contains a starter Unraid CA template for the all-in-one DiscVault container.

## Files

- `discvault.xml` - Unraid Docker template XML

## Before publishing

1. Replace `YOUR_GH_ORG` in:
   - `<Repository>`
   - `<Project>`
   - `<Icon>`
2. Create your Unraid support thread and set the `<Support>` URL.
3. Ensure your image is publicly available (`ghcr.io` or Docker Hub) and tagged.
4. Keep template XML in a dedicated template repository for CA publishing.

## Notes

- This template assumes the all-in-one image listens on:
  - `80` for Web UI/API (`/api/*`) and MCP endpoint (`/mcp`)
  - `6090` for optional direct MCP access
- Persistent data is stored at `/data`.
