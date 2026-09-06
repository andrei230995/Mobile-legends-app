import Foundation
import UIKit
import Vision
import CoreGraphics

/// Conservative template matching. Distances are not confidence probabilities.
/// Thresholds are experimental until evaluated against real iPhone game captures.
final class ImageRecognition {
    private var templates: [(String, [Float])] = []
    private var emptyTemplate: [Float]?
    private var hudTemplate: [Float]?
    private var boardTemplate: [Float]?
    init(catalogue: Catalogue) {
        for item in catalogue.items {
            guard let icon = item.icon,
                  let url = Bundle.main.url(forResource: icon, withExtension: nil, subdirectory: "Resources/Items"),
                  let image = UIImage(contentsOfFile: url.path)?.cgImage,
                  let vector = Self.vector(image, inset: 0.12) else { continue }
            templates.append((item.id, vector))
        }
        refreshCalibration()
    }
    func refreshCalibration() {
        let store = SharedStore.shared
        emptyTemplate = store.image("empty-template.png").flatMap { $0.cgImage }.flatMap { Self.vector($0, inset: 0.12) }
        hudTemplate = store.image("hud-template.png").flatMap { $0.cgImage }.flatMap { Self.vector($0, inset: 0) }
        boardTemplate = store.image("board-template.png").flatMap { $0.cgImage }.flatMap { Self.vector($0, inset: 0) }
    }
    static func crop(_ image: CGImage, rect: NormalizedRect, inset: Double = 0) -> CGImage? {
        guard rect.isValid else { return nil }
        var bounds = CGRect(x: rect.x * Double(image.width), y: rect.y * Double(image.height), width: rect.width * Double(image.width), height: rect.height * Double(image.height))
        bounds = bounds.insetBy(dx: bounds.width * inset, dy: bounds.height * inset).integral
        guard bounds.width > 2, bounds.height > 2 else { return nil }
        return image.cropping(to: bounds)
    }
    static func vector(_ image: CGImage, inset: Double) -> [Float]? {
        let size = 12
        let bounds = CGRect(x: 0, y: 0, width: CGFloat(image.width), height: CGFloat(image.height))
        guard let crop = image.cropping(to: bounds.insetBy(dx: bounds.width * inset, dy: bounds.height * inset)) else { return nil }
        var bytes = [UInt8](repeating: 0, count: size * size * 4)
        let ok = bytes.withUnsafeMutableBytes { raw -> Bool in
            guard let context = CGContext(data: raw.baseAddress, width: size, height: size, bitsPerComponent: 8, bytesPerRow: size * 4, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
            context.interpolationQuality = .medium
            context.draw(crop, in: CGRect(x: 0, y: 0, width: size, height: size))
            return true
        }
        guard ok else { return nil }
        return stride(from: 0, to: bytes.count, by: 4).flatMap { [Float(bytes[$0]) / 255, Float(bytes[$0+1]) / 255, Float(bytes[$0+2]) / 255] }
    }
    private func distance(_ a: [Float], _ b: [Float]) -> Float {
        guard a.count == b.count, !a.isEmpty else { return 1 }
        return sqrt(zip(a,b).reduce(Float(0)) { $0 + ($1.0-$1.1)*($1.0-$1.1) } / Float(a.count))
    }
    func isHUD(_ image: CGImage, calibration: Calibration) -> Bool {
        matchesAnchor(image, rect: calibration.hudAnchor, template: hudTemplate)
    }
    func isScoreboard(_ image: CGImage, calibration: Calibration) -> Bool {
        matchesAnchor(image, rect: calibration.scoreboardAnchor, template: boardTemplate)
    }
    private func matchesAnchor(_ image: CGImage, rect: NormalizedRect?, template: [Float]?) -> Bool {
        guard let rect, let template, let crop = Self.crop(image, rect: rect), let vector = Self.vector(crop, inset: 0) else { return false }
        return distance(vector, template) < 0.065
    }
    enum Slot: Equatable {
        case item(String), empty, unknown
        var itemID: String? { if case .item(let id) = self { return id }; return nil }
    }
    func slot(_ image: CGImage, rect: NormalizedRect) -> Slot {
        guard let crop = Self.crop(image, rect: rect), let vector = Self.vector(crop, inset: 0.12) else { return .unknown }
        if let emptyTemplate, distance(vector, emptyTemplate) < 0.06 { return .empty }
        let ranked = templates.map { ($0.0, distance(vector, $0.1)) }.sorted { $0.1 < $1.1 }
        guard ranked.count > 1, ranked[0].1 < 0.105, ranked[1].1 - ranked[0].1 > 0.035 else { return .unknown }
        return .item(ranked[0].0)
    }
    func gold(_ image: CGImage, rect: NormalizedRect) -> Int? {
        guard let crop = Self.crop(image, rect: rect) else { return nil }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false
        request.recognitionLanguages = ["en-US"]
        let handler = VNImageRequestHandler(cgImage: crop, options: [:])
        do { try handler.perform([request]) } catch { return nil }
        guard let observations = request.results, observations.count == 1,
              let text = observations.first?.topCandidates(1).first, text.confidence >= 0.92 else { return nil }
        // A rounded 1.2k, a timer or a scoreboard total must never become spendable gold.
        let digits = text.string.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !digits.isEmpty, digits.count <= 5, digits.allSatisfy({ $0.isASCII && $0.isNumber }),
              let gold = Int(digits), (0...50000).contains(gold) else { return nil }
        return gold
    }
}
