import Foundation
import SwiftUI

enum APIError: LocalizedError {
    case invalidURL
    case unauthorized
    case serverError(String)
    case decodingError(Error)
    case networkError(Error)

    var errorDescription: String? {
        switch self {
        case .invalidURL: return "Invalid server URL."
        case .unauthorized: return "Session expired. Please sign in again."
        case .serverError(let msg): return msg
        case .decodingError(let e): return "Data error: \(e.localizedDescription)"
        case .networkError(let e): return e.localizedDescription
        }
    }
}

@Observable
final class APIClient {
    var baseURL: String = ""
    var isAuthenticated: Bool = false
    private var accessToken: String?
    private var refreshTokenValue: String?

    init() {
        loadStoredCredentials()
    }

    func loadStoredCredentials() {
        accessToken = KeychainService.retrieve(for: KeychainService.accessToken)
        refreshTokenValue = KeychainService.retrieve(for: KeychainService.refreshToken)
        baseURL = KeychainService.retrieve(for: KeychainService.serverURL) ?? ""
        isAuthenticated = accessToken != nil
    }

    func logout() {
        accessToken = nil
        refreshTokenValue = nil
        isAuthenticated = false
        KeychainService.delete(for: KeychainService.accessToken)
        KeychainService.delete(for: KeychainService.refreshToken)
    }

    // MARK: - Auth

    func login(username: String, password: String) async throws -> AuthTokens {
        struct LoginBody: Encodable { let username: String; let password: String }
        let tokens: AuthTokens = try await request(
            "/api/auth/login",
            method: "POST",
            body: LoginBody(username: username, password: password),
            skipAuth: true
        )
        accessToken = tokens.accessToken
        refreshTokenValue = tokens.refreshToken
        isAuthenticated = true
        KeychainService.save(tokens.accessToken, for: KeychainService.accessToken)
        if let rt = tokens.refreshToken {
            KeychainService.save(rt, for: KeychainService.refreshToken)
        }
        return tokens
    }

    func refreshToken() async throws {
        guard let rt = refreshTokenValue else { throw APIError.unauthorized }
        struct RefreshBody: Encodable { let refreshToken: String }
        let tokens: AuthTokens = try await request(
            "/api/auth/refresh",
            method: "POST",
            body: RefreshBody(refreshToken: rt),
            skipAuth: true
        )
        accessToken = tokens.accessToken
        KeychainService.save(tokens.accessToken, for: KeychainService.accessToken)
    }

    // MARK: - User

    func getCurrentUser() async throws -> User {
        try await request("/api/auth/me")
    }

    // MARK: - Movies

    func getMovies(search: String? = nil, format: String? = nil, wanted: Bool? = nil) async throws -> [Movie] {
        var params: [String: String] = [:]
        if let s = search, !s.isEmpty { params["q"] = s }
        if let f = format { params["format"] = f }
        if let w = wanted { params["wanted"] = w ? "true" : "false" }
        return try await request("/api/movies" + queryString(params))
    }

    func getMovie(id: Int) async throws -> Movie {
        try await request("/api/movies/\(id)")
    }

    func deleteMovie(id: Int) async throws {
        let _: EmptyResponse = try await request("/api/movies/\(id)", method: "DELETE")
    }

    func lookupBarcode(barcode: String) async throws -> Movie {
        try await request("/api/movies/lookup?barcode=\(barcode)")
    }

    func addMovieByBarcode(barcode: String) async throws -> Movie {
        struct Body: Encodable { let barcode: String }
        return try await request("/api/movies", method: "POST", body: Body(barcode: barcode))
    }

    func searchMovies(query: String) async throws -> [Movie] {
        try await request("/api/movies/search?q=\(query.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? query)")
    }

    // MARK: - Watchlist

    func getWatchlist() async throws -> [WatchlistItem] {
        try await request("/api/watchlist")
    }

    func addToWatchlist(movieId: Int) async throws {
        struct Body: Encodable { let movieId: Int }
        let _: EmptyResponse = try await request("/api/watchlist", method: "POST", body: Body(movieId: movieId))
    }

    func removeFromWatchlist(movieId: Int) async throws {
        let _: EmptyResponse = try await request("/api/watchlist/\(movieId)", method: "DELETE")
    }

    // MARK: - Watch History

    func getWatchHistory() async throws -> [WatchlistItem] {
        try await request("/api/watch-history")
    }

    func addToWatchHistory(movieId: Int) async throws {
        struct Body: Encodable { let movieId: Int }
        let _: EmptyResponse = try await request("/api/watch-history", method: "POST", body: Body(movieId: movieId))
    }

    // MARK: - Groups

    func getGroups() async throws -> [Group] {
        try await request("/api/groups")
    }

    // MARK: - Stats

    func getStats() async throws -> [String: Int] {
        try await request("/api/stats")
    }

    // MARK: - Poster URL helper

    func posterURL(for path: String?) -> URL? {
        guard let path, !path.isEmpty else { return nil }
        if path.hasPrefix("http") { return URL(string: path) }
        return URL(string: "\(baseURL)/api/posters/\(path)")
    }

    // MARK: - Request Core

    private func request<T: Decodable>(
        _ endpoint: String,
        method: String = "GET",
        body: (any Encodable)? = nil,
        skipAuth: Bool = false,
        isRetry: Bool = false
    ) async throws -> T {
        guard !baseURL.isEmpty, let url = URL(string: baseURL + endpoint) else {
            throw APIError.invalidURL
        }

        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Accept")

        if let body {
            req.httpBody = try JSONEncoder().encode(body)
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        if !skipAuth, let token = accessToken {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await URLSession.shared.data(for: req)
        } catch {
            throw APIError.networkError(error)
        }

        guard let http = response as? HTTPURLResponse else {
            throw APIError.serverError("No HTTP response.")
        }

        if http.statusCode == 401 && !skipAuth && !isRetry {
            try await refreshToken()
            return try await request(endpoint, method: method, body: body, skipAuth: skipAuth, isRetry: true)
        }

        if http.statusCode == 401 {
            isAuthenticated = false
            throw APIError.unauthorized
        }

        if http.statusCode >= 400 {
            struct ErrorBody: Decodable { let error: String? }
            let msg = (try? JSONDecoder().decode(ErrorBody.self, from: data))?.error ?? "HTTP \(http.statusCode)"
            throw APIError.serverError(msg)
        }

        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError.decodingError(error)
        }
    }

    private func queryString(_ params: [String: String]) -> String {
        guard !params.isEmpty else { return "" }
        let items = params.map { "\($0.key)=\($0.value.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? $0.value)" }
        return "?" + items.joined(separator: "&")
    }
}

private struct EmptyResponse: Decodable {}
