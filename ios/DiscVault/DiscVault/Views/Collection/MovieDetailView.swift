import SwiftUI

struct MovieDetailView: View {
    let movie: Movie
    @Environment(APIClient.self) private var apiClient
    @Environment(\.dismiss) private var dismiss

    @State private var viewModel: MovieDetailViewModel?

    var body: some View {
        ZStack {
            Color(red: 0.06, green: 0.06, blue: 0.14).ignoresSafeArea()

            if let vm = viewModel {
                detail(vm: vm)
            } else {
                ProgressView().tint(.white)
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .task {
            if viewModel == nil {
                let vm = MovieDetailViewModel(movie: movie, apiClient: apiClient)
                viewModel = vm
                await vm.loadDetails()
            }
        }
    }

    @ViewBuilder
    private func detail(vm: MovieDetailViewModel) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                heroSection(vm: vm)
                actionsSection(vm: vm)
                    .padding(.horizontal, 16)
                    .padding(.top, 16)
                infoSection(vm: vm)
                    .padding(.horizontal, 16)
                    .padding(.top, 20)
                plotSection(vm: vm)
                    .padding(.horizontal, 16)
                    .padding(.top, 20)
                castSection(vm: vm)
                    .padding(.top, 20)
                Spacer().frame(height: 40)
            }
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                ShareLink(
                    item: "\(vm.movie.title) (\(vm.movie.year ?? ""))",
                    subject: Text("Movie"),
                    message: Text("Check out \(vm.movie.title) on DiscVault!")
                )
                .foregroundStyle(.white)
            }
        }
        .alert("Error", isPresented: Binding(
            get: { vm.errorMessage != nil },
            set: { if !$0 { vm.errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(vm.errorMessage ?? "")
        }
    }

    // MARK: - Hero

    private func heroSection(vm: MovieDetailViewModel) -> some View {
        ZStack(alignment: .bottomLeading) {
            // Backdrop
            Group {
                if let url = apiClient.posterURL(for: vm.movie.backdrop ?? vm.movie.poster) {
                    AsyncImage(url: url) { phase in
                        if case .success(let img) = phase {
                            img.resizable().aspectRatio(contentMode: .fill)
                        } else {
                            backdropPlaceholder
                        }
                    }
                } else {
                    backdropPlaceholder
                }
            }
            .frame(maxWidth: .infinity)
            .frame(height: 240)
            .clipped()
            .overlay(
                LinearGradient(
                    colors: [.clear, Color(red: 0.06, green: 0.06, blue: 0.14)],
                    startPoint: .top,
                    endPoint: .bottom
                )
            )

            // Poster + title overlay
            HStack(alignment: .bottom, spacing: 14) {
                // Poster
                if let url = apiClient.posterURL(for: vm.movie.poster) {
                    AsyncImage(url: url) { phase in
                        if case .success(let img) = phase {
                            img.resizable().aspectRatio(contentMode: .fill)
                                .frame(width: 90, height: 135)
                                .clipShape(RoundedRectangle(cornerRadius: 10))
                                .shadow(radius: 10)
                        }
                    }
                }

                VStack(alignment: .leading, spacing: 6) {
                    Text(vm.movie.title)
                        .font(.title2.bold())
                        .foregroundStyle(.white)
                        .lineLimit(3)

                    HStack(spacing: 8) {
                        if let year = vm.movie.year {
                            Text(year)
                                .font(.caption)
                                .foregroundStyle(.white.opacity(0.6))
                        }
                        if let runtime = vm.movie.runtime {
                            Text("·")
                                .foregroundStyle(.white.opacity(0.3))
                            Text(runtime)
                                .font(.caption)
                                .foregroundStyle(.white.opacity(0.6))
                        }
                    }

                    if let format = vm.movie.format {
                        formatBadge(format)
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 16)
        }
    }

    private var backdropPlaceholder: some View {
        LinearGradient(
            colors: [Color(red: 0.15, green: 0.1, blue: 0.3), Color(red: 0.06, green: 0.06, blue: 0.14)],
            startPoint: .top, endPoint: .bottom
        )
    }

    // MARK: - Actions

    private func actionsSection(vm: MovieDetailViewModel) -> some View {
        HStack(spacing: 10) {
            ActionButton(
                icon: vm.isInWatchlist ? "bookmark.fill" : "bookmark",
                label: vm.isInWatchlist ? "In Watchlist" : "Watchlist",
                color: vm.isInWatchlist ? .yellow : .white.opacity(0.7)
            ) {
                Task { await vm.toggleWatchlist() }
            }

            ActionButton(icon: "eye", label: "Mark Watched", color: .green.opacity(0.8)) {
                Task { await vm.markAsWatched() }
            }

            ActionButton(icon: "trash", label: "Delete", color: .red.opacity(0.8)) {
                Task {
                    if await vm.deleteMovie() {
                        dismiss()
                    }
                }
            }
        }
    }

    // MARK: - Info Grid

    private func infoSection(vm: MovieDetailViewModel) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Details")
                .font(.headline)
                .foregroundStyle(.white)

            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                if let rating = vm.movie.ratingUs {
                    InfoCell(label: "Rating", value: rating)
                }
                if let director = vm.movie.director {
                    InfoCell(label: "Director", value: director)
                }
                if let genre = vm.movie.genre {
                    InfoCell(label: "Genre", value: genre)
                }
                if let hdr = vm.movie.hdr {
                    InfoCell(label: "HDR", value: hdr)
                }
                if let audio = vm.movie.audioCodec {
                    InfoCell(label: "Audio", value: audio)
                }
            }
        }
    }

    // MARK: - Plot

    private func plotSection(vm: MovieDetailViewModel) -> some View {
        Group {
            if let plot = vm.movie.plot, !plot.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Plot")
                        .font(.headline)
                        .foregroundStyle(.white)
                    Text(plot)
                        .font(.subheadline)
                        .foregroundStyle(.white.opacity(0.7))
                        .lineSpacing(4)
                }
            }
        }
    }

    // MARK: - Cast

    private func castSection(vm: MovieDetailViewModel) -> some View {
        Group {
            if let cast = vm.movie.cast, !cast.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Cast & Crew")
                        .font(.headline)
                        .foregroundStyle(.white)
                        .padding(.horizontal, 16)

                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 12) {
                            ForEach(cast) { member in
                                CastCardView(member: member, apiClient: apiClient)
                            }
                        }
                        .padding(.horizontal, 16)
                    }
                }
            }
        }
    }

    // MARK: - Helpers

    private func formatBadge(_ format: String) -> some View {
        let (label, color): (String, Color) = switch format {
        case "4K UHD": ("4K UHD", Color(red: 0.45, green: 0.15, blue: 0.85))
        case "Blu-ray": ("Blu-ray", Color(red: 0.15, green: 0.4, blue: 0.85))
        default: ("DVD", Color(red: 0.35, green: 0.35, blue: 0.45))
        }
        return Text(label)
            .font(.caption.weight(.semibold))
            .foregroundStyle(.white)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(color)
            .clipShape(RoundedRectangle(cornerRadius: 5))
    }
}

// MARK: - Sub-components

private struct ActionButton: View {
    let icon: String
    let label: String
    let color: Color
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 6) {
                Image(systemName: icon)
                    .font(.system(size: 20))
                    .foregroundStyle(color)
                Text(label)
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.6))
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 12)
            .background(.white.opacity(0.07))
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
    }
}

private struct InfoCell: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.caption2)
                .foregroundStyle(.white.opacity(0.4))
                .textCase(.uppercase)
            Text(value)
                .font(.caption)
                .foregroundStyle(.white.opacity(0.85))
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.white.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

private struct CastCardView: View {
    let member: CastMember
    let apiClient: APIClient

    var body: some View {
        VStack(spacing: 8) {
            Group {
                if let url = apiClient.posterURL(for: member.profilePhoto) {
                    AsyncImage(url: url) { phase in
                        if case .success(let img) = phase {
                            img.resizable().aspectRatio(contentMode: .fill)
                        } else {
                            placeholder
                        }
                    }
                } else {
                    placeholder
                }
            }
            .frame(width: 64, height: 64)
            .clipShape(Circle())

            Text(member.name)
                .font(.caption2.weight(.medium))
                .foregroundStyle(.white.opacity(0.85))
                .lineLimit(2)
                .multilineTextAlignment(.center)

            if let character = member.character, !character.isEmpty {
                Text(character)
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.4))
                    .lineLimit(1)
            }
        }
        .frame(width: 72)
    }

    private var placeholder: some View {
        ZStack {
            Circle().fill(Color.white.opacity(0.1))
            Image(systemName: "person.fill")
                .foregroundStyle(.white.opacity(0.3))
        }
    }
}
