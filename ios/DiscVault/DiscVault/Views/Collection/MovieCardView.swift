import SwiftUI

struct MovieCardView: View {
    let movie: Movie
    let apiClient: APIClient

    var body: some View {
        ZStack(alignment: .topTrailing) {
            poster
            formatBadge
            if let hdr = movie.hdr, !hdr.isEmpty {
                hdrBadge(hdr)
            }
            if movie.wanted == true {
                wantedBadge
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .shadow(color: .black.opacity(0.35), radius: 8, y: 4)
        .aspectRatio(2/3, contentMode: .fit)
    }

    private var poster: some View {
        GeometryReader { geo in
            if let url = apiClient.posterURL(for: movie.poster) {
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
                Text(movie.title)
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
            if let format = movie.format {
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

    private var wantedBadge: some View {
        Image(systemName: "bookmark.fill")
            .font(.system(size: 14))
            .foregroundStyle(.yellow)
            .shadow(color: .black.opacity(0.4), radius: 2)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .padding(6)
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
}
