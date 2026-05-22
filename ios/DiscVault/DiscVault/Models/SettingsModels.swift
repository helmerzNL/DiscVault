import Foundation

struct ServerHealth: Codable {
    let status: String?
    let version: String?
}

struct DatabaseStats: Codable {
    let dbSize: Int
    let posterCount: Int
    let posterSize: Int
    let movieCount: Int
    let logCount: Int

    init(dbSize: Int = 0, posterCount: Int = 0, posterSize: Int = 0, movieCount: Int = 0, logCount: Int = 0) {
        self.dbSize = dbSize
        self.posterCount = posterCount
        self.posterSize = posterSize
        self.movieCount = movieCount
        self.logCount = logCount
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.singleValueContainer().decode([String: Int].self)
        dbSize = values["db_size"] ?? values["dbSize"] ?? 0
        posterCount = values["poster_count"] ?? values["posterCount"] ?? 0
        posterSize = values["poster_size"] ?? values["posterSize"] ?? 0
        movieCount = values["movie_count"] ?? values["movieCount"] ?? 0
        logCount = values["log_count"] ?? values["logCount"] ?? 0
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(dbSize, forKey: .dbSize)
        try container.encode(posterCount, forKey: .posterCount)
        try container.encode(posterSize, forKey: .posterSize)
        try container.encode(movieCount, forKey: .movieCount)
        try container.encode(logCount, forKey: .logCount)
    }

    private enum CodingKeys: String, CodingKey {
        case dbSize = "db_size"
        case posterCount = "poster_count"
        case posterSize = "poster_size"
        case movieCount = "movie_count"
        case logCount = "log_count"
    }
}

struct EditionGroup: Codable, Identifiable {
    let id: Int
    let title: String
    let groupType: String?
    let badgeLabel: String?
    let parentGroupId: Int?
    let memberCount: Int?
    let childGroupCount: Int?
    let looseMovieCount: Int?
    let childMemberCount: Int?
    let movieCount: Int?
    let moviesCount: Int?
    let itemCount: Int?
    let totalCount: Int?
    let membersCount: Int?

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case groupType = "group_type"
        case badgeLabel = "badge_label"
        case parentGroupId = "parent_group_id"
        case memberCount = "member_count"
        case childGroupCount = "child_group_count"
        case looseMovieCount = "loose_movie_count"
        case childMemberCount = "child_member_count"
        case movieCount = "movie_count"
        case moviesCount = "movies_count"
        case itemCount = "item_count"
        case totalCount = "total_count"
        case membersCount = "members_count"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: SettingsCodingKey.self)
        id = container.decodeFlexibleInt(forAnyKey: ["id"]) ?? 0
        title = container.decodeFlexibleString(forAnyKey: ["title", "name"]) ?? ""
        groupType = container.decodeFlexibleString(forAnyKey: ["group_type", "groupType", "type", "kind"])
        badgeLabel = container.decodeFlexibleString(forAnyKey: ["badge_label", "badgeLabel", "label"])
        parentGroupId = container.decodeFlexibleInt(forAnyKey: ["parent_group_id", "parentGroupId", "parent_id", "parentId"])
        memberCount = container.decodeFlexibleInt(forAnyKey: ["member_count", "memberCount"])
        childGroupCount = container.decodeFlexibleInt(forAnyKey: ["child_group_count", "childGroupCount"])
        looseMovieCount = container.decodeFlexibleInt(forAnyKey: ["loose_movie_count", "looseMovieCount"])
        childMemberCount = container.decodeFlexibleInt(forAnyKey: ["child_member_count", "childMemberCount"])
        movieCount = container.decodeFlexibleInt(forAnyKey: ["movie_count", "movieCount"])
        moviesCount = container.decodeFlexibleInt(forAnyKey: ["movies_count", "moviesCount"])
        itemCount = container.decodeFlexibleInt(forAnyKey: ["item_count", "itemCount"])
        totalCount = container.decodeFlexibleInt(forAnyKey: ["total_count", "totalCount", "count"])
        membersCount = container.decodeFlexibleInt(forAnyKey: ["members_count", "membersCount"])
    }

    var containerKind: BulkContainerKind {
        let type = normalizedTypeText
        if type.normalizedContainerTypeContainsBoxSet {
            return .boxset
        }
        if type.contains("vault") {
            return .vault
        }
        if parentGroupId != nil {
            return .vault
        }
        if (childGroupCount ?? 0) > 0 || (looseMovieCount ?? 0) > 0 || (childMemberCount ?? 0) > 0 {
            return .boxset
        }
        return .vault
    }

    var displayMemberCount: Int {
        let explicit = [
            memberCount,
            movieCount,
            moviesCount,
            itemCount,
            totalCount,
            membersCount
        ].compactMap { $0 }.max() ?? 0
        let aggregate = (looseMovieCount ?? 0) + (childMemberCount ?? 0)
        return max(explicit, aggregate)
    }

    private var normalizedTypeText: String {
        [groupType, badgeLabel]
            .compactMap { $0 }
            .joined(separator: " ")
            .lowercased()
            .replacingOccurrences(of: "_", with: "")
            .replacingOccurrences(of: "-", with: "")
    }
}

extension String {
    var normalizedContainerTypeContainsBoxSet: Bool {
        let value = lowercased()
            .replacingOccurrences(of: "_", with: "")
            .replacingOccurrences(of: "-", with: "")
        return value.contains("boxset") || lowercased().contains("box set")
    }
}

struct DiscCollection: Codable, Identifiable {
    let id: Int
    let title: String
    let groupCount: Int?
    let looseMovieCount: Int?
    let egMovieCount: Int?
    let boxsetLooseCount: Int?
    let movieCount: Int?
    let moviesCount: Int?
    let itemCount: Int?
    let totalCount: Int?

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case groupCount = "group_count"
        case looseMovieCount = "loose_movie_count"
        case egMovieCount = "eg_movie_count"
        case boxsetLooseCount = "boxset_loose_count"
        case movieCount = "movie_count"
        case moviesCount = "movies_count"
        case itemCount = "item_count"
        case totalCount = "total_count"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: SettingsCodingKey.self)
        id = container.decodeFlexibleInt(forAnyKey: ["id"]) ?? 0
        title = container.decodeFlexibleString(forAnyKey: ["title", "name"]) ?? ""
        groupCount = container.decodeFlexibleInt(forAnyKey: ["group_count", "groupCount"])
        looseMovieCount = container.decodeFlexibleInt(forAnyKey: ["loose_movie_count", "looseMovieCount"])
        egMovieCount = container.decodeFlexibleInt(forAnyKey: ["eg_movie_count", "egMovieCount"])
        boxsetLooseCount = container.decodeFlexibleInt(forAnyKey: ["boxset_loose_count", "boxsetLooseCount"])
        movieCount = container.decodeFlexibleInt(forAnyKey: ["movie_count", "movieCount"])
        moviesCount = container.decodeFlexibleInt(forAnyKey: ["movies_count", "moviesCount"])
        itemCount = container.decodeFlexibleInt(forAnyKey: ["item_count", "itemCount"])
        totalCount = container.decodeFlexibleInt(forAnyKey: ["total_count", "totalCount", "count"])
    }

    var displayMemberCount: Int {
        let explicit = [
            movieCount,
            moviesCount,
            itemCount,
            totalCount
        ].compactMap { $0 }.max() ?? 0
        let aggregate = (egMovieCount ?? 0) + (looseMovieCount ?? 0) + (boxsetLooseCount ?? 0)
        return max(explicit, aggregate)
    }
}

struct EditionGroupDetail: Codable, Identifiable {
    let id: Int
    let title: String
    let badgeLabel: String?
    let groupType: String?
    let parentGroupId: Int?
    let collectionId: Int?
    let posterFile: String?
    let poster: String?
    let year: String?
    let description: String?
    let backdrop: String?
    let members: [Movie]
    let looseMovies: [Movie]
    let childGroups: [EditionGroupChild]

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case badgeLabel = "badge_label"
        case groupType = "group_type"
        case parentGroupId = "parent_group_id"
        case collectionId = "collection_id"
        case posterFile = "poster_file"
        case poster
        case year
        case description
        case backdrop
        case members
        case looseMovies = "loose_movies"
        case childGroups = "child_groups"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? container.decode(Int.self, forKey: .id)) ?? 0
        title = (try? container.decode(String.self, forKey: .title)) ?? ""
        badgeLabel = try? container.decodeIfPresent(String.self, forKey: .badgeLabel)
        groupType = try? container.decodeIfPresent(String.self, forKey: .groupType)
        parentGroupId = try? container.decodeIfPresent(Int.self, forKey: .parentGroupId)
        collectionId = try? container.decodeIfPresent(Int.self, forKey: .collectionId)
        posterFile = try? container.decodeIfPresent(String.self, forKey: .posterFile)
        poster = try? container.decodeIfPresent(String.self, forKey: .poster)
        year = try? container.decodeIfPresent(String.self, forKey: .year)
        description = try? container.decodeIfPresent(String.self, forKey: .description)
        backdrop = try? container.decodeIfPresent(String.self, forKey: .backdrop)
        members = (try? container.decode([Movie].self, forKey: .members)) ?? []
        looseMovies = (try? container.decode([Movie].self, forKey: .looseMovies)) ?? []
        childGroups = (try? container.decode([EditionGroupChild].self, forKey: .childGroups)) ?? []
    }
}

struct DiscCollectionDetail: Codable, Identifiable {
    let id: Int
    let title: String
    let posterFile: String?
    let poster: String?
    let year: String?
    let description: String?
    let backdrop: String?
    let editionGroups: [EditionGroupChild]
    let looseMovies: [Movie]
    let egMovies: [Movie]

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case posterFile = "poster_file"
        case poster
        case year
        case description
        case backdrop
        case editionGroups = "edition_groups"
        case looseMovies = "loose_movies"
        case egMovies = "eg_movies"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? container.decode(Int.self, forKey: .id)) ?? 0
        title = (try? container.decode(String.self, forKey: .title)) ?? ""
        posterFile = try? container.decodeIfPresent(String.self, forKey: .posterFile)
        poster = try? container.decodeIfPresent(String.self, forKey: .poster)
        year = try? container.decodeIfPresent(String.self, forKey: .year)
        description = try? container.decodeIfPresent(String.self, forKey: .description)
        backdrop = try? container.decodeIfPresent(String.self, forKey: .backdrop)
        editionGroups = (try? container.decode([EditionGroupChild].self, forKey: .editionGroups)) ?? []
        looseMovies = (try? container.decode([Movie].self, forKey: .looseMovies)) ?? []
        egMovies = (try? container.decode([Movie].self, forKey: .egMovies)) ?? []
    }
}

struct EditionGroupChild: Codable, Identifiable, Hashable {
    let id: Int
    let title: String
    let badgeLabel: String?
    let parentGroupId: Int?

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case badgeLabel = "badge_label"
        case parentGroupId = "parent_group_id"
    }
}

private struct SettingsCodingKey: CodingKey {
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

private extension KeyedDecodingContainer where Key == SettingsCodingKey {
    func decodeFlexibleString(forAnyKey keys: [String]) -> String? {
        for key in keys {
            if let value = decodeFlexibleString(forKey: SettingsCodingKey(key)) {
                return value
            }
        }
        return nil
    }

    func decodeFlexibleInt(forAnyKey keys: [String]) -> Int? {
        for key in keys {
            if let value = decodeFlexibleInt(forKey: SettingsCodingKey(key)) {
                return value
            }
        }
        return nil
    }

    private func decodeFlexibleString(forKey key: SettingsCodingKey) -> String? {
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

    private func decodeFlexibleInt(forKey key: SettingsCodingKey) -> Int? {
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
}
