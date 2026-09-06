import Foundation
import UIKit

final class SharedStore {
    static let shared = SharedStore()
    let directory: URL?
    init() {
        let group = Bundle.main.object(forInfoDictionaryKey: "RankwiseAppGroup") as? String ?? "group.com.example.rankwise"
        directory = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group)
    }
    func read<T: Decodable>(_ name: String, as: T.Type) -> T? {
        guard let url = directory?.appendingPathComponent(name), let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(T.self, from: data)
    }
    @discardableResult func write<T: Encodable>(_ value: T, to name: String) -> Bool {
        guard let url = directory?.appendingPathComponent(name), let data = try? JSONEncoder().encode(value) else { return false }
        do { try data.write(to: url, options: .atomic); return true } catch { return false }
    }
    func image(_ name: String) -> UIImage? {
        guard let path = directory?.appendingPathComponent(name).path else { return nil }
        return UIImage(contentsOfFile: path)
    }
    @discardableResult func saveImage(_ image: UIImage, name: String) -> Bool {
        guard let url = directory?.appendingPathComponent(name), let bytes = image.pngData() else { return false }
        do { try bytes.write(to: url, options: .atomic); return true } catch { return false }
    }
    func clearMatch() {
        _ = write(MatchSnapshot(), to: "snapshot.json")
        _ = write(UUID().uuidString, to: "reset-token.json")
    }
    func resource(_ name: String, extension ext: String) -> URL? {
        Bundle.main.url(forResource: name, withExtension: ext, subdirectory: "Resources")
    }
    func catalogue() -> Catalogue? {
        guard let url = resource("catalogue", extension: "json"), let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(Catalogue.self, from: data)
    }
}
