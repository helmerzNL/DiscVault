import SwiftUI

struct CollectionView: View {
    @Environment(APIClient.self) private var apiClient
    @EnvironmentObject private var appState: AppStateManager
    @Environment(AppLanguageManager.self) private var languageManager

    @State private var viewModel: CollectionViewModel?
    @State private var showFilters = false
    @State private var showAddSheet = false
    @State private var showScanner = false
    @State private var showGroupAssignment = false
    @State private var showContainerAssignment = false
    @State private var manualBarcode: String = ""
    @State private var isLookingUp = false
    @State private var lookupError: String? = nil
    @State private var scannedBarcode: String? = nil
    @State private var showFloatingBulkActions = false

    private let columns = [
        GridItem(.flexible(), spacing: 10),
        GridItem(.flexible(), spacing: 10),
        GridItem(.flexible(), spacing: 10)
    ]

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.06, green: 0.06, blue: 0.14).ignoresSafeArea()

                if let vm = viewModel {
                    content(vm: vm)
                } else {
                    ProgressView()
                        .tint(.white)
                }
            }
            .navigationTitle("")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                toolbarItems
            }
            .modifier(CollectionSearchModifier(
                isEnabled: viewModel?.showSearchBar ?? true,
                searchText: Binding(
                    get: { viewModel?.searchText ?? "" },
                    set: { viewModel?.searchText = $0 }
                )
            ))
            .sheet(isPresented: $showFilters) {
                if let vm = viewModel { FilterSheet(viewModel: vm) }
            }
            .sheet(isPresented: $showAddSheet) {
                addSheet
            }
            .sheet(isPresented: $showGroupAssignment) {
                if let vm = viewModel {
                    BulkGroupAssignmentSheet(viewModel: vm)
                }
            }
            .sheet(isPresented: $showContainerAssignment) {
                if let vm = viewModel {
                    BulkContainerAssignmentSheet(viewModel: vm)
                }
            }
            .fullScreenCover(isPresented: $showScanner) {
                BarcodeScannerView { barcode in
                    showScanner = false
                    showAddSheet = false
                    scannedBarcode = barcode
                    Task { await addByBarcode(barcode, vm: viewModel!) }
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: .barcodeScanned)) { note in
                if let barcode = note.userInfo?["barcode"] as? String,
                   let vm = viewModel {
                    Task { await addByBarcode(barcode, vm: vm) }
                }
            }
        }
        .task {
            if viewModel == nil {
                let vm = CollectionViewModel(apiClient: apiClient)
                viewModel = vm
                await vm.loadMovies()
            }
        }
        .onAppear {
            if let viewModel {
                Task { await viewModel.loadMovies() }
            }
        }
    }

    // MARK: - Content

    @ViewBuilder
    private func content(vm: CollectionViewModel) -> some View {
        if vm.isLoading && vm.movies.isEmpty {
            loadingView
        } else {
            ZStack(alignment: .top) {
                ScrollView {
                    statsBar(vm: vm)
                    bulkActionBar(vm: vm)

                    if vm.filteredMovies.isEmpty {
                        emptyState(vm: vm)
                            .frame(maxWidth: .infinity)
                            .padding(.top, 48)
                            .padding(.horizontal, 24)
                    } else {
                        LazyVGrid(columns: columns, spacing: 10) {
                            ForEach(vm.filteredMovies) { movie in
                                if vm.isSelectionMode {
                                    Button {
                                        vm.toggleSelection(for: movie)
                                    } label: {
                                        selectableMovieCard(movie: movie, isSelected: vm.selectedMovieIDs.contains(movie.id))
                                    }
                                    .buttonStyle(.plain)
                                } else {
                                    NavigationLink {
                                        detailDestination(for: movie)
                                    } label: {
                                        MovieCardView(
                                            movie: movie,
                                            apiClient: apiClient,
                                            digitalBadgeTypes: vm.digitalBadgeTypes(for: movie),
                                            groupMultipleEditionsEnabled: vm.isGroupingEditions
                                        )
                                    }
                                    .buttonStyle(.plain)
                                }
                            }
                        }
                        .padding(.horizontal, 12)
                        .padding(.bottom, showFloatingBulkActions ? 118 : 24)
                    }
                }
                .coordinateSpace(name: "collectionScroll")
                .refreshable { await vm.loadMovies() }
                .onPreferenceChange(BulkActionBarOffsetPreferenceKey.self) { minY in
                    withAnimation(.spring(response: 0.28, dampingFraction: 0.86)) {
                        showFloatingBulkActions = vm.isSelectionMode && minY < -18
                    }
                }
                .onChange(of: vm.isSelectionMode) { _, isSelectionMode in
                    if !isSelectionMode {
                        showFloatingBulkActions = false
                    }
                }

                if showFloatingBulkActions && vm.isSelectionMode {
                    selectionActionPanel(vm: vm, isFloating: true)
                        .padding(.horizontal, 12)
                        .padding(.top, 8)
                        .transition(.move(edge: .top).combined(with: .opacity))
                        .zIndex(10)
                }
            }
        }
    }

    private var loadingView: some View {
        VStack(spacing: 16) {
            ProgressView()
                .tint(.white)
                .scaleEffect(1.3)
            Text(languageManager.text("collection.loading"))
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.5))
        }
    }

    private func emptyState(vm: CollectionViewModel) -> some View {
        VStack(spacing: 20) {
            Image(systemName: vm.searchText.isEmpty ? "opticaldisc" : "magnifyingglass")
                .font(.system(size: 56))
                .foregroundStyle(.white.opacity(0.2))
            Text(emptyStateTitle(vm: vm))
                .font(.title3.weight(.medium))
                .foregroundStyle(.white.opacity(0.5))
            if vm.movies.isEmpty && vm.searchText.isEmpty && vm.selectedFormat == nil && vm.selectedGroupID == nil && !vm.showContainersOnly {
                Button {
                    showAddSheet = true
                } label: {
                    Label(languageManager.text("collection.addFirstDisc"), systemImage: "plus.circle.fill")
                }
                .buttonStyle(.borderedProminent)
                .tint(Color(red: 0.45, green: 0.2, blue: 0.95))
            }
        }
    }

    private func emptyStateTitle(vm: CollectionViewModel) -> String {
        if !vm.searchText.isEmpty {
            return languageManager.text("collection.noResults", vm.searchText)
        }
        if vm.selectedFormat != nil || vm.selectedGroupID != nil || vm.showContainersOnly {
            return languageManager.text("collection.noFilterMatches")
        }
        return languageManager.text("collection.empty")
    }

    @ViewBuilder
    private func detailDestination(for movie: Movie) -> some View {
        if movie.isCollection == true {
            ContainerDetailView(target: .collection(id: movie.collectionCardId ?? movie.collectionId ?? movie.id, fallback: movie))
        } else if movie.isSuperGroup == true {
            ContainerDetailView(target: .editionGroup(id: movie.parentGroupId ?? movie.superGroupId ?? movie.editionGroupId ?? movie.id, fallback: movie))
        } else if movie.isGroup == true {
            ContainerDetailView(target: .editionGroup(id: movie.editionGroupId ?? movie.parentGroupId ?? movie.id, fallback: movie))
        } else {
            MovieDetailView(movie: movie)
        }
    }

    private func statsBar(vm: CollectionViewModel) -> some View {
        HStack(spacing: 16) {
            StatChip(value: vm.stats.totalMovies, label: languageManager.text("collection.statsMovies"), color: .white)
            StatChip(value: vm.stats.total4K, label: "4K", color: Color(red: 0.6, green: 0.3, blue: 1.0))
            StatChip(value: vm.stats.totalBluray, label: "BD", color: Color(red: 0.3, green: 0.5, blue: 1.0))
            StatChip(value: vm.stats.totalDVD, label: "DVD", color: .gray)
            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
    }

    private func bulkActionBar(vm: CollectionViewModel) -> some View {
        VStack(spacing: 10) {
            if let status = vm.statusMessage {
                Text(status)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.green)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 12)
            }

            if let error = vm.errorMessage {
                Text(error)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 12)
            }

            if vm.isSelectionMode {
                selectionActionPanel(vm: vm, isFloating: false)
                    .background(
                        GeometryReader { proxy in
                            Color.clear.preference(
                                key: BulkActionBarOffsetPreferenceKey.self,
                                value: proxy.frame(in: .named("collectionScroll")).minY
                            )
                        }
                    )
                .padding(.horizontal, 12)
                .padding(.bottom, 8)
            }
        }
    }

    private func selectionActionPanel(vm: CollectionViewModel, isFloating: Bool) -> some View {
        VStack(spacing: 10) {
            HStack(spacing: 10) {
                Text(languageManager.text("bulk.count", vm.selectedMovieIDs.count))
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)

                Spacer(minLength: 0)

                Button(languageManager.text("bulk.selectAll")) {
                    vm.selectAllFilteredMovies()
                }
                .font(.caption.weight(.bold))
                .disabled(vm.filteredMovies.isEmpty)

                Button(languageManager.text("bulk.deselect")) {
                    vm.clearSelection()
                }
                .font(.caption.weight(.bold))
                .disabled(vm.selectedMovieIDs.isEmpty)
            }

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    BulkActionButton(title: languageManager.text("bulk.assignGroup"), icon: "folder.badge.plus") {
                        showGroupAssignment = true
                    }
                    BulkActionButton(title: languageManager.text("bulk.assignContainer"), icon: "shippingbox.fill") {
                        showContainerAssignment = true
                    }
                    BulkActionButton(title: languageManager.text("bulk.watchlist"), icon: "bookmark.fill") {
                        Task { await vm.addSelectedToWatchlist() }
                    }
                    BulkActionButton(title: languageManager.text("bulk.refresh"), icon: "arrow.clockwise") {
                        Task { await vm.refreshSelectedMetadata() }
                    }
                }
                .padding(.horizontal, 1)
            }
            .disabled(vm.selectedMovieIDs.isEmpty || vm.isBulkWorking)
            .opacity(vm.selectedMovieIDs.isEmpty || vm.isBulkWorking ? 0.45 : 1)

            if vm.isBulkWorking {
                ProgressView(languageManager.text("bulk.assigning"))
                    .font(.caption)
                    .tint(.white)
                    .foregroundStyle(.white.opacity(0.7))
            }
        }
        .padding(12)
        .modifier(SelectionActionPanelBackground(isFloating: isFloating))
        .shadow(color: .black.opacity(isFloating ? 0.32 : 0), radius: 18, x: 0, y: 10)
    }

    private func selectableMovieCard(movie: Movie, isSelected: Bool) -> some View {
        ZStack(alignment: .topTrailing) {
            MovieCardView(
                movie: movie,
                apiClient: apiClient,
                digitalBadgeTypes: viewModel?.digitalBadgeTypes(for: movie) ?? [],
                groupMultipleEditionsEnabled: viewModel?.isGroupingEditions ?? false
            )
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .strokeBorder(isSelected ? Color(red: 0.91, green: 0.77, blue: 0.28) : .white.opacity(0.18), lineWidth: isSelected ? 3 : 1)
                )

            Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                .font(.title3)
                .foregroundStyle(isSelected ? Color(red: 0.91, green: 0.77, blue: 0.28) : .white.opacity(0.78))
                .padding(8)
                .background(.black.opacity(0.35), in: Circle())
                .padding(6)
        }
    }

    // MARK: - Add Sheet

    private var addSheet: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.06, green: 0.06, blue: 0.14).ignoresSafeArea()
                VStack(spacing: 20) {
                    // Scan barcode
                    Button {
                        showScanner = true
                    } label: {
                        AddOptionRow(
                            icon: "barcode.viewfinder",
                            color: Color(red: 0.45, green: 0.2, blue: 0.95),
                            title: languageManager.text("collection.scanBarcode"),
                            description: languageManager.text("collection.scanBarcodeDesc")
                        )
                    }
                    .buttonStyle(.plain)

                    // Manual barcode
                    VStack(alignment: .leading, spacing: 10) {
                        AddOptionRow(
                            icon: "keyboard",
                            color: Color(red: 0.2, green: 0.5, blue: 0.9),
                            title: languageManager.text("collection.enterBarcode"),
                            description: languageManager.text("collection.enterBarcodeDesc")
                        )
                        HStack(spacing: 10) {
                            TextField("e.g. 5051892234580", text: $manualBarcode)
                                .textFieldStyle(.plain)
                                .keyboardType(.numberPad)
                                .padding(12)
                                .background(.white.opacity(0.07))
                                .clipShape(RoundedRectangle(cornerRadius: 10))
                                .foregroundStyle(.white)
                                .tint(.white)

                            Button {
                                guard let vm = viewModel else { return }
                                Task { await addByBarcode(manualBarcode, vm: vm) }
                            } label: {
                                SwiftUI.Group {
                                    if isLookingUp {
                                        ProgressView().tint(.white)
                                    } else {
                                        Image(systemName: "arrow.right")
                                            .foregroundStyle(.white)
                                    }
                                }
                                .frame(width: 44, height: 44)
                                .background(Color(red: 0.2, green: 0.5, blue: 0.9))
                                .clipShape(RoundedRectangle(cornerRadius: 10))
                            }
                            .disabled(manualBarcode.count < 8 || isLookingUp)
                        }

                        if let err = lookupError {
                            Text(err)
                                .font(.caption)
                                .foregroundStyle(.red)
                        }
                    }

                    Spacer()
                }
                .padding(24)
            }
            .navigationTitle(languageManager.text("collection.addDisc"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(languageManager.text("bulk.cancelGroup")) { showAddSheet = false }
                        .foregroundStyle(.white)
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    // MARK: - Toolbar

    @ToolbarContentBuilder
    private var toolbarItems: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            collectionLogo
        }

        ToolbarItem(placement: .topBarTrailing) {
            if let vm = viewModel {
                Button {
                    vm.isSelectionMode.toggle()
                } label: {
                    Label(vm.isSelectionMode ? languageManager.text("bulk.cancelGroup") : languageManager.text("collection.selectMode"), systemImage: vm.isSelectionMode ? "xmark" : "checkmark.circle")
                        .labelStyle(.titleAndIcon)
                        .foregroundStyle(.white)
                }
            }
        }

        ToolbarItem(placement: .topBarTrailing) {
            Button {
                showFilters = true
            } label: {
                Image(systemName: "line.3.horizontal.decrease.circle")
                    .foregroundStyle(.white)
            }
        }
        ToolbarItem(placement: .topBarTrailing) {
            Button {
                showAddSheet = true
            } label: {
                Image(systemName: "plus")
                    .foregroundStyle(.white)
            }
            .disabled(viewModel?.isSelectionMode == true)
        }
    }

    private var collectionLogo: some View {
        HStack(spacing: 7) {
            Image("DiscVaultFrontendIcon")
                .resizable()
                .interpolation(.high)
                .frame(width: 28, height: 28)
                .clipShape(RoundedRectangle(cornerRadius: 7))

            Text("DiscVault")
                .font(.subheadline.weight(.bold))
                .foregroundStyle(.white)

            Text("4K Collection")
                .font(.caption2.weight(.bold))
                .foregroundStyle(Color(red: 0.91, green: 0.77, blue: 0.28))
                .lineLimit(1)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(Color(red: 0.91, green: 0.77, blue: 0.28).opacity(0.12), in: Capsule())
                .overlay(
                    Capsule()
                        .stroke(Color(red: 0.91, green: 0.77, blue: 0.28).opacity(0.45), lineWidth: 1)
                )
        }
        .fixedSize(horizontal: true, vertical: false)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("DiscVault 4K Collection")
    }

    // MARK: - Actions

    private func addByBarcode(_ barcode: String, vm: CollectionViewModel) async {
        guard !barcode.isEmpty else { return }
        isLookingUp = true
        lookupError = nil
        do {
            _ = try await vm.addMovieByBarcode(barcode)
            manualBarcode = ""
            showAddSheet = false
        } catch {
            lookupError = error.localizedDescription
        }
        isLookingUp = false
    }
}

// MARK: - Search

private struct CollectionSearchModifier: ViewModifier {
    @Environment(AppLanguageManager.self) private var languageManager

    let isEnabled: Bool
    let searchText: Binding<String>

    @ViewBuilder
    func body(content: Content) -> some View {
        if isEnabled {
            content.searchable(text: searchText, prompt: languageManager.text("collection.searchPlaceholder"))
        } else {
            content
        }
    }
}

private struct BulkActionBarOffsetPreferenceKey: PreferenceKey {
    static let defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

private struct SelectionActionPanelBackground: ViewModifier {
    let isFloating: Bool

    func body(content: Content) -> some View {
        if isFloating {
            if #available(iOS 26.0, *) {
                content
                    .glassEffect(.regular.tint(.white.opacity(0.08)).interactive(), in: .rect(cornerRadius: 18))
                    .overlay(
                        RoundedRectangle(cornerRadius: 18)
                            .stroke(.white.opacity(0.18), lineWidth: 1)
                    )
            } else {
                content
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 18))
                    .overlay(
                        RoundedRectangle(cornerRadius: 18)
                            .stroke(.white.opacity(0.16), lineWidth: 1)
                    )
            }
        } else {
            content
                .background(.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 10))
        }
    }
}

private struct BulkActionButton: View {
    let title: String
    let icon: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Label(title, systemImage: icon)
                .font(.caption.weight(.bold))
                .foregroundStyle(.black)
                .lineLimit(1)
                .padding(.horizontal, 12)
                .frame(height: 36)
                .background(Color(red: 0.91, green: 0.77, blue: 0.28))
                .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
    }
}

private struct BulkGroupAssignmentSheet: View {
    let viewModel: CollectionViewModel
    @Environment(AppLanguageManager.self) private var languageManager
    @Environment(\.dismiss) private var dismiss
    @State private var selectedGroupIDs: Set<Int> = []

    var body: some View {
        NavigationStack {
            List {
                if viewModel.groups.isEmpty {
                    Text(languageManager.text("collection.noGroups"))
                        .foregroundStyle(.secondary)
                } else {
                    Section(languageManager.text("bulk.selectGroups")) {
                        ForEach(viewModel.groups) { group in
                            Button {
                                toggle(group.id)
                            } label: {
                                HStack {
                                    Text(group.name)
                                        .foregroundStyle(.primary)
                                    Spacer()
                                    if selectedGroupIDs.contains(group.id) {
                                        Image(systemName: "checkmark")
                                            .foregroundStyle(.blue)
                                    }
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle(languageManager.text("bulk.assignGroup"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(languageManager.text("bulk.cancelGroup")) { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(languageManager.text("bulk.applyGroup")) {
                        let groupIDs = selectedGroupIDs
                        Task {
                            await viewModel.assignSelectedMoviesToGroups(groupIDs)
                            dismiss()
                        }
                    }
                    .disabled(selectedGroupIDs.isEmpty || viewModel.isBulkWorking)
                }
            }
        }
        .preferredColorScheme(.dark)
        .task {
            await viewModel.refreshGroups()
        }
    }

    private func toggle(_ id: Int) {
        if selectedGroupIDs.contains(id) {
            selectedGroupIDs.remove(id)
        } else {
            selectedGroupIDs.insert(id)
        }
    }
}

private struct BulkContainerAssignmentSheet: View {
    let viewModel: CollectionViewModel
    @Environment(AppLanguageManager.self) private var languageManager
    @Environment(\.dismiss) private var dismiss
    @State private var newContainerTitle = ""
    @State private var newContainerKind: BulkContainerKind = .boxset

    var body: some View {
        NavigationStack {
            List {
                Section("Nieuwe set maken") {
                    Picker("Type", selection: $newContainerKind) {
                        Text(label(for: .vault)).tag(BulkContainerKind.vault)
                        Text(label(for: .boxset)).tag(BulkContainerKind.boxset)
                        Text(label(for: .collection)).tag(BulkContainerKind.collection)
                    }
                    .pickerStyle(.segmented)

                    TextField("Naam", text: $newContainerTitle)
                        .textInputAutocapitalization(.words)

                    Button {
                        let title = newContainerTitle
                        let kind = newContainerKind
                        Task {
                            await viewModel.createContainerAndAssignSelectedMovies(title: title, kind: kind)
                            if viewModel.errorMessage == nil {
                                dismiss()
                            }
                        }
                    } label: {
                        Label("Maak aan en voeg toe", systemImage: "plus.circle.fill")
                    }
                    .disabled(newContainerTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || viewModel.isBulkWorking)
                }

                if viewModel.containerTargets.isEmpty {
                    Text(languageManager.text("bulk.noContainers"))
                        .foregroundStyle(.secondary)
                } else {
                    Section(languageManager.text("bulk.selectContainer")) {
                        ForEach(viewModel.containerTargets) { target in
                            Button {
                                Task {
                                    await viewModel.assignSelectedMovies(to: target)
                                    dismiss()
                                }
                            } label: {
                                HStack(spacing: 12) {
                                    Image(systemName: icon(for: target.kind))
                                        .foregroundStyle(color(for: target.kind))
                                        .frame(width: 24)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(target.title)
                                            .foregroundStyle(.primary)
                                        Text("\(label(for: target.kind)) · \(memberCountText(target.memberCount))")
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    Image(systemName: "chevron.right")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            .disabled(viewModel.isBulkWorking)
                        }
                    }
                }
            }
            .navigationTitle(languageManager.text("bulk.assignContainer"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(languageManager.text("bulk.cancelGroup")) { dismiss() }
                }
            }
        }
        .preferredColorScheme(.dark)
        .task {
            await viewModel.refreshContainers()
        }
    }

    private func label(for kind: BulkContainerKind) -> String {
        switch kind {
        case .vault: return languageManager.text("settings.groupTypeVault")
        case .boxset: return languageManager.text("settings.groupTypeBoxSet")
        case .collection: return languageManager.text("settings.groupTypeCollection")
        }
    }

    private func memberCountText(_ count: Int) -> String {
        count == 1 ? "1 film" : "\(count) films"
    }

    private func icon(for kind: BulkContainerKind) -> String {
        switch kind {
        case .vault: return "tray.full.fill"
        case .boxset: return "shippingbox.fill"
        case .collection: return "folder.fill"
        }
    }

    private func color(for kind: BulkContainerKind) -> Color {
        switch kind {
        case .vault: return Color(red: 0.91, green: 0.77, blue: 0.28)
        case .boxset: return .blue
        case .collection: return .green
        }
    }
}

// MARK: - Container Detail

private enum ContainerDetailTarget: Hashable {
    case collection(id: Int, fallback: Movie)
    case editionGroup(id: Int, fallback: Movie?)

    var id: Int {
        switch self {
        case .collection(let id, _), .editionGroup(let id, _):
            return id
        }
    }

    var fallback: Movie? {
        switch self {
        case .collection(_, let fallback):
            return fallback
        case .editionGroup(_, let fallback):
            return fallback
        }
    }

    var fallbackTypeLabel: String {
        switch self {
        case .collection:
            return "Collection"
        case .editionGroup(_, let fallback):
            return fallback?.containerBadgeLabel ?? "Vault / Box Set"
        }
    }
}

private enum ContainerDetailTab: Hashable {
    case content
    case images
    case videos
}

private struct ContainerDetailView: View {
    let target: ContainerDetailTarget

    @Environment(APIClient.self) private var apiClient
    @Environment(AppLanguageManager.self) private var languageManager
    @State private var collectionDetail: DiscCollectionDetail?
    @State private var editionGroupDetail: EditionGroupDetail?
    @State private var childGroupDetails: [Int: EditionGroupDetail] = [:]
    @State private var errorMessage: String?
    @State private var isLoading = true
    @State private var isRefreshingMembers = false
    @State private var isDebugModeEnabled = false
    @State private var showEditSheet = false
    @State private var selectedTab: ContainerDetailTab = .content
    @State private var playingVideoKey: String?

    private let columns = [
        GridItem(.flexible(), spacing: 10),
        GridItem(.flexible(), spacing: 10),
        GridItem(.flexible(), spacing: 10)
    ]

    var body: some View {
        ZStack {
            Color(red: 0.06, green: 0.06, blue: 0.14).ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    if isDebugModeEnabled {
                        Text("\(typeLabel) #\(target.id)")
                            .font(.caption.weight(.bold))
                            .foregroundStyle(.yellow)
                            .padding(.horizontal, 16)
                    }

                    containerSummaryCard
                        .padding(.horizontal, 16)

                    containerTabBar
                        .padding(.horizontal, 16)

                    if isLoading {
                        ProgressView()
                            .tint(.white)
                            .frame(maxWidth: .infinity)
                            .padding(.top, 32)
                    } else if let errorMessage {
                        Text(errorMessage)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.red)
                            .padding(.horizontal, 16)
                    } else {
                        tabContent
                    }
                }
                .padding(.bottom, 32)
            }
        }
        .navigationTitle(displayTitle)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { await refreshContainerAndMembers() }
                } label: {
                    if isRefreshingMembers {
                        ProgressView()
                            .tint(.white)
                    } else {
                        Image(systemName: "arrow.clockwise")
                    }
                }
                .foregroundStyle(.white)
                .disabled(isLoading || isRefreshingMembers || (collectionDetail == nil && editionGroupDetail == nil))
            }

            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    showEditSheet = true
                } label: {
                    Image(systemName: "square.and.pencil")
                }
                .foregroundStyle(.white)
                .disabled(isLoading || (collectionDetail == nil && editionGroupDetail == nil))
            }
        }
        .sheet(isPresented: $showEditSheet) {
            if let collectionDetail {
                ContainerEditSheet(
                    mode: .collection(collectionDetail),
                    onSave: { title, description, _, _, _ in
                        _ = try await apiClient.updateDiscCollection(id: collectionDetail.id, title: title, description: description)
                        await reloadDetails()
                    }
                )
            } else if let editionGroupDetail {
                ContainerEditSheet(
                    mode: .editionGroup(editionGroupDetail),
                    onSave: { title, description, badgeLabel, parentGroupId, collectionId in
                        _ = try await apiClient.updateEditionGroup(
                            id: editionGroupDetail.id,
                            title: title,
                            description: description,
                            badgeLabel: badgeLabel,
                            parentGroupId: parentGroupId,
                            collectionId: collectionId
                        )
                        await reloadDetails()
                    }
                )
            }
        }
        .task { await loadDetails() }
    }

    private var containerTabBar: some View {
        HStack(spacing: 8) {
            containerTabButton(.content, title: languageManager.text("collection.tabContent"))
            containerTabButton(.images, title: languageManager.text("modal.tabImages"))
            containerTabButton(.videos, title: languageManager.text("modal.tabVideos"))
        }
        .padding(4)
        .background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
    }

    private func containerTabButton(_ tab: ContainerDetailTab, title: String) -> some View {
        Button {
            selectedTab = tab
        } label: {
            Text(title)
                .font(.caption.weight(.bold))
                .foregroundStyle(selectedTab == tab ? .black : .white.opacity(0.65))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .background(selectedTab == tab ? Color.yellow : Color.clear, in: RoundedRectangle(cornerRadius: 9))
        }
        .buttonStyle(.plain)
    }

    private var hero: some View {
        ZStack(alignment: .bottomLeading) {
            if let url = apiClient.posterURL(for: posterPath) {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable()
                            .aspectRatio(contentMode: .fill)
                    default:
                        placeholderHero
                    }
                }
            } else {
                placeholderHero
            }

            LinearGradient(colors: [.clear, Color(red: 0.06, green: 0.06, blue: 0.14)], startPoint: .top, endPoint: .bottom)

            VStack(alignment: .leading, spacing: 8) {
                Text(typeLabel)
                    .font(.caption.weight(.bold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 9)
                    .padding(.vertical, 5)
                    .background(typeColor.opacity(0.95), in: Capsule())

                Text(displayTitle)
                    .font(.title2.weight(.bold))
                    .foregroundStyle(.white)
                    .lineLimit(3)

                if let year = displayYear {
                    Text(year)
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(.white.opacity(0.7))
                }
            }
            .padding(16)
        }
        .frame(height: 320)
        .clipped()
    }

    private var placeholderHero: some View {
        LinearGradient(
            colors: [Color(red: 0.14, green: 0.14, blue: 0.25), Color(red: 0.06, green: 0.06, blue: 0.14)],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }

    private var containerSummaryCard: some View {
        ZStack(alignment: .bottomLeading) {
            summaryBackdrop

            LinearGradient(
                colors: [.clear, .black.opacity(0.78)],
                startPoint: .top,
                endPoint: .bottom
            )

            HStack(alignment: .bottom, spacing: 14) {
                summaryPoster

                VStack(alignment: .leading, spacing: 7) {
                    Text(typeLabel)
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(typeColor.opacity(0.95), in: Capsule())

                    Text(displayTitle)
                        .font(.title3.weight(.bold))
                        .foregroundStyle(.white)
                        .lineLimit(3)

                    HStack(spacing: 8) {
                        if let yearRange {
                            Text(yearRange)
                        }
                        Label("\(memberMovies.count)", systemImage: "film.stack")
                    }
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.72))

                    if let description = containerDescription {
                        Text(description)
                            .font(.caption)
                            .foregroundStyle(.white.opacity(0.72))
                            .lineLimit(4)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(14)
        }
        .frame(minHeight: 230)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(.white.opacity(0.08), lineWidth: 1)
        )
    }

    private var placeholderPoster: some View {
        ZStack {
            Color.white.opacity(0.05)
            Image(systemName: "rectangle.stack.fill")
                .font(.system(size: 24))
                .foregroundStyle(.white.opacity(0.25))
        }
    }

    private var summaryBackdrop: some View {
        SwiftUI.Group {
            if let url = apiClient.posterURL(for: activeBackdrop ?? firstMemberBackdrop ?? posterPath) {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().aspectRatio(contentMode: .fill)
                    default:
                        placeholderHero
                    }
                }
            } else {
                placeholderHero
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .clipped()
    }

    private var summaryPoster: some View {
        ZStack {
            if let url = apiClient.posterURL(for: posterPath) {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().aspectRatio(contentMode: .fill)
                    default:
                        placeholderPoster
                    }
                }
            } else {
                placeholderPoster
            }
        }
        .frame(width: 88, height: 132)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .shadow(color: .black.opacity(0.45), radius: 8, y: 4)
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(.white.opacity(0.14), lineWidth: 1)
        )
    }

    @ViewBuilder
    private var tabContent: some View {
        switch selectedTab {
        case .content:
            contentSections
        case .images:
            imagesSection
        case .videos:
            videosSection
        }
    }

    @ViewBuilder
    private var contentSections: some View {
        if collectionDetail != nil {
            movieGridSection(title: languageManager.text("collection.myMovies"), movies: memberMovies)
        } else if let editionGroupDetail {
            if !editionGroupDetail.childGroups.isEmpty {
                childGroupSection(title: languageManager.text("collection.containersOnly"), groups: editionGroupDetail.childGroups)
            }
            movieGridSection(title: languageManager.text("collection.myMovies"), movies: memberMovies)
        } else if let fallback = target.fallback {
            movieGridSection(title: languageManager.text("collection.myMovies"), movies: fallback.boxSets + fallback.vaults + fallback.subGroups + fallback.looseMovies + fallback.editions)
        }
    }

    @ViewBuilder
    private var imagesSection: some View {
        let groups = backdropGroups
        if groups.isEmpty {
            emptyMediaState(icon: "photo.on.rectangle.angled", text: languageManager.text("modal.noMedia"))
        } else {
            VStack(alignment: .leading, spacing: 22) {
                ForEach(groups) { group in
                    VStack(alignment: .leading, spacing: 10) {
                        sectionTitle(group.movie.displayTitle)
                        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                            ForEach(group.urls, id: \.self) { url in
                                backdropButton(url: url)
                            }
                        }
                        .padding(.horizontal, 12)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var videosSection: some View {
        let groups = videoGroups
        if groups.isEmpty {
            emptyMediaState(icon: "play.rectangle.on.rectangle", text: languageManager.text("modal.noVideosAuto"))
        } else {
            VStack(alignment: .leading, spacing: 22) {
                ForEach(groups) { group in
                    VStack(alignment: .leading, spacing: 10) {
                        sectionTitle(group.movie.displayTitle)
                        ForEach(group.videos) { entry in
                            containerVideoCard(entry)
                                .padding(.horizontal, 12)
                        }
                    }
                }
            }
        }
    }

    private func emptyMediaState(icon: String, text: String) -> some View {
        VStack(spacing: 8) {
            Image(systemName: icon)
                .font(.system(size: 36))
                .foregroundStyle(.white.opacity(0.25))
            Text(text)
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.5))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 24)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 40)
    }

    private func containerVideoCard(_ entry: ContainerVideoEntry) -> some View {
        guard let key = entry.video.youtubeKey else {
            return AnyView(EmptyView())
        }
        let label = (entry.video.label?.isEmpty == false ? entry.video.label : entry.video.type) ?? "Trailer"

        return AnyView(
            VStack(alignment: .leading, spacing: 6) {
                Text(label)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.white.opacity(0.85))
                    .lineLimit(2)
                YouTubeThumbnailView(
                    youtubeKey: key,
                    isPlaying: playingVideoKey == key,
                    onPlay: { playingVideoKey = key }
                )
                .aspectRatio(16/9, contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        )
    }

    private func backdropButton(url: String) -> some View {
        let isActive = url == activeBackdrop
        return Button {
            guard !isActive else { return }
            Task { await setContainerBackdrop(url) }
        } label: {
            ZStack(alignment: .topTrailing) {
                AsyncImage(url: apiClient.posterURL(for: url)) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().aspectRatio(contentMode: .fill)
                    default:
                        Color.white.opacity(0.05)
                    }
                }
                .aspectRatio(16/9, contentMode: .fill)
                .clipped()
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .overlay(
                    RoundedRectangle(cornerRadius: 10)
                        .stroke(isActive ? Color.yellow : Color.white.opacity(0.08), lineWidth: isActive ? 2 : 1)
                )

                if isActive {
                    Text(languageManager.text("edition.egContainerBackdrop"))
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(.black)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 3)
                        .background(.yellow)
                        .clipShape(RoundedRectangle(cornerRadius: 4))
                        .padding(6)
                }
            }
        }
        .buttonStyle(.plain)
    }

    private func childGroupSection(title: String, groups: [EditionGroupChild]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionTitle(title)
            VStack(spacing: 8) {
                ForEach(groups) { group in
                    NavigationLink {
                        ContainerDetailView(target: .editionGroup(id: group.id, fallback: nil))
                    } label: {
                        HStack(spacing: 12) {
                            Image(systemName: group.parentGroupId == nil ? "shippingbox.fill" : "tray.full.fill")
                                .foregroundStyle(group.parentGroupId == nil ? Color.blue : Color.purple)
                                .frame(width: 24)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(group.title)
                                    .font(.subheadline.weight(.semibold))
                                    .foregroundStyle(.white)
                                if let badge = group.badgeLabel, !badge.isEmpty {
                                    Text(badge)
                                        .font(.caption)
                                        .foregroundStyle(.white.opacity(0.55))
                                }
                            }
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.caption.weight(.bold))
                                .foregroundStyle(.white.opacity(0.35))
                        }
                        .padding(12)
                        .background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 12)
        }
    }

    private func movieGridSection(title: String, movies: [Movie]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            if !movies.isEmpty {
                sectionTitle(title)
                LazyVGrid(columns: columns, spacing: 10) {
                    ForEach(movies) { movie in
                        NavigationLink {
                            if movie.isContainerCard {
                                ContainerDetailView(target: .editionGroup(id: movie.editionGroupId ?? movie.parentGroupId ?? movie.id, fallback: movie))
                            } else {
                                MovieDetailView(movie: movie)
                            }
                        } label: {
                            MovieCardView(movie: movie, apiClient: apiClient, digitalBadgeTypes: [], groupMultipleEditionsEnabled: true)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 12)
            }
        }
    }

    private func sectionTitle(_ title: String) -> some View {
        Text(title)
            .font(.headline.weight(.bold))
            .foregroundStyle(.white)
            .padding(.horizontal, 12)
    }

    private var displayTitle: String {
        collectionDetail?.title ?? editionGroupDetail?.title ?? target.fallback?.displayTitle ?? target.fallbackTypeLabel
    }

    private var displayYear: String? {
        collectionDetail?.year ?? editionGroupDetail?.year ?? target.fallback?.displayYear
    }

    private var posterPath: String? {
        collectionDetail?.posterFile ?? collectionDetail?.poster ?? editionGroupDetail?.posterFile ?? editionGroupDetail?.poster ?? target.fallback?.posterPath(groupMultipleEditionsEnabled: true)
    }

    private var containerDescription: String? {
        let value = collectionDetail?.description ?? editionGroupDetail?.description
        let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return trimmed.isEmpty ? nil : trimmed
    }

    private var typeLabel: String {
        if collectionDetail != nil {
            return "Collection"
        }
        if let detail = editionGroupDetail {
            let fallbackLabel = target.fallback?.containerBadgeLabel ?? target.fallbackTypeLabel
            if detail.groupType?.normalizedContainerTypeContainsBoxSet == true ||
                detail.badgeLabel?.normalizedContainerTypeContainsBoxSet == true ||
                fallbackLabel.lowercased().contains("box") ||
                !detail.childGroups.isEmpty ||
                !detail.looseMovies.isEmpty {
                return "Box Set"
            }
            if detail.parentGroupId != nil {
                return "Vault"
            }
        }
        return target.fallback?.containerBadgeLabel ?? target.fallbackTypeLabel
    }

    private var typeColor: Color {
        switch typeLabel {
        case "Collection": return Color(red: 0.1, green: 0.52, blue: 0.34)
        case "Box Set": return Color(red: 0.18, green: 0.38, blue: 0.82)
        default: return Color(red: 0.55, green: 0.34, blue: 0.86)
        }
    }

    private var activeBackdrop: String? {
        collectionDetail?.backdrop ?? editionGroupDetail?.backdrop ?? target.fallback?.backdrop
    }

    private var firstMemberBackdrop: String? {
        memberMovies.compactMap { movie in
            availableBackdrops(for: movie).first
        }.first
    }

    private var yearRange: String? {
        let years = memberMovies.compactMap { movie in
            yearValue(from: movie.year)
        }.sorted()
        guard let first = years.first else {
            return displayYear
        }
        guard let last = years.last, last != first else {
            return String(first)
        }
        return "\(first) - \(last)"
    }

    private var memberMovies: [Movie] {
        let fallbackMovies = target.fallback.map { flattenedMovies(from: $0) } ?? []
        if let collectionDetail {
            let groupMovies = collectionDetail.editionGroups.flatMap { group in
                moviesFromGroupDetail(id: group.id)
            }
            return chronologicalMovies(uniqueMovies(collectionDetail.looseMovies + collectionDetail.egMovies + groupMovies + fallbackMovies))
        }
        if let editionGroupDetail {
            let childMovies = editionGroupDetail.childGroups.flatMap { child in
                if let detail = childGroupDetails[child.id] {
                    return detail.members + detail.looseMovies
                }
                return fallbackMovies.filter { $0.editionGroupId == child.id }
            }
            return chronologicalMovies(uniqueMovies(editionGroupDetail.members + editionGroupDetail.looseMovies + childMovies + fallbackMovies))
        }
        return chronologicalMovies(uniqueMovies(fallbackMovies))
    }

    private var backdropGroups: [ContainerBackdropGroup] {
        memberMovies.compactMap { movie in
            let urls = availableBackdrops(for: movie)
            guard !urls.isEmpty else { return nil }
            return ContainerBackdropGroup(movie: movie, urls: urls)
        }
    }

    private var videoGroups: [ContainerVideoGroup] {
        memberMovies.compactMap { movie in
            let entries = movie.allVideos.map { ContainerVideoEntry(movieID: movie.id, video: $0) }
            guard !entries.isEmpty else { return nil }
            return ContainerVideoGroup(movie: movie, videos: entries)
        }
    }

    private func availableBackdrops(for movie: Movie) -> [String] {
        var result = movie.parsedBackdrops
        if result.isEmpty, let primary = movie.backdrop, !primary.isEmpty {
            result = [primary]
        } else if let primary = movie.backdrop, !primary.isEmpty, !result.contains(primary) {
            result.insert(primary, at: 0)
        }
        return result
    }

    private func uniqueMovies(_ movies: [Movie]) -> [Movie] {
        var seen = Set<Int>()
        var result: [Movie] = []
        for movie in movies where !seen.contains(movie.id) {
            seen.insert(movie.id)
            result.append(movie)
        }
        return result
    }

    private func chronologicalMovies(_ movies: [Movie]) -> [Movie] {
        movies.sorted { lhs, rhs in
            let lhsYear = yearValue(from: lhs.year) ?? Int.max
            let rhsYear = yearValue(from: rhs.year) ?? Int.max
            if lhsYear != rhsYear {
                return lhsYear < rhsYear
            }
            return lhs.displayTitle.localizedCaseInsensitiveCompare(rhs.displayTitle) == .orderedAscending
        }
    }

    private func flattenedMovies(from movie: Movie) -> [Movie] {
        var result = movie.editions + movie.looseMovies
        for child in movie.boxSets + movie.vaults + movie.subGroups {
            let nested = flattenedMovies(from: child)
            result.append(contentsOf: nested.isEmpty ? [child] : nested)
        }
        if result.isEmpty && !movie.isContainerCard {
            result.append(movie)
        }
        return result
    }

    private func moviesFromGroupDetail(id: Int) -> [Movie] {
        guard let detail = childGroupDetails[id] else { return [] }
        let childMovies = detail.childGroups.flatMap { child in
            moviesFromGroupDetail(id: child.id)
        }
        return detail.members + detail.looseMovies + childMovies
    }

    private func yearValue(from value: String?) -> Int? {
        guard let value else { return nil }
        let digits = value.prefix { $0.isNumber }
        if digits.count >= 4 {
            return Int(String(digits.prefix(4)))
        }
        return nil
    }

    private func setContainerBackdrop(_ url: String) async {
        do {
            switch target {
            case .collection(let id, _):
                _ = try await apiClient.updateDiscCollectionBackdrop(id: id, backdrop: url)
            case .editionGroup(let id, _):
                _ = try await apiClient.updateEditionGroupBackdrop(id: id, backdrop: url)
            }
            await reloadDetails()
        } catch where !isCancellation(error) {
            errorMessage = error.localizedDescription
        } catch {
            // Ignore view task cancellation.
        }
    }

    private func refreshContainerAndMembers() async {
        guard !isRefreshingMembers else { return }
        isRefreshingMembers = true
        errorMessage = nil

        do {
            await reloadDetails()
            let ids = uniqueMovies(memberMovies).map(\.id)
            for id in ids {
                _ = try await apiClient.refreshMovieMetadata(id: id)
            }
            await reloadDetails()
        } catch where !isCancellation(error) {
            errorMessage = error.localizedDescription
        } catch {
            // Ignore view task cancellation.
        }

        isRefreshingMembers = false
    }

    private func loadDetails() async {
        guard isLoading else { return }
        isDebugModeEnabled = (try? await apiClient.getDebugSetting())?.debugEnabled ?? false
        do {
            switch target {
            case .collection(let id, _):
                let detail = try await apiClient.getDiscCollection(id: id)
                collectionDetail = detail
                childGroupDetails = await loadChildGroupDetails(for: detail.editionGroups)
            case .editionGroup(let id, _):
                let detail = try await apiClient.getEditionGroup(id: id)
                editionGroupDetail = detail
                childGroupDetails = await loadChildGroupDetails(for: detail.childGroups)
            }
        } catch where !isCancellation(error) {
            errorMessage = error.localizedDescription
        } catch {
            // Ignore view task cancellation.
        }
        isLoading = false
    }

    private func reloadDetails() async {
        isLoading = true
        errorMessage = nil
        collectionDetail = nil
        editionGroupDetail = nil
        childGroupDetails = [:]
        await loadDetails()
    }

    private func loadChildGroupDetails(for groups: [EditionGroupChild]) async -> [Int: EditionGroupDetail] {
        var details: [Int: EditionGroupDetail] = [:]
        for group in groups {
            if let detail = try? await apiClient.getEditionGroup(id: group.id) {
                details[group.id] = detail
                let nested = await loadChildGroupDetails(for: detail.childGroups)
                details.merge(nested) { current, _ in current }
            }
        }
        return details
    }

    private func isCancellation(_ error: Error) -> Bool {
        if error is CancellationError {
            return true
        }
        if case APIError.networkError(let underlying) = error {
            return isCancellation(underlying)
        }
        let nsError = error as NSError
        return nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled
    }

    private struct ContainerBackdropGroup: Identifiable {
        let movie: Movie
        let urls: [String]

        var id: Int { movie.id }
    }

    private struct ContainerVideoGroup: Identifiable {
        let movie: Movie
        let videos: [ContainerVideoEntry]

        var id: Int { movie.id }
    }

    private struct ContainerVideoEntry: Identifiable {
        let movieID: Int
        let video: MovieVideo

        var id: String { "\(movieID)-\(video.id)" }
    }
}

private enum ContainerEditMode {
    case collection(DiscCollectionDetail)
    case editionGroup(EditionGroupDetail)

    var title: String {
        switch self {
        case .collection(let detail):
            return detail.title
        case .editionGroup(let detail):
            return detail.title
        }
    }

    var description: String {
        switch self {
        case .collection(let detail):
            return detail.description ?? ""
        case .editionGroup(let detail):
            return detail.description ?? ""
        }
    }

    var badgeLabel: String {
        switch self {
        case .collection:
            return ""
        case .editionGroup(let detail):
            return detail.badgeLabel ?? ""
        }
    }

    var parentGroupId: Int? {
        switch self {
        case .collection:
            return nil
        case .editionGroup(let detail):
            return detail.parentGroupId
        }
    }

    var collectionId: Int? {
        switch self {
        case .collection:
            return nil
        case .editionGroup(let detail):
            return detail.collectionId
        }
    }

    var currentEditionGroupId: Int? {
        switch self {
        case .collection:
            return nil
        case .editionGroup(let detail):
            return detail.id
        }
    }
}

private enum CollectionAssignmentItem: Identifiable {
    case movie(Movie)
    case editionGroup(EditionGroup, label: String)

    var id: String {
        switch self {
        case .movie(let movie):
            return "movie-\(movie.id)"
        case .editionGroup(let group, _):
            return "group-\(group.id)"
        }
    }

    var title: String {
        switch self {
        case .movie(let movie):
            return movie.displayTitle
        case .editionGroup(let group, _):
            return group.title
        }
    }

    var subtitle: String? {
        switch self {
        case .movie(let movie):
            if let year = movie.year, !year.isEmpty {
                return "Losse film · \(year)"
            }
            return "Losse film"
        case .editionGroup(_, let label):
            return label
        }
    }
}

private struct ContainerEditSheet: View {
    let mode: ContainerEditMode
    let onSave: (String, String?, String?, Int?, Int?) async throws -> Void

    @Environment(APIClient.self) private var apiClient
    @Environment(\.dismiss) private var dismiss
    @State private var title: String
    @State private var description: String
    @State private var badgeLabel: String
    @State private var parentGroupId: Int?
    @State private var collectionId: Int?
    @State private var newParentGroupTitle = ""
    @State private var newCollectionTitle = ""
    @State private var editionGroups: [EditionGroup] = []
    @State private var collections: [DiscCollection] = []
    @State private var movies: [Movie] = []
    @State private var collectionItemSearch = ""
    @State private var selectedMovieIDs: Set<Int> = []
    @State private var selectedEditionGroupIDs: Set<Int> = []
    @State private var isSaving = false
    @State private var errorMessage: String?

    init(
        mode: ContainerEditMode,
        onSave: @escaping (String, String?, String?, Int?, Int?) async throws -> Void
    ) {
        self.mode = mode
        self.onSave = onSave
        _title = State(initialValue: mode.title)
        _description = State(initialValue: mode.description)
        _badgeLabel = State(initialValue: mode.badgeLabel)
        _parentGroupId = State(initialValue: mode.parentGroupId)
        _collectionId = State(initialValue: mode.collectionId)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Algemeen") {
                    TextField(nameFieldLabel, text: $title)
                    TextField("Beschrijving", text: $description, axis: .vertical)
                        .lineLimit(3...6)
                }

                if case .editionGroup = mode {
                    Section("Groep") {
                        TextField("Badge label", text: $badgeLabel)

                        Picker("Box-set", selection: $parentGroupId) {
                            Text("Geen box-set").tag(Int?.none)
                            ForEach(editionGroups.filter { $0.id != mode.currentEditionGroupId }) { group in
                                Text(group.title).tag(Optional(group.id))
                            }
                        }
                        TextField("Nieuwe box-set naam", text: $newParentGroupTitle)
                            .textInputAutocapitalization(.words)
                            .onChange(of: newParentGroupTitle) { _, value in
                                if !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                                    parentGroupId = nil
                                }
                            }

                        Picker("Collectie", selection: $collectionId) {
                            Text("Geen collectie").tag(Int?.none)
                            ForEach(collections) { collection in
                                Text(collection.title).tag(Optional(collection.id))
                            }
                        }
                        TextField("Nieuwe collectie naam", text: $newCollectionTitle)
                            .textInputAutocapitalization(.words)
                            .onChange(of: newCollectionTitle) { _, value in
                                if !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                                    collectionId = nil
                                }
                            }
                    }
                }

                if case .collection = mode {
                    Section("Items toevoegen") {
                        TextField("Zoek films, box-sets of vaults", text: $collectionItemSearch)

                        if selectedCollectionItems.isEmpty && collectionItemSearchTrimmed.isEmpty {
                            Text("Zoek op titel om films, box-sets of vaults toe te voegen.")
                                .foregroundStyle(.secondary)
                        }

                        if !selectedCollectionItems.isEmpty {
                            ForEach(selectedCollectionItems) { item in
                                collectionItemRow(item: item, isSelected: true) {
                                    toggle(item)
                                }
                            }
                        }

                        if !collectionItemSearchTrimmed.isEmpty {
                            if collectionSearchResults.isEmpty {
                                Text("Geen resultaten.")
                                    .foregroundStyle(.secondary)
                            } else {
                                ForEach(collectionSearchResults) { item in
                                    collectionItemRow(item: item, isSelected: itemIsSelected(item)) {
                                        toggle(item)
                                    }
                                }
                            }
                        }
                    }
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Bewerk groep")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Annuleer") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task { await save() }
                    } label: {
                        if isSaving {
                            ProgressView()
                        } else {
                            Text("Bewaar")
                        }
                    }
                    .disabled(title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isSaving)
                }
            }
        }
        .preferredColorScheme(.dark)
        .task { await loadTargets() }
    }

    private func loadTargets() async {
        switch mode {
        case .collection:
            async let loadedMovies = apiClient.getMovies(groupEditions: false)
            async let loadedGroups = apiClient.getEditionGroups()
            movies = (try? await loadedMovies) ?? []
            editionGroups = (try? await loadedGroups) ?? []
        case .editionGroup:
            async let loadedGroups = apiClient.getEditionGroups()
            async let loadedCollections = apiClient.getDiscCollections()
            editionGroups = (try? await loadedGroups) ?? []
            collections = (try? await loadedCollections) ?? []
        }
    }

    private func save() async {
        isSaving = true
        errorMessage = nil
        do {
            let createdParentGroupId = try await createParentGroupIfNeeded()
            let createdCollectionId = try await createCollectionIfNeeded()
            try await onSave(
                title.trimmingCharacters(in: .whitespacesAndNewlines),
                nilIfBlank(description),
                nilIfBlank(badgeLabel),
                createdParentGroupId ?? parentGroupId,
                createdCollectionId ?? collectionId
            )
            try await assignSelectedItemsToCollectionIfNeeded()
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
        }
        isSaving = false
    }

    private var assignableMovies: [Movie] {
        let existing = existingCollectionMovieIDs
        let query = collectionItemSearchTrimmed.lowercased()
        guard !query.isEmpty else { return [] }
        return movies
            .filter { !existing.contains($0.id) }
            .filter { query.isEmpty || $0.displayTitle.lowercased().contains(query) }
            .sorted { lhs, rhs in
                lhs.displayTitle.localizedCaseInsensitiveCompare(rhs.displayTitle) == .orderedAscending
            }
    }

    private var assignableEditionGroups: [EditionGroup] {
        let existing = existingCollectionEditionGroupIDs
        let query = collectionItemSearchTrimmed.lowercased()
        guard !query.isEmpty else { return [] }
        return editionGroups
            .filter { !existing.contains($0.id) }
            .filter { query.isEmpty || $0.title.lowercased().contains(query) }
            .sorted { lhs, rhs in
                lhs.title.localizedCaseInsensitiveCompare(rhs.title) == .orderedAscending
            }
    }

    private var collectionItemSearchTrimmed: String {
        collectionItemSearch.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var collectionSearchResults: [CollectionAssignmentItem] {
        let movieItems = assignableMovies.map { CollectionAssignmentItem.movie($0) }
        let groupItems = assignableEditionGroups.map { CollectionAssignmentItem.editionGroup($0, label: groupLabel(for: $0)) }
        return (groupItems + movieItems).sorted {
            $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending
        }
    }

    private var selectedCollectionItems: [CollectionAssignmentItem] {
        let movieItems = movies
            .filter { selectedMovieIDs.contains($0.id) }
            .map { CollectionAssignmentItem.movie($0) }
        let groupItems = editionGroups
            .filter { selectedEditionGroupIDs.contains($0.id) }
            .map { CollectionAssignmentItem.editionGroup($0, label: groupLabel(for: $0)) }
        return (groupItems + movieItems).sorted {
            $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending
        }
    }

    private var existingCollectionMovieIDs: Set<Int> {
        switch mode {
        case .collection(let detail):
            return Set((detail.looseMovies + detail.egMovies).map(\.id))
        case .editionGroup:
            return []
        }
    }

    private var existingCollectionEditionGroupIDs: Set<Int> {
        switch mode {
        case .collection(let detail):
            return Set(detail.editionGroups.map(\.id))
        case .editionGroup:
            return []
        }
    }

    private func assignSelectedItemsToCollectionIfNeeded() async throws {
        guard case .collection(let detail) = mode else { return }
        for movieID in selectedMovieIDs {
            try await apiClient.assignMovieToCollection(id: movieID, collectionId: detail.id)
        }
        for groupID in selectedEditionGroupIDs {
            try await apiClient.assignEditionGroupToCollection(id: groupID, collectionId: detail.id)
        }
    }

    private func collectionItemRow(item: CollectionAssignmentItem, isSelected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isSelected ? Color(red: 0.91, green: 0.77, blue: 0.28) : .secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.title)
                        .foregroundStyle(.primary)
                    if let subtitle = item.subtitle, !subtitle.isEmpty {
                        Text(subtitle)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer()
            }
        }
        .buttonStyle(.plain)
    }

    private func itemIsSelected(_ item: CollectionAssignmentItem) -> Bool {
        switch item {
        case .movie(let movie):
            return selectedMovieIDs.contains(movie.id)
        case .editionGroup(let group, _):
            return selectedEditionGroupIDs.contains(group.id)
        }
    }

    private func toggle(_ item: CollectionAssignmentItem) {
        switch item {
        case .movie(let movie):
            if selectedMovieIDs.contains(movie.id) {
                selectedMovieIDs.remove(movie.id)
            } else {
                selectedMovieIDs.insert(movie.id)
            }
        case .editionGroup(let group, _):
            if selectedEditionGroupIDs.contains(group.id) {
                selectedEditionGroupIDs.remove(group.id)
            } else {
                selectedEditionGroupIDs.insert(group.id)
            }
        }
    }

    private func groupLabel(for group: EditionGroup) -> String {
        let type = group.containerKind == .boxset ? "Box-set" : "Vault"
        let memberCount = group.displayMemberCount
        guard memberCount > 0 else { return type }
        return "\(type) · \(memberCount) film\(memberCount == 1 ? "" : "s")"
    }

    private var nameFieldLabel: String {
        switch mode {
        case .collection:
            return "Collectienaam"
        case .editionGroup(let detail):
            if detail.groupType?.normalizedContainerTypeContainsBoxSet == true || detail.badgeLabel?.normalizedContainerTypeContainsBoxSet == true || !detail.childGroups.isEmpty || !detail.looseMovies.isEmpty {
                return "Box-setnaam"
            }
            if detail.parentGroupId != nil {
                return "Vaultnaam"
            }
            return "Vaultnaam"
        }
    }

    private func createParentGroupIfNeeded() async throws -> Int? {
        let title = newParentGroupTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return nil }
        let group = try await apiClient.createEditionGroup(title: title, kind: .boxset)
        return group.id
    }

    private func createCollectionIfNeeded() async throws -> Int? {
        let title = newCollectionTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return nil }
        let collection = try await apiClient.createDiscCollection(title: title)
        return collection.id
    }

    private func nilIfBlank(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

// MARK: - Filter Sheet

private struct FilterSheet: View {
    @Bindable var viewModel: CollectionViewModel
    @Environment(AppLanguageManager.self) private var languageManager
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section(languageManager.text("collection.format")) {
                    formatPicker
                }
                .listRowBackground(Color.white.opacity(0.06))

                Section(languageManager.text("collection.show")) {
                    Toggle(languageManager.text("collection.wantedOnly"), isOn: $viewModel.showWantedOnly)
                    Toggle(languageManager.text("collection.containersOnly"), isOn: $viewModel.showContainersOnly)
                }
                .listRowBackground(Color.white.opacity(0.06))

                if !viewModel.groups.isEmpty {
                    Section(languageManager.text("collection.groups")) {
                        Picker(languageManager.text("collection.groups"), selection: $viewModel.selectedGroupID) {
                            Text(languageManager.text("collection.allGroups")).tag(Int?.none)
                            ForEach(viewModel.groups) { group in
                                Text(group.name).tag(Optional(group.id))
                            }
                        }
                        .pickerStyle(.menu)
                    }
                    .listRowBackground(Color.white.opacity(0.06))
                }

                Section(languageManager.text("collection.sortBy")) {
                    sortPicker
                }
                .listRowBackground(Color.white.opacity(0.06))
            }
            .scrollContentBackground(.hidden)
            .background(Color(red: 0.06, green: 0.06, blue: 0.14))
            .navigationTitle(languageManager.text("collection.filterSort"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button(languageManager.text("collection.done")) { dismiss() }
                }
            }
        }
        .preferredColorScheme(.dark)
        .task {
            await viewModel.refreshGroups()
        }
    }

    private var formatPicker: some View {
        Picker(languageManager.text("collection.format"), selection: $viewModel.selectedFormat) {
            Text(languageManager.text("collection.allFormats")).tag(String?.none)
            Text("4K UHD").tag(Optional("4K UHD"))
            Text("Blu-Ray").tag(Optional("Blu-ray"))
            Text("DVD").tag(Optional("DVD"))
        }
        .pickerStyle(.menu)
    }

    private var sortPicker: some View {
        ForEach(SortOrder.allCases, id: \.self) { order in
            Button {
                viewModel.sortOrder = order
            } label: {
                HStack {
                    Text(languageManager.text(order.translationKey))
                        .foregroundStyle(.primary)
                    Spacer()
                    if viewModel.sortOrder == order {
                        Image(systemName: "checkmark")
                            .foregroundStyle(.blue)
                    }
                }
            }
        }
    }
}

// MARK: - Helpers

private struct StatChip: View {
    let value: Int
    let label: String
    let color: Color

    var body: some View {
        HStack(spacing: 4) {
            Text("\(value)")
                .font(.caption.weight(.bold))
                .foregroundStyle(color)
            Text(label)
                .font(.caption)
                .foregroundStyle(.white.opacity(0.45))
        }
    }
}

private struct AddOptionRow: View {
    let icon: String
    let color: Color
    let title: String
    let description: String

    var body: some View {
        HStack(spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 10)
                    .fill(color.opacity(0.15))
                    .frame(width: 44, height: 44)
                Image(systemName: icon)
                    .font(.system(size: 20))
                    .foregroundStyle(color)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                Text(description)
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.45))
            }
            Spacer()
            Image(systemName: "chevron.right")
                .font(.caption)
                .foregroundStyle(.white.opacity(0.3))
        }
        .padding(14)
        .background(.white.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }
}
