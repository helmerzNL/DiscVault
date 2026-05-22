import SwiftUI

struct ListsView: View {
    @Environment(APIClient.self) private var apiClient

    @State private var selectedTab: ListTab = .watchlist
    @State private var watchlist: [WatchlistItem] = []
    @State private var watchHistory: [WatchlistItem] = []
    @State private var isLoading = false
    @State private var errorMessage: String? = nil
    @State private var watchlistSortOrder: WatchlistSortOrder = .titleAsc
    @State private var selectedHistoryYear: Int?
    @State private var selectedHistoryMonth: Int?

    enum ListTab: String, CaseIterable {
        case watchlist = "Watchlist"
        case history = "Watch History"
    }

    enum WatchlistSortOrder: String, CaseIterable, Identifiable {
        case titleAsc
        case titleDesc
        case tmdbRating
        case releaseDateAsc
        case releaseDateDesc
        case addedDateAsc
        case addedDateDesc

        var id: String { rawValue }

        var title: String {
            switch self {
            case .titleAsc: return "A-Z"
            case .titleDesc: return "Z-A"
            case .tmdbRating: return "TMDb rating"
            case .releaseDateAsc: return "Uitgiftedatum oud-nieuw"
            case .releaseDateDesc: return "Uitgiftedatum nieuw-oud"
            case .addedDateAsc: return "Toegevoegd oud-nieuw"
            case .addedDateDesc: return "Toegevoegd nieuw-oud"
            }
        }
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
                            watchlistSortBar
                            listContent(
                                items: sortedWatchlist,
                                emptyIcon: "bookmark",
                                emptyMessage: "Your watchlist is empty",
                                showsWatchedDate: false,
                                onDelete: deleteFromWatchlist
                            )
                        case .history:
                            historyDateFilterBar
                            listContent(
                                items: filteredWatchHistory,
                                emptyIcon: "clock",
                                emptyMessage: "No watch history yet",
                                showsWatchedDate: true,
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

    private var historyDateFilterBar: some View {
        HStack(spacing: 10) {
            Menu {
                Button("Alle jaren") {
                    selectedHistoryYear = nil
                    selectedHistoryMonth = nil
                }
                ForEach(historyYears, id: \.self) { year in
                    Button(String(year)) {
                        selectedHistoryYear = year
                    }
                }
            } label: {
                filterChip(title: selectedHistoryYear.map(String.init) ?? "Alle jaren", systemImage: "calendar")
            }

            Menu {
                Button("Alle maanden") {
                    selectedHistoryMonth = nil
                }
                ForEach(availableHistoryMonths, id: \.self) { month in
                    Button(monthName(month)) {
                        selectedHistoryMonth = month
                    }
                }
            } label: {
                filterChip(title: selectedHistoryMonth.map(monthName) ?? "Alle maanden", systemImage: "calendar.badge.clock")
            }
            .disabled(selectedHistoryYear == nil && historyYears.isEmpty)

            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.bottom, 8)
    }

    private var watchlistSortBar: some View {
        HStack {
            Spacer()
            Menu {
                ForEach(WatchlistSortOrder.allCases) { order in
                    Button {
                        watchlistSortOrder = order
                    } label: {
                        if watchlistSortOrder == order {
                            Label(order.title, systemImage: "checkmark")
                        } else {
                            Text(order.title)
                        }
                    }
                }
            } label: {
                Label(watchlistSortOrder.title, systemImage: "arrow.up.arrow.down")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(.white.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: 10))
            }
        }
        .padding(.horizontal, 16)
        .padding(.bottom, 8)
    }

    private func filterChip(title: String, systemImage: String) -> some View {
        Label(title, systemImage: systemImage)
            .font(.caption.weight(.semibold))
            .foregroundStyle(.white)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(.white.opacity(0.08))
            .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private var sortedWatchlist: [WatchlistItem] {
        switch watchlistSortOrder {
        case .titleAsc:
            return watchlist.sorted { $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending }
        case .titleDesc:
            return watchlist.sorted { $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedDescending }
        case .tmdbRating:
            return watchlist.sorted { $0.sortRating > $1.sortRating }
        case .releaseDateAsc:
            return watchlist.sorted { dateSortValue($0.releaseDate) < dateSortValue($1.releaseDate) }
        case .releaseDateDesc:
            return watchlist.sorted { dateSortValue($0.releaseDate) > dateSortValue($1.releaseDate) }
        case .addedDateAsc:
            return watchlist.sorted { dateSortValue($0.watchlistDate) < dateSortValue($1.watchlistDate) }
        case .addedDateDesc:
            return watchlist.sorted { dateSortValue($0.watchlistDate) > dateSortValue($1.watchlistDate) }
        }
    }

    private var filteredWatchHistory: [WatchlistItem] {
        watchHistory.filter { item in
            guard let date = parseDate(item.watchedDate) else { return false }
            let components = Calendar.current.dateComponents([.year, .month], from: date)
            if let selectedHistoryYear, components.year != selectedHistoryYear {
                return false
            }
            if let selectedHistoryMonth, components.month != selectedHistoryMonth {
                return false
            }
            return true
        }
    }

    private var historyYears: [Int] {
        let years = watchHistory.compactMap { item -> Int? in
            guard let date = parseDate(item.watchedDate) else { return nil }
            return Calendar.current.component(.year, from: date)
        }
        return Array(Set(years)).sorted(by: >)
    }

    private var availableHistoryMonths: [Int] {
        let months = watchHistory.compactMap { item -> Int? in
            guard let date = parseDate(item.watchedDate) else { return nil }
            let components = Calendar.current.dateComponents([.year, .month], from: date)
            if let selectedHistoryYear, components.year != selectedHistoryYear {
                return nil
            }
            return components.month
        }
        return Array(Set(months)).sorted()
    }

    private func monthName(_ month: Int) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "nl_NL")
        return formatter.monthSymbols[max(0, min(month - 1, formatter.monthSymbols.count - 1))].capitalized
    }

    @ViewBuilder
    private func listContent(
        items: [WatchlistItem],
        emptyIcon: String,
        emptyMessage: String,
        showsWatchedDate: Bool,
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
                    NavigationLink {
                        WatchlistMovieDetailLoader(movieID: item.detailMovieId)
                    } label: {
                        WatchlistRow(item: item, apiClient: apiClient, showsWatchedDate: showsWatchedDate)
                    }
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

    private func dateSortValue(_ value: String?) -> Date {
        parseDate(value) ?? .distantPast
    }

    private func parseDate(_ value: String?) -> Date? {
        guard let value else { return nil }
        let formats = [
            "yyyy-MM-dd'T'HH:mm:ss.SSSXXXXX",
            "yyyy-MM-dd'T'HH:mm:ssXXXXX",
            "yyyy-MM-dd'T'HH:mm:ss",
            "yyyy-MM-dd HH:mm:ss",
            "yyyy-MM-dd"
        ]
        for format in formats {
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.dateFormat = format
            if let date = formatter.date(from: value) {
                return date
            }
        }
        return ISO8601DateFormatter().date(from: value)
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
            try await apiClient.removeFromWatchlist(movieId: item.detailMovieId)
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
    let showsWatchedDate: Bool

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

                HStack(spacing: 10) {
                    if let date = showsWatchedDate ? item.watchedDate : item.watchlistDate {
                        Label(
                            showsWatchedDate ? formattedDate(date) : "Toegevoegd: \(formattedDate(date))",
                            systemImage: showsWatchedDate ? "calendar.badge.clock" : "calendar"
                        )
                    }

                    if item.sortRating > 0 {
                        Label(String(format: "TMDb %.1f", item.sortRating), systemImage: "star.fill")
                    }
                }
                .font(.caption2)
                .foregroundStyle(.white.opacity(0.35))
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
        if let date = parseDate(dateStr) {
            let out = DateFormatter()
            out.locale = Locale(identifier: "nl_NL")
            out.dateFormat = "dd-MM-yyyy"
            return out.string(from: date)
        }
        if let normalized = normalizedDateString(dateStr) {
            return normalized
        }
        return dateStr
    }

    private func normalizedDateString(_ value: String) -> String? {
        let datePart = value
            .split(separator: "T", maxSplits: 1)
            .first?
            .split(separator: " ", maxSplits: 1)
            .first
            .map(String.init)
        guard let datePart else { return nil }

        let parts = datePart.split(separator: "-").map(String.init)
        guard parts.count == 3, parts[0].count == 4 else { return nil }
        return "\(parts[2])-\(parts[1])-\(parts[0])"
    }

    private func parseDate(_ value: String) -> Date? {
        let formats = [
            "yyyy-MM-dd'T'HH:mm:ss.SSSSSSXXXXX",
            "yyyy-MM-dd'T'HH:mm:ss.SSSSSS",
            "yyyy-MM-dd'T'HH:mm:ss.SSSXXXXX",
            "yyyy-MM-dd'T'HH:mm:ss.SSS",
            "yyyy-MM-dd'T'HH:mm:ssXXXXX",
            "yyyy-MM-dd'T'HH:mm:ss",
            "yyyy-MM-dd HH:mm:ss",
            "yyyy-MM-dd"
        ]
        for format in formats {
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.dateFormat = format
            if let date = formatter.date(from: value) {
                return date
            }
        }
        return ISO8601DateFormatter().date(from: value)
    }
}

private struct WatchlistMovieDetailLoader: View {
    let movieID: Int

    @Environment(APIClient.self) private var apiClient
    @State private var movie: Movie?
    @State private var errorMessage: String?
    @State private var isLoading = true

    var body: some View {
        SwiftUI.Group {
            if let movie {
                MovieDetailView(movie: movie)
            } else {
                ZStack {
                    Color(red: 0.06, green: 0.06, blue: 0.14).ignoresSafeArea()
                    VStack(spacing: 16) {
                        if isLoading {
                            ProgressView().tint(.white)
                        } else {
                            Image(systemName: "exclamationmark.triangle")
                                .font(.largeTitle)
                                .foregroundStyle(.yellow)
                        }
                        Text(errorMessage ?? "Film laden...")
                            .font(.subheadline)
                            .foregroundStyle(.white.opacity(0.65))
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 24)
                    }
                }
            }
        }
        .task { await loadMovie() }
    }

    private func loadMovie() async {
        guard movie == nil else { return }
        isLoading = true
        errorMessage = nil
        do {
            movie = try await apiClient.getMovie(id: movieID)
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }
}
