import AuthenticationServices
import SafariServices
import SwiftUI

struct LoginView: View {
    @EnvironmentObject private var appState: AppStateManager
    @Environment(APIClient.self) private var apiClient
    @Environment(\.webAuthenticationSession) private var webAuthenticationSession

    @State private var isLoading = false
    @State private var errorMessage: String? = nil
    @State private var showServerSetup = false
    @State private var showInviteRegistration = false

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
        .sheet(isPresented: $showInviteRegistration) {
            if let url = inviteRegistrationURL {
                LoginSafariView(url: url)
                    .ignoresSafeArea()
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

            Text("Continue to your DiscVault server to sign in with your passkey.")
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
            inviteRegistrationButton
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

                Text(isLoading ? "Opening sign in" : "Sign in with Passkey")
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

    private var inviteRegistrationButton: some View {
        Button {
            showInviteRegistration = true
        } label: {
            HStack(spacing: 8) {
                Image(systemName: "person.badge.plus")
                Text("Register with invite code")
                    .font(.subheadline.weight(.semibold))
            }
            .foregroundStyle(.white.opacity(0.78))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 12)
            .background(.white.opacity(0.07))
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
        .disabled(appState.serverURL.isEmpty)
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

    private var inviteRegistrationURL: URL? {
        guard var components = URLComponents(string: appState.serverURL), !appState.serverURL.isEmpty else {
            return nil
        }
        components.path = "/"
        return components.url
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
            let callbackScheme = "discvault"
            let loginURL = try apiClient.mobileAuthStartURL(callbackScheme: callbackScheme)
            let callbackURL = try await webAuthenticationSession.authenticate(
                using: loginURL,
                callbackURLScheme: callbackScheme,
                preferredBrowserSession: .shared
            )
            let code = try mobileAuthCode(from: callbackURL)
            let response = try await apiClient.exchangeMobileAuthCode(code)
            if let username = response.username {
                KeychainService.save(username, for: KeychainService.username)
            }
            appState.phase = .main
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func mobileAuthCode(from callbackURL: URL) throws -> String {
        guard let components = URLComponents(url: callbackURL, resolvingAgainstBaseURL: false) else {
            throw APIError.invalidURL
        }

        if let error = components.queryItems?.first(where: { $0.name == "error" })?.value {
            throw APIError.serverError(error)
        }

        guard let code = components.queryItems?.first(where: { $0.name == "code" })?.value, !code.isEmpty else {
            throw APIError.serverError("The server did not return a mobile sign-in code.")
        }

        return code
    }
}

private struct LoginSafariView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        let controller = SFSafariViewController(url: url)
        controller.dismissButtonStyle = .done
        controller.preferredBarTintColor = UIColor(red: 0.06, green: 0.06, blue: 0.14, alpha: 1.0)
        controller.preferredControlTintColor = UIColor(red: 0.91, green: 0.77, blue: 0.28, alpha: 1.0)
        return controller
    }

    func updateUIViewController(_ uiViewController: SFSafariViewController, context: Context) {}
}

#Preview {
    let appState = AppStateManager()
    LoginView()
        .environmentObject(appState)
        .environment(appState.apiClient)
        .preferredColorScheme(.dark)
}
