import SwiftUI

struct AddView: View {
    @Environment(APIClient.self) private var apiClient
    @Environment(AppLanguageManager.self) private var languageManager

    @State private var selectedSection: AddSection = .scan
    @State private var draft = AddMovieDraft()
    @State private var isLookingUp = false
    @State private var isSaving = false
    @State private var statusMessage: AddStatusMessage?
    @State private var showScanner = false

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.06, green: 0.06, blue: 0.14).ignoresSafeArea()

                ScrollView {
                    VStack(spacing: 18) {
                        sectionPicker
                        selectedSectionView
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 12)
                    .padding(.bottom, 28)
                }
            }
            .navigationTitle(languageManager.text("nav.add"))
            .toolbarColorScheme(.dark, for: .navigationBar)
            .fullScreenCover(isPresented: $showScanner) {
                BarcodeScannerView { barcode in
                    showScanner = false
                    Task { await lookupBarcode(barcode) }
                }
            }
        }
    }

    private var sectionPicker: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(AddSection.allCases) { section in
                    Button {
                        selectedSection = section
                    } label: {
                        Label(languageManager.text(section.translationKey), systemImage: section.icon)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(selectedSection == section ? .black : .white.opacity(0.68))
                            .padding(.horizontal, 12)
                            .frame(height: 36)
                            .background(selectedSection == section ? AddTheme.accent : .white.opacity(0.07))
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
        case .scan:
            scanSection
        case .manual:
            manualSection
        case .importCollection:
            importSection
        }
    }

    private var scanSection: some View {
        VStack(spacing: 14) {
            AddCard(title: languageManager.text("toevoegen.scan"), icon: "barcode.viewfinder") {
                VStack(spacing: 14) {
                    Button {
                        showScanner = true
                    } label: {
                        Label(languageManager.text("scan.startCamera"), systemImage: "camera.viewfinder")
                            .font(.headline)
                            .foregroundStyle(.black)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                            .background(AddTheme.accent)
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain)

                    Divider().overlay(.white.opacity(0.12))

                    VStack(alignment: .leading, spacing: 10) {
                        Text(languageManager.text("scan.manualLabel"))
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.white.opacity(0.56))

                        HStack(spacing: 10) {
                            TextField("e.g. 5051892198103", text: $draft.barcode)
                                .textFieldStyle(.plain)
                                .keyboardType(.numberPad)
                                .foregroundStyle(.white)
                                .tint(.white)
                                .padding(12)
                                .background(.white.opacity(0.07))
                                .clipShape(RoundedRectangle(cornerRadius: 8))

                            Button {
                                Task { await lookupBarcode(draft.barcode) }
                            } label: {
                                if isLookingUp {
                                    ProgressView().tint(.white)
                                } else {
                                    Image(systemName: "magnifyingglass")
                                        .foregroundStyle(.white)
                                }
                            }
                            .frame(width: 44, height: 44)
                            .background(Color.blue)
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                            .disabled(draft.barcode.trimmingCharacters(in: .whitespacesAndNewlines).count < 6 || isLookingUp)
                        }
                    }
                }
            }

            if !draft.title.isEmpty {
                reviewCard
            }

            statusView
        }
    }

    private var manualSection: some View {
        VStack(spacing: 14) {
            AddCard(title: languageManager.text("add.title"), icon: "pencil.and.list.clipboard") {
                VStack(spacing: 12) {
                    AddTextField(title: languageManager.text("add.barcodeLabel"), text: $draft.barcode, systemImage: "barcode")
                    HStack(spacing: 10) {
                        AddTextField(title: languageManager.text("add.titleLabel"), text: $draft.title, systemImage: "film")
                        Button {
                            Task { await lookupTitle(draft.title) }
                        } label: {
                            Image(systemName: isLookingUp ? "hourglass" : "magnifyingglass")
                                .foregroundStyle(.white)
                                .frame(width: 42, height: 42)
                                .background(Color.blue)
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                        .disabled(draft.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isLookingUp)
                    }

                    Picker("Format", selection: $draft.format) {
                        Text("4K UHD").tag("4K UHD")
                        Text("Blu-ray").tag("Blu-ray")
                        Text("DVD").tag("DVD")
                    }
                    .pickerStyle(.segmented)

                    AddTextField(title: languageManager.text("edit.originalTitle"), text: $draft.originalTitle, systemImage: "textformat")
                    HStack(spacing: 10) {
                        AddTextField(title: languageManager.text("edit.year"), text: $draft.year, systemImage: "calendar")
                        AddTextField(title: languageManager.text("edit.runtime"), text: $draft.runtime, systemImage: "clock")
                    }
                    AddTextField(title: languageManager.text("edit.director"), text: $draft.director, systemImage: "megaphone")
                    AddTextField(title: languageManager.text("edit.actors"), text: $draft.actor, systemImage: "person.2")
                    AddTextField(title: languageManager.text("edit.genre"), text: $draft.genre, systemImage: "tag")
                    AddTextField(title: languageManager.text("edit.hdr"), text: $draft.hdr, systemImage: "sparkles.tv")
                    AddTextField(title: languageManager.text("edit.language"), text: $draft.language, systemImage: "globe")
                    AddTextField(title: languageManager.text("edit.audioTracks"), text: $draft.audioTracks, systemImage: "speaker.wave.3")
                    AddTextField(title: languageManager.text("edit.subtitles"), text: $draft.subtitles, systemImage: "captions.bubble")
                    AddTextField(title: languageManager.text("edit.location"), text: $draft.location, systemImage: "mappin.and.ellipse")
                    AddTextEditor(title: languageManager.text("edit.plot"), text: $draft.plot)
                    AddTextEditor(title: languageManager.text("edit.notes"), text: $draft.notes)

                    Button {
                        Task { await saveDraft() }
                    } label: {
                        HStack(spacing: 10) {
                            if isSaving {
                                ProgressView().tint(.black).scaleEffect(0.8)
                            } else {
                                Image(systemName: "checkmark.circle.fill")
                            }
                            Text(isSaving ? "Saving" : languageManager.text("add.submit"))
                                .font(.headline)
                        }
                        .foregroundStyle(.black)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(AddTheme.accent)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain)
                    .disabled(draft.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isSaving)

                    Button(languageManager.text("add.clear")) { draft = AddMovieDraft() }
                        .buttonStyle(.bordered)
                        .tint(.white)
                }
            }

            statusView
        }
    }

    private var importSection: some View {
        AddCard(title: languageManager.text("import.title"), icon: "tray.and.arrow.down.fill") {
            VStack(spacing: 14) {
                AddInfoRow(
                    icon: "doc.badge.plus",
                    title: "CSV / XML import",
                    subtitle: "The PWA import flow supports CLZ Movies, iCollect, Blu-ray.com and custom spreadsheets. Native import needs a document picker and field-mapping screen, so this tab is reserved for that workflow."
                )
                AddInfoRow(
                    icon: "arrow.triangle.2.circlepath",
                    title: "Current option",
                    subtitle: "Use the web app import screen for bulk imports until native file import is implemented."
                )
            }
        }
    }

    private var reviewCard: some View {
        AddCard(title: "Movie Information", icon: "film.stack") {
            VStack(spacing: 12) {
                AddInfoRow(icon: "film", title: draft.title, subtitle: [draft.year, draft.format].filter { !$0.isEmpty }.joined(separator: " · "))
                if !draft.director.isEmpty {
                    AddInfoRow(icon: "megaphone", title: "Director", subtitle: draft.director)
                }
                if !draft.plot.isEmpty {
                    Text(draft.plot)
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.54))
                        .fixedSize(horizontal: false, vertical: true)
                }

                Button {
                    Task { await saveDraft() }
                } label: {
                    Label(isSaving ? "Saving" : "Save Movie", systemImage: "checkmark.circle.fill")
                        .font(.headline)
                        .foregroundStyle(.black)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(AddTheme.accent)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .disabled(isSaving)

                Button("Edit Details") { selectedSection = .manual }
                    .buttonStyle(.bordered)
                    .tint(.white)
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

    private func lookupBarcode(_ barcode: String) async {
        let value = barcode.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return }
        isLookingUp = true
        statusMessage = AddStatusMessage(text: "Looking up barcode...", isError: false)
        do {
            let response = try await apiClient.lookupBarcodeForAdd(value)
            handleLookupResponse(response, fallbackBarcode: value)
        } catch {
            statusMessage = AddStatusMessage(text: error.localizedDescription, isError: true)
        }
        isLookingUp = false
    }

    private func lookupTitle(_ title: String) async {
        let value = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return }
        isLookingUp = true
        statusMessage = AddStatusMessage(text: "Looking up title...", isError: false)
        do {
            let response = try await apiClient.lookupTitleForAdd(value)
            handleLookupResponse(response, fallbackBarcode: draft.barcode)
        } catch {
            statusMessage = AddStatusMessage(text: error.localizedDescription, isError: true)
        }
        isLookingUp = false
    }

    private func handleLookupResponse(_ response: LookupResponse, fallbackBarcode: String) {
        if response.status == "exists", let movie = response.movie {
            draft = AddMovieDraft(lookupMovie: movie, barcode: fallbackBarcode)
            statusMessage = AddStatusMessage(text: "This movie is already in your collection.", isError: false)
            return
        }

        guard response.status == "found", let movie = response.movie else {
            statusMessage = AddStatusMessage(text: response.error ?? "No movie found.", isError: true)
            return
        }

        draft = AddMovieDraft(lookupMovie: movie, barcode: fallbackBarcode)
        if draft.format.isEmpty, let detected = response.detectedFormat, !detected.isEmpty {
            draft.format = detected
        }
        statusMessage = AddStatusMessage(text: "Found \(draft.title). Review and save it.", isError: false)
    }

    private func saveDraft() async {
        let title = draft.title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return }
        isSaving = true
        statusMessage = nil
        do {
            var payload = draft
            if payload.barcode.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                payload.barcode = generatedBarcode(for: title)
            }
            _ = try await apiClient.createMovie(payload)
            draft = AddMovieDraft()
            statusMessage = AddStatusMessage(text: "Movie added to your collection.", isError: false)
        } catch {
            statusMessage = AddStatusMessage(text: error.localizedDescription, isError: true)
        }
        isSaving = false
    }

    private func generatedBarcode(for title: String) -> String {
        let safe = title.uppercased().filter { $0.isLetter || $0.isNumber }.prefix(30)
        return "TITLE-\(safe)-\(Int(Date().timeIntervalSince1970) % 1_000_000)"
    }
}

private enum AddSection: String, CaseIterable, Identifiable {
    case scan
    case manual
    case importCollection

    var id: String { rawValue }

    var title: String {
        switch self {
        case .scan: "Barcode"
        case .manual: "Manual"
        case .importCollection: "Import"
        }
    }

    var icon: String {
        switch self {
        case .scan: "barcode.viewfinder"
        case .manual: "pencil.and.list.clipboard"
        case .importCollection: "tray.and.arrow.down"
        }
    }

    var translationKey: String {
        switch self {
        case .scan: "toevoegen.scan"
        case .manual: "toevoegen.manual"
        case .importCollection: "toevoegen.import"
        }
    }
}

private struct AddStatusMessage: Identifiable {
    let id = UUID()
    let text: String
    let isError: Bool
}

private enum AddTheme {
    static let accent = Color(red: 0.91, green: 0.77, blue: 0.28)
}

private struct AddCard<Content: View>: View {
    let title: String
    let icon: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label(title, systemImage: icon)
                .font(.headline)
                .foregroundStyle(.white)
                .labelStyle(.titleAndIcon)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(.white.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.white.opacity(0.08)))
    }
}

private struct AddTextField: View {
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
            }
            .padding(12)
            .background(.white.opacity(0.06))
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }
}

private struct AddTextEditor: View {
    let title: String
    @Binding var text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.white.opacity(0.6))
            TextEditor(text: $text)
                .frame(minHeight: 82)
                .scrollContentBackground(.hidden)
                .foregroundStyle(.white)
                .tint(.white)
                .padding(8)
                .background(.white.opacity(0.06))
                .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }
}

private struct AddInfoRow: View {
    let icon: String
    let title: String
    let subtitle: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .foregroundStyle(AddTheme.accent)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 3) {
                Text(title.isEmpty ? "-" : title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                if !subtitle.isEmpty {
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.48))
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
    }
}

#Preview {
    AddView()
        .environment(AppStateManager().apiClient)
        .preferredColorScheme(.dark)
}
