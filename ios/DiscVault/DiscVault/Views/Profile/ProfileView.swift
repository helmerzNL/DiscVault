import SwiftUI

struct ProfileView: View {
    @EnvironmentObject private var appState: AppStateManager
    @Environment(APIClient.self) private var apiClient

    @State private var currentUser: User? = nil
    @State private var groups: [Group] = []
    @State private var isLoading = true
    @State private var showServerEdit = false
    @State private var showSignOutConfirm = false

    var isAdmin: Bool { currentUser?.role == "admin" }

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.06, green: 0.06, blue: 0.14).ignoresSafeArea()

                if isLoading {
                    ProgressView().tint(.white)
                } else {
                    profileList
                }
            }
            .navigationTitle("Profile")
            .toolbarColorScheme(.dark, for: .navigationBar)
            .task { await loadProfile() }
            .sheet(isPresented: $showServerEdit) {
                NavigationStack { ServerSetupView(isEditMode: true) }
            }
            .confirmationDialog("Sign Out", isPresented: $showSignOutConfirm, titleVisibility: .visible) {
                Button("Sign Out", role: .destructive) { appState.signOut() }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("You'll need to sign in again to access your collection.")
            }
        }
    }

    private var profileList: some View {
        List {
            // User header (outside normal list flow)
            Section {
                userHeader
            }
            .listRowBackground(Color.clear)
            .listRowInsets(EdgeInsets())

            // Groups section
            if !groups.isEmpty {
                Section("My Groups") {
                    ForEach(groups) { group in
                        GroupRow(group: group)
                    }
                }
                .listRowBackground(Color.white.opacity(0.06))
            }

            // Admin section
            if isAdmin {
                Section("Admin") {
                    NavigationLink {
                        AdminSettingsView()
                    } label: {
                        Label("Admin Settings", systemImage: "gear.badge")
                            .foregroundStyle(.orange)
                    }
                }
                .listRowBackground(Color.white.opacity(0.06))
            }

            // Connection section
            Section("Server") {
                Button {
                    showServerEdit = true
                } label: {
                    HStack {
                        Label {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Server URL")
                                    .font(.subheadline)
                                    .foregroundStyle(.white)
                                Text(appState.serverURL.isEmpty ? "Not configured" : appState.serverURL)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                        } icon: {
                            Image(systemName: "server.rack")
                                .foregroundStyle(.blue)
                        }
                        Spacer()
                        Image(systemName: "pencil")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .buttonStyle(.plain)
            }
            .listRowBackground(Color.white.opacity(0.06))

            // Account section
            Section("Account") {
                Button(role: .destructive) {
                    showSignOutConfirm = true
                } label: {
                    Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
                }
            }
            .listRowBackground(Color.white.opacity(0.06))
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
    }

    // MARK: - User Header

    private var userHeader: some View {
        VStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(
                        LinearGradient(
                            colors: [Color(red: 0.45, green: 0.2, blue: 0.95), Color(red: 0.2, green: 0.45, blue: 0.95)],
                            startPoint: .topLeading, endPoint: .bottomTrailing
                        )
                    )
                    .frame(width: 80, height: 80)

                Text(initials(for: currentUser))
                    .font(.system(size: 28, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)
            }
            .shadow(color: .purple.opacity(0.4), radius: 16)

            VStack(spacing: 4) {
                Text(currentUser?.displayName ?? currentUser?.username ?? "")
                    .font(.title3.bold())
                    .foregroundStyle(.white)

                if let username = currentUser?.username, username != currentUser?.displayName {
                    Text("@\(username)")
                        .font(.subheadline)
                        .foregroundStyle(.white.opacity(0.5))
                }

                if let role = currentUser?.role {
                    Text(role.capitalized)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 4)
                        .background(roleColor(role).opacity(0.25))
                        .overlay(
                            Capsule().strokeBorder(roleColor(role).opacity(0.4), lineWidth: 1)
                        )
                        .clipShape(Capsule())
                }
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 24)
    }

    // MARK: - Helpers

    private func loadProfile() async {
        isLoading = true
        async let user = try? apiClient.getCurrentUser()
        async let grps = (try? apiClient.getGroups()) ?? []
        currentUser = await user
        groups = await grps
        isLoading = false
    }

    private func initials(for user: User?) -> String {
        let name = user?.displayName ?? user?.username ?? "?"
        let parts = name.split(separator: " ")
        if parts.count >= 2 {
            return String(parts[0].prefix(1) + parts[1].prefix(1)).uppercased()
        }
        return String(name.prefix(2)).uppercased()
    }

    private func roleColor(_ role: String) -> Color {
        switch role {
        case "admin": return .orange
        case "user": return .blue
        default: return .gray
        }
    }
}

// MARK: - Group Row

private struct GroupRow: View {
    let group: Group

    var body: some View {
        HStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(Color(red: 0.2, green: 0.45, blue: 0.9).opacity(0.2))
                    .frame(width: 36, height: 36)
                Image(systemName: "person.2.fill")
                    .font(.system(size: 14))
                    .foregroundStyle(Color(red: 0.4, green: 0.6, blue: 1.0))
            }

            VStack(alignment: .leading, spacing: 2) {
                Text(group.name)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.white)
                if let count = group.memberCount {
                    Text("\(count) member\(count == 1 ? "" : "s")")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Spacer()
        }
    }
}

#Preview {
    ProfileView()
        .environmentObject(AppStateManager())
        .preferredColorScheme(.dark)
}
