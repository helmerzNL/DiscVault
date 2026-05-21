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

// MARK: - Passkey Auth

struct PasskeyLoginOptionsResponse: Decodable {
    let options: PasskeyLoginOptions
}

struct PasskeyLoginOptions: Decodable {
    let challenge: String
    let rpId: String
    let allowCredentials: [PasskeyAllowedCredential]?
    let userVerification: String?
}

struct PasskeyAllowedCredential: Decodable {
    let type: String
    let id: String
}

struct PasskeyAssertionCredential: Encodable {
    let id: String
    let rawId: String
    let response: PasskeyAssertionResponse
    let type: String
    let authenticatorAttachment: String?
}

struct PasskeyAssertionResponse: Encodable {
    let authenticatorData: String
    let clientDataJSON: String
    let signature: String
    let userHandle: String?
}

struct PasskeyLoginVerificationRequest: Encodable {
    let credential: PasskeyAssertionCredential
}

struct PasskeyAuthResponse: Codable {
    let status: String?
    let token: String
    let username: String?
}

// MARK: - LoginRequest

struct LoginRequest: Encodable {
    let username: String
    let password: String
}
