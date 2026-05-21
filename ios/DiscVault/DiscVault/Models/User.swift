import Foundation

// MARK: - User

struct User: Codable, Identifiable {
    let id: Int
    let username: String
    let displayName: String?
    let role: String?
    let avatar: String?

    enum CodingKeys: String, CodingKey {
        case id
        case username
        case displayName = "display_name"
        case role
        case avatar
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

// MARK: - LoginRequest

struct LoginRequest: Encodable {
    let username: String
    let password: String
}
