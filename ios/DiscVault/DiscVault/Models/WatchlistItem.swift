import Foundation

// MARK: - WatchlistItem

struct WatchlistItem: Codable, Identifiable {
    let id: Int
    let movieId: Int?
    let title: String
    let year: String?
    let poster: String?
    let format: String?
    let addedAt: String?
    let watchlistAddedAt: String?
    let watchedAt: String?
    let lastWatched: String?
    let releaseDate: String?
    let tmdbRating: Double?
    let voteAverage: Double?
    let rating: String?

    enum CodingKeys: String, CodingKey {
        case id
        case movieId = "movie_id"
        case title
        case year
        case poster
        case format
        case addedAt = "added_at"
        case watchlistAddedAt = "watchlist_added_at"
        case watchedAt = "watched_at"
        case lastWatched = "last_watched"
        case releaseDate = "release_date"
        case tmdbRating = "tmdb_rating"
        case voteAverage = "vote_average"
        case rating
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: WatchlistCodingKey.self)
        id = container.decodeFlexibleInt(forAnyKey: ["id"]) ?? container.decodeFlexibleInt(forAnyKey: ["movie_id", "movieId"]) ?? 0
        movieId = container.decodeFlexibleInt(forAnyKey: ["movie_id", "movieId", "movie.id"])
        title = container.decodeFlexibleString(forAnyKey: ["title", "name"]) ?? ""
        year = container.decodeFlexibleString(forAnyKey: ["year"])
        poster = container.decodeFlexibleString(forAnyKey: ["poster", "poster_file", "posterFile"])
        format = container.decodeFlexibleString(forAnyKey: ["format"])
        addedAt = container.decodeFlexibleString(forAnyKey: ["added_at", "addedAt", "created_at", "createdAt"])
        watchlistAddedAt = container.decodeFlexibleString(
            forAnyKey: [
                "watchlist_added_at",
                "watchlistAddedAt",
                "added_to_watchlist_at",
                "addedToWatchlistAt",
                "watchlist_at",
                "watchlistAt",
                "wanted_at",
                "wantedAt"
            ]
        )
        watchedAt = container.decodeFlexibleString(forAnyKey: ["watched_at", "watchedAt"])
        lastWatched = container.decodeFlexibleString(forAnyKey: ["last_watched", "lastWatched"])
        releaseDate = container.decodeFlexibleString(forAnyKey: ["release_date", "releaseDate"])
        tmdbRating = container.decodeFlexibleDouble(forAnyKey: ["tmdb_rating", "tmdbRating", "tmdb_vote_average"])
        voteAverage = container.decodeFlexibleDouble(forAnyKey: ["vote_average", "voteAverage"])
        rating = container.decodeFlexibleString(forAnyKey: ["rating"])
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encodeIfPresent(movieId, forKey: .movieId)
        try container.encode(title, forKey: .title)
        try container.encodeIfPresent(year, forKey: .year)
        try container.encodeIfPresent(poster, forKey: .poster)
        try container.encodeIfPresent(format, forKey: .format)
        try container.encodeIfPresent(addedAt, forKey: .addedAt)
        try container.encodeIfPresent(watchlistAddedAt, forKey: .watchlistAddedAt)
        try container.encodeIfPresent(watchedAt, forKey: .watchedAt)
        try container.encodeIfPresent(lastWatched, forKey: .lastWatched)
        try container.encodeIfPresent(releaseDate, forKey: .releaseDate)
        try container.encodeIfPresent(tmdbRating, forKey: .tmdbRating)
        try container.encodeIfPresent(voteAverage, forKey: .voteAverage)
        try container.encodeIfPresent(rating, forKey: .rating)
    }

    var watchedDate: String? {
        watchedAt ?? lastWatched
    }

    var detailMovieId: Int {
        movieId ?? id
    }

    var watchlistDate: String? {
        watchlistAddedAt ?? addedAt
    }

    var sortRating: Double {
        tmdbRating ?? voteAverage ?? Double(rating ?? "") ?? 0
    }
}

private struct WatchlistCodingKey: CodingKey {
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

private extension KeyedDecodingContainer where Key == WatchlistCodingKey {
    func decodeFlexibleString(forAnyKey keys: [String]) -> String? {
        for key in keys {
            if let value = decodeFlexibleString(forKey: WatchlistCodingKey(key)) {
                return value
            }
        }
        return nil
    }

    func decodeFlexibleInt(forAnyKey keys: [String]) -> Int? {
        for key in keys {
            if let value = decodeFlexibleInt(forKey: WatchlistCodingKey(key)) {
                return value
            }
        }
        return nil
    }

    func decodeFlexibleDouble(forAnyKey keys: [String]) -> Double? {
        for key in keys {
            if let value = decodeFlexibleDouble(forKey: WatchlistCodingKey(key)) {
                return value
            }
        }
        return nil
    }

    private func decodeFlexibleString(forKey key: WatchlistCodingKey) -> String? {
        if (try? decodeNil(forKey: key)) == true {
            return nil
        }
        if let value = try? decode(String.self, forKey: key) {
            let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : trimmed
        }
        if let value = try? decode(Int.self, forKey: key) {
            return String(value)
        }
        if let value = try? decode(Double.self, forKey: key) {
            return String(value)
        }
        return nil
    }

    private func decodeFlexibleInt(forKey key: WatchlistCodingKey) -> Int? {
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

    private func decodeFlexibleDouble(forKey key: WatchlistCodingKey) -> Double? {
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
            return Double(value.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        return nil
    }
}
