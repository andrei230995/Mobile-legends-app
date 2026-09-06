# Build without owning a Mac

GitHub Actions can compile this project using a hosted Mac. The prepared workflow is at the repository root: `.github/workflows/ios-build.yml`. It must be placed at that location in a GitHub repository; the existing source repository is not itself a GitHub Actions runner.

This first workflow is manually triggered and performs an **unsigned simulator compile check** plus the existing purchase-engine tests. It does not sign, install, distribute or publish the app, and it needs no Apple signing secrets. A successful run would establish compilation, not real-game recognition or screen-capture reliability.

## First run

1. Connect the GitHub integration and select a repository you control, or upload the complete cloud-build package into your own private GitHub repository.
2. Keep `.github/workflows/ios-build.yml` at the repository root and the native project under `ios/`.
3. Open Actions → iPhone prototype — compile check → Run workflow.
4. Inspect the result and downloadable build log. Fix native compilation errors before proceeding.

The workflow is prepared but has **not been run**. GitHub Actions usage depends on your account's allowance and billing settings; the workflow does not provision paid runner capacity or change billing.

## Getting it onto the iPhone

Compilation and installation are separate. A simulator app is not an installable iPhone app. The next stage needs Apple signing for both the app and its broadcast extension, plus their shared App Group.

TestFlight is a distribution option under an Apple Developer Program membership. Apple lists membership at US$99 per year or the local price shown during enrollment. Do not purchase membership just to test whether this source compiles; the unsigned build above comes first.

Signed cloud builds require authorized Apple certificates, provisioning profiles and distribution access. Do not paste signing private keys or Apple account passwords into a chat. Configure those later through the build provider's protected secret storage. Signing and TestFlight distribution are not configured in this prototype.

Sources:
- https://docs.github.com/en/actions/reference/runners/github-hosted-runners
- https://docs.github.com/actions/use-cases-and-examples/deploying/installing-an-apple-certificate-on-macos-runners-for-xcode-development
- https://developer.apple.com/programs/enroll/
