import SwiftUI

enum SortOrder: String, CaseIterable {
    case addedDesc = "Newest First"
    case addedAsc = "Oldest First"
    case titleAsc = "Title A–Z"
    case titleDesc = "Title Z–A"
    case yearDesc = "Year (Newest)"
    case yearAsc = "Year (Oldest)"
}

struct CollectionStats {
    var totalMovies: Int = 0
    var total4K: Int = 0
    var totalBluray: Int = 0
    var totalDVD: Int = 0
    var wantedCount: Int = 0
}

@Observable
final class CollectionViewModel {
    var movies: [Movie] = []
    var filteredMovies: [Movie] = []
    var isLoading = false
    var errorMessage: String? = nil

    var searchText: String = "" { didSet { applyFilters() } }
    var selectedFormat: String? = nil { didSet { applyFilters() } }
    var showWantedOnly = false { didSet { applyFilters() } }
    var sortOrder: SortOrder = .addedDesc { didSet { applyFilters() } }

    var stats = CollectionStats()

    private let apiClient: APIClient

    init(apiClient: APIClient) {
        self.apiClient = apiClient
    }

    func loadMovies() async {
        isLoading = true
        errorMessage = nil
        do {
            movies = try await apiClient.getMovies()
            applyFilters()
            computeStats()
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    func deleteMovie(_ movie: Movie) async {
        do {
            try await apiClient.deleteMovie(id: movie.id)
            movies.removeAll { $0.id == movie.id }
            applyFilters()
            computeStats()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func addMovieByBarcode(_ barcode: String) async throws -> Movie {
        let movie = try await apiClient.addMovieByBarcode(barcode: barcode)
        movies.insert(movie, at: 0)
        applyFilters()
        computeStats()
        return movie
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
}
