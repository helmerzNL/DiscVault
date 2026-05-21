import SwiftUI

struct LoginView: View {
    @EnvironmentObject private var appState: AppStateManager
    @Environment(APIClient.self) private var apiClient

    @State private var passkeyAuthenticator = PasskeyAuthenticator()
    @State private var isLoading = false
    @State private var errorMessage: String? = nil
    @State private var showServerSetup = false

    var body: some View {
        ZStack {
            background

            VStack(spacing: 0) {
                Spacer(minLength: 64)

                logoSection
                    .padding(.bottom, 44)

                passkeyCard
                    .padding(.horizontal, 24)

                serverRow
                    .padding(.horizontal, 24)
                    .padding(.top, 24)

                Spacer(minLength: 40)
            }
        }
        .sheet(isPresented: $showServerSetup) {
            NavigationStack {
                ServerSetupView(isEditMode: true)
            }
        }
    }

    // MARK: - Sections

    private var background: some View {
        LinearGradient(
            colors: [
                Color(red: 0.04, green: 0.04, blue: 0.08),
                Color(red: 0.07, green: 0.07, blue: 0.12)
            ],
            startPoint: .top,
            endPoint: .bottom
        )
        .ignoresSafeArea()
    }

    private var logoSection: some View {
        VStack(spacing: 18) {
            Image("DiscVaultLogo")
                .resizable()
                .interpolation(.high)
                .frame(width: 88, height: 88)
                .clipShape(RoundedRectangle(cornerRadius: 22))
                .shadow(color: Color(red: 0.91, green: 0.77, blue: 0.28).opacity(0.28), radius: 24)

            VStack(spacing: 8) {
                Text("DiscVault")
                    .font(.system(size: 34, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)

                Text("Sign in with your passkey")
                    .font(.subheadline)
                    .foregroundStyle(.white.opacity(0.56))
            }
        }
    }

    private var passkeyCard: some View {
        VStack(spacing: 18) {
            Image(systemName: "person.badge.key.fill")
                .font(.system(size: 30))
                .foregroundStyle(Color(red: 0.91, green: 0.77, blue: 0.28))
                .frame(width: 56, height: 56)
                .background(.white.opacity(0.07))
                .clipShape(RoundedRectangle(cornerRadius: 14))

            Text("Use the passkey registered with your DiscVault account.")
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.64))
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            if let msg = errorMessage {
                HStack(spacing: 8) {
                    Image(systemName: "exclamationmark.circle.fill")
                        .foregroundStyle(.red)
                    Text(msg)
                        .font(.caption)
                        .foregroundStyle(.red.opacity(0.9))
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 4)
            }

            signInButton
        }
        .padding(24)
        .background(.white.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 20))
        .overlay(
            RoundedRectangle(cornerRadius: 20)
                .strokeBorder(.white.opacity(0.08), lineWidth: 1)
        )
    }

    private var signInButton: some View {
        Button {
            Task { await attemptPasskeyLogin() }
        } label: {
            HStack(spacing: 10) {
                if isLoading {
                    ProgressView().tint(.black).scaleEffect(0.85)
                } else {
                    Image(systemName: "key.fill")
                }

                Text(isLoading ? "Waiting for passkey" : "Sign in with Passkey")
                    .font(.headline)
            }
            .foregroundStyle(.black)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 16)
            .background(Color(red: 0.91, green: 0.77, blue: 0.28).opacity(isLoading ? 0.65 : 1.0))
            .clipShape(RoundedRectangle(cornerRadius: 14))
        }
        .disabled(isLoading || appState.serverURL.isEmpty)
        .padding(.top, 2)
    }

    private var serverRow: some View {
        Button {
            showServerSetup = true
        } label: {
            HStack(spacing: 8) {
                Image(systemName: "server.rack")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.38))
                Text(appState.serverURL.isEmpty ? "Configure server before signing in" : appState.serverURL)
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.38))
                    .lineLimit(1)
                Spacer()
                Image(systemName: "pencil")
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.28))
            }
        }
    }

    // MARK: - Actions

    private func attemptPasskeyLogin() async {
        guard !appState.serverURL.isEmpty else {
            errorMessage = "Configure your DiscVault server first."
            return
        }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let options = try await apiClient.getPasskeyLoginOptions()
            let credential = try await passkeyAuthenticator.requestAssertion(options: options)
            let response = try await apiClient.verifyPasskeyLogin(credential: credential)
            if let username = response.username {
                KeychainService.save(username, for: KeychainService.username)
            }
            appState.phase = .main
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

#Preview {
    let appState = AppStateManager()
    LoginView()
        .environmentObject(appState)
        .environment(appState.apiClient)
        .preferredColorScheme(.dark)
}
