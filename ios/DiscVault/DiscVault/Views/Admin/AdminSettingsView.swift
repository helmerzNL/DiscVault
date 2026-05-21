import SwiftUI

struct AdminSettingsView: View {
    @EnvironmentObject private var appState: AppStateManager
    @Environment(APIClient.self) private var apiClient
    @Environment(\.dismiss) private var dismiss

    @State private var showServerURLEditor = false
    @State private var stats: [String: Int] = [:]
    @State private var isLoadingStats = true

    var body: some View {
        List {
            statsSection
            serverSection
            dangerSection
        }
        .navigationTitle("Admin Settings")
        .navigationBarTitleDisplayMode(.large)
        .scrollContentBackground(.hidden)
        .background(Color(red: 0.06, green: 0.06, blue: 0.14))
        .task { await loadStats() }
        .sheet(isPresented: $showServerURLEditor) {
            NavigationStack {
                ServerSetupView(isEditMode: true)
            }
        }
    }

    // MARK: - Sections

    private var statsSection: some View {
        Section("Database") {
            if isLoadingStats {
                HStack {
                    ProgressView()
                    Text("Loading stats…")
                        .foregroundStyle(.secondary)
                }
            } else {
                StatsRow(label: "Total Movies", value: stats["total_movies"] ?? 0, icon: "opticaldisc.fill", color: .purple)
                StatsRow(label: "4K UHD Titles", value: stats["total_4k"] ?? 0, icon: "4k.tv.fill", color: .indigo)
                StatsRow(label: "Blu-ray Titles", value: stats["total_bluray"] ?? 0, icon: "opticaldisc", color: .blue)
                StatsRow(label: "DVD Titles", value: stats["total_dvd"] ?? 0, icon: "opticaldisc", color: .gray)
                StatsRow(label: "Wanted", value: stats["wanted_count"] ?? 0, icon: "star.fill", color: .yellow)
            }
        }
        .listRowBackground(Color.white.opacity(0.06))
    }

    private var serverSection: some View {
        Section("Server") {
            HStack {
                Label {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Server URL")
                            .font(.subheadline.weight(.medium))
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

                Button("Edit") {
                    showServerURLEditor = true
                }
                .font(.subheadline)
                .foregroundStyle(.blue)
                .buttonStyle(.plain)
            }
            .padding(.vertical, 4)

            Button {
                Task { await testServerConnection() }
            } label: {
                Label("Test Connection", systemImage: "network")
            }
        }
        .listRowBackground(Color.white.opacity(0.06))
    }

    private var dangerSection: some View {
        Section("Danger Zone") {
            Button(role: .destructive) {
                clearCache()
            } label: {
                Label("Clear Image Cache", systemImage: "trash")
            }
        }
        .listRowBackground(Color.white.opacity(0.06))
    }

    // MARK: - Actions

    private func loadStats() async {
        isLoadingStats = true
        do {
            stats = try await apiClient.getStats()
        } catch {
            // Stats are informational, ignore errors silently
        }
        isLoadingStats = false
    }

    @State private var connectionAlertMessage: String?
    @State private var showConnectionAlert = false

    private func testServerConnection() async {
        guard let url = URL(string: "\(appState.serverURL)/api/health") else { return }
        do {
            let (_, response) = try await URLSession.shared.data(from: url)
            let ok = (response as? HTTPURLResponse)?.statusCode == 200
            connectionAlertMessage = ok ? "Connection successful." : "Server returned an error."
        } catch {
            connectionAlertMessage = "Could not reach server: \(error.localizedDescription)"
        }
        showConnectionAlert = true
    }

    private func clearCache() {
        URLCache.shared.removeAllCachedResponses()
    }
}

private struct StatsRow: View {
    let label: String
    let value: Int
    let icon: String
    let color: Color

    var body: some View {
        HStack {
            Label(label, systemImage: icon)
                .foregroundStyle(.primary)
            Spacer()
            Text("\(value)")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(color)
        }
    }
}

#Preview {
    NavigationStack {
        AdminSettingsView()
            .environmentObject(AppStateManager())
    }
    .preferredColorScheme(.dark)
}
