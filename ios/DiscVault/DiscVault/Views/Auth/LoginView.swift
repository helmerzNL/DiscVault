import SwiftUI

struct LoginView: View {
    @EnvironmentObject private var appState: AppStateManager
    @Environment(APIClient.self) private var apiClient

    @State private var username: String = ""
    @State private var password: String = ""
    @State private var isLoading = false
    @State private var errorMessage: String? = nil
    @State private var showServerSetup = false
    @FocusState private var focusedField: Field?

    private enum Field { case username, password }

    var body: some View {
        ZStack {
            background

            ScrollView {
                VStack(spacing: 0) {
                    Spacer().frame(height: 64)

                    logoSection
                        .padding(.bottom, 48)

                    formCard
                        .padding(.horizontal, 24)

                    serverRow
                        .padding(.horizontal, 24)
                        .padding(.top, 24)

                    Spacer().frame(height: 32)
                }
            }
        }
        .sheet(isPresented: $showServerSetup) {
            NavigationStack {
                ServerSetupView(isEditMode: true)
            }
        }
        .onAppear {
            username = KeychainService.retrieve(for: KeychainService.username) ?? ""
        }
    }

    // MARK: - Sections

    private var background: some View {
        ZStack {
            Color(red: 0.04, green: 0.04, blue: 0.12).ignoresSafeArea()
            Circle()
                .fill(RadialGradient(
                    colors: [Color(red: 0.3, green: 0.1, blue: 0.8).opacity(0.3), .clear],
                    center: .center, startRadius: 0, endRadius: 280
                ))
                .frame(width: 480).offset(x: -120, y: -240)
            Circle()
                .fill(RadialGradient(
                    colors: [Color(red: 0.1, green: 0.3, blue: 0.9).opacity(0.2), .clear],
                    center: .center, startRadius: 0, endRadius: 220
                ))
                .frame(width: 360).offset(x: 160, y: 160)
        }
    }

    private var logoSection: some View {
        VStack(spacing: 16) {
            ZStack {
                Circle()
                    .fill(LinearGradient(
                        colors: [Color(red: 0.45, green: 0.2, blue: 0.95), Color(red: 0.2, green: 0.45, blue: 0.95)],
                        startPoint: .topLeading, endPoint: .bottomTrailing
                    ))
                    .frame(width: 72, height: 72)
                    .shadow(color: .purple.opacity(0.4), radius: 20)
                Image(systemName: "opticaldisc.fill")
                    .font(.system(size: 34))
                    .foregroundStyle(.white)
            }
            Text("DiscVault")
                .font(.system(size: 32, weight: .bold, design: .rounded))
                .foregroundStyle(.white)
            Text("Sign in to your account")
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.5))
        }
    }

    private var formCard: some View {
        VStack(spacing: 16) {
            // Username
            inputField(
                icon: "person.fill",
                placeholder: "Username",
                text: $username,
                isSecure: false,
                field: .username,
                nextField: .password
            )

            // Password
            inputField(
                icon: "lock.fill",
                placeholder: "Password",
                text: $password,
                isSecure: true,
                field: .password,
                nextField: nil
            )

            if let msg = errorMessage {
                HStack(spacing: 8) {
                    Image(systemName: "exclamationmark.circle.fill")
                        .foregroundStyle(.red)
                    Text(msg)
                        .font(.caption)
                        .foregroundStyle(.red.opacity(0.9))
                    Spacer()
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

    private func inputField(
        icon: String,
        placeholder: String,
        text: Binding<String>,
        isSecure: Bool,
        field: Field,
        nextField: Field?
    ) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .foregroundStyle(.white.opacity(0.45))
                .frame(width: 18)

            if isSecure {
                SecureField(placeholder, text: text)
                    .textFieldStyle(.plain)
                    .foregroundStyle(.white)
                    .tint(.white)
                    .focused($focusedField, equals: field)
                    .submitLabel(nextField == nil ? .go : .next)
                    .onSubmit {
                        if let next = nextField { focusedField = next }
                        else { Task { await attemptLogin() } }
                    }
            } else {
                TextField(placeholder, text: text)
                    .textFieldStyle(.plain)
                    .foregroundStyle(.white)
                    .tint(.white)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .focused($focusedField, equals: field)
                    .submitLabel(nextField == nil ? .go : .next)
                    .onSubmit {
                        if let next = nextField { focusedField = next }
                        else { Task { await attemptLogin() } }
                    }
            }
        }
        .padding(14)
        .background(.white.opacity(0.07))
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(focusedField == field ? .white.opacity(0.25) : .clear, lineWidth: 1)
        )
        .animation(.easeInOut(duration: 0.15), value: focusedField)
    }

    private var signInButton: some View {
        Button {
            Task { await attemptLogin() }
        } label: {
            HStack(spacing: 10) {
                if isLoading {
                    ProgressView().tint(.white).scaleEffect(0.85)
                } else {
                    Image(systemName: "arrow.right.circle.fill")
                }
                Text(isLoading ? "Signing in…" : "Sign In")
                    .font(.headline)
            }
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 16)
            .background(
                LinearGradient(
                    colors: [Color(red: 0.45, green: 0.2, blue: 0.95), Color(red: 0.2, green: 0.45, blue: 0.95)],
                    startPoint: .leading, endPoint: .trailing
                )
                .opacity(isLoading ? 0.6 : 1.0)
            )
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .shadow(color: .purple.opacity(0.3), radius: 16)
        }
        .disabled(isLoading)
        .padding(.top, 4)
    }

    private var serverRow: some View {
        Button {
            showServerSetup = true
        } label: {
            HStack(spacing: 8) {
                Image(systemName: "server.rack")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.35))
                Text(appState.serverURL.isEmpty ? "Configure server" : appState.serverURL)
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.35))
                    .lineLimit(1)
                Spacer()
                Image(systemName: "pencil")
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.25))
            }
        }
    }

    // MARK: - Actions

    private func attemptLogin() async {
        focusedField = nil
        guard !username.isEmpty, !password.isEmpty else {
            errorMessage = "Enter your username and password."
            return
        }
        isLoading = true
        errorMessage = nil
        do {
            _ = try await apiClient.login(username: username, password: password)
            KeychainService.save(username, for: KeychainService.username)
            appState.phase = .main
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }
}

#Preview {
    LoginView()
        .environmentObject(AppStateManager())
        .preferredColorScheme(.dark)
}
