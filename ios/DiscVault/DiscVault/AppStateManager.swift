import SwiftUI
import Combine

enum AppPhase {
    case welcome       // First launch — no server URL stored
    case serverSetup   // Has been to welcome but needs to set/fix server URL
    case login         // Has server URL, not authenticated
    case main          // Authenticated and ready
}

@Observable
final class AppStateManager: ObservableObject {

    var phase: AppPhase = .welcome
    var serverURL: String = ""

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
