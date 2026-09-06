"""Restore pinned recognition images before opening or building the Xcode project."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def restore(asset):
    target = ROOT / "Resources" / "Items" / asset["name"]
    expected = asset["sha256"]
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
        return
    request = Request(asset["url"], headers={"User-Agent": "Rankwise-prototype-assets/1.0"})
    with urlopen(request, timeout=45) as response:
        data = response.read(2_000_001)
    if len(data) > 2_000_000 or hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError(f"Image changed or invalid: {asset['name']}; refusing to use it")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


if __name__ == "__main__":
    assets = json.loads((ROOT / "Resources" / "image-downloads.json").read_text())
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(restore, assets))
    print(f"Verified {len(assets)} recognition images")
