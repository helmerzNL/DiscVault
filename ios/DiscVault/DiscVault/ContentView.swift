import SwiftUI

// Renamed from Tab to AppTab to avoid shadowing SwiftUI.Tab (iOS 18)
enum AppTab: String, Hashable {
    case collection, lists, add, search, profile
}

struct MainTabView: View {
    @EnvironmentObject private var appState: AppStateManager
    @Environment(APIClient.self) private var apiClient
    @Environment(AppLanguageManager.self) private var languageManager

    @State private var selectedTab: AppTab = .collection
    @State private var showScanner = false

    var body: some View {
        TabView(selection: $selectedTab) {
            SwiftUI.Tab(languageManager.text("nav.collection"), systemImage: "square.grid.2x2.fill", value: AppTab.collection) {
                CollectionView()
            }
            SwiftUI.Tab(languageManager.text("nav.lists"), systemImage: "bookmark.fill", value: AppTab.lists) {
                ListsView()
            }
            SwiftUI.Tab(languageManager.text("nav.add"), systemImage: "plus.circle.fill", value: AppTab.add) {
                AddView()
            }
            SwiftUI.Tab(languageManager.text("nav.search"), systemImage: "magnifyingglass", value: AppTab.search) {
                SearchView()
            }
            SwiftUI.Tab(languageManager.text("nav.profile"), systemImage: "person.fill", value: AppTab.profile) {
                ProfileView()
            }
        }
        .tabViewStyle(.sidebarAdaptable)
        .tint(Color(red: 0.55, green: 0.35, blue: 1.0))
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
