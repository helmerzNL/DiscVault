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

@MainActor
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

    func mobileAuthStartURL(callbackScheme: String) throws -> URL {
        guard var components = URLComponents(string: baseURL), !baseURL.isEmpty else {
            throw APIError.invalidURL
        }

        let basePath = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        components.path = basePath.isEmpty ? "/api/auth/mobile/start" : "/\(basePath)/api/auth/mobile/start"
        components.queryItems = [
            URLQueryItem(name: "callback_scheme", value: callbackScheme)
        ]

        guard let url = components.url else {
            throw APIError.invalidURL
        }
        return url
    }

    func exchangeMobileAuthCode(_ code: String) async throws -> MobileAuthResponse {
        let response: MobileAuthResponse = try await request(
            "/api/auth/mobile/exchange",
            method: "POST",
            body: MobileAuthExchangeRequest(code: code),
            skipAuth: true
        )
        storeAuthToken(response.token)
        return response
    }

    private func storeAuthToken(_ token: String) {
        accessToken = token
        refreshTokenValue = nil
        isAuthenticated = true
        KeychainService.save(token, for: KeychainService.accessToken)
        KeychainService.delete(for: KeychainService.refreshToken)
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

    func updateProfile(username: String, firstName: String, lastName: String) async throws -> ProfileUpdateResponse {
        let response: ProfileUpdateResponse = try await request(
            "/api/auth/profile",
            method: "PUT",
            body: ProfileUpdateRequest(username: username, firstName: firstName, lastName: lastName)
        )
        if let token = response.token {
            storeAuthToken(token)
        }
        return response
    }

    func uploadProfileAvatar(data: Data, filename: String, mimeType: String) async throws -> AvatarUpdateResponse {
        try await uploadMultipart(
            "/api/auth/profile/avatar",
            fieldName: "avatar",
            fileName: filename,
            mimeType: mimeType,
            data: data
        )
    }

    func deleteProfileAvatar() async throws {
        let _: EmptyResponse = try await request("/api/auth/profile/avatar", method: "DELETE")
    }

    func getAuthStatus() async throws -> AuthStatus {
        try await request("/api/auth/status", skipAuth: true)
    }

    func getPasskeyCredentials() async throws -> [PasskeyCredential] {
        try await request("/api/auth/credentials")
    }

    func deletePasskeyCredential(id: String) async throws {
        let encodedID = id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? id
        let _: EmptyResponse = try await request("/api/auth/credentials/\(encodedID)", method: "DELETE")
    }

    func getUserPreferences() async throws -> [String: String] {
        try await request("/api/auth/preferences")
    }

    func updateUserPreferences(_ preferences: [String: String]) async throws {
        let _: EmptyResponse = try await request("/api/auth/preferences", method: "PUT", body: preferences)
    }

    func getAPIKeys() async throws -> [APIKeySummary] {
        try await request("/api/user/api-keys")
    }

    func createAPIKey(label: String) async throws -> APIKeyCreateResponse {
        try await request("/api/user/api-keys", method: "POST", body: APIKeyCreateRequest(label: label))
    }

    func deleteAPIKey(id: Int) async throws {
        let _: EmptyResponse = try await request("/api/user/api-keys/\(id)", method: "DELETE")
    }

    func getMCPLogs(limit: Int = 50) async throws -> [MCPLogEntry] {
        try await request("/api/user/mcp-logs?limit=\(limit)")
    }

    func avatarURL(for path: String?) -> URL? {
        guard let path, !path.isEmpty else { return nil }
        if path.hasPrefix("http") { return URL(string: path) }
        return URL(string: baseURL + path)
    }

    // MARK: - Admin

    func setAuthEnabled(_ enabled: Bool) async throws -> BooleanSettingResponse {
        try await request("/api/auth/toggle", method: "POST", body: AuthToggleRequest(enabled: enabled))
    }

    func setRegistrationEnabled(_ enabled: Bool) async throws -> BooleanSettingResponse {
        try await request("/api/settings/registration", method: "POST", body: RegistrationToggleRequest(registrationEnabled: enabled))
    }

    func getAdminUsers() async throws -> [AdminUser] {
        try await request("/api/auth/users")
    }

    func deleteAdminUser(id: String) async throws {
        let encodedID = id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? id
        let _: EmptyResponse = try await request("/api/auth/users/\(encodedID)", method: "DELETE")
    }

    func updateAdminUserRole(id: String, role: String) async throws -> RoleUpdateResponse {
        let encodedID = id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? id
        return try await request("/api/auth/users/\(encodedID)/role", method: "PUT", body: RoleChangeRequest(role: role))
    }

    func resetAdminUserPasskey(id: String) async throws {
        let encodedID = id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? id
        let _: EmptyResponse = try await request("/api/auth/users/\(encodedID)/reset-passkey", method: "POST", body: EmptyRequestBody())
    }

    func createInviteCode(username: String) async throws -> InviteCodeCreateResponse {
        try await request("/api/auth/invite", method: "POST", body: InviteCreateRequest(username: username))
    }

    func getInviteCodes() async throws -> [InviteCode] {
        try await request("/api/auth/invite")
    }

    func deleteInviteCode(id: Int) async throws {
        let _: EmptyResponse = try await request("/api/auth/invite/\(id)", method: "DELETE")
    }

    func createGroup(name: String) async throws -> GroupCreateResponse {
        try await request("/api/groups", method: "POST", body: GroupCreateRequest(name: name))
    }

    func updateGroup(id: Int, name: String) async throws {
        let _: EmptyResponse = try await request("/api/groups/\(id)", method: "PUT", body: GroupUpdateRequest(name: name))
    }

    func deleteGroup(id: Int) async throws {
        let _: EmptyResponse = try await request("/api/groups/\(id)", method: "DELETE")
    }

    func getGroupMembers(groupId: Int) async throws -> [GroupMember] {
        try await request("/api/groups/\(groupId)/members")
    }

    func removeGroupMember(groupId: Int, memberId: String) async throws {
        let encodedID = memberId.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? memberId
        let _: EmptyResponse = try await request("/api/groups/\(groupId)/members/\(encodedID)", method: "DELETE")
    }

    func inviteUserToGroup(groupId: Int, username: String) async throws {
        let _: EmptyResponse = try await request("/api/groups/\(groupId)/invite", method: "POST", body: GroupInviteRequest(username: username))
    }

    func getAdminLogs(level: String? = nil, category: String? = nil, limit: Int = 200) async throws -> [AdminLogEntry] {
        var params: [String: String] = ["limit": "\(limit)"]
        if let level, !level.isEmpty { params["level"] = level }
        if let category, !category.isEmpty { params["category"] = category }
        return try await request("/api/logs" + queryString(params))
    }

    func clearAdminLogs() async throws {
        let _: EmptyResponse = try await request("/api/logs", method: "DELETE")
    }

    func createBackup() async throws -> BackupCreateResponse {
        try await request("/api/settings/backup", method: "POST", body: EmptyRequestBody())
    }

    func getBackups() async throws -> [BackupSummary] {
        try await request("/api/settings/backups")
    }

    func deleteBackup(name: String) async throws {
        let encodedName = name.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? name
        let _: EmptyResponse = try await request("/api/settings/backup/\(encodedName)", method: "DELETE")
    }

    func getDebugSetting() async throws -> BooleanSettingResponse {
        try await request("/api/settings/debug")
    }

    func setDebugEnabled(_ enabled: Bool) async throws -> BooleanSettingResponse {
        try await request("/api/settings/debug", method: "POST", body: DebugToggleRequest(debugEnabled: enabled))
    }

    func getMCPSetting() async throws -> BooleanSettingResponse {
        try await request("/api/settings/mcp")
    }

    func setMCPEnabled(_ enabled: Bool) async throws -> BooleanSettingResponse {
        try await request("/api/settings/mcp", method: "POST", body: MCPToggleRequest(mcpEnabled: enabled))
    }

    func getMetadataSourceSettings() async throws -> MetadataSourceSettings {
        try await request("/api/settings/sources")
    }

    func setMetadataSourceSettings(_ settings: MetadataSourceSettingsUpdate) async throws -> MetadataSourceSettingsUpdate {
        try await request("/api/settings/sources", method: "POST", body: settings)
    }

    func getMetadataAPIKeys() async throws -> MetadataAPIKeySettings {
        try await request("/api/settings/api-keys")
    }

    func updateMetadataAPIKeys(tmdbKey: String? = nil, omdbKey: String? = nil) async throws {
        let _: EmptyResponse = try await request("/api/settings/api-keys", method: "POST", body: MetadataAPIKeysUpdate(tmdbKey: tmdbKey, omdbKey: omdbKey))
    }

    func getDigitalSources() async throws -> [DigitalLibrarySource] {
        try await request("/api/digital-sources")
    }

    func createDigitalSource(name: String, type: String, baseURL: String, token: String) async throws -> DigitalLibrarySource {
        try await request("/api/digital-sources", method: "POST", body: DigitalSourceRequest(name: name, type: type, baseUrl: baseURL, token: token))
    }

    func deleteDigitalSource(id: Int) async throws {
        let _: EmptyResponse = try await request("/api/digital-sources/\(id)", method: "DELETE")
    }

    func syncDigitalSource(id: Int) async throws -> DigitalSourceSyncStatus {
        try await request("/api/digital-sources/\(id)/sync", method: "POST", body: EmptyRequestBody())
    }

    func getDigitalSourceSyncStatus(id: Int) async throws -> DigitalSourceSyncStatus {
        try await request("/api/digital-sources/\(id)/sync-status")
    }

    func testDigitalSource(id: Int) async throws -> DigitalSourceTestResponse {
        try await request("/api/digital-sources/\(id)/test", method: "POST", body: EmptyRequestBody())
    }

    func getCollectionCompare() async throws -> CollectionCompareResponse {
        try await request("/api/collection/compare")
    }

    // MARK: - Movies

    func getMovies(search: String? = nil, format: String? = nil, wanted: Bool? = nil, groupEditions: Bool = false) async throws -> [Movie] {
        var params: [String: String] = [:]
        if let s = search, !s.isEmpty { params["q"] = s }
        if let f = format { params["format"] = f }
        if let w = wanted { params["wanted"] = w ? "true" : "false" }
        if groupEditions { params["group_editions"] = "true" }
        return try await request("/api/movies" + queryString(params))
    }

    func getMovie(id: Int) async throws -> Movie {
        try await request("/api/movies/\(id)")
    }

    func updateMovie(id: Int, draft: MovieEditDraft) async throws -> Movie {
        let _: EmptyResponse = try await request("/api/movies/\(id)", method: "PUT", body: draft)
        return try await getMovie(id: id)
    }

    func getDiscCollection(id: Int) async throws -> DiscCollectionDetail {
        try await request("/api/collections/\(id)")
    }

    func getEditionGroup(id: Int) async throws -> EditionGroupDetail {
        try await request("/api/edition-groups/\(id)")
    }

    func createDiscCollection(title: String) async throws -> DiscCollection {
        try await request("/api/collections", method: "POST", body: DiscCollectionCreateRequest(title: title))
    }

    func createEditionGroup(title: String, kind: BulkContainerKind) async throws -> EditionGroup {
        try await request("/api/edition-groups", method: "POST", body: EditionGroupCreateRequest(title: title, kind: kind))
    }

    func updateDiscCollection(id: Int, title: String, description: String?) async throws -> DiscCollectionDetail {
        try await request(
            "/api/collections/\(id)",
            method: "PUT",
            body: DiscCollectionUpdateRequest(title: title, description: description)
        )
    }

    func updateDiscCollectionBackdrop(id: Int, backdrop: String) async throws -> DiscCollectionDetail {
        try await request(
            "/api/collections/\(id)",
            method: "PUT",
            body: ContainerBackdropUpdateRequest(backdrop: backdrop)
        )
    }

    func updateEditionGroup(
        id: Int,
        title: String,
        description: String?,
        badgeLabel: String?,
        parentGroupId: Int?,
        collectionId: Int?
    ) async throws -> EditionGroupDetail {
        try await request(
            "/api/edition-groups/\(id)",
            method: "PUT",
            body: EditionGroupUpdateRequest(
                title: title,
                description: description,
                badgeLabel: badgeLabel,
                parentGroupId: parentGroupId,
                collectionId: collectionId
            )
        )
    }

    func updateEditionGroupBackdrop(id: Int, backdrop: String) async throws -> EditionGroupDetail {
        try await request(
            "/api/edition-groups/\(id)",
            method: "PUT",
            body: ContainerBackdropUpdateRequest(backdrop: backdrop)
        )
    }

    func assignMovieToCollection(id: Int, collectionId: Int) async throws {
        let _: EmptyResponse = try await request(
            "/api/movies/\(id)",
            method: "PUT",
            body: MovieCollectionAssignment(collectionId: collectionId)
        )
    }

    func assignEditionGroupToCollection(id: Int, collectionId: Int) async throws {
        let _: EditionGroupDetail = try await request(
            "/api/edition-groups/\(id)",
            method: "PUT",
            body: EditionGroupCollectionAssignment(collectionId: collectionId)
        )
    }

    /// Loads cast & crew (with person details) for a movie.
    func getMovieCast(id: Int) async throws -> [CastMember] {
        try await request("/api/movies/\(id)/cast")
    }

    /// Loads full person details, including biography and movies-in-collection.
    func getPerson(id: Int) async throws -> Person {
        try await request("/api/people/\(id)")
    }

    /// Loads extended actor/director filmography when the user's detailed actor page preference is enabled.
    func getPersonFilmography(id: Int, language: String) async throws -> PersonFilmographyResponse {
        let encodedLanguage = language.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? language
        return try await request("/api/people/\(id)/filmography?language=\(encodedLanguage)")
    }

    func deleteMovie(id: Int) async throws {
        let _: EmptyResponse = try await request("/api/movies/\(id)", method: "DELETE")
    }

    /// Sets a specific backdrop URL as the active hero image for a movie.
    func setMovieBackdrop(id: Int, url: String) async throws {
        struct Body: Encodable { let backdrop: String }
        let _: EmptyResponse = try await request("/api/movies/\(id)", method: "PUT", body: Body(backdrop: url))
    }

    func updateMovieGroupIDs(id: Int, groupIDs: [Int]) async throws {
        let _: EmptyResponse = try await request("/api/movies/\(id)/groups", method: "PUT", body: MovieGroupAssignment(groupIDs: groupIDs))
    }

    func addMoviesToGroups(movieIDs: [Int], groupIDs: [Int]) async throws {
        let _: EmptyResponse = try await request(
            "/api/movies/bulk/groups",
            method: "PUT",
            body: MovieBulkGroupAssignment(movieIDs: movieIDs, groupIDs: groupIDs)
        )
    }

    func assignMovie(id: Int, to target: BulkContainerTarget) async throws {
        let _: EmptyResponse = try await request("/api/movies/\(id)", method: "PUT", body: MovieContainerAssignment(target: target))
    }

    func lookupBarcode(barcode: String) async throws -> Movie {
        try await request("/api/movies/lookup?barcode=\(barcode)")
    }

    func addMovieByBarcode(barcode: String) async throws -> Movie {
        let lookup = try await lookupBarcodeForAdd(barcode)
        guard lookup.status == "found", let lookupMovie = lookup.movie else {
            throw APIError.serverError(lookup.error ?? "No movie found for barcode \(barcode).")
        }
        var draft = AddMovieDraft(lookupMovie: lookupMovie, barcode: barcode)
        if draft.format.isEmpty, let detectedFormat = lookup.detectedFormat, !detectedFormat.isEmpty {
            draft.format = detectedFormat
        }
        return try await createMovie(draft)
    }

    func lookupBarcodeForAdd(_ barcode: String) async throws -> LookupResponse {
        let encoded = barcode.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? barcode
        return try await request("/api/lookup/\(encoded)")
    }

    func lookupTitleForAdd(_ title: String) async throws -> LookupResponse {
        let encoded = title.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? title
        return try await request("/api/search_title?q=\(encoded)")
    }

    func createMovie(_ draft: AddMovieDraft) async throws -> Movie {
        try await request("/api/movies", method: "POST", body: draft)
    }

    func searchMovies(query: String) async throws -> [Movie] {
        try await request("/api/movies/search?q=\(query.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? query)")
    }

    // MARK: - Watchlist

    func getWatchlist() async throws -> [WatchlistItem] {
        try await request("/api/watchlist")
    }

    func addToWatchlist(movieId: Int) async throws {
        let _: EmptyResponse = try await request("/api/watchlist/\(movieId)", method: "POST", body: EmptyRequestBody())
    }

    func removeFromWatchlist(movieId: Int) async throws {
        let _: EmptyResponse = try await request("/api/watchlist/\(movieId)", method: "DELETE")
    }

    // MARK: - Watch History

    func getWatchHistory() async throws -> [WatchlistItem] {
        try await request("/api/watch-history")
    }

    /// Marks a movie as watched on a given date (YYYY-MM-DD). Uses the same endpoint as the web app.
    func markWatched(movieId: Int, watchedAt: String) async throws {
        struct Body: Encodable {
            let watchedAt: String
            enum CodingKeys: String, CodingKey { case watchedAt = "watched_at" }
        }
        struct Response: Decodable {
            let status: String?
            let watchedAt: String?
            enum CodingKeys: String, CodingKey {
                case status
                case watchedAt = "watched_at"
            }
        }
        let _: Response = try await request("/api/watched/\(movieId)", method: "POST", body: Body(watchedAt: watchedAt))
    }

    /// Refreshes a single movie's metadata from all configured sources (TMDb / OMDb / IMDb scrape).
    /// Returns the freshly fetched movie.
    func refreshMovieMetadata(id: Int) async throws -> Movie {
        struct Body: Encodable {
            let fetchPosters: Bool
            let fetchPeople: Bool
            let fetchPeopleImages: Bool
            let fetchCastPhotos: Bool
            enum CodingKeys: String, CodingKey {
                case fetchPosters = "fetch_posters"
                case fetchPeople = "fetch_people"
                case fetchPeopleImages = "fetch_people_images"
                case fetchCastPhotos = "fetch_cast_photos"
            }
        }
        struct SyncResponse: Decodable {
            let status: String?
        }
        let _: SyncResponse = try await request(
            "/api/movies/\(id)/sync-all",
            method: "POST",
            body: Body(
                fetchPosters: true,
                fetchPeople: true,
                fetchPeopleImages: true,
                fetchCastPhotos: true
            )
        )
        return try await getMovie(id: id)
    }

    // MARK: - Groups

    func getGroups() async throws -> [Group] {
        try await request("/api/groups")
    }

    func getEditionGroups() async throws -> [EditionGroup] {
        try await request("/api/edition-groups")
    }

    func updateEditionGroupTitle(id: Int, title: String) async throws -> EditionGroup {
        try await request("/api/edition-groups/\(id)", method: "PUT", body: GroupTitleUpdate(title: title))
    }

    func deleteEditionGroup(id: Int) async throws {
        let _: EmptyResponse = try await request("/api/edition-groups/\(id)", method: "DELETE")
    }

    func getDiscCollections() async throws -> [DiscCollection] {
        try await request("/api/collections")
    }

    func updateDiscCollectionTitle(id: Int, title: String) async throws -> DiscCollection {
        try await request("/api/collections/\(id)", method: "PUT", body: GroupTitleUpdate(title: title))
    }

    func deleteDiscCollection(id: Int) async throws {
        let _: EmptyResponse = try await request("/api/collections/\(id)", method: "DELETE")
    }

    // MARK: - Stats

    func getStats() async throws -> [String: Int] {
        try await request("/api/stats")
    }

    func getDatabaseStats() async throws -> DatabaseStats {
        try await request("/api/settings/db-stats")
    }

    func getServerHealth() async throws -> ServerHealth {
        try await request("/api/health", skipAuth: true)
    }

    // MARK: - Poster URL helper

    func posterURL(for path: String?) -> URL? {
        guard let path else { return nil }
        let trimmed = path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed != "N/A" else { return nil }
        if trimmed.hasPrefix("http") { return URL(string: trimmed) }
        let base = baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if trimmed.hasPrefix("/api/") || trimmed.hasPrefix("/uploads/") || trimmed.hasPrefix("/images/") {
            return URL(string: base + trimmed)
        }
        let fileName = URL(fileURLWithPath: trimmed).lastPathComponent
        let encoded = fileName.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? fileName
        return URL(string: "\(base)/api/posters/\(encoded)")
    }

    func personImageURL(for path: String?) -> URL? {
        guard let path, !path.isEmpty else { return nil }
        if path.hasPrefix("http") { return URL(string: path) }
        if path.hasPrefix("/api/") || path.hasPrefix("/uploads/") || path.hasPrefix("/images/") {
            return URL(string: baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + path)
        }
        if path.hasPrefix("/") {
            return URL(string: "https://image.tmdb.org/t/p/w185\(path)")
        }

        let encoded = path.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? path
        var components = URLComponents(string: baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/api/profiles/\(encoded)")
        components?.queryItems = [URLQueryItem(name: "v", value: path)]
        return components?.url
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
        } catch where isCancellation(error) {
            throw CancellationError()
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
            let msg = (try? JSONDecoder().decode(APIErrorBody.self, from: data))?.error ?? "HTTP \(http.statusCode)"
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

    private func uploadMultipart<T: Decodable>(
        _ endpoint: String,
        fieldName: String,
        fileName: String,
        mimeType: String,
        data: Data
    ) async throws -> T {
        guard !baseURL.isEmpty, let url = URL(string: baseURL + endpoint) else {
            throw APIError.invalidURL
        }

        let boundary = "Boundary-\(UUID().uuidString)"
        var body = Data()
        body.appendString("--\(boundary)\r\n")
        body.appendString("Content-Disposition: form-data; name=\"\(fieldName)\"; filename=\"\(fileName)\"\r\n")
        body.appendString("Content-Type: \(mimeType)\r\n\r\n")
        body.append(data)
        body.appendString("\r\n--\(boundary)--\r\n")

        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.httpBody = body
        if let token = accessToken {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        let (responseData, response): (Data, URLResponse)
        do {
            (responseData, response) = try await URLSession.shared.data(for: req)
        } catch where isCancellation(error) {
            throw CancellationError()
        } catch {
            throw APIError.networkError(error)
        }

        guard let http = response as? HTTPURLResponse else {
            throw APIError.serverError("No HTTP response.")
        }
        if http.statusCode >= 400 {
            let msg = (try? JSONDecoder().decode(APIErrorBody.self, from: responseData))?.error ?? "HTTP \(http.statusCode)"
            throw APIError.serverError(msg)
        }

        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        do {
            return try decoder.decode(T.self, from: responseData)
        } catch {
            throw APIError.decodingError(error)
        }
    }

    private func queryString(_ params: [String: String]) -> String {
        guard !params.isEmpty else { return "" }
        let items = params.map { "\($0.key)=\($0.value.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? $0.value)" }
        return "?" + items.joined(separator: "&")
    }

    private func isCancellation(_ error: Error) -> Bool {
        if error is CancellationError {
            return true
        }

        let nsError = error as NSError
        return nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled
    }
}

private extension Data {
    mutating func appendString(_ string: String) {
        if let data = string.data(using: .utf8) {
            append(data)
        }
    }
}

private struct APIErrorBody: Decodable {
    let error: String?
}

private struct GroupTitleUpdate: Encodable {
    let title: String
}

private struct MovieGroupAssignment: Encodable {
    let groupIDs: [Int]

    enum CodingKeys: String, CodingKey {
        case groupIDs = "group_ids"
    }
}

private struct MovieBulkGroupAssignment: Encodable {
    let movieIDs: [Int]
    let groupIDs: [Int]

    enum CodingKeys: String, CodingKey {
        case movieIDs = "movie_ids"
        case groupIDs = "group_ids"
    }
}

private struct MovieContainerAssignment: Encodable {
    let editionGroupID: Int?
    let collectionID: Int?

    init(target: BulkContainerTarget) {
        switch target.kind {
        case .vault, .boxset:
            editionGroupID = target.rawID
            collectionID = nil
        case .collection:
            editionGroupID = nil
            collectionID = target.rawID
        }
    }

    enum CodingKeys: String, CodingKey {
        case editionGroupID = "edition_group_id"
        case collectionID = "collection_id"
    }
}

private struct DiscCollectionUpdateRequest: Encodable {
    let title: String
    let description: String?

    enum CodingKeys: String, CodingKey {
        case title
        case description
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(title, forKey: .title)
        try container.encode(description, forKey: .description)
    }
}

private struct DiscCollectionCreateRequest: Encodable {
    let title: String
}

private struct EditionGroupCreateRequest: Encodable {
    let title: String
    let kind: BulkContainerKind

    enum CodingKeys: String, CodingKey {
        case title
        case groupType = "group_type"
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(title, forKey: .title)
        try container.encode(kind == .boxset ? "boxset" : "vault", forKey: .groupType)
    }
}

private struct EditionGroupUpdateRequest: Encodable {
    let title: String
    let description: String?
    let badgeLabel: String?
    let parentGroupId: Int?
    let collectionId: Int?

    enum CodingKeys: String, CodingKey {
        case title
        case description
        case badgeLabel = "badge_label"
        case parentGroupId = "parent_group_id"
        case collectionId = "collection_id"
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(title, forKey: .title)
        try container.encode(description, forKey: .description)
        try container.encode(badgeLabel, forKey: .badgeLabel)
        try container.encode(parentGroupId, forKey: .parentGroupId)
        try container.encode(collectionId, forKey: .collectionId)
    }
}

private struct ContainerBackdropUpdateRequest: Encodable {
    let backdrop: String
}

private struct MovieCollectionAssignment: Encodable {
    let collectionId: Int

    enum CodingKeys: String, CodingKey {
        case collectionId = "collection_id"
    }
}

private struct EditionGroupCollectionAssignment: Encodable {
    let collectionId: Int

    enum CodingKeys: String, CodingKey {
        case collectionId = "collection_id"
    }
}

private struct AuthToggleRequest: Encodable {
    let enabled: Bool
}

private struct RegistrationToggleRequest: Encodable {
    let registrationEnabled: Bool

    enum CodingKeys: String, CodingKey {
        case registrationEnabled = "registration_enabled"
    }
}

private struct RoleChangeRequest: Encodable {
    let role: String
}

private struct InviteCreateRequest: Encodable {
    let username: String
}

private struct GroupCreateRequest: Encodable {
    let name: String
}

private struct GroupUpdateRequest: Encodable {
    let name: String
}

private struct GroupInviteRequest: Encodable {
    let username: String
}

private struct DebugToggleRequest: Encodable {
    let debugEnabled: Bool

    enum CodingKeys: String, CodingKey {
        case debugEnabled = "debug_enabled"
    }
}

private struct MCPToggleRequest: Encodable {
    let mcpEnabled: Bool

    enum CodingKeys: String, CodingKey {
        case mcpEnabled = "mcp_enabled"
    }
}

struct MetadataSourceSettingsUpdate: Codable {
    let omdbEnabled: Bool
    let tmdbEnabled: Bool
    let blurayScrapeEnabled: Bool
    let bluraydiscdeScrapeEnabled: Bool

    enum CodingKeys: String, CodingKey {
        case omdbEnabled = "omdb_enabled"
        case tmdbEnabled = "tmdb_enabled"
        case blurayScrapeEnabled = "bluray_scrape_enabled"
        case bluraydiscdeScrapeEnabled = "bluraydiscde_scrape_enabled"
    }
}

private struct MetadataAPIKeysUpdate: Encodable {
    let tmdbKey: String?
    let omdbKey: String?

    enum CodingKeys: String, CodingKey {
        case tmdbKey = "tmdb_key"
        case omdbKey = "omdb_key"
    }
}

private struct DigitalSourceRequest: Encodable {
    let name: String
    let type: String
    let baseUrl: String
    let token: String

    enum CodingKeys: String, CodingKey {
        case name
        case type
        case baseUrl = "base_url"
        case token
    }
}

private struct EmptyRequestBody: Encodable {}
private struct EmptyResponse: Decodable {}
