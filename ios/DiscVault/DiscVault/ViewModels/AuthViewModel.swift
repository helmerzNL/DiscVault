import SwiftUI
import AuthenticationServices

@MainActor
@Observable
final class AuthViewModel: NSObject {
    var isLoading = false
    var errorMessage: String? = nil
    var currentUser: User? = nil

    private let apiClient: APIClient
    private let appState: AppStateManager

    init(apiClient: APIClient, appState: AppStateManager) {
        self.apiClient = apiClient
        self.appState = appState
    }

    func login(username: String, password: String) async {
        guard !username.isEmpty, !password.isEmpty else {
            errorMessage = "Please enter your username and password."
            return
        }
        isLoading = true
        errorMessage = nil
        do {
            _ = try await apiClient.login(username: username, password: password)
            currentUser = try await apiClient.getCurrentUser()
            appState.phase = .main
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    func logout() {
        apiClient.logout()
        currentUser = nil
        appState.phase = .login
    }

    func checkAuthStatus() async {
        guard apiClient.isAuthenticated else { return }
        do {
            currentUser = try await apiClient.getCurrentUser()
            appState.phase = .main
        } catch {
            apiClient.logout()
        }
    }
}
