"""Capture the app's real initial screens on an available iPhone simulator."""
import json
import os
from pathlib import Path
import subprocess
import time

def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)

devices = json.loads(run("xcrun", "simctl", "list", "devices", "available", "--json", capture_output=True).stdout)
phones = [d for ds in devices["devices"].values() for d in ds if "iPhone" in d["name"]]
phone = next((d for d in phones if d["name"] == "iPhone 17 Pro Max"), phones[0])
udid = phone["udid"]
print("Simulator:", phone["name"], flush=True)
if phone["state"] != "Booted":
    run("xcrun", "simctl", "boot", udid)
run("xcrun", "simctl", "bootstatus", udid, "-b")
run("xcrun", "simctl", "status_bar", udid, "override", "--time", "9:41", "--batteryState", "charged", "--batteryLevel", "100")
root = Path(os.environ["RUNNER_TEMP"])
app = root / "rankwise-build/Build/Products/Debug-iphonesimulator/Rankwise.app"
for extension in (app / "PlugIns").glob("*.appex"):
    run("codesign", "--force", "--sign", "-", str(extension))
run("codesign", "--force", "--sign", "-", str(app))
run("xcrun", "simctl", "install", udid, str(app))
out = root / "rankwise-screenshots"
out.mkdir(exist_ok=True)
for tab, name in enumerate(["01-match", "02-setup", "03-items"]):
    env = dict(os.environ, SIMCTL_CHILD_RANKWISE_SCREENSHOT_TAB=str(tab))
    run("xcrun", "simctl", "launch", udid, "com.example.rankwise", env=env)
    time.sleep(5)
    run("xcrun", "simctl", "io", udid, "screenshot", str(out / (name + ".png")))
    run("xcrun", "simctl", "terminate", udid, "com.example.rankwise")
(out / "README.txt").write_text("Actual native simulator screenshots. No live match data or demonstrated game capture. Simulator: " + phone["name"] + "\n")
