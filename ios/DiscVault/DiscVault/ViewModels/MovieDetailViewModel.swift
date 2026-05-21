import SwiftUI

@Observable
final class MovieDetailViewModel {
    var movie: Movie
    var isLoading = false
    var isInWatchlist = false
    var errorMessage: String? = nil

    private let apiClient: APIClient

    init(movie: Movie, apiClient: APIClient) {
        self.movie = movie
        self.apiClient = apiClient
        self.isInWatchlist = movie.wanted ?? false
    }

    func loadDetails() async {
        isLoading = true
        do {
            movie = try await apiClient.getMovie(id: movie.id)
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    func toggleWatchlist() async {
        do {
            if isInWatchlist {
                try await apiClient.removeFromWatchlist(movieId: movie.id)
            } else {
                try await apiClient.addToWatchlist(movieId: movie.id)
            }
            isInWatchlist.toggle()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func markAsWatched() async {
        do {
            try await apiClient.addToWatchHistory(movieId: movie.id)
        } catch {
            errorMessage = error.localizedDescription
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
}
