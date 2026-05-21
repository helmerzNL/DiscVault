import SwiftUI

struct CollectionView: View {
    @Environment(APIClient.self) private var apiClient
    @EnvironmentObject private var appState: AppStateManager

    @State private var viewModel: CollectionViewModel?
    @State private var showFilters = false
    @State private var showAddSheet = false
    @State private var showScanner = false
    @State private var manualBarcode: String = ""
    @State private var isLookingUp = false
    @State private var lookupError: String? = nil

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
                    ProgressView().tint(.white)
                }
            }
            .navigationTitle("Collection")
            .navigationBarTitleDisplayMode(.large)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar { toolbarItems }
            .searchable(
                text: Binding(
                    get: { viewModel?.searchText ?? "" },
                    set: { viewModel?.searchText = $0 }
                ),
                prompt: "Search titles, directors\u{2026}"
            )
            .sheet(isPresented: $showFilters) {
                if let vm = viewModel { FilterSheet(viewModel: vm) }
            }
            .sheet(isPresented: $showAddSheet) {
                addSheet
            }
            .fullScreenCover(isPresented: $showScanner) {
                BarcodeScannerView { barcode in
                    showScanner = false
                    showAddSheet = false
                    if let vm = viewModel {
                        Task { await addByBarcode(barcode, vm: vm) }
                    }
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
    }

    // MARK: - Content

    @ViewBuilder
    private func content(vm: CollectionViewModel) -> some View {
        if vm.isLoading && vm.movies.isEmpty {
            loadingView
        } else if vm.filteredMovies.isEmpty {
            emptyState(vm: vm)
        } else {
            ScrollView {
                statsBar(vm: vm)
                LazyVGrid(columns: columns, spacing: 10) {
                    ForEach(vm.filteredMovies) { movie in
                        NavigationLink {
                            MovieDetailView(movie: movie)
                        } label: {
                            MovieCardView(movie: movie, apiClient: apiClient)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 12)
                .padding(.bottom, 24)
            }
            .refreshable { await vm.loadMovies() }
        }
    }

    private var loadingView: some View {
        VStack(spacing: 16) {
            ProgressView().tint(.white).scaleEffect(1.3)
            Text("Loading collection\u{2026}")
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.5))
        }
    }

    private func emptyState(vm: CollectionViewModel) -> some View {
        VStack(spacing: 20) {
            Image(systemName: vm.searchText.isEmpty ? "opticaldisc" : "magnifyingglass")
                .font(.system(size: 56))
                .foregroundStyle(.white.opacity(0.2))
            Text(vm.searchText.isEmpty
                 ? "Your collection is empty"
                 : "No results for \"\(vm.searchText)\"")
                .font(.title3.weight(.medium))
                .foregroundStyle(.white.opacity(0.5))
            if vm.searchText.isEmpty {
                Button {
                    showAddSheet = true
                } label: {
                    Label("Add your first disc", systemImage: "plus.circle.fill")
                }
                .buttonStyle(.borderedProminent)
                .tint(Color(red: 0.45, green: 0.2, blue: 0.95))
            }
        }
    }

    private func statsBar(vm: CollectionViewModel) -> some View {
        HStack(spacing: 16) {
            StatChip(value: vm.stats.totalMovies, label: "Movies", color: .white)
            StatChip(value: vm.stats.total4K,     label: "4K",     color: Color(red: 0.6, green: 0.3, blue: 1.0))
            StatChip(value: vm.stats.totalBluray, label: "BD",     color: Color(red: 0.3, green: 0.5, blue: 1.0))
            StatChip(value: vm.stats.totalDVD,    label: "DVD",    color: .gray)
            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
    }

    // MARK: - Add Sheet

    private var addSheet: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.06, green: 0.06, blue: 0.14).ignoresSafeArea()
                VStack(spacing: 20) {
                    Button {
                        showScanner = true
                    } label: {
                        AddOptionRow(
                            icon: "barcode.viewfinder",
                            color: Color(red: 0.45, green: 0.2, blue: 0.95),
                            title: "Scan Barcode",
                            description: "Point your camera at the disc case barcode"
                        )
                    }
                    .buttonStyle(.plain)

                    VStack(alignment: .leading, spacing: 10) {
                        AddOptionRow(
                            icon: "keyboard",
                            color: Color(red: 0.2, green: 0.5, blue: 0.9),
                            title: "Enter Barcode Manually",
                            description: "Type the EAN-13 or UPC-A barcode number"
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
                                // ZStack instead of Group so .frame() applies correctly
                                ZStack {
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
                            Text(err).font(.caption).foregroundStyle(.red)
                        }
                    }
                    Spacer()
                }
                .padding(24)
            }
            .navigationTitle("Add Disc")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { showAddSheet = false }.foregroundStyle(.white)
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    // MARK: - Toolbar

    @ToolbarContentBuilder
    private var toolbarItems: some ToolbarContent {
        ToolbarItem(placement: .topBarTrailing) {
            Button { showFilters = true } label: {
                Image(systemName: "line.3.horizontal.decrease.circle").foregroundStyle(.white)
            }
        }
        ToolbarItem(placement: .topBarTrailing) {
            Button { showAddSheet = true } label: {
                Image(systemName: "plus").foregroundStyle(.white)
            }
        }
    }

    // MARK: - Actions

    private func addByBarcode(_ barcode: String, vm: CollectionViewModel) async {
        guard !barcode.isEmpty else { return }
        isLookingUp = true; lookupError = nil
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

// MARK: - Filter Sheet

private struct FilterSheet: View {
    @Bindable var viewModel: CollectionViewModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section("Format") { formatPicker }
                    .listRowBackground(Color.white.opacity(0.06))
                Section("Show") {
                    Toggle("Wanted only", isOn: $viewModel.showWantedOnly)
                }
                .listRowBackground(Color.white.opacity(0.06))
                Section("Sort By") { sortPicker }
                    .listRowBackground(Color.white.opacity(0.06))
            }
            .scrollContentBackground(.hidden)
            .background(Color(red: 0.06, green: 0.06, blue: 0.14))
            .navigationTitle("Filter & Sort")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private var formatPicker: some View {
        VStack(spacing: 0) {
            FormatRow(label: "All Formats", selected: viewModel.selectedFormat == nil) {
                viewModel.selectedFormat = nil
            }
            FormatRow(label: "4K UHD", selected: viewModel.selectedFormat == "4K UHD") {
                viewModel.selectedFormat = "4K UHD"
            }
            FormatRow(label: "Blu-ray", selected: viewModel.selectedFormat == "Blu-ray") {
                viewModel.selectedFormat = "Blu-ray"
            }
            FormatRow(label: "DVD", selected: viewModel.selectedFormat == "DVD") {
                viewModel.selectedFormat = "DVD"
            }
        }
    }

    private var sortPicker: some View {
        ForEach(SortOrder.allCases, id: \.self) { order in
            Button {
                viewModel.sortOrder = order
            } label: {
                HStack {
                    Text(order.rawValue).foregroundStyle(.primary)
                    Spacer()
                    if viewModel.sortOrder == order {
                        Image(systemName: "checkmark").foregroundStyle(.blue)
                    }
                }
            }
        }
    }
}

private struct FormatRow: View {
    let label: String
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack {
                Text(label).foregroundStyle(.primary)
                Spacer()
                if selected { Image(systemName: "checkmark").foregroundStyle(.blue) }
            }
        }
    }
}

// MARK: - Helpers

private struct StatChip: View {
    let value: Int; let label: String; let color: Color
    var body: some View {
        HStack(spacing: 4) {
            Text("\(value)").font(.caption.weight(.bold)).foregroundStyle(color)
            Text(label).font(.caption).foregroundStyle(.white.opacity(0.45))
        }
    }
}

private struct AddOptionRow: View {
    let icon: String; let color: Color; let title: String; let description: String
    var body: some View {
        HStack(spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 10).fill(color.opacity(0.15)).frame(width: 44, height: 44)
                Image(systemName: icon).font(.system(size: 20)).foregroundStyle(color)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(.white)
                Text(description).font(.caption).foregroundStyle(.white.opacity(0.45))
            }
            Spacer()
            Image(systemName: "chevron.right").font(.caption).foregroundStyle(.white.opacity(0.3))
        }
        .padding(14)
        .background(.white.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }
}
