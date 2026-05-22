import SwiftUI

@MainActor
@Observable
final class MovieDetailViewModel {
    var movie: Movie
    var cast: [CastMember] = []
    var isLoading = false
    var isRefreshing = false
    var isInWatchlist = false
    var lastWatched: String? = nil
    var errorMessage: String? = nil
    var castImageRefreshToken = 0

    private let apiClient: APIClient

    init(movie: Movie, apiClient: APIClient) {
        self.movie = movie
        self.apiClient = apiClient
        self.isInWatchlist = movie.onWatchlist ?? movie.wanted ?? false
        self.lastWatched = movie.lastWatched
    }

    func loadDetails() async {
        isLoading = true
        await withTaskGroup(of: Void.self) { group in
            group.addTask { [apiClient, movie] in
                do {
                    let fresh = try await apiClient.getMovie(id: movie.id)
                    await MainActor.run { self.applyFetched(fresh) }
                } catch {
                    await MainActor.run { self.errorMessage = error.localizedDescription }
                }
            }
            group.addTask { [apiClient, movie] in
                let cast = (try? await apiClient.getMovieCast(id: movie.id)) ?? []
                await MainActor.run { self.cast = cast }
            }
        }
        isLoading = false
    }

    func toggleWatchlist() async {
        let previous = isInWatchlist
        isInWatchlist.toggle()
        do {
            if previous {
                try await apiClient.removeFromWatchlist(movieId: movie.id)
            } else {
                try await apiClient.addToWatchlist(movieId: movie.id)
            }
        } catch {
            // Roll back on failure.
            isInWatchlist = previous
            errorMessage = error.localizedDescription
        }
    }

    /// Marks the movie as watched on the given date (YYYY-MM-DD). Defaults to today.
    func markAsWatched(on date: Date = Date()) async {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withFullDate]
        let dateString = formatter.string(from: date)
        do {
            try await apiClient.markWatched(movieId: movie.id, watchedAt: dateString)
            lastWatched = dateString
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func setBackdrop(_ url: String) async {
        do {
            try await apiClient.setMovieBackdrop(id: movie.id, url: url)
            let fresh = try await apiClient.getMovie(id: movie.id)
            applyFetched(fresh)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func refreshMetadata() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        do {
            let fresh = try await apiClient.refreshMovieMetadata(id: movie.id)
            applyFetched(fresh)
            let freshCast = (try? await apiClient.getMovieCast(id: movie.id)) ?? []
            cast = freshCast
            castImageRefreshToken += 1
        } catch {
            errorMessage = error.localizedDescription
        }
        isRefreshing = false
    }

    func updateMovie(_ draft: MovieEditDraft) async -> Bool {
        do {
            let fresh = try await apiClient.updateMovie(id: movie.id, draft: draft)
            applyFetched(fresh)
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func deleteMovie() async -> Bool {
        do {
            try await apiClient.deleteMovie(id: movie.id)
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    /// Applies a freshly fetched movie payload, preserving watchlist/watched
    /// state that the single-movie endpoint does not return.
    private func applyFetched(_ fresh: Movie) {
        movie = fresh
        if let onWatchlist = fresh.onWatchlist {
            isInWatchlist = onWatchlist
        }
        if let lw = fresh.lastWatched {
            lastWatched = lw
        }
    }
}
