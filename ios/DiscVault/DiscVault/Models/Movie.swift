import Foundation
import SwiftUI

// MARK: - CastMember

/// Matches the response from `GET /api/movies/:id/cast`.
struct CastMember: Decodable, Identifiable, Hashable, Sendable {
    let personId: Int
    let name: String
    let role: String?
    let character: String?
    let job: String?
    let photoUrl: String?
    let photoFile: String?
    let tmdbId: Int?

    var id: Int { personId }

    private enum CodingKeys: String, CodingKey {
        case personId
        case id
        case name
        case role
        case character
        case job
        case photoUrl
        case photoFile
        case profilePath
        case tmdbId
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        personId = (try? container.decode(Int.self, forKey: .personId))
            ?? (try? container.decode(Int.self, forKey: .id))
            ?? 0
        name = (try? container.decode(String.self, forKey: .name)) ?? ""
        role = try? container.decodeIfPresent(String.self, forKey: .role)
        character = try? container.decodeIfPresent(String.self, forKey: .character)
        job = try? container.decodeIfPresent(String.self, forKey: .job)
        photoUrl = try? container.decodeIfPresent(String.self, forKey: .photoUrl)
        photoFile = (try? container.decodeIfPresent(String.self, forKey: .photoFile))
            ?? (try? container.decodeIfPresent(String.self, forKey: .profilePath))
        tmdbId = try? container.decodeIfPresent(Int.self, forKey: .tmdbId)
    }
}

// MARK: - MovieVideo

/// A single video entry as stored in the `videos` JSON column of a movie.
struct MovieVideo: Codable, Hashable, Identifiable, Sendable {
    let url: String
    let label: String?
    let type: String?
    let source: String?

    /// Stable identifier derived from the URL (YouTube videos share keys).
    var id: String { url }

    /// Extracted YouTube video key (e.g. `dQw4w9WgXcQ`), or nil if not a YouTube URL.
    var youtubeKey: String? {
        Self.extractYouTubeKey(from: url)
    }

    static func extractYouTubeKey(from url: String) -> String? {
        guard !url.isEmpty else { return nil }
        if let comps = URLComponents(string: url) {
            if let v = comps.queryItems?.first(where: { $0.name == "v" })?.value, !v.isEmpty {
                return v
            }
            if comps.host?.contains("youtu.be") == true {
                let key = comps.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
                if !key.isEmpty { return key }
            }
            if comps.host?.contains("youtube") == true,
               comps.path.hasPrefix("/embed/") {
                let key = String(comps.path.dropFirst("/embed/".count))
                if !key.isEmpty { return key }
            }
        }
        return nil
    }
}

// MARK: - Movie

struct Movie: Codable, Identifiable, Hashable, Sendable {
    let id: Int
    let title: String
    let originalTitle: String?
    let year: String?
    let releaseDate: String?
    let imdbId: String?
    let imdbUrl: String?
    let tmdbId: Int?
    let director: String?
    let actor: String?
    let producer: String?
    let studios: String?
    let plot: String?
    let genre: String?
    let runtime: String?
    let format: String?
    let rating: String?
    let voteAverage: Double?
    let voteCount: Int?
    let audienceRating: String?
    let contentRatings: String?
    let ratingUs: String?
    let language: String?
    let audioTracks: String?
    let subtitles: String?
    let country: String?
    let barcode: String?
    let location: String?
    let notes: String?
    let edition: String?
    let editionType: String?
    let editionReleaseYear: String?
    let editionReleaseDate: String?
    let customEditionLabel: String?
    let packaging: String?
    let screenRatios: String?
    let regions: String?
    let extras: String?
    let boxSet: String?
    let localizedTitles: [String: String]
    let localizedPlots: [String: String]
    let poster: String?
    let posterFile: String?
    let backdrop: String?
    let backdrops: String?
    let trailerUrl: String?
    let videos: String?
    let ownerId: Int?
    let addedAt: String?
    let wanted: Bool?
    let watched: Bool?
    let onWatchlist: Bool?
    let lastWatched: String?
    let hdr: String?
    let dolbyVision: Bool?
    let audioCodec: String?
    let groupIds: [Int]?
    let editionGroupId: Int?
    let superGroupId: Int?
    let collectionId: Int?
    let editionsCount: Int?
    let isGroup: Bool?
    let isSuperGroup: Bool?
    let isCollection: Bool?
    let groupTitle: String?
    let groupBadgeLabel: String?
    let containerPosterFile: String?
    let collectionCardId: Int?
    let parentGroupId: Int?
    let editions: [Movie]
    let subGroups: [Movie]
    let looseMovies: [Movie]
    let boxSets: [Movie]
    let vaults: [Movie]

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case originalTitle
        case year
        case releaseDate
        case imdbId
        case imdbUrl
        case tmdbId
        case director
        case actor
        case producer
        case studios
        case plot
        case genre
        case runtime
        case format
        case rating
        case voteAverage
        case voteCount
        case audienceRating
        case contentRatings
        case ratingUs
        case language
        case audioTracks
        case subtitles
        case country
        case barcode
        case location
        case notes
        case edition
        case editionType
        case editionReleaseYear
        case editionReleaseDate
        case customEditionLabel
        case packaging
        case screenRatios
        case regions
        case extras
        case boxSet
        case poster
        case posterFile
        case backdrop
        case backdrops
        case trailerUrl
        case videos
        case ownerId
        case addedAt
        case wanted
        case watched
        case onWatchlist
        case lastWatched
        case hdr
        case dolbyVision
        case audioCodec
        case groupIds
        case editionGroupId
        case superGroupId
        case collectionId
        case editionsCount
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let rawContainer = try decoder.container(keyedBy: RawMovieCodingKey.self)

        id = container.decodeFlexibleInt(forKey: .id) ?? 0
        title = container.decodeFlexibleString(forKey: .title) ?? ""
        originalTitle = container.decodeFlexibleString(forKey: .originalTitle)
        year = container.decodeFlexibleString(forKey: .year)
        releaseDate = container.decodeFlexibleString(forKey: .releaseDate)
        imdbId = container.decodeFlexibleString(forKey: .imdbId)
        imdbUrl = container.decodeFlexibleString(forKey: .imdbUrl)
        tmdbId = container.decodeFlexibleInt(forKey: .tmdbId)
        director = container.decodeFlexibleString(forKey: .director)
        actor = container.decodeFlexibleString(forKey: .actor)
        producer = container.decodeFlexibleString(forKey: .producer)
        studios = container.decodeFlexibleString(forKey: .studios)
        plot = container.decodeFlexibleString(forKey: .plot)
        genre = container.decodeFlexibleString(forKey: .genre)
        runtime = container.decodeFlexibleString(forKey: .runtime)
        format = container.decodeFlexibleString(forKey: .format)
        rating = container.decodeFlexibleString(forKey: .rating)
        voteAverage = container.decodeFlexibleDouble(forKey: .voteAverage)
            ?? rawContainer.decodeFlexibleDouble(forAnyKey: ["vote_average"])
        voteCount = container.decodeFlexibleInt(forKey: .voteCount)
            ?? rawContainer.decodeFlexibleInt(forAnyKey: ["vote_count"])
        audienceRating = container.decodeFlexibleString(forKey: .audienceRating)
        contentRatings = container.decodeFlexibleString(forKey: .contentRatings)
        ratingUs = container.decodeFlexibleString(forKey: .ratingUs)
        language = container.decodeFlexibleString(forKey: .language)
        audioTracks = container.decodeFlexibleString(forKey: .audioTracks)
        subtitles = container.decodeFlexibleString(forKey: .subtitles)
        country = container.decodeFlexibleString(forKey: .country)
        barcode = container.decodeFlexibleString(forKey: .barcode)
        location = container.decodeFlexibleString(forKey: .location)
        notes = container.decodeFlexibleString(forKey: .notes)
        edition = container.decodeFlexibleString(forKey: .edition)
        editionType = container.decodeFlexibleString(forKey: .editionType)
        editionReleaseYear = container.decodeFlexibleString(forKey: .editionReleaseYear)
        editionReleaseDate = container.decodeFlexibleString(forKey: .editionReleaseDate)
        customEditionLabel = container.decodeFlexibleString(forKey: .customEditionLabel)
        packaging = container.decodeFlexibleString(forKey: .packaging)
        screenRatios = container.decodeFlexibleString(forKey: .screenRatios)
        regions = container.decodeFlexibleString(forKey: .regions)
        extras = container.decodeFlexibleString(forKey: .extras)
        boxSet = container.decodeFlexibleString(forKey: .boxSet)
        localizedTitles = Self.decodeLocalizedValues(
            from: rawContainer,
            prefix: "title",
            keys: ["nl", "fr", "de", "es", "pt", "it"]
        )
        localizedPlots = Self.decodeLocalizedValues(
            from: rawContainer,
            prefix: "plot",
            keys: ["nl", "fr", "de", "es", "pt", "it"]
        )
        poster = container.decodeFlexibleString(forKey: .poster)
        posterFile = container.decodeFlexibleString(forKey: .posterFile)
            ?? rawContainer.decodeFlexibleString(forAnyKey: ["poster_file"])
        backdrop = container.decodeFlexibleString(forKey: .backdrop)
        backdrops = container.decodeFlexibleString(forKey: .backdrops)
        videos = container.decodeFlexibleString(forKey: .videos)
        trailerUrl = container.decodeFlexibleString(forKey: .trailerUrl)
            ?? rawContainer.decodeFlexibleString(forAnyKey: ["trailer_url"])
        ownerId = container.decodeFlexibleInt(forKey: .ownerId)
        addedAt = container.decodeFlexibleString(forKey: .addedAt)
        wanted = container.decodeFlexibleBool(forKey: .wanted)
        watched = container.decodeFlexibleBool(forKey: .watched)
        onWatchlist = container.decodeFlexibleBool(forKey: .onWatchlist)
        lastWatched = container.decodeFlexibleString(forKey: .lastWatched)
        hdr = container.decodeFlexibleString(forKey: .hdr)
        dolbyVision = container.decodeFlexibleBool(forKey: .dolbyVision)
        audioCodec = container.decodeFlexibleString(forKey: .audioCodec)
        groupIds = container.decodeFlexibleIntArray(forKey: .groupIds)
        editionGroupId = container.decodeFlexibleInt(forKey: .editionGroupId)
        superGroupId = container.decodeFlexibleInt(forKey: .superGroupId)
        collectionId = container.decodeFlexibleInt(forKey: .collectionId)
        editionsCount = container.decodeFlexibleInt(forKey: .editionsCount)
        isGroup = rawContainer.decodeFlexibleBool(forAnyKey: ["_is_group", "_isGroup", "isGroup"])
        isSuperGroup = rawContainer.decodeFlexibleBool(forAnyKey: ["_is_super_group", "_isSuperGroup", "isSuperGroup"])
        isCollection = rawContainer.decodeFlexibleBool(forAnyKey: ["_is_collection", "_isCollection", "isCollection"])
        groupTitle = rawContainer.decodeFlexibleString(forAnyKey: ["_group_title", "_groupTitle", "groupTitle"])
        groupBadgeLabel = rawContainer.decodeFlexibleString(forAnyKey: ["_group_badge_label", "_groupBadgeLabel", "groupBadgeLabel"])
        containerPosterFile = rawContainer.decodeFlexibleString(forAnyKey: ["_container_poster_file", "_containerPosterFile", "containerPosterFile"])
        collectionCardId = rawContainer.decodeFlexibleInt(forAnyKey: ["_collection_id", "_collectionId", "collectionId"])
        parentGroupId = rawContainer.decodeFlexibleInt(forAnyKey: ["_parent_group_id", "_parentGroupId", "parentGroupId"])
        editions = rawContainer.decodeMovies(forAnyKey: ["editions"])
        subGroups = rawContainer.decodeMovies(forAnyKey: ["_sub_groups", "_subGroups", "subGroups"])
        looseMovies = rawContainer.decodeMovies(forAnyKey: ["_loose_movies", "_looseMovies", "looseMovies"])
        boxSets = rawContainer.decodeMovies(forAnyKey: ["_box_sets", "_boxSets", "boxSets"])
        vaults = rawContainer.decodeMovies(forAnyKey: ["_vaults", "vaults"])
    }

    // MARK: - Computed Properties

    var displayTitle: String {
        groupTitle ?? title
    }

    func localizedTitle(_ languageCode: String) -> String {
        let shortCode = String(languageCode.prefix(2)).lowercased()
        let value = localizedTitles[shortCode]?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return value.isEmpty ? title : value
    }

    func localizedPlot(_ languageCode: String) -> String? {
        let shortCode = String(languageCode.prefix(2)).lowercased()
        let value = localizedPlots[shortCode]?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return value.isEmpty ? plot : value
    }

    var displayYear: String? {
        if isContainerCard {
            return firstContainedYear ?? year
        }
        return year
    }

    var posterPath: String? {
        containerPosterFile ?? childContainerPosterPath ?? posterFile ?? poster
    }

    func posterPath(groupMultipleEditionsEnabled: Bool) -> String? {
        guard groupMultipleEditionsEnabled, isPartOfContainerHierarchy else {
            return posterFile ?? poster
        }

        return containerPosterFile ?? childContainerPosterPath ?? posterFile ?? poster
    }

    var isContainerCard: Bool {
        isCollection == true || isSuperGroup == true || isGroup == true
    }

    var isPartOfContainerHierarchy: Bool {
        isContainerCard ||
        editionGroupId != nil ||
        superGroupId != nil ||
        collectionId != nil ||
        !editions.isEmpty ||
        !subGroups.isEmpty ||
        !looseMovies.isEmpty ||
        !boxSets.isEmpty ||
        !vaults.isEmpty
    }

    var containerBadgeLabel: String? {
        if isCollection == true {
            return "Collection"
        }
        if isSuperGroup == true {
            return "Box Set"
        }
        if isGroup == true {
            return "Vault"
        }
        return nil
    }

    private var firstContainedYear: String? {
        let years = containedMovies
            .compactMap(\.year)
            .compactMap { Self.yearValue(from: $0) }

        if let first = years.min() {
            return String(first)
        }
        return nil
    }

    private var containedMovies: [Movie] {
        var result = editions + looseMovies
        for group in subGroups + boxSets + vaults {
            let nested = group.containedMovies
            result.append(contentsOf: nested.isEmpty ? [group] : nested)
        }
        return result
    }

    private var childContainerPosterPath: String? {
        for child in boxSets + vaults + subGroups where child.isContainerCard {
            if let path = child.posterPath {
                return path
            }
        }

        for child in looseMovies + editions {
            if let path = child.posterPath {
                return path
            }
        }

        return nil
    }

    private static func yearValue(from value: String) -> Int? {
        let digits = value.prefix { $0.isNumber }
        if digits.count >= 4 {
            return Int(String(digits.prefix(4)))
        }
        return nil
    }

    /// Returns a SwiftUI Color badge appropriate for the disc format.
    var formatBadgeColor: Color {
        switch format {
        case "4K UHD":
            return .purple
        case "Blu-ray":
            return .blue
        case "DVD":
            return .gray
        default:
            return .gray
        }
    }

    /// Convenience accessor for the wanted/watchlist state.
    var isWanted: Bool {
        wanted ?? false
    }

    /// Parsed list of additional videos from the `videos` JSON column.
    var parsedExtraVideos: [MovieVideo] {
        guard let raw = videos, !raw.isEmpty, let data = raw.data(using: .utf8) else {
            return []
        }
        return (try? JSONDecoder().decode([MovieVideo].self, from: data)) ?? []
    }

    /// Parsed list of backdrop URLs from the `backdrops` JSON column.
    var parsedBackdrops: [String] {
        guard let raw = backdrops, !raw.isEmpty, let data = raw.data(using: .utf8) else {
            return []
        }
        if let urls = try? JSONDecoder().decode([String].self, from: data) {
            return urls
        }
        return []
    }

    /// Combined video list: trailer first (if present), followed by extras.
    var allVideos: [MovieVideo] {
        var result: [MovieVideo] = []
        if let url = trailerUrl, !url.isEmpty, MovieVideo.extractYouTubeKey(from: url) != nil {
            result.append(MovieVideo(url: url, label: nil, type: "Trailer", source: "tmdb"))
        }
        for v in parsedExtraVideos where v.youtubeKey != nil {
            result.append(v)
        }
        return result
    }

    private static func decodeLocalizedValues(
        from container: KeyedDecodingContainer<RawMovieCodingKey>,
        prefix: String,
        keys: [String]
    ) -> [String: String] {
        var values: [String: String] = [:]
        for key in keys {
            let candidates = [
                "\(prefix)_\(key)",
                "\(prefix)\(key.uppercased())",
                "\(prefix)\(key.capitalized)"
            ]
            if let value = container.decodeFlexibleString(forAnyKey: candidates) {
                values[key] = value
            }
        }
        return values
    }
}

struct MovieEditDraft: Encodable, Sendable {
    var title: String
    var originalTitle: String
    var year: String
    var releaseDate: String
    var director: String
    var actor: String
    var producer: String
    var studios: String
    var genre: String
    var plot: String
    var format: String
    var barcode: String
    var location: String
    var notes: String
    var edition: String
    var editionType: String
    var editionReleaseYear: String
    var editionReleaseDate: String
    var customEditionLabel: String
    var packaging: String
    var screenRatios: String
    var regions: String
    var extras: String
    var boxSet: String
    var runtime: String
    var rating: String
    var audienceRating: String
    var hdr: String
    var language: String
    var audioTracks: String
    var subtitles: String
    var country: String
    var imdbId: String
    var imdbUrl: String
    var tmdbId: String
    var editionGroupId: Int?
    var collectionId: Int?

    init(movie: Movie) {
        title = movie.title
        originalTitle = movie.originalTitle ?? ""
        year = movie.year ?? ""
        releaseDate = movie.releaseDate ?? ""
        director = movie.director ?? ""
        actor = movie.actor ?? ""
        producer = movie.producer ?? ""
        studios = movie.studios ?? ""
        genre = movie.genre ?? ""
        plot = movie.plot ?? ""
        format = movie.format ?? "4K UHD"
        barcode = movie.barcode ?? ""
        location = movie.location ?? ""
        notes = movie.notes ?? ""
        edition = movie.edition ?? ""
        editionType = movie.editionType ?? ""
        editionReleaseYear = movie.editionReleaseYear ?? ""
        editionReleaseDate = movie.editionReleaseDate ?? ""
        customEditionLabel = movie.customEditionLabel ?? ""
        packaging = movie.packaging ?? ""
        screenRatios = movie.screenRatios ?? ""
        regions = movie.regions ?? ""
        extras = movie.extras ?? ""
        boxSet = movie.boxSet ?? ""
        runtime = movie.runtime ?? ""
        rating = movie.rating ?? ""
        audienceRating = movie.audienceRating ?? movie.ratingUs ?? ""
        hdr = movie.hdr ?? ""
        language = movie.language ?? ""
        audioTracks = movie.audioTracks ?? ""
        subtitles = movie.subtitles ?? ""
        country = movie.country ?? ""
        imdbId = movie.imdbId ?? ""
        imdbUrl = movie.imdbUrl ?? ""
        tmdbId = movie.tmdbId.map(String.init) ?? ""
        editionGroupId = movie.editionGroupId
        collectionId = movie.collectionId
    }

    enum CodingKeys: String, CodingKey {
        case title
        case originalTitle = "original_title"
        case year
        case releaseDate = "release_date"
        case director
        case actor
        case producer
        case studios
        case genre
        case plot
        case format
        case barcode
        case location
        case notes
        case edition
        case editionType = "edition_type"
        case editionReleaseYear = "edition_release_year"
        case editionReleaseDate = "edition_release_date"
        case customEditionLabel = "custom_edition_label"
        case packaging
        case screenRatios = "screen_ratios"
        case regions
        case extras
        case boxSet = "box_set"
        case runtime
        case rating
        case audienceRating = "audience_rating"
        case hdr
        case language
        case audioTracks = "audio_tracks"
        case subtitles
        case country
        case imdbId = "imdb_id"
        case imdbUrl = "imdb_url"
        case tmdbId = "tmdb_id"
        case editionGroupId = "edition_group_id"
        case collectionId = "collection_id"
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(title, forKey: .title)
        try container.encode(originalTitle, forKey: .originalTitle)
        try container.encode(year, forKey: .year)
        try container.encode(releaseDate, forKey: .releaseDate)
        try container.encode(director, forKey: .director)
        try container.encode(actor, forKey: .actor)
        try container.encode(producer, forKey: .producer)
        try container.encode(studios, forKey: .studios)
        try container.encode(genre, forKey: .genre)
        try container.encode(plot, forKey: .plot)
        try container.encode(format, forKey: .format)
        try container.encode(barcode, forKey: .barcode)
        try container.encode(location, forKey: .location)
        try container.encode(notes, forKey: .notes)
        try container.encode(edition, forKey: .edition)
        try container.encode(editionType, forKey: .editionType)
        try container.encode(editionReleaseYear, forKey: .editionReleaseYear)
        try container.encode(editionReleaseDate, forKey: .editionReleaseDate)
        try container.encode(customEditionLabel, forKey: .customEditionLabel)
        try container.encode(packaging, forKey: .packaging)
        try container.encode(screenRatios, forKey: .screenRatios)
        try container.encode(regions, forKey: .regions)
        try container.encode(extras, forKey: .extras)
        try container.encode(boxSet, forKey: .boxSet)
        try container.encode(runtime, forKey: .runtime)
        try container.encode(rating, forKey: .rating)
        try container.encode(audienceRating, forKey: .audienceRating)
        try container.encode(hdr, forKey: .hdr)
        try container.encode(language, forKey: .language)
        try container.encode(audioTracks, forKey: .audioTracks)
        try container.encode(subtitles, forKey: .subtitles)
        try container.encode(country, forKey: .country)
        try container.encode(imdbId, forKey: .imdbId)
        try container.encode(imdbUrl, forKey: .imdbUrl)
        try container.encode(tmdbId, forKey: .tmdbId)
        try container.encode(editionGroupId, forKey: .editionGroupId)
        try container.encode(collectionId, forKey: .collectionId)
    }
}

private struct RawMovieCodingKey: CodingKey {
    let stringValue: String
    let intValue: Int? = nil

    init(_ stringValue: String) {
        self.stringValue = stringValue
    }

    init?(stringValue: String) {
        self.stringValue = stringValue
    }

    init?(intValue: Int) {
        return nil
    }
}

private extension KeyedDecodingContainer {
    func decodeFlexibleString(forAnyKey keys: [String]) -> String? {
        for key in keys {
            if let value = decodeFlexibleString(forKey: Key(stringValue: key)!) {
                return value
            }
        }
        return nil
    }

    func decodeFlexibleInt(forAnyKey keys: [String]) -> Int? {
        for key in keys {
            if let value = decodeFlexibleInt(forKey: Key(stringValue: key)!) {
                return value
            }
        }
        return nil
    }

    func decodeFlexibleDouble(forAnyKey keys: [String]) -> Double? {
        for key in keys {
            if let value = decodeFlexibleDouble(forKey: Key(stringValue: key)!) {
                return value
            }
        }
        return nil
    }

    func decodeFlexibleBool(forAnyKey keys: [String]) -> Bool? {
        for key in keys {
            if let value = decodeFlexibleBool(forKey: Key(stringValue: key)!) {
                return value
            }
        }
        return nil
    }

    func decodeMovies(forAnyKey keys: [String]) -> [Movie] {
        for key in keys {
            if let values = try? decode([Movie].self, forKey: Key(stringValue: key)!) {
                return values
            }
        }
        return []
    }

    func decodeFlexibleString(forKey key: Key) -> String? {
        if (try? decodeNil(forKey: key)) == true {
            return nil
        }
        if let value = try? decode(String.self, forKey: key) {
            return value.isEmpty ? nil : value
        }
        if let value = try? decode(Int.self, forKey: key) {
            return String(value)
        }
        if let value = try? decode(Double.self, forKey: key) {
            return String(value)
        }
        if let value = try? decode(Bool.self, forKey: key) {
            return value ? "true" : "false"
        }
        return nil
    }

    func decodeFlexibleInt(forKey key: Key) -> Int? {
        if (try? decodeNil(forKey: key)) == true {
            return nil
        }
        if let value = try? decode(Int.self, forKey: key) {
            return value
        }
        if let value = try? decode(Double.self, forKey: key) {
            return Int(value)
        }
        if let value = try? decode(String.self, forKey: key) {
            return Int(value.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        return nil
    }

    func decodeFlexibleBool(forKey key: Key) -> Bool? {
        if (try? decodeNil(forKey: key)) == true {
            return nil
        }
        if let value = try? decode(Bool.self, forKey: key) {
            return value
        }
        if let value = try? decode(Int.self, forKey: key) {
            return value != 0
        }
        if let value = try? decode(String.self, forKey: key) {
            let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            if ["1", "true", "yes", "on"].contains(normalized) {
                return true
            }
            if ["0", "false", "no", "off"].contains(normalized) {
                return false
            }
        }
        return nil
    }

    func decodeFlexibleIntArray(forKey key: Key) -> [Int]? {
        if (try? decodeNil(forKey: key)) == true {
            return nil
        }
        if let values = try? decode([Int].self, forKey: key) {
            return values
        }
        if let values = try? decode([String].self, forKey: key) {
            return values.compactMap { Int($0.trimmingCharacters(in: .whitespacesAndNewlines)) }
        }
        if let value = decodeFlexibleInt(forKey: key) {
            return [value]
        }
        return nil
    }

    func decodeFlexibleDouble(forKey key: Key) -> Double? {
        if (try? decodeNil(forKey: key)) == true {
            return nil
        }
        if let value = try? decode(Double.self, forKey: key) {
            return value
        }
        if let value = try? decode(Int.self, forKey: key) {
            return Double(value)
        }
        if let value = try? decode(String.self, forKey: key) {
            return Double(value)
        }
        return nil
    }
}
