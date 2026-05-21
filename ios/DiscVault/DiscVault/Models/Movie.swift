import Foundation
import SwiftUI

// MARK: - CastMember

struct CastMember: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
    let role: String?
    let character: String?
    let profilePhoto: String?

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case role
        case character
        case profilePhoto = "profile_photo"
    }
}

// MARK: - Movie

struct Movie: Codable, Identifiable, Hashable {
    let id: Int
    let title: String
    let year: String?
    let imdbId: String?
    let tmdbId: Int?
    let director: String?
    let plot: String?
    let genre: String?
    let runtime: String?
    let format: String?
    let ratingUs: String?
    let poster: String?
    let backdrop: String?
    let ownerId: Int?
    let addedAt: String?
    let wanted: Bool?
    let watched: Bool?
    let hdr: String?
    let dolbyVision: Bool?
    let audioCodec: String?
    let cast: [CastMember]?

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case year
        case imdbId = "imdb_id"
        case tmdbId = "tmdb_id"
        case director
        case plot
        case genre
        case runtime
        case format
        case ratingUs = "rating_us"
        case poster
        case backdrop
        case ownerId = "owner_id"
        case addedAt = "added_at"
        case wanted
        case watched
        case hdr
        case dolbyVision = "dolby_vision"
        case audioCodec = "audio_codec"
        case cast
    }

    // MARK: - Computed Properties

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
}
