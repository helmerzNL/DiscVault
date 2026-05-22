import Foundation

// MARK: - Group

struct Group: Codable, Identifiable {
    let id: Int
    let name: String
    let createdBy: FlexibleStringID?
    let createdByUsername: String?
    let createdAt: String?
    let memberCount: Int?
    let movieCount: Int?
    let myRole: String?

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case createdBy = "created_by"
        case createdByUsername = "created_by_username"
        case createdAt = "created_at"
        case memberCount = "member_count"
        case movieCount = "movie_count"
        case myRole = "my_role"
    }
}

// MARK: - GroupMember

struct GroupMember: Codable, Identifiable {
    let id: FlexibleStringID
    let username: String
    let displayName: String?
    let avatar: String?
    let joinedAt: String?
    let role: String?
    let groupRole: String?

    enum CodingKeys: String, CodingKey {
        case id
        case username
        case displayName = "display_name"
        case avatar
        case joinedAt = "joined_at"
        case role
        case groupRole = "group_role"
    }
}

struct FlexibleStringID: Codable, Hashable, CustomStringConvertible {
    let value: String

    var description: String { value }

    init(_ value: String) {
        self.value = value
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let stringValue = try? container.decode(String.self) {
            value = stringValue
        } else if let intValue = try? container.decode(Int.self) {
            value = "\(intValue)"
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Expected string or integer ID")
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(value)
    }
}
