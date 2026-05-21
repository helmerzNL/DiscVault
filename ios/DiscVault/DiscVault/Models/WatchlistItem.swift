import Foundation

// MARK: - WatchlistItem

struct WatchlistItem: Codable, Identifiable {
    let id: Int
    let title: String
    let year: String?
    let poster: String?
    let format: String?
    let addedAt: String?

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case year
        case poster
        case format
        case addedAt = "added_at"
    }
}
