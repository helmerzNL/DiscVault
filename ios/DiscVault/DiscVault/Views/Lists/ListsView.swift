import SwiftUI

struct ListsView: View {
    @Environment(APIClient.self) private var apiClient

    @State private var selectedTab: ListTab = .watchlist
    @State private var watchlist: [WatchlistItem] = []
    @State private var watchHistory: [WatchlistItem] = []
    @State private var isLoading = false
    @State private var errorMessage: String? = nil

    enum ListTab: String, CaseIterable {
        case watchlist = "Watchlist"
        case history = "Watch History"
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.06, green: 0.06, blue: 0.14).ignoresSafeArea()

                VStack(spacing: 0) {
                    tabPicker
                        .padding(.horizontal, 16)
                        .padding(.vertical, 12)

                    if isLoading {
                        Spacer()
                        ProgressView().tint(.white)
                        Spacer()
                    } else {
                        switch selectedTab {
                        case .watchlist:
                            listContent(
                                items: watchlist,
                                emptyIcon: "bookmark",
                                emptyMessage: "Your watchlist is empty",
                                onDelete: deleteFromWatchlist
                            )
                        case .history:
                            listContent(
                                items: watchHistory,
                                emptyIcon: "clock",
                                emptyMessage: "No watch history yet",
                                onDelete: nil
                            )
                        }
                    }
                }
            }
            .navigationTitle("Lists")
            .toolbarColorScheme(.dark, for: .navigationBar)
            .task { await loadAll() }
            .refreshable { await loadAll() }
        }
    }

    // MARK: - Tab Picker

    private var tabPicker: some View {
        HStack(spacing: 0) {
            ForEach(ListTab.allCases, id: \.self) { tab in
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        selectedTab = tab
                    }
                } label: {
                    Text(tab.rawValue)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(selectedTab == tab ? .white : .white.opacity(0.45))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .background(
                            selectedTab == tab
                                ? Color(red: 0.45, green: 0.2, blue: 0.95)
                                : Color.clear
                        )
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(3)
        .background(.white.opacity(0.07))
        .clipShape(RoundedRectangle(cornerRadius: 13))
    }

    // MARK: - List Content

    @ViewBuilder
    private func listContent(
        items: [WatchlistItem],
        emptyIcon: String,
        emptyMessage: String,
        onDelete: ((WatchlistItem) async -> Void)?
    ) -> some View {
        if items.isEmpty {
            Spacer()
            VStack(spacing: 16) {
                Image(systemName: emptyIcon)
                    .font(.system(size: 52))
                    .foregroundStyle(.white.opacity(0.15))
                Text(emptyMessage)
                    .font(.title3)
                    .foregroundStyle(.white.opacity(0.4))
            }
            Spacer()
        } else {
            List {
                ForEach(items) { item in
                    WatchlistRow(item: item, apiClient: apiClient)
                        .listRowBackground(Color.white.opacity(0.05))
                        .listRowSeparatorTint(.white.opacity(0.08))
                        .swipeActions(edge: .trailing) {
                            if let onDelete {
                                Button(role: .destructive) {
                                    Task { await onDelete(item) }
                                } label: {
                                    Label("Remove", systemImage: "trash")
                                }
                            }
                        }
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
        }
    }

    // MARK: - Load Data

    private func loadAll() async {
        isLoading = true
        watchlist = (try? await apiClient.getWatchlist()) ?? []
        watchHistory = (try? await apiClient.getWatchHistory()) ?? []
        isLoading = false
    }

    private func deleteFromWatchlist(_ item: WatchlistItem) async {
        do {
            try await apiClient.removeFromWatchlist(movieId: item.id)
            watchlist.removeAll { $0.id == item.id }
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

// MARK: - Row

private struct WatchlistRow: View {
    let item: WatchlistItem
    let apiClient: APIClient

    var body: some View {
        HStack(spacing: 12) {
            SwiftUI.Group {
                if let url = apiClient.posterURL(for: item.poster) {
                    AsyncImage(url: url) { phase in
                        if case .success(let img) = phase {
                            img.resizable().aspectRatio(contentMode: .fill)
                        } else {
                            posterPlaceholder
                        }
                    }
                } else {
                    posterPlaceholder
                }
            }
            .frame(width: 44, height: 66)
            .clipShape(RoundedRectangle(cornerRadius: 6))

            VStack(alignment: .leading, spacing: 4) {
                Text(item.title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                    .lineLimit(2)

                HStack(spacing: 6) {
                    if let year = item.year {
                        Text(year)
                            .font(.caption)
                            .foregroundStyle(.white.opacity(0.5))
                    }
                    if let format = item.format {
                        formatBadge(format)
                    }
                }

                if let date = item.addedAt {
                    Text(formattedDate(date))
                        .font(.caption2)
                        .foregroundStyle(.white.opacity(0.35))
                }
            }

            Spacer()
        }
        .padding(.vertical, 4)
    }

    private var posterPlaceholder: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 6).fill(.white.opacity(0.08))
            Image(systemName: "opticaldisc").foregroundStyle(.white.opacity(0.2))
        }
    }

    private func formatBadge(_ format: String) -> some View {
        let (label, color): (String, Color) = switch format {
        case "4K UHD": ("4K", Color(red: 0.45, green: 0.15, blue: 0.85))
        case "Blu-ray": ("BD", Color(red: 0.15, green: 0.4, blue: 0.85))
        default: ("DVD", Color(red: 0.35, green: 0.35, blue: 0.45))
        }
        return Text(label)
            .font(.system(size: 9, weight: .bold))
            .foregroundStyle(.white)
            .padding(.horizontal, 5).padding(.vertical, 2)
            .background(color)
            .clipShape(RoundedRectangle(cornerRadius: 4))
    }

    private func formattedDate(_ dateStr: String) -> String {
        let df = DateFormatter()
        df.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        if let date = df.date(from: dateStr) {
            let out = DateFormatter()
            out.dateStyle = .medium
            return out.string(from: date)
        }
        return dateStr
    }
}
