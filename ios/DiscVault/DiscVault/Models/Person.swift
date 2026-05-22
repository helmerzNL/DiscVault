import Foundation

/// A movie appearance for a person, returned by `GET /api/people/:id`.
struct PersonMovieAppearance: Decodable, Identifiable, Hashable {
    let id: Int
    let title: String
    let year: String?
    let posterFile: String?
    let poster: String?
    let format: String?
    let tmdbId: Int?
    let role: String?
    let character: String?
    let job: String?

    private enum CodingKeys: String, CodingKey {
        case id
        case movieId
        case title
        case year
        case posterFile
        case poster
        case format
        case tmdbId
        case role
        case character
        case job
    }

    init(from decoder: Decoder) throws {
        guard let container = try? decoder.container(keyedBy: CodingKeys.self) else {
            id = 0
            title = ""
            year = nil
            posterFile = nil
            poster = nil
            format = nil
            tmdbId = nil
            role = nil
            character = nil
            job = nil
            return
        }

        id = container.decodeFlexibleInt(for: .id) ?? container.decodeFlexibleInt(for: .movieId) ?? 0
        title = container.decodeFlexibleStringIfPresent(for: .title) ?? ""
        year = container.decodeFlexibleStringIfPresent(for: .year)
        posterFile = container.decodeFlexibleStringIfPresent(for: .posterFile)
        poster = container.decodeFlexibleStringIfPresent(for: .poster)
        format = container.decodeFlexibleStringIfPresent(for: .format)
        tmdbId = container.decodeFlexibleInt(for: .tmdbId)
        role = container.decodeFlexibleStringIfPresent(for: .role)
        character = container.decodeFlexibleStringIfPresent(for: .character)
        job = container.decodeFlexibleStringIfPresent(for: .job)
    }
}

/// Detailed person record returned by `GET /api/people/:id`.
struct Person: Decodable, Identifiable, Hashable {
    let id: Int
    let name: String
    let tmdbId: Int?
    let photoFile: String?
    let photoUrl: String?
    let biography: String?
    let biographyNl: String?
    let biographyFr: String?
    let biographyDe: String?
    let biographyEs: String?
    let biographyPt: String?
    let biographyIt: String?
    let knownFor: String?
    let birthday: String?
    let deathday: String?
    let placeOfBirth: String?
    let movies: [PersonMovieAppearance]?

    fileprivate enum CodingKeys: String, CodingKey {
        case person
        case data
        case id
        case personId
        case name
        case tmdbId
        case photoFile
        case profilePath
        case photoUrl
        case biography
        case biographyNl
        case biographyFr
        case biographyDe
        case biographyEs
        case biographyPt
        case biographyIt
        case knownFor
        case birthday
        case deathday
        case placeOfBirth
        case movies
        case filmography
        case credits
    }

    init(from decoder: Decoder) throws {
        guard let container = try? decoder.container(keyedBy: CodingKeys.self) else {
            id = 0
            name = ""
            tmdbId = nil
            photoFile = nil
            photoUrl = nil
            biography = nil
            biographyNl = nil
            biographyFr = nil
            biographyDe = nil
            biographyEs = nil
            biographyPt = nil
            biographyIt = nil
            knownFor = nil
            birthday = nil
            deathday = nil
            placeOfBirth = nil
            movies = nil
            return
        }

        if !container.contains(.id), !container.contains(.personId) {
            if let wrapped = try? container.decodeIfPresent(Person.self, forKey: .person) {
                let wrapperMovies = container.decodeMovieListFromWrapper()
                self = wrapped.replacingMoviesIfNeeded(wrapperMovies)
                return
            }
            if let wrapped = try? container.decodeIfPresent(Person.self, forKey: .data) {
                let wrapperMovies = container.decodeMovieListFromWrapper()
                self = wrapped.replacingMoviesIfNeeded(wrapperMovies)
                return
            }
        }

        id = container.decodeFlexibleInt(for: .id) ?? container.decodeFlexibleInt(for: .personId) ?? 0
        name = container.decodeFlexibleStringIfPresent(for: .name) ?? ""
        tmdbId = container.decodeFlexibleInt(for: .tmdbId)
        photoFile = container.decodeFlexibleStringIfPresent(for: .photoFile) ?? container.decodeFlexibleStringIfPresent(for: .profilePath)
        photoUrl = container.decodeFlexibleStringIfPresent(for: .photoUrl)
        biography = container.decodeFlexibleStringIfPresent(for: .biography)
        biographyNl = container.decodeFlexibleStringIfPresent(for: .biographyNl)
        biographyFr = container.decodeFlexibleStringIfPresent(for: .biographyFr)
        biographyDe = container.decodeFlexibleStringIfPresent(for: .biographyDe)
        biographyEs = container.decodeFlexibleStringIfPresent(for: .biographyEs)
        biographyPt = container.decodeFlexibleStringIfPresent(for: .biographyPt)
        biographyIt = container.decodeFlexibleStringIfPresent(for: .biographyIt)
        knownFor = container.decodeFlexibleStringIfPresent(for: .knownFor)
        birthday = container.decodeFlexibleStringIfPresent(for: .birthday)
        deathday = container.decodeFlexibleStringIfPresent(for: .deathday)
        placeOfBirth = container.decodeFlexibleStringIfPresent(for: .placeOfBirth)
        movies = container.decodeMovieListFromWrapper()
    }

    private init(
        id: Int,
        name: String,
        tmdbId: Int?,
        photoFile: String?,
        photoUrl: String?,
        biography: String?,
        biographyNl: String?,
        biographyFr: String?,
        biographyDe: String?,
        biographyEs: String?,
        biographyPt: String?,
        biographyIt: String?,
        knownFor: String?,
        birthday: String?,
        deathday: String?,
        placeOfBirth: String?,
        movies: [PersonMovieAppearance]?
    ) {
        self.id = id
        self.name = name
        self.tmdbId = tmdbId
        self.photoFile = photoFile
        self.photoUrl = photoUrl
        self.biography = biography
        self.biographyNl = biographyNl
        self.biographyFr = biographyFr
        self.biographyDe = biographyDe
        self.biographyEs = biographyEs
        self.biographyPt = biographyPt
        self.biographyIt = biographyIt
        self.knownFor = knownFor
        self.birthday = birthday
        self.deathday = deathday
        self.placeOfBirth = placeOfBirth
        self.movies = movies
    }

    private func replacingMoviesIfNeeded(_ wrapperMovies: [PersonMovieAppearance]?) -> Person {
        Person(
            id: id,
            name: name,
            tmdbId: tmdbId,
            photoFile: photoFile,
            photoUrl: photoUrl,
            biography: biography,
            biographyNl: biographyNl,
            biographyFr: biographyFr,
            biographyDe: biographyDe,
            biographyEs: biographyEs,
            biographyPt: biographyPt,
            biographyIt: biographyIt,
            knownFor: knownFor,
            birthday: birthday,
            deathday: deathday,
            placeOfBirth: placeOfBirth,
            movies: movies ?? wrapperMovies
        )
    }

    /// Returns the biography in the requested language, falling back to English.
    func localizedBiography(_ languageCode: String) -> String? {
        let candidate: String?
        switch languageCode {
        case "nl": candidate = biographyNl
        case "fr": candidate = biographyFr
        case "de": candidate = biographyDe
        case "es": candidate = biographyEs
        case "pt": candidate = biographyPt
        case "it": candidate = biographyIt
        default: candidate = nil
        }
        if let candidate, !candidate.isEmpty { return candidate }
        return biography
    }
}

private extension KeyedDecodingContainer {
    func decodeFlexibleInt(for key: Key) -> Int? {
        if let intValue = try? decodeIfPresent(Int.self, forKey: key) {
            return intValue
        }
        if let stringValue = try? decodeIfPresent(String.self, forKey: key) {
            return Int(stringValue)
        }
        return nil
    }

    func decodeFlexibleStringIfPresent(for key: Key) -> String? {
        if let stringValue = try? decodeIfPresent(String.self, forKey: key) {
            return stringValue
        }
        if let intValue = try? decodeIfPresent(Int.self, forKey: key) {
            return String(intValue)
        }
        if let doubleValue = try? decodeIfPresent(Double.self, forKey: key) {
            return String(doubleValue)
        }
        return nil
    }
}

private extension KeyedDecodingContainer where Key == Person.CodingKeys {
    func decodeMovieListFromWrapper() -> [PersonMovieAppearance]? {
        if let movies = try? decodeIfPresent([PersonMovieAppearance].self, forKey: .movies) {
            return movies
        }
        if let filmography = try? decodeIfPresent([PersonMovieAppearance].self, forKey: .filmography) {
            return filmography
        }
        if let credits = try? decodeIfPresent([PersonMovieAppearance].self, forKey: .credits) {
            return credits
        }
        if let credits = try? decodeIfPresent(PersonCredits.self, forKey: .credits) {
            return credits.movies
        }
        return nil
    }
}

struct PersonFilmographyResponse: Decodable, Hashable {
    let cast: [PersonFilmographyItem]
    let crew: [PersonFilmographyItem]
    let tmdbAvailable: Bool?
}

struct PersonFilmographyItem: Decodable, Identifiable, Hashable {
    let tmdbId: Int?
    let title: String
    let year: String?
    let poster: String?
    let voteAverage: Double?
    let character: String?
    let job: String?
    let inCollection: Bool?
    let collectionId: Int?
    let collectionFormat: String?
    let inDigital: Bool?
    let digitalSource: String?

    var id: String {
        if let tmdbId { return "tmdb-\(tmdbId)" }
        if let collectionId { return "collection-\(collectionId)" }
        return "\(title)-\(year ?? "")-\(character ?? job ?? "")"
    }
}

private struct PersonCredits: Decodable {
    let cast: [PersonMovieAppearance]?
    let crew: [PersonMovieAppearance]?

    var movies: [PersonMovieAppearance] {
        (cast ?? []) + (crew ?? [])
    }
}
