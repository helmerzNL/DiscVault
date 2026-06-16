def health_check(context):
    return {
        "status": "ok",
        "service": "api",
        "builtIn": True,
        "basePath": "/api/next/api/v1",
        "tokenPrefix": "dvapi_",
        "permissions": [
            "api.read",
            "api.write",
            "api.tokens.manage",
            "metadata.search",
            "collection.add",
            "collection.import",
        ],
    }
