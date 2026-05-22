import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var appState: AppStateManager
    @Environment(APIClient.self) private var apiClient
    @Environment(AppLanguageManager.self) private var languageManager

    @State private var selectedSection: SettingsSection = .general
    @State private var stats: DatabaseStats?
    @State private var currentUser: User?
    @State private var preferences = SettingsPreferences()
    @State private var hasLoadedPreferences = false
    @State private var serverVersion: String?
    @State private var editionGroups: [EditionGroup] = []
    @State private var collections: [DiscCollection] = []
    @State private var groupFilter: GroupManagementFilter = .all
    @State private var editingTitles: [String: String] = [:]
    @State private var isLoading = true
    @State private var isSavingPreferences = false
    @State private var statusMessage: SettingsStatusMessage?
    @State private var showServerEdit = false
    @State private var showDeleteConfirm = false
    @State private var itemPendingDelete: GroupManagementItem?

    private var canManageGroups: Bool { currentUser?.role == "admin" }

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.06, green: 0.06, blue: 0.14).ignoresSafeArea()

                ScrollView {
                    VStack(spacing: 18) {
                        header
                        sectionPicker
                        selectedSectionView
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 12)
                    .padding(.bottom, 28)
                }
            }
            .navigationTitle("")
            .toolbarColorScheme(.dark, for: .navigationBar)
            .task { await loadSettings() }
            .sheet(isPresented: $showServerEdit) {
                NavigationStack { ServerSetupView(isEditMode: true) }
            }
            .confirmationDialog("Delete group", isPresented: $showDeleteConfirm, presenting: itemPendingDelete) { item in
                Button("Delete \(item.title)", role: .destructive) {
                    Task { await deleteItem(item) }
                }
                Button("Cancel", role: .cancel) {}
            } message: { item in
                Text("Movies remain in your collection, but this \(item.typeTitle.lowercased()) grouping will be removed.")
            }
        }
    }

    private var header: some View {
        SettingsCard {
            HStack(spacing: 14) {
                Image(systemName: "gearshape.fill")
                    .font(.system(size: 26, weight: .semibold))
                    .foregroundStyle(SettingsTheme.accent)
                    .frame(width: 48, height: 48)
                    .background(.white.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: 8))

                VStack(alignment: .leading, spacing: 4) {
                    Text(languageManager.text("settings.headerTitle"))
                        .font(.title3.bold())
                        .foregroundStyle(.white)
                    Text(languageManager.text("settings.headerSubtitle"))
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.52))
                }

                Spacer(minLength: 0)
            }
        }
    }

    private var sectionPicker: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(SettingsSection.allCases) { section in
                    Button {
                        selectedSection = section
                    } label: {
                        Label(languageManager.text(section.translationKey), systemImage: section.icon)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(selectedSection == section ? .black : .white.opacity(0.68))
                            .padding(.horizontal, 12)
                            .frame(height: 36)
                            .background(selectedSection == section ? SettingsTheme.accent : .white.opacity(0.07))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 1)
        }
    }

    @ViewBuilder
    private var selectedSectionView: some View {
        switch selectedSection {
        case .general:
            generalSection
        case .collectionManagement:
            collectionManagementSection
        case .about:
            aboutSection
        }
    }

    private var generalSection: some View {
        VStack(spacing: 14) {
            SettingsCard(title: "Database Information", icon: "externaldrive.fill") {
                if isLoading && stats == nil {
                    HStack(spacing: 10) {
                        ProgressView().tint(.white)
                        Text("Loading database stats")
                            .font(.subheadline)
                            .foregroundStyle(.white.opacity(0.56))
                    }
                } else if let stats {
                    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                        StatTile(title: "Movies", value: "\(stats.movieCount)", icon: "film.fill")
                        StatTile(title: "Posters", value: "\(stats.posterCount)", icon: "photo.stack.fill")
                        StatTile(title: "Database", value: ByteCountFormatter.string(fromByteCount: Int64(stats.dbSize), countStyle: .file), icon: "cylinder.fill")
                        StatTile(title: "Poster Size", value: ByteCountFormatter.string(fromByteCount: Int64(stats.posterSize), countStyle: .file), icon: "archivebox.fill")
                    }
                } else {
                    SettingsEmptyState(icon: "exclamationmark.triangle", title: "No stats available", subtitle: "The server did not return database information.")
                }
            }

            SettingsCard(title: "Server", icon: "server.rack") {
                VStack(spacing: 12) {
                    SettingsInfoRow(icon: "link", title: "Server URL", subtitle: appState.serverURL.isEmpty ? "Not configured" : appState.serverURL)

                    Button {
                        showServerEdit = true
                    } label: {
                        Label("Edit Server", systemImage: "square.and.pencil")
                            .font(.headline)
                            .foregroundStyle(.black)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 13)
                            .background(SettingsTheme.accent)
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain)
                }
            }

            SettingsCard(title: "Offline Queue", icon: "wifi.slash") {
                VStack(spacing: 12) {
                    SettingsInfoRow(
                        icon: "tray",
                        title: "Native queue",
                        subtitle: "The PWA keeps browser-only offline mutations. The native app currently uses live API calls and URL cache only."
                    )

                    Button(role: .destructive) {
                        URLCache.shared.removeAllCachedResponses()
                        statusMessage = SettingsStatusMessage(text: "Image and request cache cleared.", isError: false)
                    } label: {
                        Label("Clear Native Cache", systemImage: "trash")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .tint(.red)
                }
            }

            SettingsCard(title: "Language", icon: "globe") {
                VStack(alignment: .leading, spacing: 10) {
                    Picker("Language", selection: $preferences.language) {
                        ForEach(SettingsLanguage.allCases) { language in
                            Text(language.title).tag(language)
                        }
                    }
                    .pickerStyle(.menu)
                    .tint(SettingsTheme.accent)
                    .onChange(of: preferences.language) { _, language in
                        guard hasLoadedPreferences else { return }
                        languageManager.setLanguage(language.preferenceValue)
                        Task { await savePreference(key: "lang", value: language.preferenceValue) }
                    }

                    Text("This matches the PWA language preference and syncs through your DiscVault account.")
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.48))
                }
            }

            statusView
        }
    }

    private var aboutSection: some View {
        VStack(spacing: 14) {
            SettingsCard(title: languageManager.text("settings.aboutTitle"), icon: "info.circle.fill") {
                VStack(spacing: 12) {
                    SettingsInfoRow(
                        icon: "app.fill",
                        title: languageManager.text("settings.aboutAppVersion"),
                        subtitle: "DiscVault \(appVersionText)"
                    )
                    SettingsInfoRow(
                        icon: "server.rack",
                        title: languageManager.text("settings.aboutBackendVersion"),
                        subtitle: backendVersionText
                    )
                    SettingsInfoRow(
                        icon: "link",
                        title: languageManager.text("settings.aboutServerURL"),
                        subtitle: appState.serverURL.isEmpty ? languageManager.text("settings.notConfigured") : appState.serverURL
                    )

                    Text(languageManager.text("settings.aboutDescription"))
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.48))
                        .frame(maxWidth: .infinity, alignment: .leading)

                    HStack(spacing: 12) {
                        Image("TMDBLogo")
                            .resizable()
                            .scaledToFit()
                            .frame(width: 44, height: 44)
                            .accessibilityLabel("TMDB")
                        Text(languageManager.text("settings.aboutTmdbAttribution"))
                            .font(.caption)
                            .foregroundStyle(.white.opacity(0.58))
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                    }
                    .padding(12)
                    .background(.white.opacity(0.05))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
            }
        }
    }

    private var appVersionText: String {
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "-"
        let build = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? ""
        guard !build.isEmpty, build != version else { return version }
        return "\(version) (\(build))"
    }

    private var backendVersionText: String {
        guard let serverVersion else {
            return isLoading ? languageManager.text("settings.versionLoading") : languageManager.text("settings.versionUnknown")
        }
        return serverVersion.isEmpty ? languageManager.text("settings.versionUnknown") : "DiscVault \(serverVersion)"
    }

    private var collectionManagementSection: some View {
        VStack(spacing: 14) {
            SettingsCard(title: "Edition Management", icon: "folder.fill") {
                VStack(spacing: 14) {
                    SettingsToggleRow(
                        title: "Collectors mode",
                        subtitle: "Enable vaults, box sets and collection grouping features.",
                        isOn: $preferences.collectorsMode
                    )
                    .onChange(of: preferences.collectorsMode) { _, enabled in
                        guard hasLoadedPreferences else { return }
                        if !enabled {
                            preferences.groupEditions = false
                        }
                        Task { await saveCollectionPreferences() }
                    }

                    SettingsToggleRow(
                        title: "Group multiple editions",
                        subtitle: "Show multiple physical editions of the same movie as one title.",
                        isOn: $preferences.groupEditions
                    )
                    .disabled(!preferences.collectorsMode)
                    .opacity(preferences.collectorsMode ? 1 : 0.45)
                    .onChange(of: preferences.groupEditions) { _, _ in
                        guard hasLoadedPreferences else { return }
                        Task { await saveCollectionPreferences() }
                    }

                    Divider().overlay(.white.opacity(0.12))

                    SettingsToggleRow(
                        title: "Digital badges",
                        subtitle: "Show Plex or Jellyfin availability on movie tiles.",
                        isOn: $preferences.digitalBadges
                    )
                    .onChange(of: preferences.digitalBadges) { _, _ in
                        guard hasLoadedPreferences else { return }
                        Task { await saveCollectionPreferences() }
                    }

                    if preferences.digitalBadges {
                        Picker("Badge source", selection: $preferences.digitalBadgeFilter) {
                            ForEach(DigitalBadgeFilter.allCases) { filter in
                                Text(filter.title).tag(filter)
                            }
                        }
                        .pickerStyle(.segmented)
                        .onChange(of: preferences.digitalBadgeFilter) { _, _ in
                            guard hasLoadedPreferences else { return }
                            Task { await saveCollectionPreferences() }
                        }
                    }
                }
            }

            SettingsCard(title: "Vaults, Box Sets & Collections", icon: "shippingbox.fill") {
                VStack(spacing: 12) {
                    SettingsInfoRow(
                        icon: canManageGroups ? "pencil" : "lock.fill",
                        title: canManageGroups ? "Manage groups" : "Read-only",
                        subtitle: canManageGroups ? "Rename or remove groups from the native app." : "Only administrators can rename or delete groups."
                    )

                    Picker("Filter", selection: $groupFilter) {
                        ForEach(GroupManagementFilter.allCases) { filter in
                            Text(filter.title).tag(filter)
                        }
                    }
                    .pickerStyle(.segmented)

                    if filteredItems.isEmpty {
                        SettingsEmptyState(icon: "tray", title: "No groups found", subtitle: "Vaults, box sets and collections created in the PWA will appear here.")
                    } else {
                        VStack(spacing: 10) {
                            ForEach(filteredItems) { item in
                                GroupManagementRow(
                                    item: item,
                                    title: Binding(
                                        get: { editingTitles[item.key] ?? item.title },
                                        set: { editingTitles[item.key] = $0 }
                                    ),
                                    canManage: canManageGroups,
                                    onSave: { Task { await renameItem(item) } },
                                    onDelete: {
                                        itemPendingDelete = item
                                        showDeleteConfirm = true
                                    }
                                )
                            }
                        }
                    }
                }
            }

            statusView
        }
    }

    private var filteredItems: [GroupManagementItem] {
        let editionItems = editionGroups.compactMap { group -> GroupManagementItem? in
            let type = managementType(for: group)
            guard groupFilter == .all || groupFilter.matches(type) else { return nil }
            return GroupManagementItem(id: group.id, source: .editionGroup, title: group.title, type: type, memberCount: group.displayMemberCount)
        }

        let collectionItems = collections.compactMap { collection -> GroupManagementItem? in
            guard groupFilter == .all || groupFilter == .collection else { return nil }
            return GroupManagementItem(id: collection.id, source: .collection, title: collection.title, type: .collection, memberCount: collection.displayMemberCount)
        }

        return (editionItems + collectionItems).sorted { $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending }
    }

    private func managementType(for group: EditionGroup) -> GroupManagementType {
        if group.containerKind == .boxset {
            return .boxset
        }
        return .vault
    }

    private var statusView: some View {
        SwiftUI.Group {
            if let statusMessage {
                Text(statusMessage.text)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(statusMessage.isError ? .red : .green)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 4)
            }
        }
    }

    private func loadSettings() async {
        isLoading = true
        async let statsTask = loadDatabaseStats()
        async let healthTask = loadServerHealth()
        async let userTask = loadCurrentUser()
        async let preferencesTask = loadPreferences()
        async let groupsTask = loadGroups()
        _ = await (statsTask, healthTask, userTask, preferencesTask, groupsTask)
        isLoading = false
    }

    private func loadDatabaseStats() async {
        do {
            stats = try await apiClient.getDatabaseStats()
        } catch {
            stats = nil
        }
    }

    private func loadServerHealth() async {
        do {
            let health = try await apiClient.getServerHealth()
            serverVersion = health.version
        } catch {
            serverVersion = nil
        }
    }

    private func loadCurrentUser() async {
        do {
            currentUser = try await apiClient.getCurrentUser()
        } catch {
            currentUser = nil
        }
    }

    private func loadPreferences() async {
        do {
            let values = try await apiClient.getUserPreferences()
            preferences = SettingsPreferences(values: values)
            languageManager.setLanguage(preferences.language.preferenceValue)
            hasLoadedPreferences = true
        } catch {
            statusMessage = SettingsStatusMessage(text: error.localizedDescription, isError: true)
            hasLoadedPreferences = true
        }
    }

    private func loadGroups() async {
        do {
            async let editions = apiClient.getEditionGroups()
            async let cols = apiClient.getDiscCollections()
            let (loadedEditions, loadedCollections) = try await (editions, cols)
            editionGroups = loadedEditions
            collections = loadedCollections
            editingTitles = Dictionary(uniqueKeysWithValues: filteredItems.map { ($0.key, $0.title) })
        } catch {
            statusMessage = SettingsStatusMessage(text: "Could not load group management data: \(error.localizedDescription)", isError: true)
        }
    }

    private func savePreference(key: String, value: String) async {
        isSavingPreferences = true
        do {
            try await apiClient.updateUserPreferences([key: value])
            statusMessage = SettingsStatusMessage(text: "Settings saved.", isError: false)
        } catch {
            statusMessage = SettingsStatusMessage(text: error.localizedDescription, isError: true)
        }
        isSavingPreferences = false
    }

    private func saveCollectionPreferences() async {
        isSavingPreferences = true
        let values = preferences.collectionValues
        do {
            try await apiClient.updateUserPreferences(values)
            statusMessage = SettingsStatusMessage(text: "Collection settings saved.", isError: false)
        } catch {
            statusMessage = SettingsStatusMessage(text: error.localizedDescription, isError: true)
        }
        isSavingPreferences = false
    }

    private func renameItem(_ item: GroupManagementItem) async {
        let newTitle = (editingTitles[item.key] ?? item.title).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !newTitle.isEmpty, newTitle != item.title else { return }

        do {
            switch item.source {
            case .editionGroup:
                _ = try await apiClient.updateEditionGroupTitle(id: item.id, title: newTitle)
            case .collection:
                _ = try await apiClient.updateDiscCollectionTitle(id: item.id, title: newTitle)
            }
            statusMessage = SettingsStatusMessage(text: "Group renamed.", isError: false)
            await loadGroups()
        } catch {
            statusMessage = SettingsStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func deleteItem(_ item: GroupManagementItem) async {
        do {
            switch item.source {
            case .editionGroup:
                try await apiClient.deleteEditionGroup(id: item.id)
            case .collection:
                try await apiClient.deleteDiscCollection(id: item.id)
            }
            statusMessage = SettingsStatusMessage(text: "Group deleted.", isError: false)
            await loadGroups()
        } catch {
            statusMessage = SettingsStatusMessage(text: error.localizedDescription, isError: true)
        }
    }
}

private enum SettingsSection: String, CaseIterable, Identifiable {
    case general
    case collectionManagement
    case about

    var id: String { rawValue }

    var title: String {
        switch self {
        case .general: "General"
        case .collectionManagement: "Vaults + Sets"
        case .about: "About"
        }
    }

    var icon: String {
        switch self {
        case .general: "gearshape"
        case .collectionManagement: "folder"
        case .about: "info.circle"
        }
    }

    var translationKey: String {
        switch self {
        case .general: "settings.menuGeneral"
        case .collectionManagement: "settings.menuCollectionMgmt"
        case .about: "settings.menuAbout"
        }
    }
}

private struct SettingsPreferences {
    var language: SettingsLanguage = .english
    var collectorsMode = false
    var groupEditions = false
    var digitalBadges = false
    var digitalBadgeFilter: DigitalBadgeFilter = .all

    init() {}

    init(values: [String: String]) {
        language = SettingsLanguage(preferenceValue: values["lang"] ?? "en")
        collectorsMode = values["collectors_mode"] == "true"
        groupEditions = values["group_editions"] == "true"
        digitalBadges = values["digital_badges"] == "true"
        digitalBadgeFilter = DigitalBadgeFilter(rawValue: values["digital_badge_filter"] ?? "all") ?? .all
    }

    var collectionValues: [String: String] {
        [
            "collectors_mode": collectorsMode ? "true" : "false",
            "group_editions": groupEditions ? "true" : "false",
            "digital_badges": digitalBadges ? "true" : "false",
            "digital_badge_filter": digitalBadgeFilter.rawValue
        ]
    }
}

private enum SettingsLanguage: String, CaseIterable, Identifiable {
    case english = "en"
    case dutch = "nl"
    case french = "fr"
    case german = "de"
    case spanish = "es"
    case portuguese = "pt"
    case italian = "it"

    var id: String { rawValue }
    var preferenceValue: String { rawValue }

    init(preferenceValue: String) {
        self = SettingsLanguage(rawValue: preferenceValue) ?? .english
    }

    var title: String {
        switch self {
        case .english: "English"
        case .dutch: "Nederlands"
        case .french: "Francais"
        case .german: "Deutsch"
        case .spanish: "Espanol"
        case .portuguese: "Portugues"
        case .italian: "Italiano"
        }
    }
}

private enum DigitalBadgeFilter: String, CaseIterable, Identifiable {
    case all
    case plex
    case jellyfin

    var id: String { rawValue }

    var title: String {
        switch self {
        case .all: "All"
        case .plex: "Plex"
        case .jellyfin: "Jellyfin"
        }
    }
}

private enum GroupManagementFilter: String, CaseIterable, Identifiable {
    case all
    case vault
    case boxset
    case collection

    var id: String { rawValue }

    var title: String {
        switch self {
        case .all: "All"
        case .vault: "Vaults"
        case .boxset: "Box Sets"
        case .collection: "Collections"
        }
    }

    func matches(_ type: GroupManagementType) -> Bool {
        switch (self, type) {
        case (.vault, .vault), (.boxset, .boxset), (.collection, .collection): true
        default: self == .all
        }
    }
}

private enum GroupManagementSource {
    case editionGroup
    case collection
}

private enum GroupManagementType {
    case vault
    case boxset
    case collection
}

private struct GroupManagementItem: Identifiable {
    let id: Int
    let source: GroupManagementSource
    let title: String
    let type: GroupManagementType
    let memberCount: Int

    var key: String { "\(sourceKey)-\(id)" }
    var sourceKey: String { source == .editionGroup ? "eg" : "col" }

    var typeTitle: String {
        switch type {
        case .vault: "Vault"
        case .boxset: "Box Set"
        case .collection: "Collection"
        }
    }

    var typeColor: Color {
        switch type {
        case .vault: SettingsTheme.accent
        case .boxset: .blue
        case .collection: .green
        }
    }
}

private struct SettingsStatusMessage: Identifiable {
    let id = UUID()
    let text: String
    let isError: Bool
}

private enum SettingsTheme {
    static let accent = Color(red: 0.91, green: 0.77, blue: 0.28)
}

private struct SettingsCard<Content: View>: View {
    let title: String?
    let icon: String?
    @ViewBuilder let content: Content

    init(title: String? = nil, icon: String? = nil, @ViewBuilder content: () -> Content) {
        self.title = title
        self.icon = icon
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let title {
                HStack(spacing: 8) {
                    if let icon {
                        Image(systemName: icon)
                            .foregroundStyle(SettingsTheme.accent)
                    }
                    Text(title)
                        .font(.headline)
                        .foregroundStyle(.white)
                }
            }
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(.white.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.white.opacity(0.08)))
    }
}

private struct StatTile: View {
    let title: String
    let value: String
    let icon: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Image(systemName: icon)
                .foregroundStyle(SettingsTheme.accent)
            Text(value)
                .font(.headline.weight(.semibold))
                .foregroundStyle(.white)
                .lineLimit(1)
                .minimumScaleFactor(0.72)
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.white.opacity(0.48))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.white.opacity(0.05))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

private struct SettingsInfoRow: View {
    let icon: String
    let title: String
    let subtitle: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .foregroundStyle(SettingsTheme.accent)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.48))
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
    }
}

private struct SettingsToggleRow: View {
    let title: String
    let subtitle: String
    @Binding var isOn: Bool

    var body: some View {
        Toggle(isOn: $isOn) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.48))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .tint(SettingsTheme.accent)
    }
}

private struct SettingsEmptyState: View {
    let icon: String
    let title: String
    let subtitle: String

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: icon)
                .font(.system(size: 28))
                .foregroundStyle(.white.opacity(0.24))
            Text(title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.white.opacity(0.72))
            Text(subtitle)
                .font(.caption)
                .foregroundStyle(.white.opacity(0.46))
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 18)
    }
}

private struct GroupManagementRow: View {
    let item: GroupManagementItem
    @Binding var title: String
    let canManage: Bool
    let onSave: () -> Void
    let onDelete: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Text(item.typeTitle)
                    .font(.caption.weight(.bold))
                    .foregroundStyle(item.typeColor)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(item.typeColor.opacity(0.16))
                    .clipShape(RoundedRectangle(cornerRadius: 5))

                Text("\(item.memberCount) movie\(item.memberCount == 1 ? "" : "s")")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.46))

                Spacer(minLength: 0)
            }

            HStack(spacing: 8) {
                TextField("Title", text: $title)
                    .textFieldStyle(.plain)
                    .foregroundStyle(.white)
                    .tint(.white)
                    .disabled(!canManage)
                    .submitLabel(.done)
                    .onSubmit(onSave)
                    .padding(10)
                    .background(.white.opacity(0.06))
                    .clipShape(RoundedRectangle(cornerRadius: 8))

                if canManage {
                    Button(action: onSave) {
                        Image(systemName: "checkmark")
                            .frame(width: 34, height: 34)
                    }
                    .buttonStyle(.bordered)
                    .tint(SettingsTheme.accent)

                    Button(role: .destructive, action: onDelete) {
                        Image(systemName: "trash")
                            .frame(width: 34, height: 34)
                    }
                    .buttonStyle(.bordered)
                    .tint(.red)
                }
            }
        }
        .padding(12)
        .background(.white.opacity(0.05))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

#Preview {
    SettingsView()
        .environmentObject(AppStateManager())
        .environment(AppStateManager().apiClient)
        .environment(AppLanguageManager())
        .preferredColorScheme(.dark)
}
