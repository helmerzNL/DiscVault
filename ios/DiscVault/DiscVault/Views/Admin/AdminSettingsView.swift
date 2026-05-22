import SwiftUI

struct AdminSettingsView: View {
    @Environment(APIClient.self) private var apiClient
    @Environment(AppLanguageManager.self) private var languageManager

    @State private var selectedSection: AdminSection = .security
    @State private var authStatus: AuthStatus?
    @State private var users: [AdminUser] = []
    @State private var inviteCodes: [InviteCode] = []
    @State private var groups: [Group] = []
    @State private var logs: [AdminLogEntry] = []
    @State private var backups: [BackupSummary] = []
    @State private var digitalSources: [DigitalLibrarySource] = []
    @State private var metadataSources = MetadataSourceSettingsUpdate(
        omdbEnabled: false,
        tmdbEnabled: false,
        blurayScrapeEnabled: false,
        bluraydiscdeScrapeEnabled: false
    )
    @State private var metadataKeys: MetadataAPIKeySettings?
    @State private var omdbKeyInput = ""
    @State private var tmdbKeyInput = ""
    @State private var debugEnabled = false
    @State private var mcpEnabled = true
    @State private var newInviteUsername = ""
    @State private var createdInvite: InviteCodeCreateResponse?
    @State private var newGroupName = ""
    @State private var newDigitalType = "plex"
    @State private var newDigitalName = ""
    @State private var newDigitalURL = ""
    @State private var newDigitalToken = ""
    @State private var logLevelFilter = ""
    @State private var logCategoryFilter = ""
    @State private var statusMessage: AdminStatusMessage?
    @State private var pendingAuthEnabled: Bool?
    @State private var pendingRegistrationEnabled: Bool?
    @State private var isLoading = true
    @State private var isWorking = false
    @State private var pendingUserDelete: AdminUser?
    @State private var pendingGroupDelete: Group?
    @State private var pendingBackupDelete: BackupSummary?
    @State private var showDeleteUserConfirm = false
    @State private var showDeleteGroupConfirm = false
    @State private var showDeleteBackupConfirm = false
    @State private var showClearLogsConfirm = false

    var body: some View {
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
        .task { await loadAdmin() }
        .confirmationDialog("Delete user", isPresented: $showDeleteUserConfirm, presenting: pendingUserDelete) { user in
            Button("Delete \(user.username)", role: .destructive) {
                Task { await deleteUser(user) }
            }
            Button("Cancel", role: .cancel) {}
        } message: { user in
            Text("This deletes \(user.username)'s movies, passkeys and memberships.")
        }
        .confirmationDialog("Delete group", isPresented: $showDeleteGroupConfirm, presenting: pendingGroupDelete) { group in
            Button("Delete \(group.name)", role: .destructive) {
                Task { await deleteGroup(group) }
            }
            Button("Cancel", role: .cancel) {}
        } message: { group in
            Text("Movies remain in the database, but group links are removed for \(group.name).")
        }
        .confirmationDialog("Delete backup", isPresented: $showDeleteBackupConfirm, presenting: pendingBackupDelete) { backup in
            Button("Delete \(backup.name)", role: .destructive) {
                Task { await deleteBackup(backup) }
            }
            Button("Cancel", role: .cancel) {}
        }
        .confirmationDialog("Clear logs", isPresented: $showClearLogsConfirm) {
            Button("Clear logs", role: .destructive) {
                Task { await clearLogs() }
            }
            Button("Cancel", role: .cancel) {}
        }
    }

    private var header: some View {
        AdminCard {
            HStack(spacing: 14) {
                Image(systemName: "shield.lefthalf.filled")
                    .font(.system(size: 26, weight: .semibold))
                    .foregroundStyle(AdminTheme.accent)
                    .frame(width: 48, height: 48)
                    .background(.white.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: 8))

                VStack(alignment: .leading, spacing: 4) {
                    Text("Admin Console")
                        .font(.title3.bold())
                        .foregroundStyle(.white)
                    Text("Security, users, groups, backups and logs")
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
                ForEach(AdminSection.allCases) { section in
                    Button {
                        selectedSection = section
                        Task { await loadSection(section) }
                    } label: {
                        Label(languageManager.text(section.translationKey), systemImage: section.icon)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(selectedSection == section ? .black : .white.opacity(0.68))
                            .padding(.horizontal, 12)
                            .frame(height: 36)
                            .background(selectedSection == section ? AdminTheme.accent : .white.opacity(0.07))
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
        case .security: securitySection
        case .users: usersSection
        case .groups: groupsSection
        case .roles: rolesSection
        case .backup: backupSection
        case .logs: logsSection
        case .advanced: advancedSection
        }
    }

    private var securitySection: some View {
        VStack(spacing: 14) {
            AdminCard(title: languageManager.text("settings.adminAuthTitle"), icon: "lock.shield.fill") {
                VStack(spacing: 14) {
                    if let authStatus {
                        AdminInfoRow(icon: "person.2.fill", title: "Users", subtitle: "\(authStatus.userCount) users · \(authStatus.groupCount) groups")
                        AdminInfoRow(icon: "globe", title: "Relying party", subtitle: authStatus.rpID ?? "-")
                    }

                    AdminToggleRow(
                        title: languageManager.text("settings.authActive"),
                        subtitle: "Require passkey login before using the API.",
                        isOn: Binding(
                            get: { pendingAuthEnabled ?? authStatus?.authEnabled ?? false },
                            set: { newValue in
                                guard !isWorking else { return }
                                pendingAuthEnabled = newValue
                                if let authStatus {
                                    self.authStatus = authStatus.updated(authEnabled: newValue)
                                }
                                Task { await setAuthEnabled(newValue) }
                            }
                        )
                    )
                    .disabled(isWorking)

                    AdminToggleRow(
                        title: languageManager.text("settings.registrationEnabled"),
                        subtitle: "Allow new users to register when authentication is enabled.",
                        isOn: Binding(
                            get: { pendingRegistrationEnabled ?? authStatus?.registrationEnabled ?? true },
                            set: { newValue in
                                guard !isWorking else { return }
                                pendingRegistrationEnabled = newValue
                                if let authStatus {
                                    self.authStatus = authStatus.updated(registrationEnabled: newValue)
                                }
                                Task { await setRegistrationEnabled(newValue) }
                            }
                        )
                    )
                    .disabled(isWorking || !(pendingAuthEnabled ?? authStatus?.authEnabled ?? false))
                    .opacity((pendingAuthEnabled ?? authStatus?.authEnabled ?? false) ? 1 : 0.45)
                }
            }

            AdminCard(title: languageManager.text("settings.inviteTitle"), icon: "envelope.badge.fill") {
                VStack(spacing: 12) {
                    HStack(spacing: 10) {
                        AdminTextField(title: "Username", text: $newInviteUsername, systemImage: "person.badge.plus")
                        Button {
                            Task { await createInviteCode() }
                        } label: {
                            Image(systemName: "plus")
                                .foregroundStyle(.black)
                                .frame(width: 42, height: 42)
                                .background(AdminTheme.accent)
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                        .disabled(newInviteUsername.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isWorking)
                    }

                    if let createdInvite {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Invite for \(createdInvite.username)")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.white.opacity(0.6))
                            Text(createdInvite.code)
                                .font(.system(.title3, design: .monospaced).weight(.bold))
                                .foregroundStyle(AdminTheme.accent)
                            if let expiresAt = createdInvite.expiresAt {
                                Text("Expires \(adminLocalDateTime(expiresAt))")
                                    .font(.caption)
                                    .foregroundStyle(.white.opacity(0.48))
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                        .background(AdminTheme.accent.opacity(0.08))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }

                    if inviteCodes.isEmpty {
                        AdminEmptyState(icon: "envelope", title: "No invite codes", subtitle: "Create an invite code to let a new user register.")
                    } else {
                        VStack(spacing: 10) {
                            ForEach(inviteCodes) { invite in
                                InviteRow(invite: invite) {
                                    Task { await deleteInviteCode(invite) }
                                }
                            }
                        }
                    }
                }
            }

            statusView
        }
    }

    private var usersSection: some View {
        VStack(spacing: 14) {
            AdminCard(title: languageManager.text("settings.usersTitle"), icon: "person.3.fill") {
                if users.isEmpty {
                    AdminEmptyState(icon: "person.slash", title: "No users", subtitle: "Registered users will appear here.")
                } else {
                    VStack(spacing: 10) {
                        ForEach(users) { user in
                            AdminUserRow(
                                user: user,
                                onResetPasskey: { Task { await resetPasskey(user) } },
                                onToggleRole: { Task { await toggleRole(user) } },
                                onDelete: {
                                    pendingUserDelete = user
                                    showDeleteUserConfirm = true
                                }
                            )
                        }
                    }
                }
            }
            statusView
        }
    }

    private var groupsSection: some View {
        VStack(spacing: 14) {
            AdminCard(title: languageManager.text("settings.groupsTitle"), icon: "folder.badge.person.crop") {
                VStack(spacing: 12) {
                    HStack(spacing: 10) {
                        AdminTextField(title: "Group Name", text: $newGroupName, systemImage: "folder.badge.plus")
                        Button {
                            Task { await createGroup() }
                        } label: {
                            Image(systemName: "plus")
                                .foregroundStyle(.black)
                                .frame(width: 42, height: 42)
                                .background(AdminTheme.accent)
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                        .disabled(newGroupName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isWorking)
                    }

                    if groups.isEmpty {
                        AdminEmptyState(icon: "folder", title: "No groups", subtitle: "Create groups for shared collections and membership management.")
                    } else {
                        VStack(spacing: 10) {
                            ForEach(groups) { group in
                                AdminGroupRow(group: group) {
                                    pendingGroupDelete = group
                                    showDeleteGroupConfirm = true
                                }
                            }
                        }
                    }
                }
            }
            statusView
        }
    }

    private var rolesSection: some View {
        AdminCard(title: languageManager.text("settings.rolesTitle"), icon: "shield.checkered") {
            VStack(spacing: 12) {
                AdminInfoRow(
                    icon: "clock.badge.exclamationmark",
                    title: "Not yet implemented natively",
                    subtitle: "The backend exposes role endpoints, but the PWA marks this menu as coming soon. Native role editing should be added when the role UX is finalized."
                )
            }
        }
    }

    private var backupSection: some View {
        VStack(spacing: 14) {
            AdminCard(title: languageManager.text("settings.backup"), icon: "externaldrive.badge.timemachine") {
                VStack(spacing: 12) {
                    Button {
                        Task { await createBackup() }
                    } label: {
                        Label(isWorking ? "Creating Backup" : "Create Backup", systemImage: "externaldrive.badge.plus")
                            .font(.headline)
                            .foregroundStyle(.black)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                            .background(AdminTheme.accent)
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain)
                    .disabled(isWorking)

                    if backups.isEmpty {
                        AdminEmptyState(icon: "externaldrive", title: "No backups", subtitle: "Backups created on the server will appear here.")
                    } else {
                        VStack(spacing: 10) {
                            ForEach(backups) { backup in
                                BackupRow(backup: backup) {
                                    pendingBackupDelete = backup
                                    showDeleteBackupConfirm = true
                                }
                            }
                        }
                    }
                }
            }
            statusView
        }
    }

    private var logsSection: some View {
        VStack(spacing: 14) {
            AdminCard(title: languageManager.text("settings.menuLogs"), icon: "doc.text.magnifyingglass") {
                VStack(spacing: 12) {
                    HStack(spacing: 10) {
                        Picker("Level", selection: $logLevelFilter) {
                            Text("All").tag("")
                            Text("Error").tag("error")
                            Text("Warn").tag("warn")
                            Text("Success").tag("success")
                            Text("Info").tag("info")
                        }
                        .pickerStyle(.menu)
                        .tint(AdminTheme.accent)

                        Picker("Category", selection: $logCategoryFilter) {
                            Text("All").tag("")
                            Text("Import").tag("import")
                            Text("Refresh").tag("refresh")
                            Text("Lookup").tag("lookup")
                            Text("Add").tag("add")
                            Text("Delete").tag("delete")
                            Text("General").tag("general")
                            Text("Auth").tag("auth")
                            Text("Settings").tag("settings")
                        }
                        .pickerStyle(.menu)
                        .tint(AdminTheme.accent)

                        Button {
                            Task { await loadLogs() }
                        } label: {
                            Image(systemName: "arrow.clockwise")
                        }
                        .buttonStyle(.bordered)
                        .tint(AdminTheme.accent)
                    }

                    Button(role: .destructive) {
                        showClearLogsConfirm = true
                    } label: {
                        Label("Clear Logs", systemImage: "trash")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .tint(.red)

                    if logs.isEmpty {
                        AdminEmptyState(icon: "doc.text", title: "No logs", subtitle: "Server events will appear here.")
                    } else {
                        VStack(spacing: 10) {
                            ForEach(logs) { log in
                                LogRow(log: log)
                            }
                        }
                    }
                }
            }
            statusView
        }
    }

    private var advancedSection: some View {
        VStack(spacing: 14) {
            AdminCard(title: languageManager.text("settings.advanced"), icon: "gearshape.2.fill") {
                VStack(spacing: 14) {
                    AdminToggleRow(title: "Debug mode", subtitle: "Show technical IDs in detail views for troubleshooting.", isOn: $debugEnabled)
                        .onChange(of: debugEnabled) { _, value in
                            Task { await setDebugEnabled(value) }
                        }

                    AdminToggleRow(title: "MCP Server", subtitle: "Allow MCP API access for configured clients.", isOn: $mcpEnabled)
                        .onChange(of: mcpEnabled) { _, value in
                            Task { await setMCPEnabled(value) }
                        }
                }
            }

            digitalLibrariesCard
            metadataSourcesCard
            statusView
        }
    }

    private var digitalLibrariesCard: some View {
        AdminCard(title: "Digital Libraries", icon: "play.tv.fill") {
            VStack(spacing: 12) {
                if digitalSources.isEmpty {
                    AdminEmptyState(icon: "play.tv", title: "No digital libraries", subtitle: "Add Plex or Jellyfin to compare physical discs with digital movies.")
                } else {
                    VStack(spacing: 10) {
                        ForEach(digitalSources) { source in
                            DigitalSourceRow(
                                source: source,
                                onSync: { Task { await syncDigitalSource(source) } },
                                onDelete: { Task { await deleteDigitalSource(source) } }
                            )
                        }
                    }
                }

                Picker("Type", selection: $newDigitalType) {
                    Text("Plex").tag("plex")
                    Text("Jellyfin").tag("jellyfin")
                }
                .pickerStyle(.segmented)

                AdminTextField(title: "Name", text: $newDigitalName, systemImage: "textformat")
                AdminTextField(title: "Server URL", text: $newDigitalURL, systemImage: "link")
                AdminTextField(title: newDigitalType == "plex" ? "Plex token" : "Jellyfin token", text: $newDigitalToken, systemImage: "key.fill")

                Button {
                    Task { await createDigitalSource() }
                } label: {
                    Label("Add \(newDigitalType == "plex" ? "Plex" : "Jellyfin")", systemImage: "plus.circle.fill")
                        .font(.headline)
                        .foregroundStyle(.black)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 13)
                        .background(AdminTheme.accent)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .disabled(newDigitalName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || newDigitalURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isWorking)
            }
        }
    }

    private var metadataSourcesCard: some View {
        AdminCard(title: "Metadata Sources", icon: "sparkles.tv.fill") {
            VStack(spacing: 14) {
                AdminToggleRow(title: "OMDb", subtitle: metadataKeySubtitle(isSet: metadataKeys?.omdbKeySet), isOn: Binding(
                    get: { metadataSources.omdbEnabled },
                    set: { value in
                        metadataSources = metadataSources.updated(omdbEnabled: value)
                        Task { await saveMetadataSources() }
                    }
                ))
                .disabled(metadataKeys?.omdbKeySet == false)
                .opacity(metadataKeys?.omdbKeySet == false ? 0.45 : 1)

                MetadataKeyRow(title: "OMDb API key", keyText: $omdbKeyInput, isSet: metadataKeys?.omdbKeySet ?? false) {
                    Task { await saveMetadataKey(service: "omdb") }
                } onClear: {
                    Task { await clearMetadataKey(service: "omdb") }
                }

                AdminToggleRow(title: "TMDb", subtitle: metadataKeySubtitle(isSet: metadataKeys?.tmdbKeySet), isOn: Binding(
                    get: { metadataSources.tmdbEnabled },
                    set: { value in
                        metadataSources = metadataSources.updated(tmdbEnabled: value)
                        Task { await saveMetadataSources() }
                    }
                ))
                .disabled(metadataKeys?.tmdbKeySet == false)
                .opacity(metadataKeys?.tmdbKeySet == false ? 0.45 : 1)

                MetadataKeyRow(title: "TMDb API key", keyText: $tmdbKeyInput, isSet: metadataKeys?.tmdbKeySet ?? false) {
                    Task { await saveMetadataKey(service: "tmdb") }
                } onClear: {
                    Task { await clearMetadataKey(service: "tmdb") }
                }

                Divider().overlay(.white.opacity(0.12))

                AdminToggleRow(title: "Blu-ray.com scraper", subtitle: "Experimental HDR, audio and subtitle lookup.", isOn: Binding(
                    get: { metadataSources.blurayScrapeEnabled },
                    set: { value in
                        metadataSources = metadataSources.updated(blurayScrapeEnabled: value)
                        Task { await saveMetadataSources() }
                    }
                ))

                AdminToggleRow(title: "bluray-disc.de scraper", subtitle: "Experimental metadata scraper.", isOn: Binding(
                    get: { metadataSources.bluraydiscdeScrapeEnabled },
                    set: { value in
                        metadataSources = metadataSources.updated(bluraydiscdeScrapeEnabled: value)
                        Task { await saveMetadataSources() }
                    }
                ))
            }
        }
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

    private func loadAdmin() async {
        isLoading = true
        async let auth = loadSecurity()
        async let adminUsers = loadUsers()
        async let adminGroups = loadGroups()
        _ = await (auth, adminUsers, adminGroups)
        await loadSection(selectedSection)
        isLoading = false
    }

    private func loadSection(_ section: AdminSection) async {
        switch section {
        case .security:
            await loadSecurity()
        case .users:
            await loadUsers()
        case .groups:
            await loadGroups()
        case .roles:
            break
        case .backup:
            await loadBackups()
        case .logs:
            await loadLogs()
        case .advanced:
            await loadAdvanced()
        }
    }

    private func loadSecurity() async {
        do {
            async let status = apiClient.getAuthStatus()
            async let invites = apiClient.getInviteCodes()
            authStatus = try await status
            pendingAuthEnabled = nil
            pendingRegistrationEnabled = nil
            inviteCodes = try await invites
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func loadUsers() async {
        do {
            users = try await apiClient.getAdminUsers()
        } catch {
            users = []
        }
    }

    private func loadGroups() async {
        do {
            groups = try await apiClient.getGroups()
        } catch {
            groups = []
        }
    }

    private func loadBackups() async {
        do {
            backups = try await apiClient.getBackups()
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func loadLogs() async {
        do {
            logs = try await apiClient.getAdminLogs(level: logLevelFilter, category: logCategoryFilter)
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func loadAdvanced() async {
        do {
            async let debug = apiClient.getDebugSetting()
            async let mcp = apiClient.getMCPSetting()
            async let loadedSources = apiClient.getDigitalSources()
            async let loadedMetadataSources = apiClient.getMetadataSourceSettings()
            async let loadedMetadataKeys = apiClient.getMetadataAPIKeys()
            debugEnabled = try await debug.debugEnabled ?? false
            mcpEnabled = try await mcp.mcpEnabled ?? true
            digitalSources = try await loadedSources
            let sourceSettings = try await loadedMetadataSources
            metadataSources = MetadataSourceSettingsUpdate(
                omdbEnabled: sourceSettings.omdbEnabled,
                tmdbEnabled: sourceSettings.tmdbEnabled,
                blurayScrapeEnabled: sourceSettings.blurayScrapeEnabled,
                bluraydiscdeScrapeEnabled: sourceSettings.bluraydiscdeScrapeEnabled
            )
            metadataKeys = try await loadedMetadataKeys
            omdbKeyInput = metadataKeys?.omdbKey ?? ""
            tmdbKeyInput = metadataKeys?.tmdbKey ?? ""
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func setAuthEnabled(_ enabled: Bool) async {
        isWorking = true
        statusMessage = AdminStatusMessage(text: "Saving authentication setting...", isError: false)
        defer { isWorking = false }

        do {
            let response = try await apiClient.setAuthEnabled(enabled)
            if let authEnabled = response.authEnabled {
                authStatus = authStatus?.updated(authEnabled: authEnabled)
                pendingAuthEnabled = authEnabled
            }
            statusMessage = AdminStatusMessage(text: enabled ? "Authentication enabled." : "Authentication disabled.", isError: false)
            await loadSecurity()
        } catch {
            pendingAuthEnabled = nil
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
            await loadSecurity()
        }
    }

    private func setRegistrationEnabled(_ enabled: Bool) async {
        isWorking = true
        statusMessage = AdminStatusMessage(text: "Saving registration setting...", isError: false)
        defer { isWorking = false }

        do {
            let response = try await apiClient.setRegistrationEnabled(enabled)
            if let registrationEnabled = response.registrationEnabled {
                authStatus = authStatus?.updated(registrationEnabled: registrationEnabled)
                pendingRegistrationEnabled = registrationEnabled
            }
            statusMessage = AdminStatusMessage(text: "Registration setting saved.", isError: false)
            await loadSecurity()
        } catch {
            pendingRegistrationEnabled = nil
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
            await loadSecurity()
        }
    }

    private func createInviteCode() async {
        let username = newInviteUsername.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !username.isEmpty else { return }
        isWorking = true
        do {
            createdInvite = try await apiClient.createInviteCode(username: username)
            newInviteUsername = ""
            statusMessage = AdminStatusMessage(text: "Invite code created.", isError: false)
            inviteCodes = try await apiClient.getInviteCodes()
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
        isWorking = false
    }

    private func deleteInviteCode(_ invite: InviteCode) async {
        do {
            try await apiClient.deleteInviteCode(id: invite.id)
            inviteCodes.removeAll { $0.id == invite.id }
            statusMessage = AdminStatusMessage(text: "Invite code revoked.", isError: false)
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func resetPasskey(_ user: AdminUser) async {
        do {
            try await apiClient.resetAdminUserPasskey(id: user.id)
            statusMessage = AdminStatusMessage(text: "Passkeys reset for \(user.username).", isError: false)
            await loadUsers()
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func toggleRole(_ user: AdminUser) async {
        let nextRole = user.role == "admin" ? "MemberGroups" : user.role == "MemberGroups" ? "user" : "admin"
        do {
            _ = try await apiClient.updateAdminUserRole(id: user.id, role: nextRole)
            statusMessage = AdminStatusMessage(text: "Role updated for \(user.username).", isError: false)
            await loadUsers()
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func deleteUser(_ user: AdminUser) async {
        do {
            try await apiClient.deleteAdminUser(id: user.id)
            users.removeAll { $0.id == user.id }
            statusMessage = AdminStatusMessage(text: "User deleted.", isError: false)
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func createGroup() async {
        let name = newGroupName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        isWorking = true
        do {
            _ = try await apiClient.createGroup(name: name)
            newGroupName = ""
            statusMessage = AdminStatusMessage(text: "Group created.", isError: false)
            await loadGroups()
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
        isWorking = false
    }

    private func deleteGroup(_ group: Group) async {
        do {
            try await apiClient.deleteGroup(id: group.id)
            groups.removeAll { $0.id == group.id }
            statusMessage = AdminStatusMessage(text: "Group deleted.", isError: false)
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func createBackup() async {
        isWorking = true
        do {
            let response = try await apiClient.createBackup()
            statusMessage = AdminStatusMessage(text: "Backup created: \(response.name).", isError: false)
            await loadBackups()
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
        isWorking = false
    }

    private func deleteBackup(_ backup: BackupSummary) async {
        do {
            try await apiClient.deleteBackup(name: backup.name)
            backups.removeAll { $0.name == backup.name }
            statusMessage = AdminStatusMessage(text: "Backup deleted.", isError: false)
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func clearLogs() async {
        do {
            try await apiClient.clearAdminLogs()
            await loadLogs()
            statusMessage = AdminStatusMessage(text: "Logs cleared.", isError: false)
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func setDebugEnabled(_ enabled: Bool) async {
        do {
            _ = try await apiClient.setDebugEnabled(enabled)
            statusMessage = AdminStatusMessage(text: "Debug setting saved.", isError: false)
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func setMCPEnabled(_ enabled: Bool) async {
        do {
            _ = try await apiClient.setMCPEnabled(enabled)
            statusMessage = AdminStatusMessage(text: "MCP setting saved.", isError: false)
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func createDigitalSource() async {
        let name = newDigitalName.trimmingCharacters(in: .whitespacesAndNewlines)
        let url = newDigitalURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, !url.isEmpty else { return }
        isWorking = true
        do {
            _ = try await apiClient.createDigitalSource(name: name, type: newDigitalType, baseURL: url, token: newDigitalToken)
            newDigitalName = ""
            newDigitalURL = ""
            newDigitalToken = ""
            statusMessage = AdminStatusMessage(text: "Digital library added.", isError: false)
            digitalSources = try await apiClient.getDigitalSources()
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
        isWorking = false
    }

    private func deleteDigitalSource(_ source: DigitalLibrarySource) async {
        do {
            try await apiClient.deleteDigitalSource(id: source.id)
            digitalSources.removeAll { $0.id == source.id }
            statusMessage = AdminStatusMessage(text: "Digital library removed.", isError: false)
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func syncDigitalSource(_ source: DigitalLibrarySource) async {
        do {
            _ = try await apiClient.syncDigitalSource(id: source.id)
            statusMessage = AdminStatusMessage(text: "Digital library sync started.", isError: false)
            digitalSources = try await apiClient.getDigitalSources()
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func saveMetadataSources() async {
        do {
            _ = try await apiClient.setMetadataSourceSettings(metadataSources)
            statusMessage = AdminStatusMessage(text: "Metadata sources saved.", isError: false)
            await loadAdvanced()
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func saveMetadataKey(service: String) async {
        do {
            if service == "omdb" {
                try await apiClient.updateMetadataAPIKeys(omdbKey: omdbKeyInput.trimmingCharacters(in: .whitespacesAndNewlines))
            } else {
                try await apiClient.updateMetadataAPIKeys(tmdbKey: tmdbKeyInput.trimmingCharacters(in: .whitespacesAndNewlines))
            }
            statusMessage = AdminStatusMessage(text: "API key saved.", isError: false)
            await loadAdvanced()
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func clearMetadataKey(service: String) async {
        do {
            if service == "omdb" {
                try await apiClient.updateMetadataAPIKeys(omdbKey: "")
            } else {
                try await apiClient.updateMetadataAPIKeys(tmdbKey: "")
            }
            statusMessage = AdminStatusMessage(text: "API key removed.", isError: false)
            await loadAdvanced()
        } catch {
            statusMessage = AdminStatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func metadataKeySubtitle(isSet: Bool?) -> String {
        if isSet == false { return "Add an API key before enabling this source." }
        return isSet == true ? "API key configured." : "Loading API key status."
    }

}

private enum AdminSection: String, CaseIterable, Identifiable {
    case security
    case users
    case groups
    case roles
    case backup
    case logs
    case advanced

    var id: String { rawValue }

    var title: String {
        switch self {
        case .security: "Security"
        case .users: "Users"
        case .groups: "Groups"
        case .roles: "Roles"
        case .backup: "Backup"
        case .logs: "Logs"
        case .advanced: "Advanced"
        }
    }

    var icon: String {
        switch self {
        case .security: "shield.lefthalf.filled"
        case .users: "person.3.fill"
        case .groups: "folder.badge.person.crop"
        case .roles: "shield.checkered"
        case .backup: "externaldrive.fill"
        case .logs: "doc.text.magnifyingglass"
        case .advanced: "gearshape.2.fill"
        }
    }

    var translationKey: String {
        switch self {
        case .security: "settings.menuSecurity"
        case .users: "settings.menuUsers"
        case .groups: "settings.menuGroups"
        case .roles: "settings.menuRoles"
        case .backup: "settings.menuBackup"
        case .logs: "settings.menuLogs"
        case .advanced: "settings.menuAdvanced"
        }
    }
}

private extension MetadataSourceSettingsUpdate {
    func updated(
        omdbEnabled: Bool? = nil,
        tmdbEnabled: Bool? = nil,
        blurayScrapeEnabled: Bool? = nil,
        bluraydiscdeScrapeEnabled: Bool? = nil
    ) -> MetadataSourceSettingsUpdate {
        MetadataSourceSettingsUpdate(
            omdbEnabled: omdbEnabled ?? self.omdbEnabled,
            tmdbEnabled: tmdbEnabled ?? self.tmdbEnabled,
            blurayScrapeEnabled: blurayScrapeEnabled ?? self.blurayScrapeEnabled,
            bluraydiscdeScrapeEnabled: bluraydiscdeScrapeEnabled ?? self.bluraydiscdeScrapeEnabled
        )
    }
}

private struct AdminStatusMessage: Identifiable {
    let id = UUID()
    let text: String
    let isError: Bool
}

private enum AdminTheme {
    static let accent = Color(red: 0.91, green: 0.77, blue: 0.28)
}

private struct AdminCard<Content: View>: View {
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
                        Image(systemName: icon).foregroundStyle(AdminTheme.accent)
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

private struct AdminInfoRow: View {
    let icon: String
    let title: String
    let subtitle: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .foregroundStyle(AdminTheme.accent)
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

private struct AdminToggleRow: View {
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
        .tint(AdminTheme.accent)
    }
}

private struct AdminTextField: View {
    let title: String
    @Binding var text: String
    let systemImage: String

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.white.opacity(0.6))
            HStack(spacing: 10) {
                Image(systemName: systemImage)
                    .foregroundStyle(.white.opacity(0.42))
                    .frame(width: 18)
                TextField(title, text: $text)
                    .textFieldStyle(.plain)
                    .foregroundStyle(.white)
                    .tint(.white)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
            }
            .padding(12)
            .background(.white.opacity(0.06))
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }
}

private struct DigitalSourceRow: View {
    let source: DigitalLibrarySource
    let onSync: () -> Void
    let onDelete: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: source.type == "plex" ? "play.rectangle.fill" : "play.tv.fill")
                .foregroundStyle(source.type == "plex" ? .orange : .blue)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 3) {
                Text(source.name)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                Text(source.baseUrl)
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.48))
                    .lineLimit(1)
                Text(syncInfo)
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.38))
            }
            Spacer()
            Button(action: onSync) {
                Image(systemName: "arrow.triangle.2.circlepath")
            }
            .buttonStyle(.bordered)
            .tint(AdminTheme.accent)
            Button(role: .destructive, action: onDelete) {
                Image(systemName: "trash")
            }
            .buttonStyle(.bordered)
            .tint(.red)
        }
        .padding(12)
        .background(.white.opacity(0.05))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var syncInfo: String {
        let count = source.itemCount ?? 0
        guard let lastSynced = source.lastSynced, !lastSynced.isEmpty else {
            return "\(count) items · never synced"
        }
        return "\(count) items · last synced \(String(lastSynced.prefix(10)))"
    }
}

private struct MetadataKeyRow: View {
    let title: String
    @Binding var keyText: String
    let isSet: Bool
    let onSave: () -> Void
    let onClear: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label(title, systemImage: "key.fill")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.62))
                Spacer()
                Text(isSet ? "Set" : "No key")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(isSet ? .green : .orange)
            }
            HStack(spacing: 8) {
                SecureField(title, text: $keyText)
                    .textFieldStyle(.plain)
                    .foregroundStyle(.white)
                    .tint(.white)
                    .padding(10)
                    .background(.white.opacity(0.06))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                Button(action: onSave) {
                    Image(systemName: "checkmark")
                }
                .buttonStyle(.bordered)
                .tint(AdminTheme.accent)
                if isSet {
                    Button(role: .destructive, action: onClear) {
                        Image(systemName: "trash")
                    }
                    .buttonStyle(.bordered)
                    .tint(.red)
                }
            }
        }
    }
}

private struct AdminEmptyState: View {
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

private func adminLocalDateTime(_ value: String?) -> String {
    guard let rawValue = value?.trimmingCharacters(in: .whitespacesAndNewlines), !rawValue.isEmpty else { return "-" }

    let date = parseAdminDate(rawValue)
    guard let date else {
        return String(rawValue.prefix(16)).replacingOccurrences(of: "T", with: " ")
    }

    let formatter = DateFormatter()
    formatter.locale = .current
    formatter.timeZone = .current
    formatter.dateStyle = .medium
    formatter.timeStyle = .short

    if let abbreviation = TimeZone.current.abbreviation(for: date) {
        return "\(formatter.string(from: date)) \(abbreviation)"
    }
    return formatter.string(from: date)
}

private func parseAdminDate(_ value: String) -> Date? {
    let isoFormatter = ISO8601DateFormatter()
    isoFormatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = isoFormatter.date(from: value) {
        return date
    }

    isoFormatter.formatOptions = [.withInternetDateTime]
    if let date = isoFormatter.date(from: value) {
        return date
    }

    let utcFormatter = DateFormatter()
    utcFormatter.locale = Locale(identifier: "en_US_POSIX")
    utcFormatter.timeZone = TimeZone(secondsFromGMT: 0)

    for format in ["yyyy-MM-dd'T'HH:mm:ss.SSSSSS", "yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd HH:mm:ss"] {
        utcFormatter.dateFormat = format
        if let date = utcFormatter.date(from: value) {
            return date
        }
    }

    return nil
}

private struct InviteRow: View {
    let invite: InviteCode
    let onRevoke: () -> Void

    private var isUsed: Bool { invite.usedAt?.isEmpty == false }

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: isUsed ? "checkmark.seal.fill" : "envelope.fill")
                .foregroundStyle(isUsed ? .green : AdminTheme.accent)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 3) {
                Text(invite.username)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                Text("Expires \(adminLocalDateTime(invite.expiresAt))")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.48))
            }
            Spacer()
            if !isUsed {
                Button(role: .destructive, action: onRevoke) {
                    Image(systemName: "trash")
                }
                .buttonStyle(.bordered)
                .tint(.red)
            }
        }
        .padding(12)
        .background(.white.opacity(0.05))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

}

private struct AdminUserRow: View {
    let user: AdminUser
    let onResetPasskey: () -> Void
    let onToggleRole: () -> Void
    let onDelete: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                Image(systemName: user.role == "admin" ? "crown.fill" : user.role == "MemberGroups" ? "key.fill" : "person.fill")
                    .foregroundStyle(roleColor)
                    .frame(width: 24)
                VStack(alignment: .leading, spacing: 3) {
                    Text(user.displayName?.isEmpty == false ? user.displayName! : user.username)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.white)
                    Text("\(user.username) · \(user.credentialCount) passkey\(user.credentialCount == 1 ? "" : "s")")
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.48))
                }
                Spacer()
                Text(user.role)
                    .font(.caption.weight(.bold))
                    .foregroundStyle(roleColor)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(roleColor.opacity(0.16))
                    .clipShape(RoundedRectangle(cornerRadius: 5))
            }

            HStack(spacing: 8) {
                Button(action: onResetPasskey) {
                    Label("Reset", systemImage: "key.slash")
                }
                .buttonStyle(.bordered)
                .tint(AdminTheme.accent)

                Button(action: onToggleRole) {
                    Label("Role", systemImage: "arrow.triangle.2.circlepath")
                }
                .buttonStyle(.bordered)
                .tint(.blue)

                Button(role: .destructive, action: onDelete) {
                    Label("Delete", systemImage: "trash")
                }
                .buttonStyle(.bordered)
                .tint(.red)
            }
            .font(.caption.weight(.semibold))
        }
        .padding(12)
        .background(.white.opacity(0.05))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var roleColor: Color {
        switch user.role {
        case "admin": .orange
        case "MemberGroups": .blue
        default: .white.opacity(0.6)
        }
    }
}

private struct AdminGroupRow: View {
    let group: Group
    let onDelete: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "folder.fill")
                .foregroundStyle(AdminTheme.accent)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 3) {
                Text(group.name)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                Text("\(group.memberCount ?? 0) member\((group.memberCount ?? 0) == 1 ? "" : "s")")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.48))
            }
            Spacer()
            Button(role: .destructive, action: onDelete) {
                Image(systemName: "trash")
            }
            .buttonStyle(.bordered)
            .tint(.red)
        }
        .padding(12)
        .background(.white.opacity(0.05))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

private struct BackupRow: View {
    let backup: BackupSummary
    let onDelete: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "externaldrive.fill")
                .foregroundStyle(AdminTheme.accent)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 3) {
                Text(backup.name)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                    .lineLimit(1)
                Text("\(backup.movieCount ?? 0) movies · \(backup.posterCount ?? 0) posters · \(ByteCountFormatter.string(fromByteCount: Int64(backup.size), countStyle: .file))")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.48))
            }
            Spacer()
            Button(role: .destructive, action: onDelete) {
                Image(systemName: "trash")
            }
            .buttonStyle(.bordered)
            .tint(.red)
        }
        .padding(12)
        .background(.white.opacity(0.05))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

private struct LogRow: View {
    let log: AdminLogEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text((log.level ?? "info").uppercased())
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(levelColor)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(levelColor.opacity(0.16))
                    .clipShape(RoundedRectangle(cornerRadius: 5))
                Text(log.category ?? "general")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.58))
                Spacer()
                Text(shortDate(log.timestamp))
                    .font(.caption2.monospaced())
                    .foregroundStyle(.white.opacity(0.38))
            }

            Text(log.message ?? "-")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.white)
                .fixedSize(horizontal: false, vertical: true)

            if let detail = log.detail, !detail.isEmpty {
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.46))
                    .lineLimit(3)
            }
        }
        .padding(12)
        .background(.white.opacity(0.05))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var levelColor: Color {
        switch log.level {
        case "error": .red
        case "warn": .yellow
        case "success": .green
        default: .blue
        }
    }

    private func shortDate(_ value: String?) -> String {
        guard let value, !value.isEmpty else { return "-" }
        return String(value.prefix(19)).replacingOccurrences(of: "T", with: " ")
    }
}

private extension AuthStatus {
    func updated(authEnabled: Bool? = nil, registrationEnabled: Bool? = nil) -> AuthStatus {
        AuthStatus(
            authEnabled: authEnabled ?? self.authEnabled,
            hasUsers: hasUsers,
            hasCredentials: hasCredentials,
            rpID: rpID,
            userCount: userCount,
            groupCount: groupCount,
            role: role,
            registrationEnabled: registrationEnabled ?? self.registrationEnabled
        )
    }
}

#Preview {
    NavigationStack {
        AdminSettingsView()
            .environment(AppStateManager().apiClient)
    }
    .preferredColorScheme(.dark)
}
