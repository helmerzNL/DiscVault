import AuthenticationServices
import Foundation
import UIKit

@MainActor
final class PasskeyAuthenticator: NSObject {
    private var continuation: CheckedContinuation<ASAuthorizationPlatformPublicKeyCredentialAssertion, Error>?

    func requestAssertion(options: PasskeyLoginOptions) async throws -> PasskeyAssertionCredential {
        guard let challenge = Data(base64URLEncoded: options.challenge) else {
            throw PasskeyError.invalidChallenge
        }

        let provider = ASAuthorizationPlatformPublicKeyCredentialProvider(relyingPartyIdentifier: options.rpId)
        let request = provider.createCredentialAssertionRequest(challenge: challenge)
        if let allowCredentials = options.allowCredentials, !allowCredentials.isEmpty {
            request.allowedCredentials = allowCredentials.compactMap { credential in
                guard let credentialID = Data(base64URLEncoded: credential.id) else { return nil }
                return ASAuthorizationPlatformPublicKeyCredentialDescriptor(credentialID: credentialID)
            }
        }

        let assertion = try await withCheckedThrowingContinuation { continuation in
            self.continuation = continuation
            let controller = ASAuthorizationController(authorizationRequests: [request])
            controller.delegate = self
            controller.presentationContextProvider = self
            controller.performRequests()
        }

        return PasskeyAssertionCredential(assertion: assertion)
    }
}

extension PasskeyAuthenticator: ASAuthorizationControllerDelegate {
    nonisolated func authorizationController(controller: ASAuthorizationController, didCompleteWithAuthorization authorization: ASAuthorization) {
        Task { @MainActor in
            guard let assertion = authorization.credential as? ASAuthorizationPlatformPublicKeyCredentialAssertion else {
                continuation?.resume(throwing: PasskeyError.invalidCredential)
                continuation = nil
                return
            }

            continuation?.resume(returning: assertion)
            continuation = nil
        }
    }

    nonisolated func authorizationController(controller: ASAuthorizationController, didCompleteWithError error: Error) {
        Task { @MainActor in
            continuation?.resume(throwing: error)
            continuation = nil
        }
    }
}

extension PasskeyAuthenticator: ASAuthorizationControllerPresentationContextProviding {
    nonisolated func presentationAnchor(for controller: ASAuthorizationController) -> ASPresentationAnchor {
        MainActor.assumeIsolated {
            UIApplication.shared.connectedScenes
                .compactMap { $0 as? UIWindowScene }
                .flatMap(\.windows)
                .first { $0.isKeyWindow } ?? ASPresentationAnchor()
        }
    }
}

enum PasskeyError: LocalizedError {
    case invalidChallenge
    case invalidCredential

    var errorDescription: String? {
        switch self {
        case .invalidChallenge:
            return "The server returned an invalid passkey challenge."
        case .invalidCredential:
            return "The selected passkey could not be used."
        }
    }
}

extension PasskeyAssertionCredential {
    init(assertion: ASAuthorizationPlatformPublicKeyCredentialAssertion) {
        self.init(
            id: assertion.credentialID.base64URLEncodedString(),
            rawId: assertion.credentialID.base64URLEncodedString(),
            response: PasskeyAssertionResponse(
                authenticatorData: assertion.rawAuthenticatorData.base64URLEncodedString(),
                clientDataJSON: assertion.rawClientDataJSON.base64URLEncodedString(),
                signature: assertion.signature.base64URLEncodedString(),
                userHandle: assertion.userID.base64URLEncodedString()
            ),
            type: "public-key",
            authenticatorAttachment: nil
        )
    }
}

extension Data {
    init?(base64URLEncoded string: String) {
        var base64 = string
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        let padding = base64.count % 4
        if padding > 0 {
            base64.append(String(repeating: "=", count: 4 - padding))
        }
        self.init(base64Encoded: base64)
    }

    func base64URLEncodedString() -> String {
        base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
