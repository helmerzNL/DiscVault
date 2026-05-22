import SwiftUI

private enum PersonFilmographyTab: String, CaseIterable, Identifiable {
    case collection
    case digital
    case filmography

    var id: String { rawValue }
}

private enum PersonFilmographySort: String, CaseIterable, Identifiable {
    case newest
    case oldest
    case az
    case rating

    var id: String { rawValue }
}

struct PersonDetailView: View {
    let personId: Int
    let initialName: String?

    @Environment(APIClient.self) private var apiClient
    @Environment(AppLanguageManager.self) private var languageManager

    @State private var person: Person?
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var detailedActorDetails = false
    @State private var extendedFilmography: PersonFilmographyResponse?
    @State private var isLoadingExtendedFilmography = false
    @State private var selectedFilmographyTab: PersonFilmographyTab = .collection
    @State private var filmographySort: PersonFilmographySort = .newest
    @State private var isDebugModeEnabled = false

    private let backgroundColor = Color(red: 0.06, green: 0.06, blue: 0.14)

    init(personId: Int, initialName: String? = nil) {
        self.personId = personId
        self.initialName = initialName
    }

    var body: some View {
        ZStack {
            backgroundColor.ignoresSafeArea()

            if isLoading && person == nil {
                ProgressView().tint(.white)
            } else if let person = person {
                content(person: person)
            } else if let msg = errorMessage {
                Text(msg)
                    .foregroundStyle(.white.opacity(0.7))
                    .padding()
            }
        }
        .navigationTitle(person?.name ?? initialName ?? "")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .task {
            if person == nil { await load() }
        }
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }

        do {
            person = try await apiClient.getPerson(id: personId)
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
            return
        }

        let values = (try? await apiClient.getUserPreferences()) ?? [:]
        detailedActorDetails = boolPreference(values["detailed_actor"], defaultValue: false)
        isDebugModeEnabled = (try? await apiClient.getDebugSetting())?.debugEnabled ?? false
        if detailedActorDetails {
            await loadExtendedFilmography()
        }
    }

    private func loadExtendedFilmography() async {
        isLoadingExtendedFilmography = true
        defer { isLoadingExtendedFilmography = false }

        extendedFilmography = try? await apiClient.getPersonFilmography(
            id: personId,
            language: tmdbLanguageCode
        )
    }

    @ViewBuilder
    private func content(person: Person) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                header(person: person)
                if let bio = person.localizedBiography(languageManager.languageCode), !bio.isEmpty {
                    biographyBlock(bio: bio)
                }
                filmography(person: person)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 16)
        }
    }

    // MARK: - Header

    private func header(person: Person) -> some View {
        HStack(alignment: .top, spacing: 16) {
            photo(person: person)

            VStack(alignment: .leading, spacing: 8) {
                Text(person.name)
                    .font(.title3.bold())
                    .foregroundStyle(.white)
                    .lineLimit(2)

                if isDebugModeEnabled {
                    debugIdentityBadge(person: person)
                }

                metaRows(person: person)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func debugIdentityBadge(person: Person) -> some View {
        Label("Person #\(person.id)", systemImage: "number")
            .font(.caption2.weight(.bold))
            .foregroundStyle(.white.opacity(0.9))
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(.black.opacity(0.45), in: Capsule())
            .overlay(Capsule().stroke(.white.opacity(0.18), lineWidth: 0.5))
    }

    private func photo(person: Person) -> some View {
        let resolvedURL: URL? = {
            if let url = person.photoUrl, !url.isEmpty {
                return apiClient.personImageURL(for: url)
            }
            return apiClient.personImageURL(for: person.photoFile)
        }()

        return ZStack {
            RoundedRectangle(cornerRadius: 10)
                .fill(Color.white.opacity(0.08))

            if let url = resolvedURL {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let img):
                        img.resizable().aspectRatio(contentMode: .fill)
                    default:
                        placeholderIcon
                    }
                }
            } else {
                placeholderIcon
            }
        }
        .frame(width: 120, height: 160)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(Color.white.opacity(0.06), lineWidth: 1)
        )
    }

    private var placeholderIcon: some View {
        Image(systemName: "person.fill")
            .font(.system(size: 40))
            .foregroundStyle(.white.opacity(0.3))
    }

    @ViewBuilder
    private func metaRows(person: Person) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            if let knownFor = person.knownFor, !knownFor.isEmpty {
                metaRow(icon: "star.fill", text: knownFor)
            }
            if let birthday = person.birthday, !birthday.isEmpty {
                metaRow(icon: "calendar", text: birthday + lifespanSuffix(person: person))
            }
            if let place = person.placeOfBirth, !place.isEmpty {
                metaRow(icon: "mappin.and.ellipse", text: place)
            }
        }
    }

    private func metaRow(icon: String, text: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon)
                .font(.caption2)
                .foregroundStyle(.white.opacity(0.45))
                .frame(width: 14)
            Text(text)
                .font(.caption)
                .foregroundStyle(.white.opacity(0.7))
                .lineLimit(2)
        }
    }

    private func lifespanSuffix(person: Person) -> String {
        if let death = person.deathday, !death.isEmpty {
            return " - " + death
        }
        return ""
    }

    // MARK: - Biography

    private func biographyBlock(bio: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(localized("modal.tabInfo", fallback: "Biography"))
                .font(.headline)
                .foregroundStyle(.white)
            Text(bio)
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.7))
                .lineSpacing(4)
        }
    }

    // MARK: - Filmography

    @ViewBuilder
    private func filmography(person: Person) -> some View {
        if detailedActorDetails {
            detailedFilmography(person: person)
        } else {
            collectionFilmography(movies: person.movies ?? [])
        }
    }

    private func detailedFilmography(person: Person) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            filmographyControls(collectionCount: (person.movies ?? []).count)

            switch selectedFilmographyTab {
            case .collection:
                collectionFilmography(movies: sortedCollectionMovies(person.movies ?? []))
            case .digital:
                extendedFilmographySection(
                    title: "Digital library",
                    emptyTitle: "No digital movies found.",
                    items: digitalMovies
                )
            case .filmography:
                extendedFilmographySection(
                    title: localized("person.tabFilmography", fallback: "Filmography"),
                    emptyTitle: localized("person.noFilmography", fallback: "No filmography available."),
                    items: fullFilmographyMovies
                )
            }
        }
    }

    private func filmographyControls(collectionCount: Int) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(localized("person.tabFilmography", fallback: "Filmography"))
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.55))
                    .textCase(.uppercase)

                Spacer(minLength: 12)

                Picker("Sort", selection: $filmographySort) {
                    ForEach(PersonFilmographySort.allCases) { sort in
                        Text(sortTitle(sort)).tag(sort)
                    }
                }
                .pickerStyle(.menu)
                .tint(.white.opacity(0.85))
            }

            HStack(spacing: 6) {
                filmographyTabButton(.collection, title: localized("person.tabCollection", fallback: "In collection"), count: collectionCount)
                filmographyTabButton(.digital, title: localized("person.tabDigital", fallback: "Digital"), count: digitalMovies.count)
                filmographyTabButton(.filmography, title: localized("person.tabFilmography", fallback: "Filmography"), count: fullFilmographyMovies.count)
            }
            .padding(4)
            .background(.white.opacity(0.06))
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }

    private func filmographyTabButton(_ tab: PersonFilmographyTab, title: String, count: Int) -> some View {
        Button {
            selectedFilmographyTab = tab
        } label: {
            VStack(spacing: 2) {
                Text(title)
                    .font(.caption.weight(.semibold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
                Text(isLoadingExtendedFilmography && tab != .collection ? "..." : "\(count)")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(selectedFilmographyTab == tab ? .white.opacity(0.75) : .white.opacity(0.38))
            }
            .foregroundStyle(selectedFilmographyTab == tab ? .white : .white.opacity(0.55))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 8)
            .background(
                SwiftUI.Group {
                    if selectedFilmographyTab == tab {
                        RoundedRectangle(cornerRadius: 6)
                            .fill(.white.opacity(0.12))
                    }
                }
            )
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private func collectionFilmography(movies: [PersonMovieAppearance]) -> some View {
        if movies.isEmpty {
            EmptyView()
        } else {
            VStack(alignment: .leading, spacing: 10) {
                sectionTitle(localized("person.inCollection", fallback: "In your collection"), count: movies.count)

                LazyVGrid(columns: [
                    GridItem(.adaptive(minimum: 100, maximum: 130), spacing: 12)
                ], spacing: 14) {
                    ForEach(movies) { appearance in
                        FilmographyCardView(appearance: appearance, apiClient: apiClient)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func extendedFilmographySection(title: String, emptyTitle: String, items: [PersonFilmographyItem]) -> some View {
        if isLoadingExtendedFilmography {
            HStack(spacing: 10) {
                ProgressView().tint(.white)
                Text("Loading")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.55))
            }
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.vertical, 24)
        } else if items.isEmpty {
            Text(emptyTitle)
                .font(.caption)
                .foregroundStyle(.white.opacity(0.5))
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, 24)
        } else {
            VStack(alignment: .leading, spacing: 10) {
                sectionTitle(title, count: items.count)

                LazyVGrid(columns: [
                    GridItem(.adaptive(minimum: 100, maximum: 130), spacing: 12)
                ], spacing: 14) {
                    ForEach(items) { item in
                        ExtendedFilmographyCardView(item: item)
                    }
                }
            }
        }
    }

    private func sectionTitle(_ title: String, count: Int) -> some View {
        Text("\(title) (\(count))")
            .font(.caption.weight(.semibold))
            .foregroundStyle(.white.opacity(0.55))
            .textCase(.uppercase)
            .padding(.bottom, 4)
    }

    private var digitalMovies: [PersonFilmographyItem] {
        sortedExtendedMovies((extendedFilmography?.cast ?? []).filter { $0.inDigital == true })
    }

    private var fullFilmographyMovies: [PersonFilmographyItem] {
        sortedExtendedMovies(extendedFilmography?.cast ?? [])
    }

    private func sortedCollectionMovies(_ movies: [PersonMovieAppearance]) -> [PersonMovieAppearance] {
        movies.sorted { lhs, rhs in
            switch filmographySort {
            case .newest:
                return yearValue(lhs.year) > yearValue(rhs.year)
            case .oldest:
                return yearValue(lhs.year) < yearValue(rhs.year)
            case .az:
                return lhs.title.localizedCaseInsensitiveCompare(rhs.title) == .orderedAscending
            case .rating:
                return rating(for: lhs) > rating(for: rhs)
            }
        }
    }

    private func sortedExtendedMovies(_ items: [PersonFilmographyItem]) -> [PersonFilmographyItem] {
        items.sorted { lhs, rhs in
            switch filmographySort {
            case .newest:
                return yearValue(lhs.year) > yearValue(rhs.year)
            case .oldest:
                return yearValue(lhs.year) < yearValue(rhs.year)
            case .az:
                return lhs.title.localizedCaseInsensitiveCompare(rhs.title) == .orderedAscending
            case .rating:
                return (lhs.voteAverage ?? 0) > (rhs.voteAverage ?? 0)
            }
        }
    }

    private func rating(for movie: PersonMovieAppearance) -> Double {
        guard let tmdbId = movie.tmdbId else { return 0 }
        return extendedFilmography?.cast.first { $0.tmdbId == tmdbId }?.voteAverage ?? 0
    }

    private func yearValue(_ year: String?) -> Int {
        Int((year ?? "").prefix(4)) ?? 0
    }

    private func sortTitle(_ sort: PersonFilmographySort) -> String {
        switch sort {
        case .newest:
            return localized("person.sortNewest", fallback: "Newest first")
        case .oldest:
            return localized("person.sortOldest", fallback: "Oldest first")
        case .az:
            return "A-Z"
        case .rating:
            return localized("person.sortRating", fallback: "TMDb rating")
        }
    }

    // MARK: - Helpers

    private var tmdbLanguageCode: String {
        switch languageManager.languageCode {
        case "nl": return "nl-NL"
        case "fr": return "fr-FR"
        case "de": return "de-DE"
        case "es": return "es-ES"
        case "pt": return "pt-PT"
        case "it": return "it-IT"
        default: return "en-US"
        }
    }

    private func boolPreference(_ value: String?, defaultValue: Bool) -> Bool {
        guard let value else { return defaultValue }
        return ["1", "true", "yes", "on"].contains(value.lowercased())
    }

    private func localized(_ key: String, fallback: String) -> String {
        let value = languageManager.text(key)
        return value == key ? fallback : value
    }
}

// MARK: - Filmography Card

private struct FilmographyCardView: View {
    let appearance: PersonMovieAppearance
    let apiClient: APIClient

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            poster
            Text(appearance.title)
                .font(.caption.weight(.medium))
                .foregroundStyle(.white.opacity(0.9))
                .lineLimit(2)

            if let subtitle = subtitle {
                Text(subtitle)
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.5))
                    .lineLimit(1)
            } else if let year = appearance.year {
                Text(year)
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.5))
            }
        }
    }

    private var poster: some View {
        ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: 6)
                .fill(Color.white.opacity(0.08))
                .aspectRatio(2/3, contentMode: .fit)

            if let url = resolvedPosterURL {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let img):
                        img.resizable().aspectRatio(contentMode: .fill)
                    default:
                        EmptyView()
                    }
                }
                .aspectRatio(2/3, contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 6))
            }

            if let format = appearance.format, !format.isEmpty {
                Text(formatLabel(format))
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 4)
                    .padding(.vertical, 2)
                    .background(formatColor(format))
                    .clipShape(RoundedRectangle(cornerRadius: 3))
                    .padding(4)
            }
        }
    }

    private var resolvedPosterURL: URL? {
        if let p = appearance.poster, !p.isEmpty {
            if p.hasPrefix("http") { return URL(string: p) }
            return apiClient.posterURL(for: p)
        }
        return apiClient.posterURL(for: appearance.posterFile)
    }

    private var subtitle: String? {
        if let c = appearance.character, !c.isEmpty {
            return "as \(c)"
        }
        if let j = appearance.job, !j.isEmpty {
            return j
        }
        return nil
    }

    private func formatLabel(_ format: String) -> String {
        switch format {
        case "4K UHD": return "4K"
        case "Blu-ray": return "BD"
        default: return "DVD"
        }
    }

    private func formatColor(_ format: String) -> Color {
        switch format {
        case "4K UHD": return Color(red: 0.45, green: 0.15, blue: 0.85)
        case "Blu-ray": return Color(red: 0.15, green: 0.4, blue: 0.85)
        default: return Color(red: 0.35, green: 0.35, blue: 0.45)
        }
    }
}

private struct ExtendedFilmographyCardView: View {
    let item: PersonFilmographyItem

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ZStack(alignment: .topLeading) {
                RoundedRectangle(cornerRadius: 6)
                    .fill(Color.white.opacity(0.08))
                    .aspectRatio(2/3, contentMode: .fit)

                if let url = posterURL {
                    AsyncImage(url: url) { phase in
                        switch phase {
                        case .success(let img):
                            img.resizable().aspectRatio(contentMode: .fill)
                        default:
                            EmptyView()
                        }
                    }
                    .aspectRatio(2/3, contentMode: .fit)
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                }

                badge
                    .padding(4)
            }

            Text(item.title)
                .font(.caption.weight(.medium))
                .foregroundStyle(.white.opacity(0.9))
                .lineLimit(2)

            if let year = item.year, !year.isEmpty {
                Text(year)
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.5))
            }

            if let subtitle {
                Text(subtitle)
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.5))
                    .lineLimit(1)
            }
        }
        .opacity(item.inCollection == true || item.inDigital == true ? 1 : 0.55)
    }

    private var posterURL: URL? {
        guard let poster = item.poster, !poster.isEmpty else { return nil }
        return URL(string: poster)
    }

    @ViewBuilder
    private var badge: some View {
        if let format = item.collectionFormat, !format.isEmpty {
            Text(formatLabel(format))
                .font(.system(size: 9, weight: .bold))
                .foregroundStyle(.white)
                .padding(.horizontal, 4)
                .padding(.vertical, 2)
                .background(formatColor(format))
                .clipShape(RoundedRectangle(cornerRadius: 3))
        } else if item.inDigital == true {
            Text((item.digitalSource ?? "digital").uppercased().prefix(1))
                .font(.system(size: 9, weight: .bold))
                .foregroundStyle(.white)
                .padding(.horizontal, 5)
                .padding(.vertical, 2)
                .background(Color(red: 0.1, green: 0.45, blue: 0.65))
                .clipShape(RoundedRectangle(cornerRadius: 3))
        }
    }

    private var subtitle: String? {
        if let character = item.character, !character.isEmpty { return "as \(character)" }
        if let job = item.job, !job.isEmpty { return job }
        if let voteAverage = item.voteAverage, voteAverage > 0 { return String(format: "TMDb %.1f", voteAverage) }
        return nil
    }

    private func formatLabel(_ format: String) -> String {
        switch format {
        case "4K UHD": return "4K"
        case "Blu-ray": return "BD"
        default: return "DVD"
        }
    }

    private func formatColor(_ format: String) -> Color {
        switch format {
        case "4K UHD": return Color(red: 0.45, green: 0.15, blue: 0.85)
        case "Blu-ray": return Color(red: 0.15, green: 0.4, blue: 0.85)
        default: return Color(red: 0.35, green: 0.35, blue: 0.45)
        }
    }
}
