import Foundation
import JavaScriptCore

final class AdvisorBridge {
    private let context: JSContext?
    init() {
        context = JSContext()
        guard let url = SharedStore.shared.resource("Advisor", extension: "js"), let script = try? String(contentsOf: url) else { return }
        context?.evaluateScript(script)
    }
    func recommend(_ snapshot: MatchSnapshot, settings: Settings, catalogue: Catalogue) -> Advice? {
        struct Input: Encodable { let snapshot: MatchSnapshot; let settings: Settings; let catalogue: Catalogue; let now: Double }
        let input = Input(snapshot: snapshot, settings: settings, catalogue: catalogue, now: Date().timeIntervalSince1970)
        guard let data = try? JSONEncoder().encode(input), let json = String(data: data, encoding: .utf8),
              let result = context?.objectForKeyedSubscript("recommendJSON")?.call(withArguments: [json])?.toString(),
              let resultData = result.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(Advice.self, from: resultData)
    }
}
