import Foundation

struct AddMovieDraft: Codable {
    var barcode = ""
    var title = ""
    var originalTitle = ""
    var year = ""
    var releaseDate = ""
    var director = ""
    var actor = ""
    var producer = ""
    var studios = ""
    var genre = ""
    var format = "4K UHD"
    var runtime = ""
    var rating = ""
    var hdr = ""
    var language = ""
    var audioTracks = ""
    var subtitles = ""
    var country = ""
    var plot = ""
    var imdbId = ""
    var imdbUrl = ""
    var tmdbId = ""
    var location = ""
    var notes = ""
    var edition = ""
    var editionType = ""
    var editionReleaseYear = ""
    var editionReleaseDate = ""
    var customEditionLabel = ""
    var packaging = ""
    var screenRatios = ""
    var regions = ""
    var extras = ""
    var boxSet = ""
    var poster = ""
    var editionGroupId: Int?
    var collectionId: Int?

    enum CodingKeys: String, CodingKey {
        case barcode
        case title
        case originalTitle = "original_title"
        case year
        case releaseDate = "release_date"
        case director
        case actor
        case producer
        case studios
        case genre
        case format
        case runtime
        case rating
        case hdr
        case language
        case audioTracks = "audio_tracks"
        case subtitles
        case country
        case plot
        case imdbId = "imdb_id"
        case imdbUrl = "imdb_url"
        case tmdbId = "tmdb_id"
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
        case poster
        case editionGroupId = "edition_group_id"
        case collectionId = "collection_id"
    }

    init() {}

    init(lookupMovie: LookupMovie, barcode: String = "") {
        self.barcode = barcode
        title = lookupMovie.title ?? ""
        originalTitle = lookupMovie.originalTitle ?? ""
        year = lookupMovie.year ?? ""
        releaseDate = lookupMovie.releaseDate ?? ""
        director = lookupMovie.director ?? ""
        actor = lookupMovie.actor ?? ""
        producer = lookupMovie.producer ?? ""
        studios = lookupMovie.studios ?? ""
        genre = lookupMovie.genre ?? ""
        format = lookupMovie.format?.isEmpty == false ? lookupMovie.format! : "4K UHD"
        runtime = lookupMovie.runtime ?? ""
        rating = lookupMovie.rating ?? ""
        hdr = lookupMovie.hdr ?? ""
        language = lookupMovie.language ?? ""
        audioTracks = lookupMovie.audioTracks ?? ""
        subtitles = lookupMovie.subtitles ?? ""
        country = lookupMovie.country ?? ""
        plot = lookupMovie.plot ?? ""
        imdbId = lookupMovie.imdbId ?? ""
        imdbUrl = lookupMovie.imdbUrl ?? ""
        if let tmdbId = lookupMovie.tmdbId {
            self.tmdbId = tmdbId.description
        }
        poster = lookupMovie.poster ?? ""
        edition = lookupMovie.edition ?? ""
        editionType = lookupMovie.editionType ?? ""
        editionReleaseYear = lookupMovie.editionReleaseYear ?? ""
        editionReleaseDate = lookupMovie.editionReleaseDate ?? ""
        customEditionLabel = lookupMovie.customEditionLabel ?? ""
        packaging = lookupMovie.packaging ?? ""
        screenRatios = lookupMovie.screenRatios ?? ""
        regions = lookupMovie.regions ?? ""
        extras = lookupMovie.extras ?? ""
        boxSet = lookupMovie.boxSet ?? ""
        editionGroupId = lookupMovie.editionGroupId?.value
        collectionId = lookupMovie.collectionId?.value
    }
}

struct LookupResponse: Codable {
    let status: String?
    let movie: LookupMovie?
    let barcode: String?
    let rawTitle: String?
    let detectedFormat: String?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case status
        case movie
        case barcode
        case rawTitle = "raw_title"
        case detectedFormat = "detected_format"
        case error
    }
}

struct LookupMovie: Codable {
    let title: String?
    let originalTitle: String?
    let year: String?
    let releaseDate: String?
    let director: String?
    let actor: String?
    let producer: String?
    let studios: String?
    let genre: String?
    let format: String?
    let runtime: String?
    let rating: String?
    let hdr: String?
    let language: String?
    let audioTracks: String?
    let subtitles: String?
    let country: String?
    let plot: String?
    let imdbId: String?
    let imdbUrl: String?
    let tmdbId: FlexibleInt?
    let poster: String?
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
    let editionGroupId: FlexibleInt?
    let collectionId: FlexibleInt?

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
        case format
        case runtime
        case rating
        case hdr
        case language
        case audioTracks = "audio_tracks"
        case subtitles
        case country
        case plot
        case imdbId = "imdb_id"
        case imdbUrl = "imdb_url"
        case tmdbId = "tmdb_id"
        case poster
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
        case editionGroupId = "edition_group_id"
        case collectionId = "collection_id"
    }
}

struct FlexibleInt: Codable, Hashable, CustomStringConvertible, Sendable {
    let value: Int

    var description: String { "\(value)" }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let intValue = try? container.decode(Int.self) {
            value = intValue
        } else if let stringValue = try? container.decode(String.self), let intValue = Int(stringValue) {
            value = intValue
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Expected integer or integer string")
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(value)
    }
}
