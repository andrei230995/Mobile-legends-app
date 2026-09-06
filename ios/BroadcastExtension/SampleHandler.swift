import ReplayKit
import CoreImage
import ImageIO
import UserNotifications

final class SampleHandler: RPBroadcastSampleHandler {
    private let processing = DispatchQueue(label: "rankwise.capture", qos: .utility)
    private let gate = DispatchSemaphore(value: 1)
    private let ciContext = CIContext(options: [.cacheIntermediates: false])
    private let store = SharedStore.shared
    private var snapshot = MatchSnapshot()
    private var recognition: ImageRecognition?
    private var catalogue: Catalogue?
    private let advisor = AdvisorBridge()
    private var lastFrame = Date.distantPast
    private var lastNotification = Date.distantPast
    private var lastAdviceID: String?
    private var lastGold: Int?
    private var pendingOwn: [ImageRecognition.Slot]?
    private var pendingEnemy: [[ImageRecognition.Slot]]?
    private var resetToken: String?
    private var active = false

    override func broadcastStarted(withSetupInfo setupInfo: [String : NSObject]?) {
        processing.sync {
            catalogue = store.catalogue()
            if let catalogue { recognition = ImageRecognition(catalogue: catalogue) }
            snapshot = MatchSnapshot(); snapshot.captureActive = true
            snapshot.status = "Waiting for the calibrated game screen."
            active = true
            resetToken = store.read("reset-token.json", as: String.self)
            _ = store.write(snapshot, to: "snapshot.json")
        }
    }
    override func broadcastPaused() {
        processing.sync {
            active = false; snapshot.captureActive = false; snapshot.gold = nil
            snapshot.ownInventoryKnown = false; snapshot.advice = nil
            snapshot.status = "Broadcast paused."
            _ = store.write(snapshot, to: "snapshot.json")
        }
    }
    override func broadcastResumed() {
        processing.sync { active = true; snapshot.captureActive = true; lastGold = nil; pendingOwn = nil; pendingEnemy = nil }
    }
    override func broadcastFinished() {
        processing.sync {
            active = false; snapshot.captureActive = false; snapshot.gold = nil; snapshot.advice = nil
            snapshot.status = "Broadcast stopped."
            _ = store.write(snapshot, to: "snapshot.json")
        }
    }
    override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer, with sampleBufferType: RPSampleBufferType) {
        guard sampleBufferType == .video, gate.wait(timeout: .now()) == .success else { return }
        // Retain only one frame. New frames are dropped while OCR is busy.
        processing.async { [weak self] in
            guard let self else { return }
            defer { self.gate.signal() }
            guard self.active, Date().timeIntervalSince(self.lastFrame) >= 1.0 else { return }
            self.lastFrame = Date()
            autoreleasepool { self.process(sampleBuffer) }
        }
    }
    private func process(_ sample: CMSampleBuffer) {
        let now = Date().timeIntervalSince1970
        let token = store.read("reset-token.json", as: String.self)
        if token != resetToken {
            resetToken = token; snapshot = MatchSnapshot(); lastGold = nil; pendingOwn = nil; pendingEnemy = nil; lastAdviceID = nil
        }
        guard let settings = store.read("settings.json", as: Settings.self), settings.calibration.completed,
              let catalogue, let recognition, let pixelBuffer = CMSampleBufferGetImageBuffer(sample) else {
            snapshot.gold = nil; snapshot.ownInventoryKnown = false; snapshot.advice = nil
            snapshot.status = "Complete screen calibration in Rankwise first."
            snapshot.captureActive = true; snapshot.capturedAt = now
            _ = store.write(snapshot, to: "snapshot.json"); return
        }
        let rawOrientation = (CMGetAttachment(sample, key: RPVideoSampleOrientationKey as CFString, attachmentModeOut: nil) as? NSNumber)?.uint32Value ?? 1
        let orientation = CGImagePropertyOrientation(rawValue: rawOrientation) ?? .up
        var frame = CIImage(cvPixelBuffer: pixelBuffer).oriented(orientation)
        let extent = frame.extent
        guard extent.width > extent.height,
              abs(Double(extent.width / extent.height) - settings.calibration.aspectRatio) < 0.035 else {
            invalidate("Waiting for the calibrated landscape game view.", now: now); return
        }
        frame = frame.transformed(by: CGAffineTransform(scaleX: min(1, 1280 / extent.width), y: min(1, 1280 / extent.width)))
        guard let image = ciContext.createCGImage(frame, from: frame.extent) else { return }
        recognition.refreshCalibration()
        snapshot.captureActive = true; snapshot.capturedAt = now
        let isBoard = recognition.isScoreboard(image, calibration: settings.calibration)
        if isBoard, let ownRect = settings.calibration.ownItems, let enemyRect = settings.calibration.enemyItems {
            let own = (0..<6).map { recognition.slot(image, rect: ownRect.cell(column: $0)) }
            let enemy = (0..<5).map { row in (0..<6).map { recognition.slot(image, rect: enemyRect.cell(column: $0, row: row, rows: 5)) } }
            snapshot.gold = nil; lastGold = nil
            if own == pendingOwn && !own.contains(.unknown) {
                snapshot.ownItems = own.compactMap(\.itemID); snapshot.ownInventoryKnown = true; snapshot.ownItemsAt = now
            } else {
                snapshot.ownInventoryKnown = false
            }
            if enemy == pendingEnemy {
                snapshot.enemyItems = enemy.map { $0.compactMap(\.itemID) }
                snapshot.enemyItemsAt = now
                snapshot.unknownEnemySlots = enemy.flatMap { $0 }.filter { $0 == .unknown }.count
            }
            pendingOwn = own; pendingEnemy = enemy
            snapshot.status = snapshot.ownInventoryKnown ? "Equipment read. Close the scoreboard to read spendable gold." : "Hold the equipment scoreboard open. Some slots are not yet recognized."
        } else if recognition.isHUD(image, calibration: settings.calibration), let rect = settings.calibration.gold {
            pendingOwn = nil; pendingEnemy = nil
            if let value = recognition.gold(image, rect: rect) {
                if let previous = lastGold, value < previous - 10 {
                    snapshot.ownInventoryKnown = false
                    snapshot.status = "Gold decreased. Open the scoreboard to refresh your items."
                }
                if let previous = lastGold, abs(value - previous) <= 300 {
                    snapshot.gold = value; snapshot.goldAt = now
                    snapshot.status = "Reading your match. Open the scoreboard regularly to refresh equipment."
                } else { snapshot.gold = nil }
                lastGold = value
            } else { snapshot.gold = nil; lastGold = nil; snapshot.status = "Gold is unreadable or abbreviated. Advice paused." }
        } else {
            invalidate("Game screen not recognized. Advice paused.", now: now); return
        }
        snapshot.advice = advisor.recommend(snapshot, settings: settings, catalogue: catalogue)
        if settings.notifications { notifyIfNeeded(catalogue: catalogue) }
        _ = store.write(snapshot, to: "snapshot.json")
    }
    private func invalidate(_ reason: String, now: Double) {
        snapshot.gold = nil; snapshot.ownInventoryKnown = false; snapshot.advice = nil; snapshot.status = reason
        snapshot.captureActive = true; snapshot.capturedAt = now; lastGold = nil; pendingOwn = nil; pendingEnemy = nil
        _ = store.write(snapshot, to: "snapshot.json")
    }
    private func notifyIfNeeded(catalogue: Catalogue) {
        guard let advice = snapshot.advice, advice.status == "buy", let id = advice.itemID, let cost = advice.cost,
              id != lastAdviceID, Date().timeIntervalSince(lastNotification) >= 45 else { return }
        let content = UNMutableNotificationContent()
        content.title = advice.title + " · " + String(cost) + " gold"
        content.body = advice.reason
        content.threadIdentifier = "rankwise-match"
        content.sound = .default
        let request = UNNotificationRequest(identifier: "rankwise-next-buy", content: content, trigger: nil)
        lastNotification = Date(); lastAdviceID = id
        UNUserNotificationCenter.current().add(request) { [weak self] error in
            guard let error, let self else { return }
            self.processing.async {
                self.snapshot.notificationError = error.localizedDescription
                _ = self.store.write(self.snapshot, to: "snapshot.json")
            }
        }
    }
}
