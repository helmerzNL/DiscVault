import SwiftUI

struct MovieCardView: View {
    let movie: Movie
    let apiClient: APIClient
    let digitalBadgeTypes: Set<String>
    let groupMultipleEditionsEnabled: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            posterCard
            cardInfo
        }
    }

    private var posterCard: some View {
        ZStack(alignment: .topTrailing) {
            poster
            formatBadge
            if let hdr = movie.hdr, !hdr.isEmpty {
                hdrBadge(hdr)
            }
            if movie.wanted == true || !digitalBadgeTypes.isEmpty {
                topLeadingBadges
            }
            if movie.isContainerCard, let count = movie.editionsCount, count > 0 {
                containerCountBadge(count)
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .shadow(color: .black.opacity(0.35), radius: 8, y: 4)
        .aspectRatio(2/3, contentMode: .fit)
    }

    private var cardInfo: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(movie.displayTitle)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.white)
                .lineLimit(2)
                .multilineTextAlignment(.leading)
                .frame(maxWidth: .infinity, alignment: .leading)

            Text(movie.displayYear ?? " ")
                .font(.caption2.weight(.medium))
                .foregroundStyle(.white.opacity(0.55))
                .lineLimit(1)
        }
        .frame(height: 42, alignment: .top)
    }

    private var poster: some View {
        GeometryReader { geo in
            if let url = apiClient.posterURL(for: movie.posterPath(groupMultipleEditionsEnabled: groupMultipleEditionsEnabled)) {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let img):
                        img.resizable()
                            .aspectRatio(contentMode: .fill)
                            .frame(width: geo.size.width, height: geo.size.height)
                            .clipped()
                    default:
                        placeholder(size: geo.size)
                    }
                }
            } else {
                placeholder(size: geo.size)
            }
        }
    }

    private func placeholder(size: CGSize) -> some View {
        ZStack {
            LinearGradient(
                colors: [Color(red: 0.12, green: 0.12, blue: 0.22), Color(red: 0.08, green: 0.08, blue: 0.16)],
                startPoint: .topLeading, endPoint: .bottomTrailing
            )
            VStack(spacing: 8) {
                Image(systemName: "opticaldisc")
                    .font(.system(size: 28))
                    .foregroundStyle(.white.opacity(0.25))
                Text(movie.displayTitle)
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.4))
                    .multilineTextAlignment(.center)
                    .lineLimit(3)
                    .padding(.horizontal, 8)
            }
        }
        .frame(width: size.width, height: size.height)
    }

    private var formatBadge: some View {
        SwiftUI.Group {
            if let label = movie.containerBadgeLabel {
                Text(label)
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 4)
                    .background(containerBadgeColor.opacity(0.95))
                    .clipShape(Capsule())
                    .overlay(Capsule().stroke(.white.opacity(0.22), lineWidth: 0.5))
                    .padding(6)
            } else if let format = movie.format {
                Text(formatLabel(format))
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 5)
                    .padding(.vertical, 3)
                    .background(formatColor(format))
                    .clipShape(RoundedRectangle(cornerRadius: 4))
                    .padding(6)
            }
        }
    }

    private func hdrBadge(_ hdr: String) -> some View {
        let label = hdr.lowercased().contains("dolby") ? "DV" : "HDR"
        return Text(label)
            .font(.system(size: 8, weight: .heavy))
            .foregroundStyle(.white)
            .padding(.horizontal, 4)
            .padding(.vertical, 2)
            .background(hdr.lowercased().contains("dolby") ? Color(red: 0.0, green: 0.4, blue: 0.8) : Color(red: 0.8, green: 0.5, blue: 0.0))
            .clipShape(RoundedRectangle(cornerRadius: 3))
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
            .padding(6)
    }

    private func containerCountBadge(_ count: Int) -> some View {
        Label("\(count)", systemImage: "film.stack")
            .labelStyle(.titleAndIcon)
            .font(.system(size: 10, weight: .bold))
            .foregroundStyle(.white)
            .padding(.horizontal, 7)
            .padding(.vertical, 4)
            .background(.black.opacity(0.72))
            .clipShape(Capsule())
            .overlay(Capsule().stroke(.white.opacity(0.18), lineWidth: 0.5))
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
            .padding(6)
            .accessibilityLabel("\(count) movies")
    }

    private var topLeadingBadges: some View {
        VStack(alignment: .leading, spacing: 4) {
            if !digitalBadgeTypes.isEmpty {
                digitalBadges
            }
            if movie.wanted == true {
                wantedBadge
            }
        }
        .padding(6)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private var digitalBadges: some View {
        VStack(spacing: 3) {
            if digitalBadgeTypes.contains("plex") {
                digitalBadge(accessibilityLabel: "Plex") {
                    Image("PlexLogo")
                        .resizable()
                        .scaledToFit()
                        .frame(width: 14, height: 14)
                }
            }
            if digitalBadgeTypes.contains("jellyfin") {
                digitalBadge(accessibilityLabel: "Jellyfin") {
                    Image("JellyfinLogo")
                        .resizable()
                        .scaledToFit()
                        .frame(width: 14, height: 14)
                }
            }
        }
    }

    private func digitalBadge<Logo: View>(accessibilityLabel: String, @ViewBuilder logo: () -> Logo) -> some View {
        logo()
            .frame(width: 18, height: 18)
            .background(.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 4))
            .accessibilityLabel(accessibilityLabel)
    }

    private var wantedBadge: some View {
        Image(systemName: "bookmark.fill")
            .font(.system(size: 14))
            .foregroundStyle(.yellow)
            .shadow(color: .black.opacity(0.4), radius: 2)
            .frame(width: 18, height: 18)
            .background(.black.opacity(0.35), in: RoundedRectangle(cornerRadius: 4))
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

    private var containerBadgeColor: Color {
        if movie.isCollection == true {
            return Color(red: 0.1, green: 0.52, blue: 0.34)
        }
        if movie.isSuperGroup == true {
            return Color(red: 0.18, green: 0.38, blue: 0.82)
        }
        return Color(red: 0.55, green: 0.34, blue: 0.86)
    }
}
