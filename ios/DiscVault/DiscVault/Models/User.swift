import Foundation

// MARK: - User

struct User: Codable, Identifiable {
    let id: String
    let username: String
    let displayName: String?
    let role: String?
    let avatar: String?
    let avatarURL: String?
    let firstName: String?
    let lastName: String?
    let createdAt: String?
    let authenticated: Bool?
    let permissions: [String]?
    let customRoles: [String]?

    enum CodingKeys: String, CodingKey {
        case id
        case username
        case displayName = "display_name"
        case role
        case avatar
        case avatarURL
        case avatarUrl
        case avatarURLSnake = "avatar_url"
        case firstName
        case firstNameSnake = "first_name"
        case lastName
        case lastNameSnake = "last_name"
        case createdAt
        case createdAtSnake = "created_at"
        case authenticated
        case permissions
        case customRoles
        case customRolesSnake = "custom_roles"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if let stringID = try? container.decode(String.self, forKey: .id) {
            id = stringID
        } else {
            id = String(try container.decode(Int.self, forKey: .id))
        }
        username = try container.decode(String.self, forKey: .username)
        displayName = try container.decodeIfPresent(String.self, forKey: .displayName)
        role = try container.decodeIfPresent(String.self, forKey: .role)
        avatar = try container.decodeIfPresent(String.self, forKey: .avatar)
        avatarURL = (try? container.decodeIfPresent(String.self, forKey: .avatarURL))
            ?? (try? container.decodeIfPresent(String.self, forKey: .avatarUrl))
            ?? (try? container.decodeIfPresent(String.self, forKey: .avatarURLSnake))
        firstName = (try? container.decodeIfPresent(String.self, forKey: .firstName))
            ?? (try? container.decodeIfPresent(String.self, forKey: .firstNameSnake))
        lastName = (try? container.decodeIfPresent(String.self, forKey: .lastName))
            ?? (try? container.decodeIfPresent(String.self, forKey: .lastNameSnake))
        createdAt = (try? container.decodeIfPresent(String.self, forKey: .createdAt))
            ?? (try? container.decodeIfPresent(String.self, forKey: .createdAtSnake))
        authenticated = try container.decodeIfPresent(Bool.self, forKey: .authenticated)
        permissions = try container.decodeIfPresent([String].self, forKey: .permissions)
        customRoles = (try? container.decodeIfPresent([String].self, forKey: .customRoles))
            ?? (try? container.decodeIfPresent([String].self, forKey: .customRolesSnake))
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(username, forKey: .username)
        try container.encodeIfPresent(displayName, forKey: .displayName)
        try container.encodeIfPresent(role, forKey: .role)
        try container.encodeIfPresent(avatar, forKey: .avatar)
        try container.encodeIfPresent(avatarURL, forKey: .avatarURLSnake)
        try container.encodeIfPresent(firstName, forKey: .firstNameSnake)
        try container.encodeIfPresent(lastName, forKey: .lastNameSnake)
        try container.encodeIfPresent(createdAt, forKey: .createdAtSnake)
        try container.encodeIfPresent(authenticated, forKey: .authenticated)
        try container.encodeIfPresent(permissions, forKey: .permissions)
        try container.encodeIfPresent(customRoles, forKey: .customRolesSnake)
    }
}

// MARK: - AuthTokens

struct AuthTokens: Codable {
    let accessToken: String
    let refreshToken: String?

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
    }
}

// MARK: - Mobile Auth

struct MobileAuthExchangeRequest: Encodable {
    let code: String
}

struct MobileAuthResponse: Codable {
    let status: String?
    let token: String
    let username: String?
}

// MARK: - Profile

struct ProfileUpdateRequest: Encodable {
    let username: String
    let firstName: String
    let lastName: String

    enum CodingKeys: String, CodingKey {
        case username
        case firstName = "first_name"
        case lastName = "last_name"
    }
}

struct AvatarUpdateResponse: Decodable {
    let status: String?
    let avatarURL: String?

    enum CodingKeys: String, CodingKey {
        case status
        case avatarURL
        case avatarUrl
        case avatarURLSnake = "avatar_url"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        status = try container.decodeIfPresent(String.self, forKey: .status)
        avatarURL = (try? container.decodeIfPresent(String.self, forKey: .avatarURL))
            ?? (try? container.decodeIfPresent(String.self, forKey: .avatarUrl))
            ?? (try? container.decodeIfPresent(String.self, forKey: .avatarURLSnake))
    }
}

struct ProfileUpdateResponse: Decodable {
    let status: String?
    let token: String?
    let id: String?
    let username: String
    let displayName: String?
    let role: String?
    let firstName: String?
    let lastName: String?
    let avatar: String?
    let avatarURL: String?

    enum CodingKeys: String, CodingKey {
        case status
        case token
        case id
        case username
        case displayName
        case displayNameSnake = "display_name"
        case role
        case firstName
        case firstNameSnake = "first_name"
        case lastName
        case lastNameSnake = "last_name"
        case avatar
        case avatarURL
        case avatarUrl
        case avatarURLSnake = "avatar_url"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        status = try container.decodeIfPresent(String.self, forKey: .status)
        token = try container.decodeIfPresent(String.self, forKey: .token)
        id = try container.decodeFlexibleStringIfPresent(forKey: .id)
        username = (try? container.decode(String.self, forKey: .username)) ?? ""
        displayName = (try? container.decodeIfPresent(String.self, forKey: .displayName))
            ?? (try? container.decodeIfPresent(String.self, forKey: .displayNameSnake))
        role = try container.decodeIfPresent(String.self, forKey: .role)
        firstName = (try? container.decodeIfPresent(String.self, forKey: .firstName))
            ?? (try? container.decodeIfPresent(String.self, forKey: .firstNameSnake))
        lastName = (try? container.decodeIfPresent(String.self, forKey: .lastName))
            ?? (try? container.decodeIfPresent(String.self, forKey: .lastNameSnake))
        avatar = try container.decodeIfPresent(String.self, forKey: .avatar)
        avatarURL = (try? container.decodeIfPresent(String.self, forKey: .avatarURL))
            ?? (try? container.decodeIfPresent(String.self, forKey: .avatarUrl))
            ?? (try? container.decodeIfPresent(String.self, forKey: .avatarURLSnake))
    }
}

struct AuthStatus: Decodable {
    let authEnabled: Bool
    let hasUsers: Bool
    let hasCredentials: Bool
    let rpID: String?
    let userCount: Int
    let groupCount: Int
    let role: String?
    let registrationEnabled: Bool

    enum CodingKeys: String, CodingKey {
        case authEnabled
        case enabled
        case hasUsers
        case hasCredentials
        case rpId
        case userCount
        case groupCount
        case role
        case registrationEnabled
    }

    init(
        authEnabled: Bool,
        hasUsers: Bool,
        hasCredentials: Bool,
        rpID: String?,
        userCount: Int,
        groupCount: Int,
        role: String?,
        registrationEnabled: Bool
    ) {
        self.authEnabled = authEnabled
        self.hasUsers = hasUsers
        self.hasCredentials = hasCredentials
        self.rpID = rpID
        self.userCount = userCount
        self.groupCount = groupCount
        self.role = role
        self.registrationEnabled = registrationEnabled
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        authEnabled = try container.decodeBoolIfPresent(forKey: .authEnabled)
            ?? container.decodeBoolIfPresent(forKey: .enabled)
            ?? false
        hasUsers = try container.decodeBoolIfPresent(forKey: .hasUsers) ?? false
        hasCredentials = try container.decodeBoolIfPresent(forKey: .hasCredentials) ?? false
        rpID = try container.decodeIfPresent(String.self, forKey: .rpId)
        userCount = (try? container.decodeIfPresent(Int.self, forKey: .userCount)) ?? 0
        groupCount = (try? container.decodeIfPresent(Int.self, forKey: .groupCount)) ?? 0
        role = try container.decodeIfPresent(String.self, forKey: .role)
        registrationEnabled = try container.decodeBoolIfPresent(forKey: .registrationEnabled) ?? true
    }
}

private extension KeyedDecodingContainer {
    func decodeFlexibleStringIfPresent(forKey key: Key) throws -> String? {
        if let stringValue = try? decodeIfPresent(String.self, forKey: key) {
            return stringValue
        }
        if let intValue = try? decodeIfPresent(Int.self, forKey: key) {
            return String(intValue)
        }
        return nil
    }

    func decodeBoolIfPresent(forKey key: Key) throws -> Bool? {
        if let boolValue = try? decodeIfPresent(Bool.self, forKey: key) {
            return boolValue
        }
        if let stringValue = try? decodeIfPresent(String.self, forKey: key) {
            return ["1", "true", "yes", "on"].contains(stringValue.lowercased())
        }
        return nil
    }
}

struct PasskeyCredential: Decodable, Identifiable {
    let id: String
    let credentialName: String?
    let createdAt: String?
    let signCount: Int?
    let username: String?

    enum CodingKeys: String, CodingKey {
        case id
        case credentialName = "credential_name"
        case createdAt = "created_at"
        case signCount = "sign_count"
        case username
    }
}

struct APIKeySummary: Decodable, Identifiable {
    let id: Int
    let label: String?
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case id
        case label
        case createdAt = "created_at"
    }
}

struct APIKeyCreateRequest: Encodable {
    let label: String
}

struct APIKeyCreateResponse: Decodable {
    let id: Int
    let label: String?
    let createdAt: String?
    let key: String

    enum CodingKeys: String, CodingKey {
        case id
        case label
        case createdAt = "created_at"
        case key
    }
}

struct MCPLogEntry: Decodable, Identifiable {
    let id: Int
    let timestamp: String?
    let level: String?
    let message: String?
    let detail: String?
}

// MARK: - LoginRequest

struct LoginRequest: Encodable {
    let username: String
    let password: String
}
