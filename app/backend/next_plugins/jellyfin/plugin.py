def health_check(context=None):
    return {"status": "available", "message": "Jellyfin plugin module loaded."}


def discover_library(payload=None, context=None):
    context = context or {}
    return {
        "status": "planned",
        "connector": "jellyfin",
        "dryRun": bool((payload or {}).get("dryRun")),
        "settingsConfigured": bool(context.get("settingsConfigured")),
        "secretsConfigured": bool(context.get("secretsConfigured")),
        "items": [],
        "message": "Jellyfin library discovery execution path is ready; real Jellyfin API sync is the next connector slice.",
    }


def sync_library(payload=None, context=None):
    context = context or {}
    return {
        "status": "planned",
        "connector": "jellyfin",
        "dryRun": bool((payload or {}).get("dryRun")),
        "settingsConfigured": bool(context.get("settingsConfigured")),
        "secretsConfigured": bool(context.get("secretsConfigured")),
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "message": "Jellyfin sync job execution path is ready; real library reconciliation is the next connector slice.",
    }
