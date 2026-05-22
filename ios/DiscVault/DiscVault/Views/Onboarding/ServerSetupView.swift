import SwiftUI

struct ServerSetupView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var appState: AppStateManager

    @State private var serverURL: String = ""
    @State private var connectionState: ConnectionState = .idle
    @State private var showLoginSheet = false

    private enum ConnectionState {
        case idle
        case testing
        case success(version: String)
        case failure(message: String)
    }

    var isEditMode: Bool = false

    var body: some View {
        ZStack {
            background

            ScrollView {
                VStack(spacing: 32) {
                    if !isEditMode {
                        headerSection
                    }

                    serverURLSection

                    if case .success = connectionState {
                        successBanner
                    }

                    if case .failure(let msg) = connectionState {
                        errorBanner(msg)
                    }

                    actionButtons

                    helpSection
                }
                .padding(.horizontal, 24)
                .padding(.top, isEditMode ? 16 : 48)
                .padding(.bottom, 32)
            }
        }
        .navigationTitle(isEditMode ? "Server URL" : "")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if isEditMode {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                        .foregroundStyle(.white)
                }
            }
        }
        .onAppear {
            serverURL = appState.serverURL
        }
    }

    private var isTesting: Bool {
        if case .testing = connectionState { return true }
        return false
    }

    private var isSuccess: Bool {
        if case .success = connectionState { return true }
        return false
    }

    // MARK: - Sections

    private var background: some View {
        ZStack {
            Color(red: 0.04, green: 0.04, blue: 0.12).ignoresSafeArea()
            Circle()
                .fill(
                    RadialGradient(
                        colors: [Color(red: 0.2, green: 0.45, blue: 0.9).opacity(0.25), .clear],
                        center: .center, startRadius: 0, endRadius: 300
                    )
                )
                .frame(width: 500)
                .offset(x: 150, y: -100)
        }
    }

    private var headerSection: some View {
        VStack(spacing: 16) {
            ZStack {
                Circle()
                    .fill(Color(red: 0.2, green: 0.45, blue: 0.9).opacity(0.15))
                    .frame(width: 80, height: 80)
                Image(systemName: "server.rack")
                    .font(.system(size: 36))
                    .foregroundStyle(Color(red: 0.4, green: 0.6, blue: 1.0))
            }

            VStack(spacing: 8) {
                Text("Connect to Your Server")
                    .font(.title2.bold())
                    .foregroundStyle(.white)
                Text("Enter the address of your DiscVault instance")
                    .font(.subheadline)
                    .foregroundStyle(.white.opacity(0.55))
                    .multilineTextAlignment(.center)
            }
        }
    }

    private var serverURLSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Server Address")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.white.opacity(0.7))

            HStack(spacing: 12) {
                Image(systemName: "globe")
                    .foregroundStyle(.white.opacity(0.5))
                    .frame(width: 20)

                TextField("http://192.168.1.100:6080", text: $serverURL)
                    .textFieldStyle(.plain)
                    .foregroundStyle(.white)
                    .tint(.white)
                    .keyboardType(.URL)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .onSubmit { testConnection() }

                if !serverURL.isEmpty {
                    Button {
                        serverURL = ""
                        connectionState = .idle
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(.white.opacity(0.4))
                    }
                }
            }
            .padding(16)
            .background(.white.opacity(0.07))
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .strokeBorder(borderColor, lineWidth: 1)
            )

            Text("Include the port if your server uses a non-standard one.")
                .font(.caption)
                .foregroundStyle(.white.opacity(0.35))
        }
    }

    private var borderColor: Color {
        switch connectionState {
        case .success: return .green.opacity(0.6)
        case .failure: return .red.opacity(0.5)
        case .testing: return Color(red: 0.4, green: 0.6, blue: 1.0).opacity(0.5)
        case .idle: return .white.opacity(0.1)
        }
    }

    private var successBanner: some View {
        HStack(spacing: 12) {
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(.green)
            if case .success(let version) = connectionState {
                Text("Connected · DiscVault \(version)")
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.white)
            }
            Spacer()
        }
        .padding(16)
        .background(.green.opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .strokeBorder(.green.opacity(0.3), lineWidth: 1)
        )
    }

    private func errorBanner(_ message: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.red)
            Text(message)
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.8))
            Spacer()
        }
        .padding(16)
        .background(.red.opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .strokeBorder(.red.opacity(0.3), lineWidth: 1)
        )
    }

    private var actionButtons: some View {
        VStack(spacing: 12) {
            Button {
                testConnection()
            } label: {
                HStack(spacing: 10) {
                    if isTesting {
                        ProgressView()
                            .tint(.white)
                            .scaleEffect(0.85)
                    } else {
                        Image(systemName: "network")
                    }
                    Text(isTesting ? "Testing…" : "Test Connection")
                        .font(.headline)
                }
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 16)
                .background(
                    LinearGradient(
                        colors: [Color(red: 0.2, green: 0.45, blue: 0.9), Color(red: 0.3, green: 0.3, blue: 0.9)],
                        startPoint: .leading, endPoint: .trailing
                    )
                    .opacity(serverURL.isEmpty ? 0.4 : 1.0)
                )
                .clipShape(RoundedRectangle(cornerRadius: 14))
            }
            .disabled(serverURL.isEmpty || isTesting)

            if isSuccess {
                Button {
                    saveAndContinue()
                } label: {
                    HStack(spacing: 10) {
                        Text(isEditMode ? "Save" : "Continue to Sign In")
                            .font(.headline)
                        if !isEditMode {
                            Image(systemName: "arrow.right")
                        }
                    }
                    .foregroundStyle(.white)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 16)
                    .background(
                        LinearGradient(
                            colors: [Color(red: 0.45, green: 0.2, blue: 0.95), Color(red: 0.2, green: 0.45, blue: 0.95)],
                            startPoint: .leading, endPoint: .trailing
                        )
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 14))
                    .shadow(color: .purple.opacity(0.4), radius: 16)
                }
                .transition(.scale.combined(with: .opacity))
            }
        }
        .animation(.spring(duration: 0.4), value: isSuccess)
    }

    private var helpSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Where to find your server address")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.white.opacity(0.6))

            VStack(alignment: .leading, spacing: 8) {
                HelpRow(icon: "house.fill", text: "Home network: usually http://192.168.x.x:6080")
                HelpRow(icon: "cloud.fill", text: "Remote: your domain or public IP with port")
                HelpRow(icon: "lock.fill", text: "HTTPS recommended for remote access")
            }
        }
        .padding(16)
        .background(.white.opacity(0.04))
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }

    // MARK: - Actions

    private func testConnection() {
        guard !serverURL.isEmpty else { return }
        let normalised = serverURL.hasSuffix("/") ? String(serverURL.dropLast()) : serverURL
        connectionState = .testing

        Task {
            do {
                let url = URL(string: "\(normalised)/api/health")!
                let (data, response) = try await URLSession.shared.data(from: url)
                guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                    await MainActor.run {
                        connectionState = .failure(message: "Server returned an unexpected response.")
                    }
                    return
                }

                struct HealthResponse: Decodable {
                    let status: String
                    let version: String?
                }

                let health = try JSONDecoder().decode(HealthResponse.self, from: data)
                await MainActor.run {
                    serverURL = normalised
                    connectionState = .success(version: health.version ?? "")
                }
            } catch {
                await MainActor.run {
                    connectionState = .failure(message: "Could not reach server. Check the address and try again.")
                }
            }
        }
    }

    private func saveAndContinue() {
        appState.updateServerURL(serverURL)
        if isEditMode {
            dismiss()
        } else {
            appState.phase = .login
        }
    }
}

private struct HelpRow: View {
    let icon: String
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon)
                .font(.caption)
                .foregroundStyle(.white.opacity(0.4))
                .frame(width: 14, alignment: .center)
                .padding(.top, 1)
            Text(text)
                .font(.caption)
                .foregroundStyle(.white.opacity(0.45))
        }
    }
}


#Preview {
    ServerSetupView()
        .environmentObject(AppStateManager())
}
