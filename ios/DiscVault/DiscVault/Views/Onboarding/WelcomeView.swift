import SwiftUI

struct WelcomeView: View {
    @State private var showServerSetup = false
    @State private var animateIn = false

    var body: some View {
        ZStack {
            background

            VStack(spacing: 0) {
                Spacer()

                logoSection
                    .offset(y: animateIn ? 0 : 40)
                    .opacity(animateIn ? 1 : 0)

                Spacer()

                featuresSection
                    .offset(y: animateIn ? 0 : 30)
                    .opacity(animateIn ? 1 : 0)

                Spacer()

                getStartedButton
                    .offset(y: animateIn ? 0 : 20)
                    .opacity(animateIn ? 1 : 0)

                Text("Self-hosted · Private · Yours")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.4))
                    .padding(.top, 16)
                    .padding(.bottom, 48)
                    .opacity(animateIn ? 1 : 0)
            }
            .padding(.horizontal, 32)
        }
        .onAppear {
            withAnimation(.spring(duration: 0.8, bounce: 0.3).delay(0.1)) {
                animateIn = true
            }
        }
        .fullScreenCover(isPresented: $showServerSetup) {
            ServerSetupView()
        }
    }

    private var background: some View {
        ZStack {
            Color(red: 0.04, green: 0.04, blue: 0.12)
                .ignoresSafeArea()

            // Ambient glows
            Circle()
                .fill(
                    RadialGradient(
                        colors: [Color(red: 0.3, green: 0.1, blue: 0.8).opacity(0.35), .clear],
                        center: .center,
                        startRadius: 0,
                        endRadius: 300
                    )
                )
                .frame(width: 500, height: 500)
                .offset(x: -100, y: -250)

            Circle()
                .fill(
                    RadialGradient(
                        colors: [Color(red: 0.1, green: 0.3, blue: 0.9).opacity(0.25), .clear],
                        center: .center,
                        startRadius: 0,
                        endRadius: 250
                    )
                )
                .frame(width: 400, height: 400)
                .offset(x: 150, y: 200)
        }
    }

    private var logoSection: some View {
        VStack(spacing: 24) {
            ZStack {
                Circle()
                    .fill(
                        LinearGradient(
                            colors: [
                                Color(red: 0.45, green: 0.2, blue: 0.95),
                                Color(red: 0.2, green: 0.45, blue: 0.95)
                            ],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .frame(width: 100, height: 100)
                    .shadow(color: Color(red: 0.45, green: 0.2, blue: 0.95).opacity(0.5), radius: 30)

                Image(systemName: "opticaldisc.fill")
                    .font(.system(size: 48))
                    .foregroundStyle(.white)
            }

            VStack(spacing: 8) {
                Text("DiscVault")
                    .font(.system(size: 42, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)

                Text("Your physical media collection")
                    .font(.title3)
                    .foregroundStyle(.white.opacity(0.6))
                    .multilineTextAlignment(.center)
            }
        }
    }

    private var featuresSection: some View {
        VStack(spacing: 16) {
            FeatureRow(
                icon: "opticaldisc",
                color: Color(red: 0.6, green: 0.2, blue: 1.0),
                title: "Catalog Everything",
                description: "4K UHD, Blu-ray & DVD in one place"
            )
            FeatureRow(
                icon: "barcode.viewfinder",
                color: Color(red: 0.2, green: 0.6, blue: 1.0),
                title: "Scan to Add",
                description: "Instant metadata from any barcode"
            )
            FeatureRow(
                icon: "person.2.fill",
                color: Color(red: 0.2, green: 0.8, blue: 0.6),
                title: "Share & Collaborate",
                description: "Create groups and share collections"
            )
        }
        .padding(.horizontal, 8)
    }

    private var getStartedButton: some View {
        Button {
            showServerSetup = true
        } label: {
            HStack(spacing: 12) {
                Text("Get Started")
                    .font(.headline)
                Image(systemName: "arrow.right")
                    .font(.headline)
            }
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 18)
            .background(
                LinearGradient(
                    colors: [
                        Color(red: 0.45, green: 0.2, blue: 0.95),
                        Color(red: 0.2, green: 0.45, blue: 0.95)
                    ],
                    startPoint: .leading,
                    endPoint: .trailing
                )
            )
            .clipShape(RoundedRectangle(cornerRadius: 16))
            .shadow(color: Color(red: 0.35, green: 0.2, blue: 0.8).opacity(0.5), radius: 20)
        }
    }
}

private struct FeatureRow: View {
    let icon: String
    let color: Color
    let title: String
    let description: String

    var body: some View {
        HStack(spacing: 16) {
            ZStack {
                RoundedRectangle(cornerRadius: 12)
                    .fill(color.opacity(0.15))
                    .frame(width: 48, height: 48)

                Image(systemName: icon)
                    .font(.system(size: 22))
                    .foregroundStyle(color)
            }

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)

                Text(description)
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.5))
            }

            Spacer()
        }
        .padding(16)
        .background(.white.opacity(0.05))
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }
}

#Preview {
    WelcomeView()
}
