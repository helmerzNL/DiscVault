import Foundation

struct AdminUser: Codable, Identifiable {
    let id: String
    let username: String
    let displayName: String?
    let role: String
    let createdAt: String?
    let credentialCount: Int

    enum CodingKeys: String, CodingKey {
        case id
        case username
        case displayName = "display_name"
        case role
        case createdAt = "created_at"
        case credentialCount = "credential_count"
    }
}

struct InviteCode: Codable, Identifiable {
    let id: Int
    let username: String
    let createdAt: String?
    let expiresAt: String?
    let usedAt: String?

    private enum CodingKeys: String, CodingKey {
        case id
        case username
        case createdAt
        case createdAtSnake = "created_at"
        case expiresAt
        case expiresAtSnake = "expires_at"
        case usedAt
        case usedAtSnake = "used_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(Int.self, forKey: .id)
        username = try container.decode(String.self, forKey: .username)
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
            ?? container.decodeIfPresent(String.self, forKey: .createdAtSnake)
        expiresAt = try container.decodeIfPresent(String.self, forKey: .expiresAt)
            ?? container.decodeIfPresent(String.self, forKey: .expiresAtSnake)
        usedAt = try container.decodeIfPresent(String.self, forKey: .usedAt)
            ?? container.decodeIfPresent(String.self, forKey: .usedAtSnake)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(username, forKey: .username)
        try container.encodeIfPresent(createdAt, forKey: .createdAtSnake)
        try container.encodeIfPresent(expiresAt, forKey: .expiresAtSnake)
        try container.encodeIfPresent(usedAt, forKey: .usedAtSnake)
    }
}

struct InviteCodeCreateResponse: Codable {
    let id: Int
    let code: String
    let username: String
    let expiresAt: String?

    private enum CodingKeys: String, CodingKey {
        case id
        case code
        case username
        case expiresAt
        case expiresAtSnake = "expires_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(Int.self, forKey: .id)
        code = try container.decode(String.self, forKey: .code)
        username = try container.decode(String.self, forKey: .username)
        expiresAt = try container.decodeIfPresent(String.self, forKey: .expiresAt)
            ?? container.decodeIfPresent(String.self, forKey: .expiresAtSnake)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(code, forKey: .code)
        try container.encode(username, forKey: .username)
        try container.encodeIfPresent(expiresAt, forKey: .expiresAtSnake)
    }
}

struct DigitalLibrarySource: Decodable, Identifiable {
    let id: Int
    let name: String
    let type: String
    let baseUrl: String
    let libraryIds: String?
    let lastSynced: String?
    let itemCount: Int?
    let enabled: Bool?
    let ownerId: Int?

    private enum CodingKeys: String, CodingKey {
        case id
        case name
        case type
        case baseUrl
        case baseUrlSnake = "base_url"
        case libraryIds
        case libraryIdsSnake = "library_ids"
        case lastSynced
        case lastSyncedSnake = "last_synced"
        case itemCount
        case itemCountSnake = "item_count"
        case enabled
        case ownerId
        case ownerIdSnake = "owner_id"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? container.decode(Int.self, forKey: .id)) ?? 0
        name = (try? container.decode(String.self, forKey: .name)) ?? ""
        type = (try? container.decode(String.self, forKey: .type)) ?? ""
        baseUrl = (try? container.decode(String.self, forKey: .baseUrl))
            ?? (try? container.decode(String.self, forKey: .baseUrlSnake))
            ?? ""
        libraryIds = (try? container.decodeIfPresent(String.self, forKey: .libraryIds))
            ?? (try? container.decodeIfPresent(String.self, forKey: .libraryIdsSnake))
        lastSynced = (try? container.decodeIfPresent(String.self, forKey: .lastSynced))
            ?? (try? container.decodeIfPresent(String.self, forKey: .lastSyncedSnake))
        itemCount = (try? container.decodeIfPresent(Int.self, forKey: .itemCount))
            ?? (try? container.decodeIfPresent(Int.self, forKey: .itemCountSnake))
        enabled = try container.decodeBoolIfPresent(forKey: .enabled)
        ownerId = (try? container.decodeIfPresent(Int.self, forKey: .ownerId))
            ?? (try? container.decodeIfPresent(Int.self, forKey: .ownerIdSnake))
    }
}

struct CollectionCompareResponse: Decodable {
    let physicalAndDigital: [PhysicalDigitalMatch]
}

struct PhysicalDigitalMatch: Decodable {
    let movie: Movie?
    let digitalMatches: [DigitalMovieMatch]
}

struct DigitalMovieMatch: Decodable {
    let sourceName: String?
    let sourceType: String?
    let title: String?
    let year: String?
}

struct DigitalSourceTestResponse: Codable {
    let ok: Bool
    let message: String?

    private enum CodingKeys: String, CodingKey {
        case ok
        case message
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ok = (try container.decodeBoolIfPresent(forKey: .ok)) ?? false
        message = try container.decodeIfPresent(String.self, forKey: .message)
    }
}

struct DigitalSourceSyncStatus: Decodable {
    let status: String?
    let progress: Int?
    let total: Int?
    let error: String?
    let lastSynced: String?
    let itemCount: Int?

    private enum CodingKeys: String, CodingKey {
        case status
        case progress
        case total
        case error
        case lastSynced
        case lastSyncedSnake = "last_synced"
        case itemCount
        case itemCountSnake = "item_count"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        status = try container.decodeIfPresent(String.self, forKey: .status)
        progress = try container.decodeFlexibleIntIfPresent(forKey: .progress)
        total = try container.decodeFlexibleIntIfPresent(forKey: .total)
        error = try container.decodeIfPresent(String.self, forKey: .error)
        lastSynced = (try? container.decodeIfPresent(String.self, forKey: .lastSynced))
            ?? (try? container.decodeIfPresent(String.self, forKey: .lastSyncedSnake))
        itemCount = (try? container.decodeFlexibleIntIfPresent(forKey: .itemCount))
            ?? (try? container.decodeFlexibleIntIfPresent(forKey: .itemCountSnake))
    }
}

struct MetadataSourceSettings: Decodable {
    let omdbEnabled: Bool
    let omdbKeySet: Bool
    let tmdbEnabled: Bool
    let tmdbKeySet: Bool
    let blurayScrapeEnabled: Bool
    let bluraydiscdeScrapeEnabled: Bool

    private enum CodingKeys: String, CodingKey {
        case omdbEnabled
        case omdbEnabledSnake = "omdb_enabled"
        case omdbKeySet
        case omdbKeySetSnake = "omdb_key_set"
        case tmdbEnabled
        case tmdbEnabledSnake = "tmdb_enabled"
        case tmdbKeySet
        case tmdbKeySetSnake = "tmdb_key_set"
        case blurayScrapeEnabled
        case blurayScrapeEnabledSnake = "bluray_scrape_enabled"
        case bluraydiscdeScrapeEnabled
        case bluraydiscdeScrapeEnabledSnake = "bluraydiscde_scrape_enabled"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        omdbEnabled = (try? container.decodeBoolIfPresent(forKey: .omdbEnabled))
            ?? (try? container.decodeBoolIfPresent(forKey: .omdbEnabledSnake))
            ?? false
        omdbKeySet = (try? container.decodeBoolIfPresent(forKey: .omdbKeySet))
            ?? (try? container.decodeBoolIfPresent(forKey: .omdbKeySetSnake))
            ?? false
        tmdbEnabled = (try? container.decodeBoolIfPresent(forKey: .tmdbEnabled))
            ?? (try? container.decodeBoolIfPresent(forKey: .tmdbEnabledSnake))
            ?? false
        tmdbKeySet = (try? container.decodeBoolIfPresent(forKey: .tmdbKeySet))
            ?? (try? container.decodeBoolIfPresent(forKey: .tmdbKeySetSnake))
            ?? false
        blurayScrapeEnabled = (try? container.decodeBoolIfPresent(forKey: .blurayScrapeEnabled))
            ?? (try? container.decodeBoolIfPresent(forKey: .blurayScrapeEnabledSnake))
            ?? false
        bluraydiscdeScrapeEnabled = (try? container.decodeBoolIfPresent(forKey: .bluraydiscdeScrapeEnabled))
            ?? (try? container.decodeBoolIfPresent(forKey: .bluraydiscdeScrapeEnabledSnake))
            ?? false
    }
}

struct MetadataAPIKeySettings: Decodable {
    let tmdbKeySet: Bool
    let omdbKeySet: Bool
    let tmdbKeyMasked: String?
    let omdbKeyMasked: String?
    let tmdbKey: String?
    let omdbKey: String?

    private enum CodingKeys: String, CodingKey {
        case tmdbKeySet
        case tmdbKeySetSnake = "tmdb_key_set"
        case omdbKeySet
        case omdbKeySetSnake = "omdb_key_set"
        case tmdbKeyMasked
        case tmdbKeyMaskedSnake = "tmdb_key_masked"
        case omdbKeyMasked
        case omdbKeyMaskedSnake = "omdb_key_masked"
        case tmdbKey
        case tmdbKeySnake = "tmdb_key"
        case omdbKey
        case omdbKeySnake = "omdb_key"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        tmdbKeySet = (try? container.decodeBoolIfPresent(forKey: .tmdbKeySet))
            ?? (try? container.decodeBoolIfPresent(forKey: .tmdbKeySetSnake))
            ?? false
        omdbKeySet = (try? container.decodeBoolIfPresent(forKey: .omdbKeySet))
            ?? (try? container.decodeBoolIfPresent(forKey: .omdbKeySetSnake))
            ?? false
        tmdbKeyMasked = (try? container.decodeIfPresent(String.self, forKey: .tmdbKeyMasked))
            ?? (try? container.decodeIfPresent(String.self, forKey: .tmdbKeyMaskedSnake))
        omdbKeyMasked = (try? container.decodeIfPresent(String.self, forKey: .omdbKeyMasked))
            ?? (try? container.decodeIfPresent(String.self, forKey: .omdbKeyMaskedSnake))
        tmdbKey = (try? container.decodeIfPresent(String.self, forKey: .tmdbKey))
            ?? (try? container.decodeIfPresent(String.self, forKey: .tmdbKeySnake))
        omdbKey = (try? container.decodeIfPresent(String.self, forKey: .omdbKey))
            ?? (try? container.decodeIfPresent(String.self, forKey: .omdbKeySnake))
    }
}

struct AdminLogEntry: Codable, Identifiable {
    let id: Int
    let timestamp: String?
    let level: String?
    let category: String?
    let message: String?
    let detail: String?
}

struct BackupSummary: Codable, Identifiable {
    var id: String { name }

    let name: String
    let size: Int
    let hasDb: Bool?
    let hasJson: Bool?
    let posterCount: Int?
    let movieCount: Int?
    let format: String?
    let created: String?

    enum CodingKeys: String, CodingKey {
        case name
        case size
        case hasDb = "has_db"
        case hasJson = "has_json"
        case posterCount = "poster_count"
        case movieCount = "movie_count"
        case format
        case created
    }
}

struct BackupCreateResponse: Codable {
    let status: String?
    let name: String
    let size: Int
}

struct BooleanSettingResponse: Decodable {
    let authEnabled: Bool?
    let registrationEnabled: Bool?
    let debugEnabled: Bool?
    let mcpEnabled: Bool?

    private enum CodingKeys: String, CodingKey {
        case authEnabled
        case enabled
        case registrationEnabled
        case debugEnabled
        case mcpEnabled
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        authEnabled = try container.decodeBoolIfPresent(forKey: .authEnabled)
            ?? container.decodeBoolIfPresent(forKey: .enabled)
        registrationEnabled = try container.decodeBoolIfPresent(forKey: .registrationEnabled)
            ?? container.decodeBoolIfPresent(forKey: .enabled)
        debugEnabled = try container.decodeBoolIfPresent(forKey: .debugEnabled)
        mcpEnabled = try container.decodeBoolIfPresent(forKey: .mcpEnabled)
    }
}

private extension KeyedDecodingContainer {
    func decodeBoolIfPresent(forKey key: Key) throws -> Bool? {
        if let boolValue = try? decodeIfPresent(Bool.self, forKey: key) {
            return boolValue
        }
        if let intValue = try? decodeIfPresent(Int.self, forKey: key) {
            return intValue != 0
        }
        if let stringValue = try? decodeIfPresent(String.self, forKey: key) {
            return ["1", "true", "yes", "on"].contains(stringValue.lowercased())
        }
        return nil
    }

    func decodeFlexibleIntIfPresent(forKey key: Key) throws -> Int? {
        if let intValue = try? decodeIfPresent(Int.self, forKey: key) {
            return intValue
        }
        if let stringValue = try? decodeIfPresent(String.self, forKey: key) {
            return Int(stringValue)
        }
        return nil
    }
}

struct RoleUpdateResponse: Codable {
    let status: String?
    let role: String?
}

struct GroupCreateResponse: Codable {
    let status: String?
    let id: Int?
}
