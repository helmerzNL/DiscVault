import SwiftUI

@main
struct DiscVaultApp: App {
    @StateObject private var appState = AppStateManager()
    @State private var languageManager = AppLanguageManager()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(appState)
                .environment(appState.apiClient)
                .environment(languageManager)
                .environment(\.locale, languageManager.locale)
                .preferredColorScheme(.dark)
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var appState: AppStateManager

    var body: some View {
        ZStack {
            switch appState.phase {
            case .welcome:
                WelcomeView()
                    .transition(.opacity)
            case .serverSetup:
                NavigationStack {
                    ServerSetupView()
                }
                .transition(.opacity)
            case .login:
                LoginView()
                    .transition(.opacity)
            case .main:
                MainTabView()
                    .transition(.opacity)
            }
        }
        .animation(.easeInOut(duration: 0.35), value: appState.phase)
    }
}
