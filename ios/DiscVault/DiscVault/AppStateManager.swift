import SwiftUI

enum AppPhase: Equatable {
    case welcome
    case serverSetup
    case login
    case main
}

@MainActor
final class AppStateManager: ObservableObject {
    @Published var phase: AppPhase = .welcome
    @Published var serverURL: String = ""

    let apiClient: APIClient

    init() {
        self.apiClient = APIClient()
        self.serverURL = KeychainService.retrieve(for: KeychainService.serverURL) ?? ""
        self.apiClient.baseURL = serverURL

        let hasToken = KeychainService.retrieve(for: KeychainService.accessToken) != nil
        if serverURL.isEmpty {
            phase = .welcome
        } else if hasToken {
            phase = .main
        } else {
            phase = .login
        }
    }

    func signOut() {
        apiClient.logout()
        phase = .login
    }

    func updateServerURL(_ url: String) {
        serverURL = url
        apiClient.baseURL = url
        KeychainService.save(url, for: KeychainService.serverURL)
    }
}
