import Foundation

@MainActor
@Observable
final class AppLanguageManager {
    static let storageKey = "discvault.appLanguage"

    var languageCode: String {
        didSet {
            UserDefaults.standard.set(languageCode, forKey: Self.storageKey)
        }
    }

    var locale: Locale {
        Locale(identifier: languageCode)
    }

    init() {
        languageCode = UserDefaults.standard.string(forKey: Self.storageKey) ?? Locale.current.language.languageCode?.identifier ?? "en"
    }

    func setLanguage(_ code: String) {
        languageCode = code
    }

    func text(_ key: String, _ arguments: CVarArg...) -> String {
        let active = NativeTranslations.values[languageCode] ?? NativeTranslations.values["en"] ?? [:]
        let fallback = NativeTranslations.values["en"] ?? NativeTranslations.values["nl"] ?? [:]
        var value = active[key] ?? fallback[key] ?? key
        for (index, argument) in arguments.enumerated() {
            value = value.replacingOccurrences(of: "{\(index)}", with: String(describing: argument))
        }
        return value
    }
}
