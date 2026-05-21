import SwiftUI

struct MainTabView: View {
    @EnvironmentObject private var appState: AppStateManager
    @Environment(APIClient.self) private var apiClient

    @State private var selectedTab: Tab = .collection
    @State private var showScanner = false
    @State private var showAddSheet = false

    enum Tab: String, CaseIterable {
        case collection = "Collection"
        case lists = "Lists"
        case scan = "Scan"
        case search = "Search"
        case profile = "Profile"
    }

    var body: some View {
        TabView(selection: $selectedTab) {
            Tab("Collection", systemImage: "square.grid.2x2.fill", value: .collection) {
                CollectionView()
            }

            Tab("Lists", systemImage: "bookmark.fill", value: .lists) {
                ListsView()
            }

            // Centre action tab — opens scanner directly
            Tab("Scan", systemImage: "barcode.viewfinder", value: .scan) {
                Color.clear
            }
            .onChange(of: selectedTab) { _, newTab in
                if newTab == .scan {
                    showScanner = true
                    // Snap back so the tab doesn't stay selected
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
                        selectedTab = .collection
                    }
                }
            }

            Tab("Search", systemImage: "magnifyingglass", value: .search) {
                SearchView()
            }

            Tab("Profile", systemImage: "person.fill", value: .profile) {
                ProfileView()
            }
        }
        .tabViewStyle(.sidebarAdaptable)
        .tint(Color(red: 0.55, green: 0.35, blue: 1.0))
        .fullScreenCover(isPresented: $showScanner) {
            BarcodeScannerView { barcode in
                showScanner = false
                handleScannedBarcode(barcode)
            }
        }
    }

    private func handleScannedBarcode(_ barcode: String) {
        // Post a notification that CollectionView can pick up to trigger an add flow
        NotificationCenter.default.post(
            name: .barcodeScanned,
            object: nil,
            userInfo: ["barcode": barcode]
        )
    }
}

extension Notification.Name {
    static let barcodeScanned = Notification.Name("discvault.barcodeScanned")
}
