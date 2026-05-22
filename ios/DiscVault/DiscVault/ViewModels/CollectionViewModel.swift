import SwiftUI

enum SortOrder: String, CaseIterable {
    case addedDesc
    case addedAsc
    case titleAsc
    case titleDesc
    case yearDesc
    case yearAsc

    var translationKey: String {
        switch self {
        case .addedDesc: return "collection.sortNewest"
        case .addedAsc: return "collection.sortOldest"
        case .titleAsc: return "collection.sortTitleAsc"
        case .titleDesc: return "collection.sortTitleDesc"
        case .yearDesc: return "collection.sortYearDesc"
        case .yearAsc: return "collection.sortYearAsc"
        }
    }
}

struct CollectionStats {
    var totalMovies: Int = 0
    var total4K: Int = 0
    var totalBluray: Int = 0
    var totalDVD: Int = 0
    var wantedCount: Int = 0
}

enum BulkContainerKind: String, Hashable {
    case vault
    case boxset
    case collection
}

struct BulkContainerTarget: Identifiable, Hashable {
    let rawID: Int
    let title: String
    let kind: BulkContainerKind
    let memberCount: Int

    var id: String { "\(kind.rawValue)-\(rawID)" }
}

@MainActor
@Observable
final class CollectionViewModel {
    var movies: [Movie] = []
    var filteredMovies: [Movie] = []
    var groups: [Group] = []
    var editionGroups: [EditionGroup] = []
    var collections: [DiscCollection] = []
    var isLoading = false
    var isBulkWorking = false
    var errorMessage: String? = nil
    var statusMessage: String? = nil
    var isGroupingEditions = false
    var loadedGroupedMovies = false
    var showDigitalBadges = false
    var digitalBadgeFilter = "all"
    var digitalBadgeTypesByMovieID: [Int: Set<String>] = [:]

    var searchText: String = "" { didSet { applyFilters() } }
    var selectedFormat: String? = nil { didSet { applyFilters() } }
    var selectedGroupID: Int? = nil { didSet { applyFilters() } }
    var showWantedOnly = false { didSet { applyFilters() } }
    var showContainersOnly = false {
        didSet {
            if showContainersOnly && !loadedGroupedMovies {
                Task { await loadMovies() }
            } else {
                applyFilters()
            }
        }
    }
    var showSearchBar = true { didSet { if !showSearchBar { searchText = "" } } }
    var sortOrder: SortOrder = .addedDesc { didSet { applyFilters() } }
    var isSelectionMode = false { didSet { if !isSelectionMode { selectedMovieIDs.removeAll() } } }
    var selectedMovieIDs: Set<Int> = []

    var stats = CollectionStats()

    var containerTargets: [BulkContainerTarget] {
        let editionTargets = editionGroups.map { group in
            BulkContainerTarget(rawID: group.id, title: group.title, kind: group.containerKind, memberCount: group.displayMemberCount)
        }
        let collectionTargets = collections.map { collection in
            BulkContainerTarget(rawID: collection.id, title: collection.title, kind: .collection, memberCount: collection.displayMemberCount)
        }
        return (editionTargets + collectionTargets).sorted {
            $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending
        }
    }

    private let apiClient: APIClient

    init(apiClient: APIClient) {
        self.apiClient = apiClient
    }

    func loadMovies() async {
        isLoading = true
        errorMessage = nil
        do {
            let preferences = (try? await apiClient.getUserPreferences()) ?? [:]
            showSearchBar = bool(preferences["show_search_button"], defaultValue: true)
            isGroupingEditions = bool(preferences["group_editions"], defaultValue: false)
            showDigitalBadges = bool(preferences["digital_badges"], defaultValue: false)
            digitalBadgeFilter = preferences["digital_badge_filter"] ?? "all"
            let shouldLoadGroupedMovies = isGroupingEditions || showContainersOnly

            async let loadedMovies = apiClient.getMovies(groupEditions: shouldLoadGroupedMovies)
            async let loadedGroups = apiClient.getGroups()
            async let loadedEditionGroups = apiClient.getEditionGroups()
            async let loadedCollections = apiClient.getDiscCollections()

            movies = try await loadedMovies
            loadedGroupedMovies = shouldLoadGroupedMovies
            groups = (try? await loadedGroups) ?? []
            editionGroups = (try? await loadedEditionGroups) ?? []
            collections = (try? await loadedCollections) ?? []
            await loadDigitalBadgesIfNeeded()

            if let selectedGroupID, !groups.contains(where: { $0.id == selectedGroupID }) {
                self.selectedGroupID = nil
            }

            applyFilters()
            computeStats()
            selectedMovieIDs.formIntersection(Set(movies.map(\.id)))
        } catch where !isCancellation(error) {
            errorMessage = error.localizedDescription
        } catch {
            // SwiftUI can cancel refresh tasks when the view updates. Do not surface that as an error banner.
        }
        isLoading = false
    }

    func deleteMovie(_ movie: Movie) async {
        do {
            try await apiClient.deleteMovie(id: movie.id)
            movies.removeAll { $0.id == movie.id }
            applyFilters()
            computeStats()
        } catch where !isCancellation(error) {
            errorMessage = error.localizedDescription
        } catch {
            // Ignore user-invisible task cancellation.
        }
    }

    func addMovieByBarcode(_ barcode: String) async throws -> Movie {
        let movie = try await apiClient.addMovieByBarcode(barcode: barcode)
        movies.insert(movie, at: 0)
        applyFilters()
        computeStats()
        return movie
    }

    func refreshGroups() async {
        do {
            groups = try await apiClient.getGroups()
            if let selectedGroupID, !groups.contains(where: { $0.id == selectedGroupID }) {
                self.selectedGroupID = nil
                applyFilters()
            }
        } catch where !isCancellation(error) {
            errorMessage = error.localizedDescription
        } catch {
            // Ignore user-invisible task cancellation.
        }
    }

    func refreshContainers() async {
        do {
            async let loadedEditionGroups = apiClient.getEditionGroups()
            async let loadedCollections = apiClient.getDiscCollections()
            editionGroups = try await loadedEditionGroups
            collections = try await loadedCollections
        } catch where !isCancellation(error) {
            errorMessage = error.localizedDescription
        } catch {
            // Ignore user-invisible task cancellation.
        }
    }

    func digitalBadgeTypes(for movie: Movie) -> Set<String> {
        digitalBadgeTypesByMovieID[movie.id] ?? []
    }

    func toggleSelection(for movie: Movie) {
        if selectedMovieIDs.contains(movie.id) {
            selectedMovieIDs.remove(movie.id)
        } else {
            selectedMovieIDs.insert(movie.id)
        }
    }

    func selectAllFilteredMovies() {
        selectedMovieIDs.formUnion(filteredMovies.map(\.id))
    }

    func clearSelection() {
        selectedMovieIDs.removeAll()
    }

    func addSelectedToWatchlist() async {
        await performBulkAction(successMessage: "Added \(selectedMovieIDs.count) movie(s) to watchlist.") { ids in
            for id in ids {
                try await apiClient.addToWatchlist(movieId: id)
            }
        }
    }

    func refreshSelectedMetadata() async {
        await performBulkAction(successMessage: "Updated metadata for \(selectedMovieIDs.count) movie(s).") { ids in
            for id in ids {
                _ = try await apiClient.refreshMovieMetadata(id: id)
            }
        }
        await loadMovies()
    }

    func assignSelectedMoviesToGroups(_ groupIDs: Set<Int>) async {
        guard !groupIDs.isEmpty else {
            statusMessage = "Select at least one group."
            return
        }

        await performBulkAction(successMessage: "Assigned \(selectedMovieIDs.count) movie(s) to group(s).") { ids in
            try await apiClient.addMoviesToGroups(movieIDs: ids, groupIDs: Array(groupIDs).sorted())
        }
        await loadMovies()
    }

    func assignSelectedMovies(to container: BulkContainerTarget) async {
        await performBulkAction(successMessage: "Assigned \(selectedMovieIDs.count) movie(s) to \(container.title).") { ids in
            for movieID in ids {
                try await apiClient.assignMovie(id: movieID, to: container)
            }
        }
        await loadMovies()
    }

    func createContainerAndAssignSelectedMovies(title: String, kind: BulkContainerKind) async {
        let trimmedTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedTitle.isEmpty else {
            statusMessage = "Enter a name."
            return
        }

        isBulkWorking = true
        errorMessage = nil
        statusMessage = nil
        do {
            let target: BulkContainerTarget
            switch kind {
            case .vault, .boxset:
                let group = try await apiClient.createEditionGroup(title: trimmedTitle, kind: kind)
                target = BulkContainerTarget(rawID: group.id, title: group.title, kind: kind, memberCount: 0)
            case .collection:
                let collection = try await apiClient.createDiscCollection(title: trimmedTitle)
                target = BulkContainerTarget(rawID: collection.id, title: collection.title, kind: .collection, memberCount: 0)
            }

            let ids = Array(selectedMovieIDs)
            for movieID in ids {
                try await apiClient.assignMovie(id: movieID, to: target)
            }
            statusMessage = "Assigned \(ids.count) movie(s) to \(target.title)."
            clearSelection()
            isSelectionMode = false
        } catch where !isCancellation(error) {
            errorMessage = error.localizedDescription
        } catch {
            // Ignore user-invisible task cancellation.
        }
        isBulkWorking = false
        await loadMovies()
    }

    private func performBulkAction(successMessage: String, operation: ([Int]) async throws -> Void) async {
        let ids = Array(selectedMovieIDs)
        guard !ids.isEmpty else {
            statusMessage = "No selection."
            return
        }

        isBulkWorking = true
        errorMessage = nil
        statusMessage = nil
        do {
            try await operation(ids)
            statusMessage = successMessage
            clearSelection()
            isSelectionMode = false
        } catch where !isCancellation(error) {
            errorMessage = error.localizedDescription
        } catch {
            // Ignore user-invisible task cancellation.
        }
        isBulkWorking = false
    }

    func applyFilters() {
        var result = movies

        if !searchText.isEmpty {
            let q = searchText.lowercased()
            result = result.filter {
                $0.title.lowercased().contains(q) ||
                ($0.director?.lowercased().contains(q) ?? false) ||
                ($0.genre?.lowercased().contains(q) ?? false)
            }
        }

        if let format = selectedFormat {
            result = result.filter { $0.format == format }
        }

        if showWantedOnly {
            result = result.filter { $0.wanted == true }
        }

        if showContainersOnly {
            result = result.filter {
                loadedGroupedMovies ? isContainerCard($0) : isPartOfContainer($0)
            }
        }

        if let selectedGroupID {
            result = result.filter { movieBelongsToGroup($0, groupID: selectedGroupID) }
        }

        switch sortOrder {
        case .addedDesc: break // backend order
        case .addedAsc: result = result.reversed()
        case .titleAsc: result = result.sorted { $0.title < $1.title }
        case .titleDesc: result = result.sorted { $0.title > $1.title }
        case .yearDesc: result = result.sorted { ($0.year ?? "") > ($1.year ?? "") }
        case .yearAsc: result = result.sorted { ($0.year ?? "") < ($1.year ?? "") }
        }

        filteredMovies = result
    }

    private func computeStats() {
        stats.totalMovies = movies.count
        stats.total4K = movies.filter { $0.format == "4K UHD" }.count
        stats.totalBluray = movies.filter { $0.format == "Blu-ray" }.count
        stats.totalDVD = movies.filter { $0.format == "DVD" }.count
        stats.wantedCount = movies.filter { $0.wanted == true }.count
    }

    private func bool(_ value: String?, defaultValue: Bool) -> Bool {
        guard let value else { return defaultValue }
        return ["1", "true", "yes", "on"].contains(value.lowercased())
    }

    private func isCancellation(_ error: Error) -> Bool {
        if error is CancellationError {
            return true
        }

        if case APIError.networkError(let underlying) = error {
            return isCancellation(underlying)
        }

        let nsError = error as NSError
        if nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled {
            return true
        }

        let message = error.localizedDescription.lowercased()
        return message == "cancelled" || message == "canceled" || message == "cancelled." || message == "canceled."
    }

    private func loadDigitalBadgesIfNeeded() async {
        digitalBadgeTypesByMovieID = [:]
        guard showDigitalBadges else { return }

        do {
            let compare = try await apiClient.getCollectionCompare()
            var badgeTypes: [Int: Set<String>] = [:]
            for entry in compare.physicalAndDigital {
                guard let movieID = entry.movie?.id else { continue }
                let types = entry.digitalMatches
                    .compactMap { $0.sourceType?.lowercased() }
                    .filter { digitalBadgeFilter == "all" || $0 == digitalBadgeFilter }
                if !types.isEmpty {
                    badgeTypes[movieID] = Set(types)
                }
            }
            digitalBadgeTypesByMovieID = badgeTypes
        } catch {
            digitalBadgeTypesByMovieID = [:]
        }
    }

    private func movieBelongsToGroup(_ movie: Movie, groupID: Int) -> Bool {
        if (movie.groupIds ?? []).contains(groupID) {
            return true
        }

        return movies.contains { candidate in
            candidate.id != movie.id &&
            (candidate.groupIds ?? []).contains(groupID) &&
            sharesContainer(candidate, with: movie)
        }
    }

    private func isPartOfContainer(_ movie: Movie) -> Bool {
        movie.isContainerCard || movie.editionGroupId != nil || movie.superGroupId != nil || movie.collectionId != nil
    }

    private func isContainerCard(_ movie: Movie) -> Bool {
        movie.isContainerCard ||
        !movie.editions.isEmpty ||
        !movie.subGroups.isEmpty ||
        !movie.looseMovies.isEmpty ||
        !movie.boxSets.isEmpty ||
        !movie.vaults.isEmpty
    }

    private func sharesContainer(_ lhs: Movie, with rhs: Movie) -> Bool {
        if let lhsEditionGroupId = lhs.editionGroupId,
           let rhsEditionGroupId = rhs.editionGroupId,
           lhsEditionGroupId == rhsEditionGroupId {
            return true
        }

        if let lhsSuperGroupId = lhs.superGroupId,
           let rhsSuperGroupId = rhs.superGroupId,
           lhsSuperGroupId == rhsSuperGroupId {
            return true
        }

        if let lhsCollectionId = lhs.collectionId,
           let rhsCollectionId = rhs.collectionId,
           lhsCollectionId == rhsCollectionId {
            return true
        }

        return false
    }
}
