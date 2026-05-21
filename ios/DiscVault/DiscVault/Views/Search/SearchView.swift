import SwiftUI

struct SearchView: View {
    @Environment(APIClient.self) private var apiClient

    @State private var searchText = ""
    @State private var results: [Movie] = []
    @State private var isSearching = false
    @State private var errorMessage: String? = nil
    @State private var searchTask: Task<Void, Never>? = nil

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.06, green: 0.06, blue: 0.14).ignoresSafeArea()

                if searchText.isEmpty {
                    emptyPrompt
                } else if isSearching {
                    ProgressView()
                        .tint(.white)
                } else if results.isEmpty {
                    noResultsView
                } else {
                    resultsList
                }
            }
            .navigationTitle("Search")
            .toolbarColorScheme(.dark, for: .navigationBar)
            .searchable(text: $searchText, prompt: "Title, director, genre…")
            .onChange(of: searchText) { _, newValue in
                scheduleSearch(query: newValue)
            }
        }
    }

    // MARK: - Views

    private var emptyPrompt: some View {
        VStack(spacing: 16) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 52))
                .foregroundStyle(.white.opacity(0.15))
            Text("Search your collection")
                .font(.title3)
                .foregroundStyle(.white.opacity(0.4))
            Text("Find movies by title, director, or genre")
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.25))
                .multilineTextAlignment(.center)
        }
    }

    private var noResultsView: some View {
        VStack(spacing: 16) {
            Image(systemName: "doc.text.magnifyingglass")
                .font(.system(size: 52))
                .foregroundStyle(.white.opacity(0.15))
            Text("No results for")
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.4))
            Text(""\(searchText)"")
                .font(.title3.bold())
                .foregroundStyle(.white.opacity(0.6))
        }
    }

    private var resultsList: some View {
        List(results) { movie in
            NavigationLink {
                MovieDetailView(movie: movie)
            } label: {
                MovieSearchRow(movie: movie, apiClient: apiClient)
            }
            .listRowBackground(Color.white.opacity(0.05))
            .listRowSeparatorTint(.white.opacity(0.08))
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
    }

    // MARK: - Search

    private func scheduleSearch(query: String) {
        searchTask?.cancel()
        guard !query.trimmingCharacters(in: .whitespaces).isEmpty else {
            results = []
            return
        }
        searchTask = Task {
            try? await Task.sleep(nanoseconds: 300_000_000) // 300ms debounce
            guard !Task.isCancelled else { return }

            await MainActor.run { isSearching = true }
            do {
                let found = try await apiClient.searchMovies(query: query)
                await MainActor.run {
                    results = found
                    isSearching = false
                }
            } catch {
                await MainActor.run {
                    results = []
                    isSearching = false
                    errorMessage = error.localizedDescription
                }
            }
        }
    }
}

// MARK: - Search Row

private struct MovieSearchRow: View {
    let movie: Movie
    let apiClient: APIClient

    var body: some View {
        HStack(spacing: 12) {
            // Thumbnail
            Group {
                if let url = apiClient.posterURL(for: movie.poster) {
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
                Text(movie.title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                    .lineLimit(2)

                HStack(spacing: 6) {
                    if let year = movie.year {
                        Text(year)
                            .font(.caption)
                            .foregroundStyle(.white.opacity(0.5))
                    }
                    if let format = movie.format {
                        formatBadge(format)
                    }
                }

                if let director = movie.director {
                    Text(director)
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.4))
                        .lineLimit(1)
                }
            }

            Spacer()
        }
        .padding(.vertical, 4)
    }

    private var posterPlaceholder: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 6)
                .fill(Color.white.opacity(0.08))
            Image(systemName: "opticaldisc")
                .foregroundStyle(.white.opacity(0.25))
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
            .padding(.horizontal, 5)
            .padding(.vertical, 2)
            .background(color)
            .clipShape(RoundedRectangle(cornerRadius: 4))
    }
}
