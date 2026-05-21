import Foundation

// MARK: - Group

struct Group: Codable, Identifiable {
    let id: Int
    let name: String
    let createdBy: Int?
    let createdAt: String?
    let memberCount: Int?

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case createdBy = "created_by"
        case createdAt = "created_at"
        case memberCount = "member_count"
    }
}

// MARK: - GroupMember

struct GroupMember: Codable, Identifiable {
    let id: Int
    let username: String
    let displayName: String?
    let avatar: String?
    let joinedAt: String?

    enum CodingKeys: String, CodingKey {
        case id
        case username
        case displayName = "display_name"
        case avatar
        case joinedAt = "joined_at"
    }
}
