import SwiftUI
import WebKit

private enum MovieDetailTab: Hashable {
    case info
    case cast
    case backdrops
    case videos
}

struct MovieDetailView: View {
    let movie: Movie
    @Environment(APIClient.self) private var apiClient
    @Environment(AppLanguageManager.self) private var languageManager
    @Environment(\.dismiss) private var dismiss

    @State private var viewModel: MovieDetailViewModel?
    @State private var selectedTab: MovieDetailTab = .info
    @State private var playingVideoKey: String?
    @State private var showWatchedMenu = false
    @State private var showWatchedDatePicker = false
    @State private var showContainerAssignment = false
    @State private var showMovieEditSheet = false
    @State private var customWatchedDate: Date = Date()
    @State private var isDebugModeEnabled = false
    @State private var preferredRatingCountry = "NL"

    private let backgroundColor = Color(red: 0.06, green: 0.06, blue: 0.14)

    var body: some View {
        ZStack {
            backgroundColor.ignoresSafeArea()

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
                await loadDebugMode()
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
                tabBar
                    .padding(.horizontal, 16)
                    .padding(.top, 20)
                tabContent(vm: vm)
                    .padding(.top, 16)
                Spacer().frame(height: 40)
            }
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                HStack(spacing: 12) {
                    Button {
                        Task { await vm.refreshMetadata() }
                    } label: {
                        if vm.isRefreshing {
                            ProgressView().tint(.white)
                        } else {
                            Image(systemName: "arrow.clockwise")
                        }
                    }
                    .disabled(vm.isRefreshing)
                    .accessibilityLabel("Refresh")

                    ShareLink(
                        item: "\(vm.movie.title) (\(vm.movie.year ?? ""))",
                        subject: Text("Movie"),
                        message: Text("Check out \(vm.movie.title) on DiscVault!")
                    )
                }
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
        .confirmationDialog(
            localized("modal.watched", fallback: "Mark as watched"),
            isPresented: $showWatchedMenu,
            titleVisibility: .visible
        ) {
            Button(localized("watched.yesterday", fallback: "Yesterday")) {
                let yesterday = Calendar.current.date(byAdding: .day, value: -1, to: Date()) ?? Date()
                Task { await vm.markAsWatched(on: yesterday) }
            }
            Button(localized("watched.today", fallback: "Today")) {
                Task { await vm.markAsWatched(on: Date()) }
            }
            Button(localized("watched.pickDate", fallback: "Pick a date…")) {
                customWatchedDate = Date()
                showWatchedDatePicker = true
            }
            Button("Cancel", role: .cancel) {}
        }
        .sheet(isPresented: $showWatchedDatePicker) {
            watchedDatePickerSheet(vm: vm)
        }
        .sheet(isPresented: $showContainerAssignment) {
            MovieContainerAssignmentSheet(movie: vm.movie) {
                await vm.loadDetails()
            }
        }
        .sheet(isPresented: $showMovieEditSheet) {
            MovieEditSheet(movie: vm.movie) { draft in
                await vm.updateMovie(draft)
            }
        }
    }

    @ViewBuilder
    private func watchedDatePickerSheet(vm: MovieDetailViewModel) -> some View {
        NavigationStack {
            VStack {
                DatePicker(
                    localized("watched.pickDate", fallback: "Pick a date"),
                    selection: $customWatchedDate,
                    in: ...Date(),
                    displayedComponents: .date
                )
                .datePickerStyle(.graphical)
                .padding()
                Spacer()
            }
            .navigationTitle(localized("modal.watched", fallback: "Mark as watched"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { showWatchedDatePicker = false }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Save") {
                        let date = customWatchedDate
                        showWatchedDatePicker = false
                        Task { await vm.markAsWatched(on: date) }
                    }
                    .bold()
                }
            }
        }
        .presentationDetents([.medium])
    }

    // MARK: - Hero

    private func heroSection(vm: MovieDetailViewModel) -> some View {
        ZStack(alignment: .bottomLeading) {
            // Backdrop
            SwiftUI.Group {
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
                    colors: [.clear, backgroundColor],
                    startPoint: .top,
                    endPoint: .bottom
                )
            )

            // Poster + title overlay
            HStack(alignment: .bottom, spacing: 14) {
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
                    Text(vm.movie.localizedTitle(languageManager.languageCode))
                        .font(.title2.bold())
                        .foregroundStyle(.white)
                        .lineLimit(3)

                    if isDebugModeEnabled {
                        debugIdentityBadge(for: vm.movie)
                    }

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
            colors: [Color(red: 0.15, green: 0.1, blue: 0.3), backgroundColor],
            startPoint: .top, endPoint: .bottom
        )
    }

    private func loadDebugMode() async {
        async let debug = apiClient.getDebugSetting()
        async let preferences = apiClient.getUserPreferences()
        isDebugModeEnabled = (try? await debug)?.debugEnabled ?? false
        preferredRatingCountry = (try? await preferences)?["rating_country"] ?? "NL"
    }

    private func debugIdentityBadge(for movie: Movie) -> some View {
        let identity = debugIdentity(for: movie)
        return Label("\(identity.type) #\(identity.id)", systemImage: "number")
            .font(.caption2.weight(.bold))
            .foregroundStyle(.white.opacity(0.9))
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(.black.opacity(0.45), in: Capsule())
            .overlay(Capsule().stroke(.white.opacity(0.18), lineWidth: 0.5))
    }

    private func debugIdentity(for movie: Movie) -> (type: String, id: Int) {
        if movie.isCollection == true {
            return ("Collection", movie.collectionCardId ?? movie.collectionId ?? movie.id)
        }
        if movie.isSuperGroup == true {
            return ("Box-set", movie.parentGroupId ?? movie.superGroupId ?? movie.editionGroupId ?? movie.id)
        }
        if movie.isGroup == true {
            return ("Vault", movie.editionGroupId ?? movie.id)
        }
        return ("Movie", movie.id)
    }

    // MARK: - Actions

    private func actionsSection(vm: MovieDetailViewModel) -> some View {
        HStack(spacing: 10) {
            ActionButton(
                icon: vm.isInWatchlist ? "bookmark.fill" : "bookmark",
                label: vm.isInWatchlist
                    ? localized("modal.inWatchlist", fallback: "In Watchlist")
                    : "Watchlist",
                color: vm.isInWatchlist ? .yellow : .white.opacity(0.7)
            ) {
                Task { await vm.toggleWatchlist() }
            }

            ActionButton(
                icon: vm.lastWatched != nil ? "eye.fill" : "eye",
                label: watchedLabel(for: vm),
                color: vm.lastWatched != nil ? .green : .white.opacity(0.7)
            ) {
                showWatchedMenu = true
            }

            ActionButton(
                icon: "square.and.pencil",
                label: localized("admin.edit", fallback: "Edit"),
                color: Color(red: 0.91, green: 0.77, blue: 0.28)
            ) {
                showMovieEditSheet = true
            }

            ActionButton(
                icon: "trash",
                label: localized("admin.delete", fallback: "Delete"),
                color: .red.opacity(0.8)
            ) {
                Task {
                    if await vm.deleteMovie() {
                        dismiss()
                    }
                }
            }
        }
    }

    private func watchedLabel(for vm: MovieDetailViewModel) -> String {
        guard let date = vm.lastWatched, !date.isEmpty else {
            return localized("modal.watched", fallback: "Watched")
        }
        let display = String(date.prefix(10))
        return localized("modal.watched", fallback: "Watched") + " · " + display
    }

    // MARK: - Tab Bar

    private var tabBar: some View {
        HStack(spacing: 0) {
            tabButton(.info, title: localized("modal.tabInfo", fallback: "Info"))
            tabButton(.cast, title: localized("modal.tabCast", fallback: "Cast"))
            tabButton(.backdrops, title: localized("modal.backdrops", fallback: "Backdrops"))
            tabButton(.videos, title: localized("modal.tabVideos", fallback: "Videos"))
        }
        .padding(4)
        .background(.white.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private func tabButton(_ tab: MovieDetailTab, title: String) -> some View {
        Button {
            withAnimation(.easeInOut(duration: 0.18)) {
                selectedTab = tab
            }
        } label: {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(selectedTab == tab ? .white : .white.opacity(0.55))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .background(
                    SwiftUI.Group {
                        if selectedTab == tab {
                            RoundedRectangle(cornerRadius: 7)
                                .fill(.white.opacity(0.12))
                        }
                    }
                )
        }
        .buttonStyle(.plain)
    }

    /// Returns the localized string for `key`, or `fallback` if the key is not translated.
    private func localized(_ key: String, fallback: String) -> String {
        let value = languageManager.text(key)
        return value == key ? fallback : value
    }

    // MARK: - Tab Content

    @ViewBuilder
    private func tabContent(vm: MovieDetailViewModel) -> some View {
        switch selectedTab {
        case .info:
            VStack(alignment: .leading, spacing: 20) {
                infoSection(vm: vm)
                plotSection(vm: vm)
            }
            .padding(.horizontal, 16)
        case .cast:
            castSection(vm: vm)
        case .backdrops:
            backdropsSection(vm: vm)
                .padding(.horizontal, 16)
        case .videos:
            videosSection(vm: vm)
                .padding(.horizontal, 16)
        }
    }

    // MARK: - Info Grid

    private func infoSection(vm: MovieDetailViewModel) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(localized("modal.details", fallback: "Details"))
                .font(.headline)
                .foregroundStyle(.white)

            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                if let releaseDate = nonEmpty(vm.movie.releaseDate) {
                    InfoCell(label: "Releasedatum", value: releaseDate)
                }
                if let editionDate = nonEmpty(vm.movie.editionReleaseDate) {
                    InfoCell(label: "Uitgiftedatum", value: editionDate)
                }
                if let runtime = nonEmpty(vm.movie.runtime) {
                    InfoCell(label: "Speelduur", value: runtime)
                }
                if let rating = preferredContentRating(for: vm.movie) {
                    InfoCell(label: localized("edit.audienceRating", fallback: "Rating"), value: "\(flagEmoji(for: preferredRatingCountry)) \(rating)")
                }
                if let rating = tmdbRatingText(for: vm.movie) {
                    InfoCell(label: "TMDb rating", value: rating)
                }
                if let director = nonEmpty(vm.movie.director) {
                    InfoCell(label: localized("edit.director", fallback: "Director"), value: director)
                }
                if let genre = nonEmpty(vm.movie.genre) {
                    InfoCell(label: localized("edit.genre", fallback: "Genre"), value: genre)
                }
                if let country = nonEmpty(vm.movie.country) {
                    InfoCell(label: "Land van uitgifte", value: country)
                }
                if let language = nonEmpty(vm.movie.language) {
                    InfoCell(label: "Taal", value: language)
                }
                if let studios = nonEmpty(vm.movie.studios) {
                    InfoCell(label: "Studio", value: studios)
                }
                if let screenRatios = nonEmpty(vm.movie.screenRatios) {
                    InfoCell(label: "Schermverhouding", value: screenRatios)
                }
                if let regions = nonEmpty(vm.movie.regions) {
                    InfoCell(label: "Regio", value: regions)
                }
                if let hdr = nonEmpty(vm.movie.hdr) {
                    InfoCell(label: "HDR", value: hdr)
                }
                if let audio = audioSummary(for: vm.movie) {
                    InfoCell(label: localized("edit.audioTracks", fallback: "Audio"), value: audio)
                }
                if let subtitles = compactListSummary(vm.movie.subtitles) {
                    InfoCell(label: localized("edit.subtitles", fallback: "Subtitles"), value: subtitles)
                }
                if let addedAt = formattedDate(vm.movie.addedAt) {
                    InfoCell(label: "Toegevoegd", value: addedAt)
                }
            }
        }
    }

    // MARK: - Plot

    @ViewBuilder
    private func plotSection(vm: MovieDetailViewModel) -> some View {
        if let plot = vm.movie.localizedPlot(languageManager.languageCode), !plot.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text(localized("modal.plot", fallback: "Plot"))
                    .font(.headline)
                    .foregroundStyle(.white)
                Text(plot)
                    .font(.subheadline)
                    .foregroundStyle(.white.opacity(0.7))
                    .lineSpacing(4)
            }
        }

        if isDebugModeEnabled {
            debugLocalizedMetadata(movie: vm.movie)
                .padding(.top, 16)
        }
    }

    // MARK: - Cast

    @ViewBuilder
    private func castSection(vm: MovieDetailViewModel) -> some View {
        if !vm.cast.isEmpty {
            let groups = castGroups(from: vm.cast)
            VStack(alignment: .leading, spacing: 22) {
                ForEach(Array(groups.enumerated()), id: \.offset) { _, group in
                    castGroupSection(
                        title: group.title,
                        members: group.members,
                        imageRefreshToken: vm.castImageRefreshToken
                    )
                }
            }
            .padding(.horizontal, 16)
        } else {
            VStack {
                Text(localized("modal.noCast", fallback: "No cast/crew found. Refresh metadata to fetch this data."))
                    .font(.subheadline)
                    .foregroundStyle(.white.opacity(0.5))
                    .multilineTextAlignment(.center)
                    .padding(24)
            }
            .frame(maxWidth: .infinity)
        }
    }

    private func castGroupSection(title: String, members: [CastMember], imageRefreshToken: Int) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Text(title)
                Text("(\(members.count))")
                    .foregroundStyle(.white.opacity(0.35))
            }
            .font(.caption.weight(.semibold))
            .foregroundStyle(.white.opacity(0.55))
            .textCase(.uppercase)
            .padding(.bottom, 4)
            .overlay(alignment: .bottom) {
                Rectangle()
                    .fill(.white.opacity(0.08))
                    .frame(height: 1)
            }

            LazyVGrid(columns: [
                GridItem(.adaptive(minimum: 95, maximum: 130), spacing: 10)
            ], spacing: 14) {
                ForEach(members) { member in
                    NavigationLink {
                        PersonDetailView(personId: member.personId, initialName: member.name)
                    } label: {
                        CastCardView(
                            member: member,
                            apiClient: apiClient,
                            imageRefreshToken: imageRefreshToken
                        )
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    /// Mirrors the PWA grouping: actors first, then crew grouped by `job`,
    /// preserving the order in which jobs first appear in the API response.
    private func castGroups(from cast: [CastMember]) -> [(title: String, members: [CastMember])] {
        var result: [(title: String, members: [CastMember])] = []

        let actors = cast.filter { ($0.role ?? "").lowercased() == "actor" || ($0.role ?? "").lowercased() == "cast" }
        if !actors.isEmpty {
            result.append((localized("d.actors", fallback: "Acteurs"), actors))
        }

        var jobOrder: [String] = []
        var crewByJob: [String: [CastMember]] = [:]
        for member in cast {
            let role = (member.role ?? "").lowercased()
            guard role == "crew" else { continue }
            let job = (member.job?.isEmpty == false ? member.job! : "Other")
            if crewByJob[job] == nil {
                jobOrder.append(job)
                crewByJob[job] = []
            }
            crewByJob[job]!.append(member)
        }
        for job in jobOrder {
            result.append((job, crewByJob[job] ?? []))
        }
        return result
    }

    // MARK: - Backdrops

    @ViewBuilder
    private func backdropsSection(vm: MovieDetailViewModel) -> some View {
        let urls = availableBackdrops(for: vm.movie)
        if urls.isEmpty {
            VStack(spacing: 8) {
                Image(systemName: "photo.on.rectangle.angled")
                    .font(.system(size: 36))
                    .foregroundStyle(.white.opacity(0.25))
                Text(localized("modal.noMedia", fallback: "No backdrops available. Refresh metadata to fetch this data."))
                    .font(.subheadline)
                    .foregroundStyle(.white.opacity(0.5))
                    .multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 40)
        } else {
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                ForEach(urls, id: \.self) { url in
                    backdropCard(url: url, isActive: url == vm.movie.backdrop, vm: vm)
                }
            }
        }
    }

    private func backdropCard(url: String, isActive: Bool, vm: MovieDetailViewModel) -> some View {
        Button {
            guard !isActive else { return }
            Task { await vm.setBackdrop(url) }
        } label: {
            ZStack(alignment: .topTrailing) {
                AsyncImage(url: apiClient.posterURL(for: url)) { phase in
                    switch phase {
                    case .success(let img):
                        img.resizable().aspectRatio(contentMode: .fill)
                    default:
                        Color.white.opacity(0.05)
                    }
                }
                .aspectRatio(16/9, contentMode: .fill)
                .frame(maxWidth: .infinity)
                .clipped()
                .overlay(
                    RoundedRectangle(cornerRadius: 10)
                        .stroke(isActive ? Color.yellow : Color.white.opacity(0.08), lineWidth: isActive ? 2 : 1)
                )
                .clipShape(RoundedRectangle(cornerRadius: 10))

                if isActive {
                    Text(localized("edition.egContainerBackdrop", fallback: "Backdrop"))
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(.black)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 3)
                        .background(.yellow)
                        .clipShape(RoundedRectangle(cornerRadius: 4))
                        .padding(6)
                }
            }
        }
        .buttonStyle(.plain)
    }

    private func availableBackdrops(for movie: Movie) -> [String] {
        var result = movie.parsedBackdrops
        if result.isEmpty, let primary = movie.backdrop, !primary.isEmpty {
            result = [primary]
        } else if let primary = movie.backdrop, !primary.isEmpty, !result.contains(primary) {
            result.insert(primary, at: 0)
        }
        return result
    }

    // MARK: - Videos

    @ViewBuilder
    private func videosSection(vm: MovieDetailViewModel) -> some View {
        let groups = groupedVideos(for: vm.movie)
        if groups.isEmpty {
            VStack(spacing: 8) {
                Image(systemName: "play.rectangle.on.rectangle")
                    .font(.system(size: 36))
                    .foregroundStyle(.white.opacity(0.25))
                Text(localized("modal.noVideosAuto", fallback: "No videos available. Refresh metadata to fetch trailers and clips."))
                    .font(.subheadline)
                    .foregroundStyle(.white.opacity(0.5))
                    .multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 40)
        } else {
            VStack(alignment: .leading, spacing: 22) {
                ForEach(groups, id: \.heading) { group in
                    VStack(alignment: .leading, spacing: 10) {
                        Text(group.heading)
                            .font(.headline)
                            .foregroundStyle(.white)

                        ForEach(group.items) { item in
                            videoCard(item: item)
                        }
                    }
                }
            }
        }
    }

    private func videoCard(item: MovieVideo) -> some View {
        guard let key = item.youtubeKey else {
            return AnyView(EmptyView())
        }
        let label = (item.label?.isEmpty == false ? item.label : item.type) ?? localized("modal.trailer", fallback: "Trailer")

        return AnyView(
            VStack(alignment: .leading, spacing: 6) {
                if !label.isEmpty {
                    Text(label)
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.white.opacity(0.85))
                        .lineLimit(2)
                }
                YouTubeThumbnailView(
                    youtubeKey: key,
                    isPlaying: playingVideoKey == key,
                    onPlay: { playingVideoKey = key }
                )
                .aspectRatio(16/9, contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        )
    }

    // MARK: - Video grouping

    private struct VideoGroup {
        let heading: String
        let items: [MovieVideo]
    }

    private func groupedVideos(for movie: Movie) -> [VideoGroup] {
        let videos = movie.allVideos
        guard !videos.isEmpty else { return [] }

        let knownOrder = ["Trailer", "Teaser", "Clip", "Featurette", "Behind the Scenes", "Bloopers"]
        var buckets: [String: [MovieVideo]] = [:]
        var otherItems: [MovieVideo] = []

        for video in videos {
            if let type = video.type, knownOrder.contains(type) {
                buckets[type, default: []].append(video)
            } else {
                otherItems.append(video)
            }
        }

        var groups: [VideoGroup] = []
        for type in knownOrder {
            if let items = buckets[type], !items.isEmpty {
                groups.append(VideoGroup(heading: headingForVideoType(type), items: items))
            }
        }
        if !otherItems.isEmpty {
            groups.append(VideoGroup(heading: localized("modal.videoTypeOther", fallback: "Other"), items: otherItems))
        }
        return groups
    }

    private func headingForVideoType(_ type: String) -> String {
        switch type {
        case "Trailer": return localized("modal.trailer", fallback: "Trailer")
        default: return type
        }
    }

    // MARK: - Helpers

    private func preferredContentRating(for movie: Movie) -> String? {
        if let contentRatings = movie.contentRatings,
           let data = contentRatings.data(using: .utf8),
           let ratings = try? JSONDecoder().decode([String: String].self, from: data) {
            let country = preferredRatingCountry.uppercased()
            if let value = ratings[country], !value.isEmpty {
                return value
            }
        }
        return movie.audienceRating ?? movie.ratingUs
    }

    private func tmdbRatingText(for movie: Movie) -> String? {
        let rating = movie.voteAverage.map { String(format: "%.1f", $0) } ?? nonEmpty(movie.rating)
        guard let rating else { return nil }
        if let voteCount = movie.voteCount, voteCount > 0 {
            return "\(rating) · \(voteCount) stemmen"
        }
        return rating
    }

    private func audioSummary(for movie: Movie) -> String? {
        compactListSummary(movie.audioTracks) ?? nonEmpty(movie.audioCodec)
    }

    private func compactListSummary(_ value: String?) -> String? {
        guard let value = nonEmpty(value) else { return nil }
        let parts = value
            .replacingOccurrences(of: "\n", with: ",")
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        guard parts.count > 3 else { return value }
        return parts.prefix(3).joined(separator: ", ") + " +\(parts.count - 3)"
    }

    private func formattedDate(_ value: String?) -> String? {
        guard let value = nonEmpty(value) else { return nil }
        return String(value.prefix(10))
    }

    private func nonEmpty(_ value: String?) -> String? {
        let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return trimmed.isEmpty ? nil : trimmed
    }

    private func flagEmoji(for countryCode: String) -> String {
        let base: UInt32 = 127397
        return countryCode.uppercased().unicodeScalars.compactMap { scalar in
            UnicodeScalar(base + scalar.value).map(String.init)
        }.joined()
    }

    @ViewBuilder
    private func debugLocalizedMetadata(movie: Movie) -> some View {
        let languages: [(code: String, country: String, label: String)] = [
            ("nl", "NL", "Nederlands"),
            ("en", "GB", "English"),
            ("fr", "FR", "Français"),
            ("de", "DE", "Deutsch"),
            ("es", "ES", "Español"),
            ("pt", "PT", "Português"),
            ("it", "IT", "Italiano")
        ]

        VStack(alignment: .leading, spacing: 10) {
            Text("Debug metadata")
                .font(.headline)
                .foregroundStyle(.white)

            ForEach(languages, id: \.code) { language in
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 6) {
                        if let flag = flagAssetName(for: language.country) {
                            Image(flag)
                                .resizable()
                                .aspectRatio(contentMode: .fit)
                                .frame(width: 18, height: 12)
                                .clipShape(RoundedRectangle(cornerRadius: 2))
                        }
                        Text(language.label)
                            .font(.caption.weight(.bold))
                            .foregroundStyle(.white.opacity(0.75))
                    }
                    Text(language.code == "en" ? movie.title : movie.localizedTitle(language.code))
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.white)
                    if let plot = language.code == "en" ? movie.plot : movie.localizedPlot(language.code), !plot.isEmpty {
                        Text(plot)
                            .font(.caption)
                            .foregroundStyle(.white.opacity(0.65))
                    }
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.white.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
            }
        }
    }

    private func flagAssetName(for countryCode: String) -> String? {
        switch countryCode.uppercased() {
        case "NL": return "FlagNL"
        case "GB", "UK", "EN": return "FlagGB"
        case "FR": return "FlagFR"
        case "DE": return "FlagDE"
        case "ES": return "FlagES"
        case "PT": return "FlagPT"
        case "IT": return "FlagIT"
        default: return nil
        }
    }

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
    var isBusy: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 6) {
                ZStack {
                    Image(systemName: icon)
                        .font(.system(size: 20))
                        .foregroundStyle(color)
                        .opacity(isBusy ? 0 : 1)
                    if isBusy {
                        ProgressView()
                            .tint(.white)
                            .scaleEffect(0.8)
                    }
                }
                .frame(height: 22)
                Text(label)
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.6))
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 12)
            .background(.white.opacity(0.07))
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
        .disabled(isBusy)
    }
}

private enum MovieEditTab: String, CaseIterable, Identifiable {
    case general
    case edition
    case details

    var id: String { rawValue }
}

private struct MovieEditSheet: View {
    let movie: Movie
    let onSave: (MovieEditDraft) async -> Bool

    @Environment(APIClient.self) private var apiClient
    @Environment(AppLanguageManager.self) private var languageManager
    @Environment(\.dismiss) private var dismiss
    @State private var selectedTab: MovieEditTab = .general
    @State private var draft: MovieEditDraft
    @State private var isSaving = false
    @State private var containerSearchText = ""
    @State private var editionGroups: [EditionGroup] = []
    @State private var collections: [DiscCollection] = []
    @State private var isLoadingContainers = false

    init(movie: Movie, onSave: @escaping (MovieEditDraft) async -> Bool) {
        self.movie = movie
        self.onSave = onSave
        _draft = State(initialValue: MovieEditDraft(movie: movie))
    }

    var body: some View {
        NavigationStack {
            Form {
                Picker("Tab", selection: $selectedTab) {
                    ForEach(MovieEditTab.allCases) { tab in
                        Text(title(for: tab)).tag(tab)
                    }
                }
                .pickerStyle(.segmented)

                switch selectedTab {
                case .general:
                    generalSection
                case .edition:
                    editionSection
                case .details:
                    detailsSection
                }
            }
            .navigationTitle(localized("edit.title", fallback: "Film bewerken"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(localized("bulk.cancelGroup", fallback: "Annuleer")) { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task {
                            isSaving = true
                            if await onSave(draft) {
                                dismiss()
                            }
                            isSaving = false
                        }
                    } label: {
                        if isSaving {
                            ProgressView()
                        } else {
                            Text(localized("edit.save", fallback: "Bewaar"))
                        }
                    }
                    .disabled(isSaving || draft.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
        .preferredColorScheme(.dark)
        .task { await loadContainers() }
    }

    private var generalSection: some View {
        SwiftUI.Group {
            Section(localized("settings.menuGeneral", fallback: "Algemeen")) {
                labeledField(localized("edit.titleLabel", fallback: "Titel"), text: $draft.title)
                labeledField(localized("edit.originalTitle", fallback: "Originele titel"), text: $draft.originalTitle)
                labeledField(localized("d.year", fallback: "Jaar"), text: $draft.year, keyboardType: .numberPad)
                labeledField(localized("edit.releaseDate", fallback: "Releasedatum"), text: $draft.releaseDate)
                labeledField(localized("edit.director", fallback: "Regisseur"), text: $draft.director)
                labeledField(localized("edit.actors", fallback: "Acteurs"), text: $draft.actor, axis: .vertical, lineLimit: 2...4)
                labeledField(localized("edit.producer", fallback: "Producent"), text: $draft.producer)
                labeledField(localized("edit.studios", fallback: "Studio's"), text: $draft.studios)
                labeledField(localized("edit.genre", fallback: "Genre"), text: $draft.genre)
                labeledField(localized("edit.plot", fallback: "Plot"), text: $draft.plot, axis: .vertical, lineLimit: 4...8)
            }
        }
    }

    private func title(for tab: MovieEditTab) -> String {
        switch tab {
        case .general:
            return localized("settings.menuGeneral", fallback: "Algemeen")
        case .edition:
            return localized("edit.edition", fallback: "Editie")
        case .details:
            return localized("modal.details", fallback: "Details")
        }
    }

    private var editionSection: some View {
        SwiftUI.Group {
            Section(localized("edit.edition", fallback: "Editie")) {
                Picker(localized("edit.format", fallback: "Formaat"), selection: $draft.format) {
                    Text("4K UHD").tag("4K UHD")
                    Text("Blu-ray").tag("Blu-ray")
                    Text("DVD").tag("DVD")
                }
                labeledField(localized("add.barcodeLabel", fallback: "Barcode"), text: $draft.barcode, keyboardType: .numberPad)
                labeledField(localized("edit.location", fallback: "Locatie"), text: $draft.location)
                labeledField(localized("edit.notes", fallback: "Notities"), text: $draft.notes, axis: .vertical, lineLimit: 2...5)
                labeledField(localized("edit.editionType", fallback: "Editie type"), text: $draft.editionType)
                labeledField(localized("edit.edition", fallback: "Editie"), text: $draft.edition)
                labeledField(localized("edit.customEditionLabel", fallback: "Custom editie label"), text: $draft.customEditionLabel)
                labeledField(localized("edit.editionYear", fallback: "Editie jaar"), text: $draft.editionReleaseYear, keyboardType: .numberPad)
                labeledField(localized("edit.editionDate", fallback: "Editie datum"), text: $draft.editionReleaseDate)
                labeledField(localized("edit.boxSet", fallback: "Box-set"), text: $draft.boxSet)
            }

            Section(localized("bulk.assignContainer", fallback: "Toevoegen aan vault, box-set of collectie")) {
                TextField(localized("collection.searchPlaceholder", fallback: "Zoeken"), text: $containerSearchText)
                    .textInputAutocapitalization(.words)

                if let selectedContainer {
                    Button {
                        draft.editionGroupId = nil
                        draft.collectionId = nil
                    } label: {
                        HStack(spacing: 12) {
                            Image(systemName: icon(for: selectedContainer.kind))
                                .foregroundStyle(color(for: selectedContainer.kind))
                                .frame(width: 24)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(selectedContainer.title)
                                    .foregroundStyle(.primary)
                                Text("\(label(for: selectedContainer.kind)) · \(memberCountText(selectedContainer.memberCount))")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Image(systemName: "xmark.circle.fill")
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                if isLoadingContainers {
                    ProgressView()
                } else if containerSearchQuery.isEmpty {
                    Text(localized("collection.searchPlaceholder", fallback: "Zoeken"))
                        .foregroundStyle(.secondary)
                } else if containerSearchResults.isEmpty {
                    Text(localized("collection.noFilterMatches", fallback: "Geen resultaten."))
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(containerSearchResults) { target in
                        Button {
                            if target.kind == .collection {
                                draft.collectionId = target.rawID
                                draft.editionGroupId = nil
                            } else {
                                draft.editionGroupId = target.rawID
                                draft.collectionId = nil
                            }
                        } label: {
                            containerTargetRow(target)
                        }
                    }
                }
            }
        }
    }

    private var containerSearchQuery: String {
        containerSearchText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    private var containerTargets: [MovieEditContainerTarget] {
        let groupTargets = editionGroups.map { group in
            MovieEditContainerTarget(
                rawID: group.id,
                title: group.title,
                kind: group.containerKind,
                memberCount: group.displayMemberCount
            )
        }
        let collectionTargets = collections.map { collection in
            MovieEditContainerTarget(
                rawID: collection.id,
                title: collection.title,
                kind: .collection,
                memberCount: collection.displayMemberCount
            )
        }
        return (groupTargets + collectionTargets).sorted {
            $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending
        }
    }

    private var containerSearchResults: [MovieEditContainerTarget] {
        guard !containerSearchQuery.isEmpty else { return [] }
        return containerTargets.filter { $0.title.lowercased().contains(containerSearchQuery) }
    }

    private var selectedContainer: MovieEditContainerTarget? {
        if let collectionId = draft.collectionId {
            return containerTargets.first { $0.kind == .collection && $0.rawID == collectionId }
        }
        if let editionGroupId = draft.editionGroupId {
            return containerTargets.first { $0.kind != .collection && $0.rawID == editionGroupId }
        }
        return nil
    }

    private func loadContainers() async {
        guard editionGroups.isEmpty && collections.isEmpty else { return }
        isLoadingContainers = true
        async let loadedGroups = apiClient.getEditionGroups()
        async let loadedCollections = apiClient.getDiscCollections()
        do {
            editionGroups = try await loadedGroups
            collections = try await loadedCollections
        } catch {
            // The edit form remains usable even if container suggestions cannot be loaded.
        }
        isLoadingContainers = false
    }

    private func containerTargetRow(_ target: MovieEditContainerTarget) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon(for: target.kind))
                .foregroundStyle(color(for: target.kind))
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 2) {
                Text(target.title)
                    .foregroundStyle(.primary)
                Text("\(label(for: target.kind)) · \(memberCountText(target.memberCount))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Image(systemName: selectedContainer?.id == target.id ? "checkmark.circle.fill" : "plus.circle")
                .foregroundStyle(selectedContainer?.id == target.id ? .green : .secondary)
        }
    }

    private func memberCountText(_ count: Int) -> String {
        count == 1 ? "1 film" : "\(count) films"
    }

    private func label(for kind: BulkContainerKind) -> String {
        switch kind {
        case .vault:
            return languageManager.text("settings.groupTypeVault")
        case .boxset:
            return languageManager.text("settings.groupTypeBoxSet")
        case .collection:
            return languageManager.text("settings.groupTypeCollection")
        }
    }

    private func icon(for kind: BulkContainerKind) -> String {
        switch kind {
        case .vault: return "tray.full.fill"
        case .boxset: return "shippingbox.fill"
        case .collection: return "folder.fill"
        }
    }

    private func color(for kind: BulkContainerKind) -> Color {
        switch kind {
        case .vault: return Color(red: 0.91, green: 0.77, blue: 0.28)
        case .boxset: return .blue
        case .collection: return .green
        }
    }

    private var detailsSection: some View {
        SwiftUI.Group {
            Section(localized("modal.details", fallback: "Details")) {
                labeledField(localized("edit.runtime", fallback: "Runtime"), text: $draft.runtime)
                labeledField(localized("edit.imdbRating", fallback: "IMDb rating"), text: $draft.rating, keyboardType: .decimalPad)
                labeledField(localized("edit.audienceRating", fallback: "Leeftijdsrestrictie"), text: $draft.audienceRating)
                labeledField(localized("edit.hdr", fallback: "HDR"), text: $draft.hdr)
                labeledField(localized("edit.packaging", fallback: "Verpakking"), text: $draft.packaging)
                labeledField(localized("edit.screenRatio", fallback: "Beeldverhouding"), text: $draft.screenRatios)
                labeledField(localized("edit.language", fallback: "Taal"), text: $draft.language)
                labeledField(localized("edit.audioTracks", fallback: "Audio"), text: $draft.audioTracks, axis: .vertical, lineLimit: 2...4)
                labeledField(localized("edit.subtitles", fallback: "Ondertitels"), text: $draft.subtitles, axis: .vertical, lineLimit: 2...4)
                labeledField(localized("edit.regions", fallback: "Regio's"), text: $draft.regions)
                labeledField(localized("edit.extras", fallback: "Extra's"), text: $draft.extras, axis: .vertical, lineLimit: 2...5)
                labeledField(localized("edit.country", fallback: "Land"), text: $draft.country)
                labeledField(localized("edit.imdbId", fallback: "IMDb ID"), text: $draft.imdbId)
                labeledField(localized("edit.imdbUrl", fallback: "IMDb URL"), text: $draft.imdbUrl, keyboardType: .URL, autocapitalization: .never)
                labeledField(localized("add.tmdbId", fallback: "TMDb ID"), text: $draft.tmdbId, keyboardType: .numberPad)
            }
        }
    }

    private func localized(_ key: String, fallback: String) -> String {
        let value = languageManager.text(key)
        return value == key ? fallback : value
    }

    private func labeledField(
        _ label: String,
        text: Binding<String>,
        axis: Axis = .horizontal,
        lineLimit: ClosedRange<Int>? = nil,
        keyboardType: UIKeyboardType = .default,
        autocapitalization: TextInputAutocapitalization? = nil
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            TextField(label, text: text, axis: axis)
                .keyboardType(keyboardType)
                .textInputAutocapitalization(autocapitalization)
                .modifier(OptionalLineLimitModifier(lineLimit: lineLimit))
        }
        .padding(.vertical, 2)
    }
}

private struct MovieEditContainerTarget: Identifiable, Hashable {
    let rawID: Int
    let title: String
    let kind: BulkContainerKind
    let memberCount: Int

    var id: String { "\(kind.rawValue)-\(rawID)" }
}

private struct OptionalLineLimitModifier: ViewModifier {
    let lineLimit: ClosedRange<Int>?

    func body(content: Content) -> some View {
        if let lineLimit {
            content.lineLimit(lineLimit)
        } else {
            content
        }
    }
}

private struct MovieContainerAssignmentSheet: View {
    let movie: Movie
    let onAssigned: () async -> Void

    @Environment(APIClient.self) private var apiClient
    @Environment(AppLanguageManager.self) private var languageManager
    @Environment(\.dismiss) private var dismiss

    @State private var searchText = ""
    @State private var editionGroups: [EditionGroup] = []
    @State private var collections: [DiscCollection] = []
    @State private var isLoading = true
    @State private var isAssigning = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    TextField("Zoek vaults, box-sets of collecties", text: $searchText)
                        .textInputAutocapitalization(.words)
                }

                if isLoading {
                    HStack {
                        Spacer()
                        ProgressView()
                        Spacer()
                    }
                } else if searchQuery.isEmpty {
                    Text("Zoek op titel om deze film aan een vault, box-set of collectie toe te voegen.")
                        .foregroundStyle(.secondary)
                } else if searchResults.isEmpty {
                    Text("Geen resultaten.")
                        .foregroundStyle(.secondary)
                } else {
                    Section("Resultaten") {
                        ForEach(searchResults) { target in
                            Button {
                                Task { await assign(to: target) }
                            } label: {
                                HStack(spacing: 12) {
                                    Image(systemName: icon(for: target.kind))
                                        .foregroundStyle(color(for: target.kind))
                                        .frame(width: 24)

                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(target.title)
                                            .foregroundStyle(.primary)
                                        Text(subtitle(for: target))
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }

                                    Spacer()

                                    if isAssigning {
                                        ProgressView()
                                    } else {
                                        Image(systemName: "plus.circle")
                                            .foregroundStyle(.secondary)
                                    }
                                }
                            }
                            .disabled(isAssigning)
                        }
                    }
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Toevoegen aan")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(languageManager.text("bulk.cancelGroup")) { dismiss() }
                }
            }
        }
        .preferredColorScheme(.dark)
        .task { await loadTargets() }
    }

    private var searchQuery: String {
        searchText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    private var searchResults: [BulkContainerTarget] {
        guard !searchQuery.isEmpty else { return [] }

        let groupTargets = editionGroups.map { group in
            BulkContainerTarget(rawID: group.id, title: group.title, kind: group.containerKind, memberCount: group.displayMemberCount)
        }
        let collectionTargets = collections.map { collection in
            BulkContainerTarget(rawID: collection.id, title: collection.title, kind: .collection, memberCount: collection.displayMemberCount)
        }

        return (groupTargets + collectionTargets)
            .filter { $0.title.lowercased().contains(searchQuery) }
            .sorted { $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending }
    }

    private func loadTargets() async {
        isLoading = true
        errorMessage = nil
        async let loadedGroups = apiClient.getEditionGroups()
        async let loadedCollections = apiClient.getDiscCollections()
        do {
            editionGroups = try await loadedGroups
            collections = try await loadedCollections
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    private func assign(to target: BulkContainerTarget) async {
        guard !isAssigning else { return }
        isAssigning = true
        errorMessage = nil
        do {
            try await apiClient.assignMovie(id: movie.id, to: target)
            await onAssigned()
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
        }
        isAssigning = false
    }

    private func kind(for group: EditionGroup) -> BulkContainerKind {
        group.containerKind
    }

    private func subtitle(for target: BulkContainerTarget) -> String {
        let countText = target.memberCount == 1 ? "1 film" : "\(target.memberCount) films"
        switch target.kind {
        case .vault:
            return "\(languageManager.text("settings.groupTypeVault")) · \(countText)"
        case .boxset:
            return "\(languageManager.text("settings.groupTypeBoxSet")) · \(countText)"
        case .collection:
            return "\(languageManager.text("settings.groupTypeCollection")) · \(countText)"
        }
    }

    private func icon(for kind: BulkContainerKind) -> String {
        switch kind {
        case .vault: return "tray.full.fill"
        case .boxset: return "shippingbox.fill"
        case .collection: return "folder.fill"
        }
    }

    private func color(for kind: BulkContainerKind) -> Color {
        switch kind {
        case .vault: return Color(red: 0.91, green: 0.77, blue: 0.28)
        case .boxset: return .blue
        case .collection: return .green
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
    let imageRefreshToken: Int

    private var isActor: Bool {
        let role = (member.role ?? "").lowercased()
        return role == "actor" || role == "cast"
    }

    var body: some View {
        VStack(spacing: 8) {
            SwiftUI.Group {
                if let url = photoURL {
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
            .frame(width: 72, height: 72)
            .clipShape(Circle())
            .overlay(Circle().stroke(Color.white.opacity(0.08), lineWidth: 1))

            Text(member.name)
                .font(.caption.weight(.medium))
                .foregroundStyle(.white.opacity(0.9))
                .lineLimit(2)
                .multilineTextAlignment(.center)

            if isActor, let character = member.character, !character.isEmpty {
                Text("as \(character)")
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.45))
                    .lineLimit(2)
                    .multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
        .padding(.horizontal, 6)
        .background(.white.opacity(0.05))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color.white.opacity(0.06), lineWidth: 1)
        )
    }

    private var photoURL: URL? {
        let baseURL: URL?
        if let url = member.photoUrl, !url.isEmpty {
            baseURL = apiClient.personImageURL(for: url)
        } else {
            baseURL = apiClient.personImageURL(for: member.photoFile)
        }
        return refreshedURL(baseURL)
    }

    private func refreshedURL(_ url: URL?) -> URL? {
        guard imageRefreshToken > 0, let url else { return url }
        guard var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return url }
        var queryItems = components.queryItems ?? []
        queryItems.removeAll { $0.name == "refresh" }
        queryItems.append(URLQueryItem(name: "refresh", value: String(imageRefreshToken)))
        components.queryItems = queryItems
        return components.url ?? url
    }

    private var placeholder: some View {
        ZStack {
            Circle().fill(Color.white.opacity(0.1))
            Image(systemName: "person.fill")
                .foregroundStyle(.white.opacity(0.3))
        }
    }
}

// MARK: - YouTube Thumbnail & Player

struct YouTubeThumbnailView: View {
    let youtubeKey: String
    let isPlaying: Bool
    let onPlay: () -> Void

    var body: some View {
        ZStack {
            if isPlaying {
                YouTubePlayer(videoKey: youtubeKey)
            } else {
                thumbnail
                    .onTapGesture(perform: onPlay)
            }
        }
        .background(Color.black)
    }

    private var thumbnail: some View {
        ZStack {
            AsyncImage(url: URL(string: "https://img.youtube.com/vi/\(youtubeKey)/maxresdefault.jpg")) { phase in
                switch phase {
                case .success(let img):
                    img.resizable().aspectRatio(contentMode: .fill)
                case .failure:
                    AsyncImage(url: URL(string: "https://img.youtube.com/vi/\(youtubeKey)/hqdefault.jpg")) { fallback in
                        if case .success(let img) = fallback {
                            img.resizable().aspectRatio(contentMode: .fill)
                        } else {
                            Color.black
                        }
                    }
                default:
                    Color.black
                }
            }

            // Play button overlay
            ZStack {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.red.opacity(0.88))
                    .frame(width: 56, height: 40)
                Image(systemName: "play.fill")
                    .font(.system(size: 18, weight: .bold))
                    .foregroundStyle(.white)
                    .offset(x: 1)
            }
        }
    }
}

private struct YouTubePlayer: UIViewRepresentable {
    let videoKey: String

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.scrollView.isScrollEnabled = false
        webView.isOpaque = false
        webView.backgroundColor = .black
        webView.scrollView.backgroundColor = .black
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        let html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta name='viewport' content='width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no'>
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; }
                html, body { width: 100%; height: 100%; background: #000; overflow: hidden; }
                iframe { width: 100%; height: 100%; border: 0; }
            </style>
        </head>
        <body>
            <iframe
                src='https://www.youtube-nocookie.com/embed/\(videoKey)?autoplay=1&playsinline=1&modestbranding=1&rel=0'
                allow='autoplay; encrypted-media; picture-in-picture'
                allowfullscreen>
            </iframe>
        </body>
        </html>
        """
        webView.loadHTMLString(html, baseURL: URL(string: "https://www.youtube-nocookie.com"))
    }
}
