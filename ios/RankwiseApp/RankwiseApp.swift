import SwiftUI
import ReplayKit
import UserNotifications
import Combine

@main
struct RankwiseApp: App {
    @StateObject private var model = CoachModel()
    var body: some Scene {
        WindowGroup {
            RootView().environmentObject(model).preferredColorScheme(.dark)
                .tint(Color(red: 0.73, green: 0.63, blue: 1))
        }
    }
}
@MainActor final class CoachModel: ObservableObject {
    @Published var settings: Settings
    @Published var snapshot = MatchSnapshot()
    @Published var message: String?
    let catalogue: Catalogue?
    private var timer: AnyCancellable?
    init() {
        settings = SharedStore.shared.read("settings.json", as: Settings.self) ?? Settings()
        catalogue = SharedStore.shared.catalogue()
        timer = Timer.publish(every: 1, on: .main, in: .common).autoconnect().sink { [weak self] _ in
            guard let self else { return }
            self.snapshot = SharedStore.shared.read("snapshot.json", as: MatchSnapshot.self) ?? MatchSnapshot()
        }
    }
    var live: Bool { snapshot.captureActive && Date().timeIntervalSince1970 - snapshot.capturedAt < 5 }
    func save() {
        if !SharedStore.shared.write(settings, to: "settings.json") {
            message = "Shared storage is unavailable. Check that the same App Group is provisioned for both app targets."
        }
    }
    func resetMatch() {
        SharedStore.shared.clearMatch(); snapshot = MatchSnapshot()
        UNUserNotificationCenter.current().removeDeliveredNotifications(withIdentifiers: ["rankwise-next-buy"])
    }
    func enableNotifications(_ enabled: Bool) {
        if !enabled { settings.notifications = false; save(); return }
        Task {
            do {
                let granted = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])
                settings.notifications = granted
                if !granted { message = "Allow Rankwise notifications in iPhone Settings to receive recommendations during a match." }
                save()
            } catch { message = error.localizedDescription }
        }
    }
    func itemName(_ id: String) -> String { catalogue?.items.first(where: { $0.id == id })?.name ?? id }
}
struct RootView: View {
    @State private var selectedTab: Int = {
        #if DEBUG && targetEnvironment(simulator)
        return Int(ProcessInfo.processInfo.environment["RANKWISE_SCREENSHOT_TAB"] ?? "0") ?? 0
        #else
        return 0
        #endif
    }()
    var body: some View {
        TabView(selection: $selectedTab) {
            MatchView().tabItem { Label("Match", systemImage: "gamecontroller") }.tag(0)
            SetupView().tabItem { Label("Setup", systemImage: "viewfinder") }.tag(1)
            CatalogueView().tabItem { Label("Items", systemImage: "square.grid.2x2") }.tag(2)
        }
    }
}
struct CoachCard<Content: View>: View {
    @ViewBuilder let content: Content
    var body: some View { VStack(alignment: .leading, spacing: 16) { content }.frame(maxWidth: .infinity, alignment: .leading).padding(20).background(Color.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 20)).overlay(RoundedRectangle(cornerRadius: 20).stroke(Color.white.opacity(0.09))) }
}
struct MatchView: View {
    @EnvironmentObject private var model: CoachModel
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    HStack { Label(model.live ? "Capture running" : "Capture inactive", systemImage: model.live ? "record.circle" : "pause.circle").foregroundStyle(model.live ? .green : .secondary); Spacer(); Text("EXPERIMENTAL").font(.caption2).tracking(1.2).foregroundStyle(.secondary) }
                    Text("Your next buy,\nwith a reason.").font(.largeTitle.bold())
                    CoachCard {
                        HStack { VStack(alignment: .leading, spacing: 6) { Text(model.settings.hero).font(.title2.bold()); Text("Spendable gold").font(.subheadline).foregroundStyle(.secondary) }; Spacer(); Text(freshGold).font(.system(.largeTitle, design: .rounded).bold()).foregroundStyle(.yellow) }
                        Text(model.snapshot.status).font(.subheadline).foregroundStyle(.secondary)
                    }
                    CoachCard {
                        Label("NEXT PURCHASE", systemImage: "sparkles").font(.caption.bold()).foregroundStyle(.purple)
                        if model.live, let advice = model.snapshot.advice {
                            Text(advice.title).font(.title2.bold())
                            Text(advice.reason).font(.body)
                            if let cost = advice.cost, let remaining = advice.remainingGold {
                                HStack { Label("\(cost) gold", systemImage: "circle.circle"); Spacer(); Text("\(remaining) left") }.font(.headline).foregroundStyle(.yellow)
                            }
                            if let ids = advice.consumes, !ids.isEmpty { Text("Uses owned: " + ids.map(model.itemName).joined(separator: ", ")).font(.subheadline).foregroundStyle(.secondary) }
                            if let remaining = advice.targetRemaining, let target = advice.targetID, remaining > 0 { Text("Then save \(remaining) gold to finish \(model.itemName(target)).").font(.subheadline).foregroundStyle(.secondary) }
                            if let caveat = advice.caveat { Text(caveat).font(.caption).foregroundStyle(.secondary) }
                        } else {
                            Text("Waiting for your match").font(.title2.bold())
                            Text("Complete the one-time screen setup, start a broadcast, then open MLBB. Hold the equipment scoreboard open for at least three seconds so inventory readings can settle.").foregroundStyle(.secondary)
                        }
                    }
                    CoachCard {
                        HStack { Text("Your detected items").font(.headline); Spacer(); Text(age(model.snapshot.ownItemsAt)).font(.caption).foregroundStyle(.secondary) }
                        if model.snapshot.ownInventoryKnown {
                            if model.snapshot.ownItems.isEmpty { Text("Six empty slots detected.").foregroundStyle(.secondary) }
                            ForEach(Array(model.snapshot.ownItems.enumerated()), id: \.offset) { _, id in ItemRow(id: id) }
                        } else { Text("Not confirmed. Open the equipment scoreboard.").foregroundStyle(.secondary) }
                    }
                    CoachCard {
                        HStack { Text("Enemy equipment").font(.headline); Spacer(); Text(age(model.snapshot.enemyItemsAt)).font(.caption).foregroundStyle(.secondary) }
                        Text("\(model.snapshot.unknownEnemySlots) unrecognized slots. Rows follow the calibrated scoreboard order.").font(.caption).foregroundStyle(.secondary)
                        ForEach(Array(model.snapshot.enemyItems.enumerated()), id: \.offset) { row, items in
                            VStack(alignment: .leading, spacing: 6) { Text("Enemy row \(row+1)").font(.subheadline.bold()); Text(items.isEmpty ? "No equipment recognized" : items.map(model.itemName).joined(separator: " · ")).font(.subheadline).foregroundStyle(.secondary) }
                        }
                    }
                    Button("New match — clear previous readings", role: .destructive) { model.resetMatch() }.frame(minHeight: 44)
                    Text("No automatic purchases. Suggestions use observed equipment and a dated item catalogue; they do not model every fight.").font(.caption).foregroundStyle(.secondary)
                }.padding(20)
            }.background(Color(red: 0.06, green: 0.065, blue: 0.085)).navigationTitle("rankwise").navigationBarTitleDisplayMode(.inline)
        }
    }
    private var freshGold: String { guard model.live, Date().timeIntervalSince1970 - model.snapshot.goldAt <= 4, let gold = model.snapshot.gold else { return "—" }; return String(gold) }
    private func age(_ time: Double) -> String { time > 0 ? "\(Int(max(0, Date().timeIntervalSince1970-time)))s ago" : "Not read" }
}
struct ItemRow: View {
    @EnvironmentObject private var model: CoachModel
    let id: String
    var body: some View {
        HStack {
            if let url = Bundle.main.url(forResource: id, withExtension: "png", subdirectory: "CoachAssets/Items"), let image = UIImage(contentsOfFile: url.path) { Image(uiImage: image).resizable().frame(width: 36, height: 36).clipShape(RoundedRectangle(cornerRadius: 7)).accessibilityHidden(true) }
            Text(model.itemName(id)).font(.subheadline)
        }
    }
}
struct SetupView: View {
    @EnvironmentObject private var model: CoachModel
    var body: some View {
        NavigationStack {
            Form {
                Section("Before each match") {
                    Picker("Your hero", selection: $model.settings.hero) { ForEach((model.catalogue?.heroes.keys.sorted() ?? []), id: \.self) { Text($0).tag($0) } }.onChange(of: model.settings.hero) { _,_ in model.save(); model.resetMatch() }
                    Picker("Purchase priority", selection: $model.settings.purchasePreference) { Text("Balanced").tag("balanced"); Text("More protection").tag("survive") }.onChange(of: model.settings.purchasePreference) { _,_ in model.save() }
                    Text("Select your hero before broadcasting. Gold and equipment are read from the screen; they are not typed in.").font(.caption).foregroundStyle(.secondary)
                }
                Section("One-time screen setup") {
                    NavigationLink { CalibrationView() } label: { Label(model.settings.calibration.completed ? "Review screen calibration" : "Calibrate screen regions", systemImage: "crop") }
                    Text("Use two screenshots from a practice match: the normal game screen and the equipment scoreboard. Recalibrate after changing the game UI layout.").font(.caption).foregroundStyle(.secondary)
                }
                Section("Purchase alerts") {
                    Toggle("Send recommendations as notifications", isOn: Binding(get: { model.settings.notifications }, set: model.enableNotifications))
                    Text("Notification delivery needs device testing. Focus modes and notification settings can suppress banners. This prototype does not create an overlay over the game.").font(.caption).foregroundStyle(.secondary)
                }
                Section("Price check") {
                    Toggle("I checked these prices against my game", isOn: $model.settings.pricesConfirmed).onChange(of: model.settings.pricesConfirmed) { _,_ in model.save() }
                    Text("Catalogue reference: \(model.catalogue?.checkedAt ?? "unavailable"). Current patch not independently verified. Compare prices and recipes in the Items tab with your shop. Leave this off if they differ.").font(.caption).foregroundStyle(.secondary)
                }
                Section("Start live capture") {
                    if model.settings.calibration.completed {
                        HStack { Text("Start screen broadcast"); Spacer(); BroadcastPicker().frame(width: 54, height: 54) }
                        Text("Tap the broadcast button and choose Start Broadcast, then open MLBB. Use Control Center or the recording indicator to stop. Microphone audio is not needed.").font(.caption).foregroundStyle(.secondary)
                    } else { Text("Finish calibration to enable the broadcast button.").foregroundStyle(.secondary) }
                }
                if let message = model.message { Section("Setup message") { Text(message).foregroundStyle(.orange) } }
                if let message = model.snapshot.notificationError { Section("Notification status") { Text(message).foregroundStyle(.orange) } }
                Section("Data and sources") {
                    Text("Frames are processed on this iPhone. The prototype contains no server upload path. Calibration screenshots stay in the app’s shared storage until replaced or the app is removed.").font(.subheadline)
                    Link("Apple · ReplayKit", destination: URL(string: "https://developer.apple.com/documentation/replaykit")!)
                    Link("Item catalogue reference", destination: URL(string: "https://mlbbhub.com/items")!)
                    Text("Independent fan prototype. Not affiliated with MOONTON. Recognition thresholds, game compatibility and extension memory use have not been validated on a device.").font(.caption).foregroundStyle(.secondary)
                }
            }.navigationTitle("Match setup").onAppear { model.save() }
        }
    }
}
struct BroadcastPicker: UIViewRepresentable {
    func makeUIView(context: Context) -> RPSystemBroadcastPickerView {
        let picker = RPSystemBroadcastPickerView(frame: CGRect(x: 0, y: 0, width: 54, height: 54))
        picker.preferredExtension = Bundle.main.object(forInfoDictionaryKey: "RankwiseBroadcastID") as? String
        picker.showsMicrophoneButton = false
        picker.accessibilityLabel = "Start Rankwise screen broadcast"
        return picker
    }
    func updateUIView(_ uiView: RPSystemBroadcastPickerView, context: Context) {}
}
struct CatalogueView: View {
    @EnvironmentObject private var model: CoachModel
    @State private var query = ""
    var body: some View {
        NavigationStack {
            List {
                Section { Text("Reference prices and recipes. Current-patch accuracy is not verified. If your shop differs, keep purchase alerts disabled until the catalogue is updated.").font(.caption).foregroundStyle(.secondary) }
                ForEach((model.catalogue?.items ?? []).filter { query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) }.sorted { $0.name < $1.name }) { item in
                    VStack(alignment: .leading, spacing: 9) {
                        HStack { ItemRow(id: item.id); Spacer(); Text("\(item.cost) g").foregroundStyle(.yellow) }
                        if let issue = item.recipeIssue { Text(issue).font(.caption).foregroundStyle(.orange) }
                        if !item.recipe.isEmpty { Text("Recipe: " + item.recipe.map(model.itemName).joined(separator: " + ")).font(.caption).foregroundStyle(.secondary) }
                        if let url = URL(string: item.source) { Link("Source", destination: url).font(.caption) }
                    }.padding(.vertical, 5)
                }
            }.searchable(text: $query, prompt: "Find equipment").navigationTitle("Item reference")
        }
    }
}
