#!/usr/bin/env python3
"""Select the legacy XR1710G profile from the project's 1710 seed."""
from pathlib import Path

root = Path(__file__).resolve().parent.parent
lines = (root / "1710.config").read_text().splitlines()
drop = ("CONFIG_TARGET_DEVICE_airoha_", "CONFIG_TARGET_DEVICE_PACKAGES_airoha_", "CONFIG_TARGET_PROFILE=")
overrides = {
    "CONFIG_TARGET_MULTI_PROFILE": "n",
    "CONFIG_TARGET_PER_DEVICE_ROOTFS": "n",
    "CONFIG_TARGET_airoha_an7581_DEVICE_gemtek_xr1710g": "y",
    "CONFIG_TARGET_airoha_an7581_DEVICE_gemtek_xr1710g-ubi": "n",
    "CONFIG_TARGET_PROFILE": '"DEVICE_gemtek_xr1710g"',
    "CONFIG_SDK": "y",
}
result = []
for line in lines:
    if line.startswith(drop):
        continue
    symbol = line.removeprefix("# ").split("=", 1)[0].split(" ", 1)[0]
    if symbol in overrides:
        continue
    result.append(line)
for symbol, value in overrides.items():
    result.append(f"# {symbol} is not set" if value == "n" else f"{symbol}={value}")
(root / ".config").write_text("\n".join(result) + "\n")
