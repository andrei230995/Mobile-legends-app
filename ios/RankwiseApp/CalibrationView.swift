import SwiftUI
import PhotosUI
import UIKit

struct CalibrationView: View {
    @EnvironmentObject private var model: CoachModel
    @State private var selection: PhotosPickerItem?
    @State private var mode = "HUD"
    @State private var region = "gold"
    @State private var image: UIImage?
    @State private var proposed: NormalizedRect?
    @State private var error: String?
    private var regionChoices: [(String,String)] {
        mode == "HUD" ? [("gold","Spendable gold"),("hudAnchor","Static HUD marker")] : [("ownItems","Your six item slots"),("enemyItems","Enemy 5 × 6 item grid"),("scoreboardAnchor","Static scoreboard marker"),("emptySlot","One empty item slot")]
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("Teach Rankwise where to look").font(.title2.bold())
                Text("This setup marks screen locations once. Match gold and item values are then read automatically. Use screenshots in the same landscape orientation and layout you play with.").foregroundStyle(.secondary)
                Picker("Screenshot type", selection: $mode) { Text("Normal game").tag("HUD"); Text("Equipment board").tag("Board") }.pickerStyle(.segmented).onChange(of: mode) { _,_ in loadSaved() }
                PhotosPicker(selection: $selection, matching: .images) { Label(image == nil ? "Choose a practice screenshot" : "Replace screenshot", systemImage: "photo").frame(minHeight: 44) }
                Text("Use the native screenshot at full size. Avoid a crop, a screenshot with notifications, or a stretched image.").font(.caption).foregroundStyle(.secondary)
                if let image {
                    Picker("Region to mark", selection: $region) { ForEach(regionChoices, id: \.0) { id,title in Text(title).tag(id) } }.onChange(of: region) { _,_ in proposed = nil }
                    Text(instruction).font(.subheadline)
                    RegionCanvas(image: image, selected: $proposed)
                    Button("Save this region") { saveRegion() }.buttonStyle(.borderedProminent).disabled(proposed?.isValid != true).frame(minHeight: 44)
                    Text("For equipment rows, mark the outside edges of all six equally spaced cells, including empty slots. For the enemy grid, include all five equally spaced rows. Uneven or changed layouts need a revised calibration implementation.").font(.caption).foregroundStyle(.secondary)
                }
                if let error { Text(error).foregroundStyle(.orange) }
                CoachCard {
                    Text("Calibration checklist").font(.headline)
                    checklist("Gold area", model.settings.calibration.gold != nil)
                    checklist("HUD marker", model.settings.calibration.hudAnchor != nil)
                    checklist("Your six slots", model.settings.calibration.ownItems != nil)
                    checklist("Enemy item grid", model.settings.calibration.enemyItems != nil)
                    checklist("Scoreboard marker", model.settings.calibration.scoreboardAnchor != nil)
                    checklist("Empty-slot reference", model.settings.calibration.emptySlot != nil)
                }
                Text("Before ranked play, compare detected equipment and gold against a practice match. Unknown icons intentionally pause inventory-based recommendations. Recalibrate if detection is inconsistent.").font(.subheadline).foregroundStyle(.secondary)
            }.padding(20)
        }.navigationTitle("Screen calibration").navigationBarTitleDisplayMode(.inline)
            .onAppear { loadSaved() }
            .onChange(of: selection) { _, newValue in
                Task {
                    guard let data = try? await newValue?.loadTransferable(type: Data.self), let raw = UIImage(data: data) else { return }
                    let format = UIGraphicsImageRendererFormat(); format.scale = 1
                    let size = CGSize(width: raw.size.width, height: raw.size.height)
                    let normalized = UIGraphicsImageRenderer(size: size, format: format).image { _ in raw.draw(in: CGRect(origin: .zero, size: size)) }
                    guard normalized.size.width > normalized.size.height else { error = "Choose a landscape MLBB screenshot."; return }
                    let ratio = Double(normalized.size.width / normalized.size.height)
                    if model.settings.calibration.aspectRatio > 0 && abs(model.settings.calibration.aspectRatio-ratio) > 0.035 { error = "This screenshot has a different aspect ratio. Use matching full-screen screenshots."; return }
                    image = normalized; proposed = nil; error = nil
                    // Replacing an image invalidates its old regions and fingerprints.
                    if mode == "HUD" { model.settings.calibration.gold = nil; model.settings.calibration.hudAnchor = nil }
                    else { model.settings.calibration.ownItems = nil; model.settings.calibration.enemyItems = nil; model.settings.calibration.scoreboardAnchor = nil; model.settings.calibration.emptySlot = nil }
                    model.settings.calibration.aspectRatio = ratio; model.save()
                    if !SharedStore.shared.saveImage(normalized, name: mode == "HUD" ? "hud-calibration.png" : "board-calibration.png") { error = "Could not save calibration image. Check shared storage." }
                }
            }
    }
    @ViewBuilder private func checklist(_ title: String, _ ready: Bool) -> some View {
        Label(title, systemImage: ready ? "checkmark.circle.fill" : "circle").foregroundStyle(ready ? .green : .secondary).font(.subheadline)
    }
    private var instruction: String {
        switch region {
        case "gold": return "Drag a tight box around spendable gold digits only. Do not include the coin icon, a timer, or total earned gold."
        case "hudAnchor": return "Mark a small, unchanging HUD symbol visible only in the normal game screen. Avoid timers, hero portraits, health bars and moving scenery."
        case "ownItems": return "Mark the complete row of your six equipment slots on the scoreboard."
        case "enemyItems": return "Mark the enemy equipment grid: five rows, six slots per row. Do not include hero portraits or gold totals."
        case "scoreboardAnchor": return "Mark a static scoreboard heading or icon. It must disappear when the scoreboard closes."
        default: return "Mark one empty equipment cell, including its normal background. This teaches the recognizer to distinguish an empty slot from an unknown item."
        }
    }
    private func loadSaved() {
        image = SharedStore.shared.image(mode == "HUD" ? "hud-calibration.png" : "board-calibration.png")
        region = mode == "HUD" ? "gold" : "ownItems"; proposed = nil; error = nil
    }
    private func saveRegion() {
        guard let rect = proposed, rect.isValid, let cgImage = image?.cgImage else { return }
        let store = SharedStore.shared
        var c = model.settings.calibration
        switch region {
        case "gold": c.gold = rect
        case "ownItems": c.ownItems = rect
        case "enemyItems": c.enemyItems = rect
        case "hudAnchor", "scoreboardAnchor", "emptySlot":
            guard let crop = ImageRecognition.crop(cgImage, rect: rect) else { error = "That region could not be cropped."; return }
            let filename = region == "hudAnchor" ? "hud-template.png" : region == "scoreboardAnchor" ? "board-template.png" : "empty-template.png"
            guard store.saveImage(UIImage(cgImage: crop), name: filename) else { error = "Could not save the reference region."; return }
            if region == "hudAnchor" { c.hudAnchor = rect }
            else if region == "scoreboardAnchor" { c.scoreboardAnchor = rect }
            else { c.emptySlot = rect }
        default: break
        }
        model.settings.calibration = c; model.save(); proposed = nil; error = nil
        if let index = regionChoices.firstIndex(where: { $0.0 == region }), index + 1 < regionChoices.count { region = regionChoices[index+1].0 }
    }
}
struct RegionCanvas: View {
    let image: UIImage
    @Binding var selected: NormalizedRect?
    var body: some View {
        GeometryReader { geometry in
            let width = geometry.size.width
            let height = geometry.size.height
            ZStack(alignment: .topLeading) {
                Image(uiImage: image).resizable().scaledToFit().accessibilityLabel("Calibration screenshot. Drag across the requested region.")
                if let box = selected {
                    Rectangle().fill(Color.purple.opacity(0.2)).overlay(Rectangle().stroke(.purple, lineWidth: 2))
                        .frame(width: box.width * width, height: box.height * height)
                        .offset(x: box.x * width, y: box.y * height)
                }
            }.contentShape(Rectangle()).gesture(DragGesture(minimumDistance: 2).onChanged { value in
                let x1 = min(max(value.startLocation.x,0),width), x2 = min(max(value.location.x,0),width)
                let y1 = min(max(value.startLocation.y,0),height), y2 = min(max(value.location.y,0),height)
                selected = NormalizedRect(x: min(x1,x2)/width, y: min(y1,y2)/height, width: abs(x2-x1)/width, height: abs(y2-y1)/height)
            })
        }.aspectRatio(image.size.width / image.size.height, contentMode: .fit).clipShape(RoundedRectangle(cornerRadius: 10))
    }
}
