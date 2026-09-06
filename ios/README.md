# Rankwise for iPhone — developer prototype

**Status:** source implementation prepared for an iPhone 17 Pro Max. The purchase engine passes its automated tests. The native targets have **not** been compiled with an Apple SDK, installed, or tested against live MLBB. This is an Xcode source project, not an installable IPA or a TestFlight release.

The intended experience is to start a screen broadcast once, play MLBB, briefly open the equipment scoreboard, and receive a suggested next purchase based on automatically read gold and items. There are no manual gold or inventory entry forms. A one-time screen-region calibration and pre-match hero selection are required.

## What is implemented

- A SwiftUI iPhone app and a ReplayKit Broadcast Upload Extension.
- On-device Vision text recognition for spendable gold; rounded values such as `1.2k` are rejected.
- Conservative image-template matching for 104 equipment/reference icons.
- Separate HUD and scoreboard markers to avoid interpreting arbitrary screens as game state.
- Two successive matching inventory observations before accepting a complete own inventory.
- Gold freshness of four seconds and inventory freshness of 45 seconds. Unknown own slots pause purchase advice.
- A JavaScriptCore purchase engine: 17 curated hero paths, owned component deductions, duplicate recipe handling, affordable intermediate components, six-slot constraints, and equipment-based counter-item suggestions.
- Optional local notifications, limited to one changed recommendation per 45 seconds. Delivery from the extension and visibility while MLBB runs need device verification.
- App Group storage for settings and the latest observations; no network upload code, account login, game injection, or automatic purchases.

## No Mac available?

See `CLOUD-BUILD.md` for the prepared GitHub Actions compile check. It has not been run and does not produce an installable iPhone app.

## Open and build on a Mac

1. Use Xcode with support for the iOS version installed on the iPhone.
2. Run `python3 scripts/prepare_assets.py` to restore the 104 hash-verified recognition images, then open `Rankwise.xcodeproj`. No package-manager dependencies are needed for the app.
3. Set a unique bundle identifier and your Apple development team. To set both targets consistently, run the generator in this directory with your real values:

   ```bash
   python3 scripts/configure_project.py --bundle-id com.yourdomain.rankwise --team YOURTEAMID
   ```

   `YOURTEAMID` is a placeholder for the actual 10-character Apple Team ID. The generator produces the main bundle, `.broadcast` extension bundle and matching `group.` App Group name.

4. In **Signing & Capabilities**, provision the same App Group for both targets and select your development team. Your signing account must support the required capabilities. The generated project cannot create your Apple certificates or provisioning profiles.
5. Select the **Rankwise** scheme and run the unsigned simulator build first:

   ```bash
   bash scripts/build_on_mac.sh
   ```

6. Connect the iPhone, enable Developer Mode if Xcode requests it, select the phone, and build/run. Simulator success does not verify cross-app screen broadcasting or recognition.

Apple references: [App Groups](https://developer.apple.com/documentation/xcode/configuring-app-groups), [Developer Mode](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device), [ReplayKit](https://developer.apple.com/documentation/replaykit).

## First device trial

Use a practice match before considering ranked use.

1. Take two full-size, landscape screenshots in the game: the normal HUD with exact spendable gold, and the **equipment** scoreboard showing your row and all five enemy equipment rows. Include an empty item slot for calibration.
2. In Rankwise → Setup → Screen calibration, import the screenshots with the system photo picker. Mark:
   - Spendable gold digits only.
   - A static HUD symbol that disappears on the scoreboard.
   - Your complete six-item row.
   - The enemy five-row, six-column equipment grid.
   - A static scoreboard heading or symbol.
   - One empty equipment cell.
3. Mark each inventory region from the outside edge of the first slot to the outside edge of the last slot. Equal cell spacing is assumed. Markers should not include moving scenery, cooldown numbers or gold totals. The chosen screenshot orientation must match play.
4. Choose your hero and compare the dated item reference against your in-game shop. Price-specific advice is disabled until that comparison is acknowledged; leave it disabled if values differ.
5. Allow notifications if desired. Tap the native broadcast control, confirm Start Broadcast, then return to MLBB. The microphone is unnecessary.
6. Open the equipment scoreboard for at least three seconds. Close it so the exact spendable gold can be read. Inspect the detected values and recommendations in Rankwise during this trial.
7. Stop through the system screen-recording indicator or Control Center. Use **New match** to clear old readings before another game.

Opening Rankwise interrupts visibility of the game; it does not keep observing a hidden MLBB screen. Notification banners are the intended in-game delivery mechanism. Focus modes and user notification settings may suppress them. An arbitrary floating overlay is not implemented.

## Accuracy and practical limits

This is not direct access to MLBB's private match state. Screen capture can observe only what the game renders. Enemy equipment refreshes when the equipment scoreboard is shown. The engine does not infer unseen purchases, enemy inventory slots that failed recognition, levels, total team gold, or actual damage taken.

Template thresholds are deliberately conservative and **not calibrated against a real iPhone capture dataset**. They are distances, not confidence probabilities. Icon brightness, cooldown shading, borders, resolution, skin variants, scoreboard design and image cropping can prevent recognition. A full unknown own inventory pauses recommendations instead of silently treating unknown slots as empty.

Gold drops invalidate the owned inventory, requiring another scoreboard reading. The app never clicks the shop or spends gold. Recognition, frame orientation, memory pressure, notification delivery, and actual game compatibility remain open device-test requirements. An app can respond to capture in ways this prototype has not tested.

Suggestions are transparent heuristics, not a mathematical proof of the best buy. Hero core paths, enemy item tags, personal survival priority, item completion and available gold influence ordering. Percentage penetration is considered against recognized equipment defenses; actual hero base defenses and all combat interactions are not modeled.

## Catalogue provenance

Reference compiled from [MLBBHub's item catalogue](https://mlbbhub.com/items), retrieved 5 September 2026; the source marked its catalogue updated 3 September 2026. The current MLBB patch has not been independently verified. Exact per-item source links and artwork attribution are bundled in `CoachAssets/asset-sources.json` and `CoachAssets/catalogue.json`.

The source lists Cursed Helmet at 1,910 gold, but its listed recipe totals 1,920 gold. Its recipe is flagged and excluded from automatic purchase calculations. Do not "repair" that disagreement by guessing a number.

Artwork belongs to its respective rights holders, including MOONTON. No open redistribution license has been established. This is an independent fan prototype, not an official or approved MLBB integration.

## Validation

Run the same purchase engine used by the extension:

```bash
node --test Tests/advisor.test.js
```

The 22 tests cover freshness checks, unknown inventories, exact budgets, recursive and duplicate component credits, full inventories, alternative boots, stale enemy data, counter-item responses, malformed inputs, inconsistent recipes, and budget/slot invariants across the 17 hero paths.

No Apple SDK compile, simulator run, or real-device test has been performed in the preparation environment. Run `scripts/build_on_mac.sh` to establish the first native build result. Resolve any build or device issues before creating a signed release.

## Source layout

- `RankwiseApp/`: setup, calibration, live observation display, item reference.
- `BroadcastExtension/`: ReplayKit frame handling and notification requests.
- `Shared/`: models, shared storage, recognition and JavaScriptCore bridge.
- `CoachAssets/Advisor.js`: pure purchase-decision engine.
- `CoachAssets/catalogue.json`: dated costs, recipes, derived tags and curated hero paths.
- `scripts/configure_project.py`: deterministic native Xcode project generator.

The earlier web draft remains separate at the parent project's `dist/` directory. It is unfinished, unpublished, and is not the live iPhone companion.
