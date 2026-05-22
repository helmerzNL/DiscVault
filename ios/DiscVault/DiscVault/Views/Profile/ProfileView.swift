import PhotosUI
import SafariServices
import SwiftUI
import UIKit
import UniformTypeIdentifiers

struct ProfileView: View {
    @EnvironmentObject private var appState: AppStateManager
    @Environment(APIClient.self) private var apiClient
    @Environment(AppLanguageManager.self) private var languageManager

    @State private var selectedSection: ProfileSection = .general
    @State private var currentUser: User?
    @State private var groups: [Group] = []
    @State private var selectedGroupForMembers: Group?
    @State private var groupMembers: [GroupMember] = []
    @State private var newGroupName = ""
    @State private var inviteUsername = ""
    @State private var authStatus: AuthStatus?
    @State private var credentials: [PasskeyCredential] = []
    @State private var preferences = ProfilePreferences()
    @State private var hasLoadedPreferences = false
    @State private var apiKeys: [APIKeySummary] = []
    @State private var mcpLogs: [MCPLogEntry] = []

    @State private var username = ""
    @State private var firstName = ""
    @State private var lastName = ""
    @State private var newAPIKeyLabel = ""
    @State private var revealedAPIKey: String?

    @State private var isLoading = true
    @State private var isSavingProfile = false
    @State private var statusMessage: StatusMessage?
    @State private var showPasskeySetup = false
    @State private var showSignOutConfirm = false
    @State private var selectedAvatarItem: PhotosPickerItem?
    @State private var isUploadingAvatar = false

    private var isAdmin: Bool { currentUser?.role == "admin" }
    private var canCreateMemberGroups: Bool {
        guard let currentUser else { return false }
        return currentUser.role == "admin" || currentUser.role == "MemberGroups" || currentUser.permissions?.contains("groups.create") == true
    }

    private var ownedGroups: [Group] { groups.filter { $0.myRole == "owner" || isAdmin } }
    private var memberGroups: [Group] { groups.filter { $0.myRole != "owner" && !isAdmin } }

    private var passkeySetupURL: URL? {
        guard var components = URLComponents(string: apiClient.baseURL), !apiClient.baseURL.isEmpty else {
            return nil
        }
        components.path = "/profile"
        return components.url
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.06, green: 0.06, blue: 0.14).ignoresSafeArea()

                if isLoading {
                    ProgressView().tint(.white)
                } else {
                    ScrollView {
                        VStack(spacing: 18) {
                            userHeader
                            sectionPicker
                            selectedSectionView
                        }
                        .padding(.horizontal, 16)
                        .padding(.top, 12)
                        .padding(.bottom, 28)
                    }
                }
            }
            .navigationTitle(languageManager.text("profile.title"))
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    NavigationLink {
                        SettingsView()
                    } label: {
                        Image(systemName: "gearshape.fill")
                            .foregroundStyle(.white)
                    }
                }

                if isAdmin {
                    ToolbarItem(placement: .topBarTrailing) {
                        NavigationLink {
                            AdminSettingsView()
                        } label: {
                            Image(systemName: "shield.lefthalf.filled")
                                .foregroundStyle(.orange)
                        }
                    }
                }
            }
            .task { await loadInitialProfile() }
            .onChange(of: selectedSection) { _, section in
                Task { await loadSection(section) }
            }
            .sheet(isPresented: $showPasskeySetup) {
                if let url = passkeySetupURL {
                    SafariView(url: url)
                        .ignoresSafeArea()
                }
            }
            .confirmationDialog(languageManager.text("profile.signOut"), isPresented: $showSignOutConfirm, titleVisibility: .visible) {
                Button(languageManager.text("profile.signOut"), role: .destructive) { appState.signOut() }
                Button(languageManager.text("profile.cancel"), role: .cancel) {}
            } message: {
                Text(languageManager.text("profile.signOutMessage"))
            }
            .onChange(of: selectedAvatarItem) { _, item in
                guard let item else { return }
                Task { await uploadAvatar(item) }
            }
        }
    }

    private var userHeader: some View {
        SectionCard {
            HStack(spacing: 16) {
                avatarView

                VStack(alignment: .leading, spacing: 6) {
                    Text(displayName)
                        .font(.title3.bold())
                        .foregroundStyle(.white)
                        .lineLimit(1)

                    if !username.isEmpty {
                        Text("@\(username)")
                            .font(.subheadline)
                            .foregroundStyle(.white.opacity(0.55))
                            .lineLimit(1)
                    }

                    HStack(spacing: 8) {
                        if let role = currentUser?.role {
                            Badge(text: role.capitalized, color: roleColor(role))
                        }
                        if let createdAt = currentUser?.createdAt, !createdAt.isEmpty {
                            Text(createdAt.prefix(10))
                                .font(.caption)
                                .foregroundStyle(.white.opacity(0.4))
                        }
                    }
                }

                Spacer(minLength: 0)
            }
        }
    }

    private var avatarView: some View {
        ZStack {
            Circle()
                .fill(
                    LinearGradient(
                        colors: [Color(red: 0.45, green: 0.2, blue: 0.95), Color(red: 0.2, green: 0.45, blue: 0.95)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )

            if let url = apiClient.avatarURL(for: currentUser?.avatarURL) {
                AsyncImage(url: url) { phase in
                    if case .success(let image) = phase {
                        image.resizable().aspectRatio(contentMode: .fill)
                    } else {
                        Text(initials)
                            .font(.system(size: 22, weight: .bold, design: .rounded))
                            .foregroundStyle(.white)
                    }
                }
                .clipShape(Circle())
            } else {
                Text(initials)
                    .font(.system(size: 22, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)
            }
        }
        .frame(width: 64, height: 64)
        .shadow(color: .purple.opacity(0.28), radius: 12)
    }

    private var sectionPicker: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(ProfileSection.allCases) { section in
                    Button {
                        selectedSection = section
                    } label: {
                        Label(languageManager.text(section.translationKey), systemImage: section.icon)
                            .font(.caption.weight(.semibold))
                            .lineLimit(1)
                            .foregroundStyle(selectedSection == section ? .black : .white.opacity(0.68))
                            .padding(.horizontal, 12)
                            .frame(height: 36)
                            .background(selectedSection == section ? Color(red: 0.91, green: 0.77, blue: 0.28) : .white.opacity(0.07))
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
        case .security:
            securitySection
        case .preferences:
            preferencesSection
        case .notifications:
            notificationsSection
        case .apiKeys:
            apiKeysSection
        case .mcp:
            mcpSection
        }
    }

    private var generalSection: some View {
        VStack(spacing: 14) {
            SectionCard(title: languageManager.text("profile.userProfile"), icon: "person.crop.circle") {
                VStack(spacing: 14) {
                    ProfileTextField(title: languageManager.text("profile.username"), text: $username, systemImage: "person.fill")
                    ProfileTextField(title: languageManager.text("profile.firstName"), text: $firstName, systemImage: "textformat")
                    ProfileTextField(title: languageManager.text("profile.lastName"), text: $lastName, systemImage: "textformat")

                    Button {
                        Task { await saveProfile() }
                    } label: {
                        HStack(spacing: 10) {
                            if isSavingProfile {
                                ProgressView().tint(.black).scaleEffect(0.8)
                            } else {
                                Image(systemName: "checkmark.circle.fill")
                            }
                            Text(isSavingProfile ? languageManager.text("profile.saving") : languageManager.text("profile.saveProfile"))
                                .font(.headline)
                        }
                        .foregroundStyle(.black)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(Color(red: 0.91, green: 0.77, blue: 0.28))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                    }
                    .disabled(isSavingProfile || username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }

            SectionCard(title: languageManager.text("profile.photo"), icon: "camera") {
                VStack(spacing: 12) {
                    HStack(spacing: 14) {
                        avatarView
                        VStack(alignment: .leading, spacing: 4) {
                            Text(languageManager.text("profile.photoTitle"))
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(.white)
                            Text(languageManager.text("profile.photoSubtitle"))
                                .font(.caption)
                                .foregroundStyle(.white.opacity(0.48))
                        }
                        Spacer(minLength: 0)
                    }

                    let avatarPickerTitle = isUploadingAvatar ? languageManager.text("profile.uploadingPhoto") : languageManager.text("profile.changePhoto")

                    PhotosPicker(selection: $selectedAvatarItem, matching: .images) {
                        Label(avatarPickerTitle, systemImage: "photo.on.rectangle.angled")
                            .font(.headline)
                            .foregroundStyle(.black)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 13)
                            .background(Color(red: 0.91, green: 0.77, blue: 0.28))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    .disabled(isUploadingAvatar)

                    if currentUser?.avatarURL != nil || currentUser?.avatar != nil {
                        Button(role: .destructive) {
                            Task { await removeAvatar() }
                        } label: {
                            Label(languageManager.text("profile.removePhoto"), systemImage: "trash")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .tint(.red)
                        .disabled(isUploadingAvatar)
                    }
                }
            }

            statusView

            SectionCard(title: "Account", icon: "rectangle.portrait.and.arrow.right") {
                Button(role: .destructive) {
                    showSignOutConfirm = true
                } label: {
                    Label(languageManager.text("profile.signOut"), systemImage: "rectangle.portrait.and.arrow.right")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
            }
        }
    }

    private var securitySection: some View {
        VStack(spacing: 14) {
            SectionCard(title: languageManager.text("settings.addPasskey"), icon: "person.badge.key.fill") {
                VStack(spacing: 12) {
                    InfoRow(
                        icon: "safari.fill",
                        title: "Configure an extra passkey",
                        subtitle: "Opens the DiscVault web app so the passkey is registered for your server domain."
                    )

                    Button {
                        showPasskeySetup = true
                    } label: {
                        Label(languageManager.text("settings.addPasskeyBtn"), systemImage: "key.fill")
                            .font(.headline)
                            .foregroundStyle(.black)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                            .background(Color(red: 0.91, green: 0.77, blue: 0.28))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain)
                    .disabled(passkeySetupURL == nil)
                }
            }

            SectionCard(title: languageManager.text("settings.authTitle"), icon: "shield.lefthalf.filled") {
                VStack(spacing: 12) {
                    if let authStatus {
                        InfoRow(
                            icon: authStatus.authEnabled ? "lock.fill" : "lock.open.fill",
                            title: authStatus.authEnabled ? "Authentication enabled" : "Authentication disabled",
                            subtitle: "\(authStatus.userCount) users · \(authStatus.groupCount) groups"
                        )
                    }

                    if credentials.isEmpty {
                        EmptyState(icon: "key.slash", title: "No passkeys", subtitle: "Register passkeys in the web app security settings.")
                    } else {
                        ForEach(credentials) { credential in
                            HStack(spacing: 12) {
                                Image(systemName: "key.fill")
                                    .foregroundStyle(Color(red: 0.91, green: 0.77, blue: 0.28))
                                    .frame(width: 28)

                                VStack(alignment: .leading, spacing: 3) {
                                    Text(credential.credentialName ?? "Passkey")
                                        .font(.subheadline.weight(.semibold))
                                        .foregroundStyle(.white)
                                    Text(passkeySubtitle(credential))
                                        .font(.caption)
                                        .foregroundStyle(.white.opacity(0.48))
                                }

                                Spacer()

                                Button(role: .destructive) {
                                    Task { await deleteCredential(credential) }
                                } label: {
                                    Image(systemName: "trash")
                                }
                                .buttonStyle(.borderless)
                            }
                            .padding(12)
                            .background(.white.opacity(0.05))
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                        }
                    }
                }
            }

            memberGroupsSection
        }
    }

    private var memberGroupsSection: some View {
        SectionCard(title: languageManager.text("settings.groupsTitle"), icon: "folder.badge.person.crop") {
            VStack(spacing: 14) {
                if canCreateMemberGroups {
                    HStack(spacing: 10) {
                        ProfileTextField(title: languageManager.text("settings.groupName"), text: $newGroupName, systemImage: "folder.badge.plus")

                        Button {
                            Task { await createMemberGroup() }
                        } label: {
                            Image(systemName: "plus")
                                .foregroundStyle(.black)
                                .frame(width: 42, height: 42)
                                .background(Color(red: 0.91, green: 0.77, blue: 0.28))
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                        .disabled(newGroupName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                } else {
                    InfoRow(
                        icon: "lock.fill",
                        title: "MemberGroups role required",
                        subtitle: "Ask an admin for the MemberGroups role before creating shared groups."
                    )
                }

                if ownedGroups.isEmpty && memberGroups.isEmpty {
                EmptyState(icon: "person.2.slash", title: languageManager.text("rbac.noGroups"), subtitle: languageManager.text("profile.noGroupsSubtitle"))
                } else {
                    if !ownedGroups.isEmpty {
                        groupList(title: "Owned", groups: ownedGroups, owned: true)
                    }
                    if !memberGroups.isEmpty {
                        groupList(title: "Member", groups: memberGroups, owned: false)
                    }
                }

                if let selectedGroupForMembers {
                    memberManagementPanel(group: selectedGroupForMembers)
                }
            }
        }
    }

    private func groupList(title: String, groups: [Group], owned: Bool) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title)
                .font(.caption.weight(.bold))
                .foregroundStyle(.white.opacity(0.48))
                .textCase(.uppercase)

            ForEach(groups) { group in
                MemberGroupRow(
                    group: group,
                    owned: owned,
                    onManage: { Task { await loadMembers(for: group) } },
                    onLeave: { Task { await leaveGroup(group) } },
                    onDelete: { Task { await deleteMemberGroup(group) } }
                )
            }
        }
    }

    private func memberManagementPanel(group: Group) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(languageManager.text("js.membersOf", group.name))
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                Spacer()
                Button {
                    selectedGroupForMembers = nil
                    groupMembers = []
                } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.borderless)
            }

            if groupMembers.isEmpty {
                EmptyState(icon: "person.slash", title: languageManager.text("js.noMembers"), subtitle: languageManager.text("profile.inviteByUsername"))
            } else {
                VStack(spacing: 8) {
                    ForEach(groupMembers) { member in
                        MemberRow(
                            member: member,
                            canRemove: member.groupRole != "owner",
                            onRemove: { Task { await removeMember(member, from: group) } }
                        )
                    }
                }
            }

            HStack(spacing: 10) {
                ProfileTextField(title: languageManager.text("profile.inviteUsername"), text: $inviteUsername, systemImage: "person.badge.plus")
                Button {
                    Task { await inviteUser(to: group) }
                } label: {
                    Image(systemName: "paperplane.fill")
                        .foregroundStyle(.black)
                        .frame(width: 42, height: 42)
                        .background(Color(red: 0.91, green: 0.77, blue: 0.28))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                .disabled(inviteUsername.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(12)
        .background(.white.opacity(0.05))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var preferencesSection: some View {
        SectionCard(title: languageManager.text("profile.menuPreferences"), icon: "slider.horizontal.3") {
            VStack(spacing: 16) {
                PreferenceToggle(title: languageManager.text("settings.showAutoVideos"), subtitle: languageManager.text("settings.showAutoVideosDesc"), isOn: $preferences.showAutoVideos)
                PreferenceToggle(title: languageManager.text("settings.showSearchButton"), subtitle: languageManager.text("settings.showSearchButtonDesc"), isOn: $preferences.showSearchButton)
                PreferenceToggle(title: languageManager.text("settings.showLocalTitle"), subtitle: languageManager.text("settings.showLocalTitleDesc"), isOn: $preferences.showLocalTitle)
                PreferenceToggle(title: languageManager.text("settings.detailedActorDetails"), subtitle: languageManager.text("settings.detailedActorDetailsDesc"), isOn: $preferences.detailedActorDetails)
                    .onChange(of: preferences.detailedActorDetails) { _, enabled in
                        guard hasLoadedPreferences else { return }
                        Task { await savePreference(key: "detailed_actor", value: String(enabled)) }
                    }

                VStack(alignment: .leading, spacing: 8) {
                    Label(languageManager.text("settings.ratingCountry"), systemImage: "globe.europe.africa.fill")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.white.opacity(0.62))
                    Picker(languageManager.text("settings.ratingCountry"), selection: $preferences.ratingCountry) {
                        ForEach(ProfilePreferences.ratingCountries, id: \.self) { country in
                            Text(country).tag(country)
                        }
                    }
                    .pickerStyle(.segmented)
                }

                statusView

                Button {
                    Task { await savePreferences() }
                } label: {
                    Label("Save Preferences", systemImage: "checkmark.circle.fill")
                        .font(.headline)
                        .foregroundStyle(.black)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(Color(red: 0.91, green: 0.77, blue: 0.28))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                }
            }
        }
    }

    private var notificationsSection: some View {
        VStack(spacing: 14) {
            SectionCard(title: "Push Notifications", icon: "bell.badge") {
                EmptyState(
                    icon: "bell.slash",
                    title: "Native push is not configured yet",
                    subtitle: "The PWA handles browser push subscriptions. Native iOS push needs APNs registration and a device-token endpoint before these controls can be active."
                )
            }

            SectionCard(title: "Notification Preferences", icon: "checklist") {
                InfoRow(
                    icon: "person.crop.circle.badge.plus",
                    title: "MemberGroup invites",
                    subtitle: "This preference will be wired once native push subscription support exists."
                )
            }
        }
    }

    private var apiKeysSection: some View {
        VStack(spacing: 14) {
            SectionCard(title: "MCP API Keys", icon: "key.viewfinder") {
                VStack(spacing: 14) {
                    Text("Use a personal API key to connect DiscVault to AI clients such as Claude or Cursor through MCP.")
                        .font(.subheadline)
                        .foregroundStyle(.white.opacity(0.62))
                        .frame(maxWidth: .infinity, alignment: .leading)

                    ProfileTextField(title: "Label", text: $newAPIKeyLabel, systemImage: "tag.fill")

                    Button {
                        Task { await createAPIKey() }
                    } label: {
                        Label("Create Key", systemImage: "plus.circle.fill")
                            .font(.headline)
                            .foregroundStyle(.black)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                            .background(Color(red: 0.91, green: 0.77, blue: 0.28))
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                    }

                    if let revealedAPIKey {
                        VStack(alignment: .leading, spacing: 10) {
                            Text("Copy this key now. It will not be shown again.")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(Color(red: 0.91, green: 0.77, blue: 0.28))
                            Text(revealedAPIKey)
                                .font(.system(.caption, design: .monospaced))
                                .foregroundStyle(.white)
                                .textSelection(.enabled)
                                .padding(10)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(.black.opacity(0.22))
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                            Button {
                                UIPasteboard.general.string = revealedAPIKey
                            } label: {
                                Label("Copy", systemImage: "doc.on.doc")
                            }
                            .buttonStyle(.bordered)
                        }
                        .padding(12)
                        .background(Color(red: 0.91, green: 0.77, blue: 0.28).opacity(0.08))
                        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Color(red: 0.91, green: 0.77, blue: 0.28).opacity(0.24)))
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                    }

                    if apiKeys.isEmpty {
                        EmptyState(icon: "key.slash", title: "No API keys", subtitle: "Create a key when you need MCP access.")
                    } else {
                        ForEach(apiKeys) { key in
                            HStack(spacing: 12) {
                                Image(systemName: "key")
                                    .foregroundStyle(.blue)
                                    .frame(width: 28)
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(key.label?.isEmpty == false ? key.label! : "Unnamed")
                                        .font(.subheadline.weight(.semibold))
                                        .foregroundStyle(.white)
                                    Text("Created \((key.createdAt ?? "").prefix(10))")
                                        .font(.caption)
                                        .foregroundStyle(.white.opacity(0.48))
                                }
                                Spacer()
                                Button(role: .destructive) {
                                    Task { await deleteAPIKey(key) }
                                } label: {
                                    Image(systemName: "trash")
                                }
                                .buttonStyle(.borderless)
                            }
                            .padding(12)
                            .background(.white.opacity(0.05))
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                        }
                    }
                }
            }
        }
    }

    private var mcpSection: some View {
        SectionCard(title: "MCP Activity", icon: "terminal") {
            VStack(spacing: 12) {
                Button {
                    Task { await loadMCPLogs() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .buttonStyle(.bordered)
                .frame(maxWidth: .infinity, alignment: .trailing)

                if mcpLogs.isEmpty {
                    EmptyState(icon: "terminal", title: "No MCP activity", subtitle: "Recent MCP tool calls for your account will appear here.")
                } else {
                    VStack(spacing: 8) {
                        ForEach(mcpLogs) { log in
                            VStack(alignment: .leading, spacing: 5) {
                                HStack {
                                    Text((log.message ?? "Tool").replacingOccurrences(of: "Tool: ", with: ""))
                                        .font(.system(.caption, design: .monospaced).weight(.semibold))
                                        .foregroundStyle(logColor(log.level))
                                    Spacer()
                                    Text((log.timestamp ?? "").replacingOccurrences(of: "T", with: " ").prefix(19))
                                        .font(.caption2)
                                        .foregroundStyle(.white.opacity(0.38))
                                }
                                if let detail = log.detail, !detail.isEmpty {
                                    Text(detail)
                                        .font(.caption2)
                                        .foregroundStyle(.white.opacity(0.48))
                                        .lineLimit(2)
                                }
                            }
                            .padding(10)
                            .background(.black.opacity(0.16))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                    }
                }
            }
        }
    }


    @ViewBuilder
    private var statusView: some View {
        if let statusMessage {
            HStack(spacing: 8) {
                Image(systemName: statusMessage.isError ? "exclamationmark.circle.fill" : "checkmark.circle.fill")
                Text(statusMessage.text)
                    .font(.caption)
                Spacer(minLength: 0)
            }
            .foregroundStyle(statusMessage.isError ? .red : .green)
            .padding(12)
            .background((statusMessage.isError ? Color.red : Color.green).opacity(0.1))
            .clipShape(RoundedRectangle(cornerRadius: 10))
        }
    }

    private var displayName: String {
        let fullName = [firstName, lastName].filter { !$0.isEmpty }.joined(separator: " ")
        if !fullName.isEmpty { return fullName }
        return currentUser?.displayName ?? currentUser?.username ?? "Profile"
    }

    private var initials: String {
        let parts = displayName.split(separator: " ")
        if parts.count >= 2 {
            return String(parts[0].prefix(1) + parts[1].prefix(1)).uppercased()
        }
        return String(displayName.prefix(2)).uppercased()
    }

    private func loadInitialProfile() async {
        isLoading = true
        await loadUser()
        async let loadedGroups = (try? apiClient.getGroups()) ?? []
        groups = await loadedGroups
        await loadSection(selectedSection)
        isLoading = false
    }

    private func loadSection(_ section: ProfileSection) async {
        switch section {
        case .general:
            await loadUser()
        case .security:
            await loadSecurity()
        case .preferences:
            await loadPreferences()
        case .apiKeys:
            await loadAPIKeys()
        case .mcp:
            await loadMCPLogs()
        case .notifications:
            break
        }
    }

    private func loadUser() async {
        guard let user = try? await apiClient.getCurrentUser() else { return }
        currentUser = user
        username = user.username
        firstName = user.firstName ?? ""
        lastName = user.lastName ?? ""
    }

    private func loadSecurity() async {
        async let status = try? apiClient.getAuthStatus()
        async let creds = (try? apiClient.getPasskeyCredentials()) ?? []
        async let loadedGroups = (try? apiClient.getGroups()) ?? []
        authStatus = await status
        credentials = await creds
        groups = await loadedGroups
    }

    private func loadPreferences() async {
        guard let values = try? await apiClient.getUserPreferences() else {
            hasLoadedPreferences = true
            return
        }
        preferences = ProfilePreferences(values: values)
        hasLoadedPreferences = true
    }

    private func loadAPIKeys() async {
        apiKeys = (try? await apiClient.getAPIKeys()) ?? []
    }

    private func loadMCPLogs() async {
        mcpLogs = (try? await apiClient.getMCPLogs(limit: 50)) ?? []
    }

    private func saveProfile() async {
        isSavingProfile = true
        statusMessage = nil
        defer { isSavingProfile = false }

        do {
            let response = try await apiClient.updateProfile(username: username.trimmingCharacters(in: .whitespacesAndNewlines), firstName: firstName, lastName: lastName)
            KeychainService.save(response.username, for: KeychainService.username)
            await loadUser()
            statusMessage = StatusMessage(text: languageManager.text("profile.saved"), isError: false)
        } catch {
            statusMessage = StatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func uploadAvatar(_ item: PhotosPickerItem) async {
        isUploadingAvatar = true
        defer {
            isUploadingAvatar = false
            selectedAvatarItem = nil
        }

        do {
            guard let data = try await item.loadTransferable(type: Data.self) else {
                statusMessage = StatusMessage(text: languageManager.text("profile.photoLoadError"), isError: true)
                return
            }
            if data.count > 2 * 1024 * 1024 {
                statusMessage = StatusMessage(text: languageManager.text("profile.photoTooLarge"), isError: true)
                return
            }
            let contentType = item.supportedContentTypes.first ?? .jpeg
            let fileExtension = contentType.preferredFilenameExtension ?? "jpg"
            let mimeType = contentType.preferredMIMEType ?? "image/jpeg"
            _ = try await apiClient.uploadProfileAvatar(data: data, filename: "avatar.\(fileExtension)", mimeType: mimeType)
            await loadUser()
            statusMessage = StatusMessage(text: languageManager.text("profile.photoUpdated"), isError: false)
        } catch {
            statusMessage = StatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func removeAvatar() async {
        isUploadingAvatar = true
        defer { isUploadingAvatar = false }
        do {
            try await apiClient.deleteProfileAvatar()
            await loadUser()
            statusMessage = StatusMessage(text: languageManager.text("profile.photoRemoved"), isError: false)
        } catch {
            statusMessage = StatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func savePreferences() async {
        await savePreferenceValues(preferences.dictionary, successMessage: "Preferences saved.")
    }

    private func savePreference(key: String, value: String) async {
        await savePreferenceValues([key: value], successMessage: "Preference saved.")
    }

    private func savePreferenceValues(_ values: [String: String], successMessage: String) async {
        do {
            try await apiClient.updateUserPreferences(values)
            statusMessage = StatusMessage(text: successMessage, isError: false)
        } catch {
            statusMessage = StatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func deleteCredential(_ credential: PasskeyCredential) async {
        try? await apiClient.deletePasskeyCredential(id: credential.id)
        await loadSecurity()
    }

    private func createMemberGroup() async {
        let name = newGroupName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        do {
            _ = try await apiClient.createGroup(name: name)
            newGroupName = ""
            statusMessage = StatusMessage(text: "Group created.", isError: false)
            await loadSecurity()
        } catch {
            statusMessage = StatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func loadMembers(for group: Group) async {
        selectedGroupForMembers = group
        inviteUsername = ""
        do {
            groupMembers = try await apiClient.getGroupMembers(groupId: group.id)
        } catch {
            groupMembers = []
            statusMessage = StatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func inviteUser(to group: Group) async {
        let username = inviteUsername.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !username.isEmpty else { return }
        do {
            try await apiClient.inviteUserToGroup(groupId: group.id, username: username)
            inviteUsername = ""
            statusMessage = StatusMessage(text: "Invite sent to \(username).", isError: false)
            await loadMembers(for: group)
        } catch {
            statusMessage = StatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func removeMember(_ member: GroupMember, from group: Group) async {
        do {
            try await apiClient.removeGroupMember(groupId: group.id, memberId: member.id.value)
            statusMessage = StatusMessage(text: "Member removed.", isError: false)
            await loadMembers(for: group)
            await loadSecurity()
        } catch {
            statusMessage = StatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func leaveGroup(_ group: Group) async {
        guard let currentUser else { return }
        do {
            try await apiClient.removeGroupMember(groupId: group.id, memberId: currentUser.id)
            statusMessage = StatusMessage(text: "Left group.", isError: false)
            selectedGroupForMembers = nil
            await loadSecurity()
        } catch {
            statusMessage = StatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func deleteMemberGroup(_ group: Group) async {
        do {
            try await apiClient.deleteGroup(id: group.id)
            statusMessage = StatusMessage(text: "Group deleted.", isError: false)
            selectedGroupForMembers = nil
            await loadSecurity()
        } catch {
            statusMessage = StatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func createAPIKey() async {
        do {
            let response = try await apiClient.createAPIKey(label: newAPIKeyLabel.trimmingCharacters(in: .whitespacesAndNewlines))
            revealedAPIKey = response.key
            newAPIKeyLabel = ""
            await loadAPIKeys()
        } catch {
            statusMessage = StatusMessage(text: error.localizedDescription, isError: true)
        }
    }

    private func deleteAPIKey(_ key: APIKeySummary) async {
        try? await apiClient.deleteAPIKey(id: key.id)
        await loadAPIKeys()
    }

    private func passkeySubtitle(_ credential: PasskeyCredential) -> String {
        let username = credential.username ?? "User"
        let created = String((credential.createdAt ?? "").prefix(10))
        let signCount = credential.signCount ?? 0
        return "\(username) · \(created) · \(signCount) logins"
    }

    private func roleColor(_ role: String) -> Color {
        switch role {
        case "admin": return .orange
        case "user": return .blue
        default: return .gray
        }
    }

    private func logColor(_ level: String?) -> Color {
        switch level {
        case "error": return .red
        case "warn": return Color(red: 0.91, green: 0.77, blue: 0.28)
        case "success": return .green
        default: return .white.opacity(0.68)
        }
    }
}

private enum ProfileSection: String, CaseIterable, Identifiable {
    case general
    case security
    case preferences
    case notifications
    case apiKeys
    case mcp

    var id: String { rawValue }

    var translationKey: String {
        switch self {
        case .general: return "profile.menuGeneral"
        case .security: return "profile.menuSecurity"
        case .preferences: return "profile.menuPreferences"
        case .notifications: return "profile.menuNotifications"
        case .apiKeys: return "profile.menuAPIKeys"
        case .mcp: return "profile.menuMCP"
        }
    }

    var icon: String {
        switch self {
        case .general: return "person"
        case .security: return "shield"
        case .preferences: return "slider.horizontal.3"
        case .notifications: return "bell"
        case .apiKeys: return "key"
        case .mcp: return "terminal"
        }
    }
}

private struct ProfilePreferences {
    static let ratingCountries = ["US", "NL", "GB", "DE", "FR", "ES", "IT"]

    var showAutoVideos = true
    var showSearchButton = true
    var showLocalTitle = false
    var detailedActorDetails = false
    var ratingCountry = "US"

    init() {}

    init(values: [String: String]) {
        showAutoVideos = Self.bool(values["show_auto_videos"], defaultValue: true)
        showSearchButton = Self.bool(values["show_search_button"], defaultValue: true)
        showLocalTitle = Self.bool(values["show_local_title"], defaultValue: false)
        detailedActorDetails = Self.bool(values["detailed_actor"], defaultValue: false)
        ratingCountry = values["rating_country"]?.uppercased() ?? "US"
        if !Self.ratingCountries.contains(ratingCountry) {
            ratingCountry = "US"
        }
    }

    var dictionary: [String: String] {
        [
            "show_auto_videos": String(showAutoVideos),
            "show_search_button": String(showSearchButton),
            "show_local_title": String(showLocalTitle),
            "detailed_actor": String(detailedActorDetails),
            "rating_country": ratingCountry
        ]
    }

    private static func bool(_ value: String?, defaultValue: Bool) -> Bool {
        guard let value else { return defaultValue }
        return ["1", "true", "yes", "on"].contains(value.lowercased())
    }
}

private struct StatusMessage: Equatable {
    let text: String
    let isError: Bool
}

private struct SectionCard<Content: View>: View {
    var title: String?
    var icon: String?
    @ViewBuilder var content: Content

    init(title: String? = nil, icon: String? = nil, @ViewBuilder content: () -> Content) {
        self.title = title
        self.icon = icon
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let title {
                Label(title, systemImage: icon ?? "circle")
                    .font(.headline)
                    .foregroundStyle(.white)
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

private struct SafariView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        let controller = SFSafariViewController(url: url)
        controller.preferredBarTintColor = UIColor(red: 0.06, green: 0.06, blue: 0.14, alpha: 1.0)
        controller.preferredControlTintColor = UIColor(red: 0.91, green: 0.77, blue: 0.28, alpha: 1.0)
        return controller
    }

    func updateUIViewController(_ uiViewController: SFSafariViewController, context: Context) {}
}

private struct ProfileTextField: View {
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
            .clipShape(RoundedRectangle(cornerRadius: 10))
        }
    }
}

private struct PreferenceToggle: View {
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
            }
        }
        .tint(Color(red: 0.91, green: 0.77, blue: 0.28))
    }
}

private struct InfoRow: View {
    let icon: String
    let title: String
    let subtitle: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .foregroundStyle(Color(red: 0.91, green: 0.77, blue: 0.28))
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
        }
    }
}

private struct EmptyState: View {
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

private struct Badge: View {
    let text: String
    let color: Color

    var body: some View {
        Text(text)
            .font(.caption.weight(.semibold))
            .foregroundStyle(.white)
            .padding(.horizontal, 9)
            .padding(.vertical, 4)
            .background(color.opacity(0.22))
            .overlay(Capsule().strokeBorder(color.opacity(0.38)))
            .clipShape(Capsule())
    }
}

private struct MemberGroupRow: View {
    let group: Group
    let owned: Bool
    let onManage: () -> Void
    let onLeave: () -> Void
    let onDelete: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                Image(systemName: owned ? "crown.fill" : "folder.fill")
                    .foregroundStyle(owned ? Color(red: 0.91, green: 0.77, blue: 0.28) : .blue)
                    .frame(width: 28)
                VStack(alignment: .leading, spacing: 2) {
                    Text(group.name)
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(.white)
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.48))
                }
                Spacer()
            }

            HStack(spacing: 8) {
                if owned {
                    Button(action: onManage) {
                        Label("Members", systemImage: "person.2.fill")
                    }
                    .buttonStyle(.bordered)
                    .tint(Color(red: 0.91, green: 0.77, blue: 0.28))

                    Button(role: .destructive, action: onDelete) {
                        Label("Delete", systemImage: "trash")
                    }
                    .buttonStyle(.bordered)
                    .tint(.red)
                } else {
                    Button(action: onLeave) {
                        Label("Leave", systemImage: "rectangle.portrait.and.arrow.right")
                    }
                    .buttonStyle(.bordered)
                    .tint(.blue)
                }
            }
            .font(.caption.weight(.semibold))
        }
        .padding(12)
        .background(.white.opacity(0.05))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var subtitle: String {
        if owned {
            return "\(group.memberCount ?? 0) member\((group.memberCount ?? 0) == 1 ? "" : "s") · \(group.movieCount ?? 0) movies"
        }
        return "Owner: \(group.createdByUsername ?? "?") · \(group.movieCount ?? 0) movies"
    }
}

private struct MemberRow: View {
    let member: GroupMember
    let canRemove: Bool
    let onRemove: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: member.groupRole == "owner" ? "crown.fill" : "person.fill")
                .foregroundStyle(member.groupRole == "owner" ? Color(red: 0.91, green: 0.77, blue: 0.28) : .white.opacity(0.58))
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 2) {
                Text(member.displayName?.isEmpty == false ? member.displayName! : member.username)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.white)
                Text(member.groupRole ?? "member")
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.42))
            }
            Spacer()
            if canRemove {
                Button(role: .destructive, action: onRemove) {
                    Image(systemName: "trash")
                }
                .buttonStyle(.borderless)
            }
        }
        .padding(10)
        .background(.white.opacity(0.05))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

#Preview {
    ProfileView()
        .environmentObject(AppStateManager())
        .environment(AppStateManager().apiClient)
        .preferredColorScheme(.dark)
}
