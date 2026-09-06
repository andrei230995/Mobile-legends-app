import Foundation
import CoreGraphics

struct NormalizedRect: Codable, Equatable {
    var x: Double; var y: Double; var width: Double; var height: Double
    var cgRect: CGRect { CGRect(x: x, y: y, width: width, height: height) }
    var isValid: Bool { x >= 0 && y >= 0 && width > 0.003 && height > 0.003 && x + width <= 1.001 && y + height <= 1.001 }
    func cell(column: Int, row: Int = 0, columns: Int = 6, rows: Int = 1) -> NormalizedRect {
        .init(x: x + Double(column) * width / Double(columns), y: y + Double(row) * height / Double(rows), width: width / Double(columns), height: height / Double(rows))
    }
}
struct Calibration: Codable {
    var gold: NormalizedRect?
    var hudAnchor: NormalizedRect?
    var ownItems: NormalizedRect?
    var enemyItems: NormalizedRect?
    var scoreboardAnchor: NormalizedRect?
    var emptySlot: NormalizedRect?
    var aspectRatio: Double = 0
    var completed: Bool {
        [gold, hudAnchor, ownItems, enemyItems, scoreboardAnchor, emptySlot].allSatisfy { $0?.isValid == true } && aspectRatio > 1
    }
}
struct Settings: Codable {
    var hero = "Alice"
    var notifications = false
    var calibration = Calibration()
    // Suppress price-specific advice until the player verifies the catalogue against their game.
    var pricesConfirmed = false
    var purchasePreference = "balanced"
}
struct MatchSnapshot: Codable {
    var sessionID = UUID().uuidString
    var capturedAt: Double = 0
    var gold: Int?
    var goldAt: Double = 0
    var ownItems: [String] = []
    var ownItemsAt: Double = 0
    var enemyItems: [[String]] = []
    var enemyItemsAt: Double = 0
    var ownInventoryKnown = false
    var unknownEnemySlots = 30
    var status = "Start a broadcast in a practice match."
    var captureActive = false
    var notificationError: String?
    var advice: Advice?
}
struct Advice: Codable {
    var status: String
    var title: String
    var reason: String
    var itemID: String?
    var targetID: String?
    var cost: Int?
    var remainingGold: Int?
    var targetRemaining: Int?
    var consumes: [String]?
    var alternatives: [Alternative]?
    var caveat: String?
}
struct Alternative: Codable {
    var itemID: String
    var cost: Int
    var targetID: String
    var reason: String
}
struct Item: Codable, Identifiable {
    let id: String
    let name: String
    let cost: Int
    let recipe: [String]
    let tags: [String]
    let icon: String?
    let source: String
    let stats: [String: Double]
    let recipeIssue: String?
}
struct Catalogue: Codable {
    let checkedAt: String
    let verifiedCurrentPatch: Bool
    let items: [Item]
    let heroes: [String: HeroBuild]
}
struct HeroBuild: Codable {
    let kind: String
    let core: [String]
}
