import SwiftUI

// Renamed from Tab to AppTab to avoid shadowing SwiftUI.Tab (iOS 18)
enum AppTab: String, Hashable {
    case collection, lists, scan, search, profile
}

struct MainTabView: View {
    @EnvironmentObject private var appState: AppStateManager
    @Environment(APIClient.self) private var apiClient

    @State private var selectedTab: AppTab = .collection
    @State private var showScanner = false

    var body: some View {
        TabView(selection: $selectedTab) {
            SwiftUI.Tab("Collection", systemImage: "square.grid.2x2.fill", value: AppTab.collection) {
                CollectionView()
            }
            SwiftUI.Tab("Lists", systemImage: "bookmark.fill", value: AppTab.lists) {
                ListsView()
            }
            SwiftUI.Tab("Scan", systemImage: "barcode.viewfinder", value: AppTab.scan) {
                Color.clear
            }
            SwiftUI.Tab("Search", systemImage: "magnifyingglass", value: AppTab.search) {
                SearchView()
            }
            SwiftUI.Tab("Profile", systemImage: "person.fill", value: AppTab.profile) {
                ProfileView()
            }
        }
        .tabViewStyle(.sidebarAdaptable)
        .tint(Color(red: 0.55, green: 0.35, blue: 1.0))
        .onChange(of: selectedTab) { _, newTab in
            if newTab == .scan {
                showScanner = true
                selectedTab = .collection
            }
        }
        .fullScreenCover(isPresented: $showScanner) {
            BarcodeScannerView { barcode in
                showScanner = false
                NotificationCenter.default.post(
                    name: .barcodeScanned,
                    object: nil,
                    userInfo: ["barcode": barcode]
                )
            }
        }
    }
}

extension Notification.Name {
    static let barcodeScanned = Notification.Name("discvault.barcodeScanned")
}
