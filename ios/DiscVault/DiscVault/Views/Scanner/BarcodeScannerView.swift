import SwiftUI
@preconcurrency import AVFoundation

struct BarcodeScannerView: View {
    var onScan: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var cameraPermission: CameraPermission = .unknown
    @State private var torchOn = false
    @State private var scannedCode: String? = nil

    enum CameraPermission { case unknown, granted, denied }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            switch cameraPermission {
            case .unknown:
                ProgressView().tint(.white)
            case .denied:
                permissionDeniedView
            case .granted:
                cameraView
            }
        }
        .onAppear { checkCameraPermission() }
        .preferredColorScheme(.dark)
    }

    // MARK: - Camera View

    private var cameraView: some View {
        ZStack {
            CameraPreview(torchOn: $torchOn) { code in
                guard scannedCode == nil else { return }
                scannedCode = code
                let generator = UINotificationFeedbackGenerator()
                generator.notificationOccurred(.success)
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                    onScan(code)
                }
            }
            .ignoresSafeArea()

            scanOverlay
        }
    }

    private var scanOverlay: some View {
        GeometryReader { geo in
            let scanSize: CGFloat = min(geo.size.width * 0.7, 280)
            let scanRect = CGRect(
                x: (geo.size.width - scanSize) / 2,
                y: (geo.size.height - scanSize * 0.5) / 2,
                width: scanSize,
                height: scanSize * 0.5
            )

            ZStack {
                // Dimmed overlay with cutout
                Rectangle()
                    .fill(.black.opacity(0.55))
                    .mask(
                        Rectangle()
                            .overlay(
                                RoundedRectangle(cornerRadius: 12)
                                    .frame(width: scanRect.width, height: scanRect.height)
                                    .blendMode(.destinationOut)
                            )
                            .compositingGroup()
                    )
                    .ignoresSafeArea()

                // Corner markers
                CornerMarkers(rect: scanRect)

                // Instructions
                VStack {
                    Spacer()
                        .frame(height: scanRect.maxY + 24)

                    Text(scannedCode != nil ? "Barcode detected!" : "Align barcode within the frame")
                        .font(.subheadline)
                        .foregroundStyle(.white)
                        .shadow(radius: 4)

                    Spacer()
                }

                // Top controls
                VStack {
                    HStack {
                        Button {
                            dismiss()
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .font(.system(size: 32))
                                .foregroundStyle(.white.opacity(0.8))
                                .shadow(radius: 4)
                        }

                        Spacer()

                        Button {
                            torchOn.toggle()
                        } label: {
                            Image(systemName: torchOn ? "bolt.fill" : "bolt.slash.fill")
                                .font(.system(size: 28))
                                .foregroundStyle(torchOn ? .yellow : .white.opacity(0.7))
                                .shadow(radius: 4)
                        }
                    }
                    .padding(.horizontal, 24)
                    .padding(.top, 60)

                    Spacer()
                }
            }
        }
    }

    // MARK: - Permission Denied

    private var permissionDeniedView: some View {
        VStack(spacing: 20) {
            Image(systemName: "camera.fill")
                .font(.system(size: 56))
                .foregroundStyle(.white.opacity(0.3))

            Text("Camera Access Required")
                .font(.title3.bold())
                .foregroundStyle(.white)

            Text("DiscVault needs camera access to scan barcodes. Please enable it in Settings.")
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.6))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)

            Button {
                if let url = URL(string: UIApplication.openSettingsURLString) {
                    UIApplication.shared.open(url)
                }
            } label: {
                Label("Open Settings", systemImage: "gear")
                    .foregroundStyle(.white)
                    .padding(.horizontal, 24)
                    .padding(.vertical, 12)
                    .background(Color(red: 0.45, green: 0.2, blue: 0.95))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }

            Button("Cancel") { dismiss() }
                .foregroundStyle(.white.opacity(0.5))
        }
    }

    // MARK: - Permission Check

    private func checkCameraPermission() {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            cameraPermission = .granted
        case .denied, .restricted:
            cameraPermission = .denied
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { granted in
                DispatchQueue.main.async {
                    cameraPermission = granted ? .granted : .denied
                }
            }
        @unknown default:
            cameraPermission = .denied
        }
    }
}

// MARK: - Camera Preview

private struct CameraPreview: UIViewRepresentable {
    @Binding var torchOn: Bool
    var onScan: (String) -> Void

    func makeUIView(context: Context) -> CameraView {
        let view = CameraView()
        view.onScan = onScan
        view.setupCamera()
        return view
    }

    func updateUIView(_ uiView: CameraView, context: Context) {
        uiView.setTorch(on: torchOn)
    }
}

private class CameraView: UIView {
    var onScan: ((String) -> Void)? {
        didSet {
            metadataDelegate.onScan = onScan
        }
    }
    private var captureSession: AVCaptureSession?
    private var previewLayer: AVCaptureVideoPreviewLayer?
    private let metadataDelegate = MetadataDelegate()

    override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }

    var videoPreviewLayer: AVCaptureVideoPreviewLayer {
        layer as! AVCaptureVideoPreviewLayer
    }

    func setupCamera() {
        let session = AVCaptureSession()
        captureSession = session

        guard let device = AVCaptureDevice.default(for: .video),
              let input = try? AVCaptureDeviceInput(device: device),
              session.canAddInput(input) else { return }

        session.addInput(input)

        let output = AVCaptureMetadataOutput()
        guard session.canAddOutput(output) else { return }
        session.addOutput(output)
        output.setMetadataObjectsDelegate(metadataDelegate, queue: .main)
        output.metadataObjectTypes = [.ean13, .upce, .ean8, .code128]

        videoPreviewLayer.session = session
        videoPreviewLayer.videoGravity = .resizeAspectFill

        DispatchQueue.global(qos: .userInitiated).async {
            session.startRunning()
        }
    }

    func setTorch(on: Bool) {
        guard let device = AVCaptureDevice.default(for: .video),
              device.hasTorch else { return }
        try? device.lockForConfiguration()
        device.torchMode = on ? .on : .off
        device.unlockForConfiguration()
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        if window == nil {
            captureSession?.stopRunning()
        }
    }
}

private final class MetadataDelegate: NSObject, AVCaptureMetadataOutputObjectsDelegate {
    var onScan: ((String) -> Void)?

    func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput objects: [AVMetadataObject], from connection: AVCaptureConnection) {
        guard let obj = objects.first as? AVMetadataMachineReadableCodeObject,
              let code = obj.stringValue else { return }
        onScan?(code)
    }
}

// MARK: - Corner Markers

private struct CornerMarkers: View {
    let rect: CGRect

    var body: some View {
        ZStack {
            corner(at: CGPoint(x: rect.minX, y: rect.minY), rotation: 0)
            corner(at: CGPoint(x: rect.maxX, y: rect.minY), rotation: 90)
            corner(at: CGPoint(x: rect.maxX, y: rect.maxY), rotation: 180)
            corner(at: CGPoint(x: rect.minX, y: rect.maxY), rotation: 270)
        }
    }

    private func corner(at point: CGPoint, rotation: Double) -> some View {
        let len: CGFloat = 22
        let thick: CGFloat = 3

        return ZStack {
            Rectangle()
                .fill(Color.white)
                .frame(width: len, height: thick)
                .offset(x: len / 2, y: 0)
            Rectangle()
                .fill(Color.white)
                .frame(width: thick, height: len)
                .offset(x: 0, y: len / 2)
        }
        .rotationEffect(.degrees(rotation))
        .position(point)
    }
}
