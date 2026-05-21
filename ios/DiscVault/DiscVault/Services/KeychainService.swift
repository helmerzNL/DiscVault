import Foundation
import Security

// MARK: - KeychainService

/// Provides simple static helpers for storing, retrieving, and deleting
/// string values from the iOS Keychain using kSecClassGenericPassword.
final class KeychainService {

    // MARK: - Key Constants

    static let accessToken  = "discvault_access_token"
    static let refreshToken = "discvault_refresh_token"
    static let serverURL    = "discvault_server_url"
    static let username     = "discvault_username"

    // MARK: - Private Init

    private init() {}

    // MARK: - Public API

    /// Saves (or updates) a string value for the given key.
    /// - Returns: `true` on success, `false` on failure.
    @discardableResult
    static func save(_ value: String, for key: String) -> Bool {
        guard let data = value.data(using: .utf8) else { return false }

        // Delete any existing item first so we can do a clean add.
        delete(for: key)

        let query: [CFString: Any] = [
            kSecClass:           kSecClassGenericPassword,
            kSecAttrAccount:     key,
            kSecValueData:       data,
            kSecAttrAccessible:  kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        ]

        let status = SecItemAdd(query as CFDictionary, nil)
        return status == errSecSuccess
    }

    /// Retrieves the string value stored under the given key, or `nil` if
    /// no item exists or the data cannot be decoded.
    static func retrieve(for key: String) -> String? {
        let query: [CFString: Any] = [
            kSecClass:            kSecClassGenericPassword,
            kSecAttrAccount:      key,
            kSecReturnData:       true,
            kSecMatchLimit:       kSecMatchLimitOne
        ]

        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)

        guard status == errSecSuccess,
              let data = result as? Data,
              let string = String(data: data, encoding: .utf8)
        else {
            return nil
        }

        return string
    }

    /// Deletes the Keychain item stored under the given key. Does nothing if
    /// the item does not exist.
    static func delete(for key: String) {
        let query: [CFString: Any] = [
            kSecClass:        kSecClassGenericPassword,
            kSecAttrAccount:  key
        ]

        SecItemDelete(query as CFDictionary)
    }
}
