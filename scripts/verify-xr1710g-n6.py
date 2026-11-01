#!/usr/bin/env python3
"""Fail a candidate build on profile, payload, or known component regressions."""
import hashlib
import gzip
import json
import lzma
from pathlib import Path
import re
import shlex
import struct
import subprocess
import sys
import zlib

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = {
    "procd": "2026.10.18~5aef7c33-r1",
    "procd-ujail": "2026.10.18~5aef7c33-r1",
    "dnsmasq-full": "2.93-r5",
    "kmod-mac80211": "6.18.55.7.2-r9",
    "kmod-mt7996e": "6.18.55.2026.10.08~467ae2e3-r2",
    "ucode": "2026.10.18~d2e8b8d2-r1",
    "default-settings": "30",
}


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def fdt_nodes(data):
    header = struct.unpack_from(">10I", data)
    require(header[0] == 0xD00DFEED and header[1] <= len(data), "Invalid FDT header")
    pos, strings = header[2], header[3]
    stack, nodes = [], {}
    while pos < header[1]:
        token = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        if token == 1:
            end = data.index(b"\0", pos)
            stack.append(data[pos:end].decode())
            nodes["/" + "/".join(stack[1:])] = {}
            pos = (end + 4) & ~3
        elif token == 2:
            stack.pop()
        elif token == 3:
            size, name_offset = struct.unpack_from(">II", data, pos)
            pos += 8
            name_start = strings + name_offset
            name = data[name_start:data.index(b"\0", name_start)].decode()
            nodes["/" + "/".join(stack[1:])][name] = data[pos:pos + size]
            pos = (pos + size + 3) & ~3
        elif token == 4:
            pass
        elif token == 9:
            return nodes, header[1]
        else:
            raise SystemExit(f"Invalid FDT token {token}")
    raise SystemExit("Missing FDT end token")


def dtb_digest(data):
    nodes, _ = fdt_nodes(data)
    normalized = {path: {name: value.hex() for name, value in props.items()} for path, props in nodes.items()}
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def source_check():
    config = (ROOT / ".config").read_text()
    for line in ["CONFIG_TARGET_airoha_an7581_DEVICE_gemtek_xr1710g=y", "CONFIG_SDK=y"]:
        require(line in config.splitlines(), f"Required effective setting missing: {line}")
    require("CONFIG_TARGET_airoha_an7581_DEVICE_gemtek_xr1710g-ubi=y" not in config, "Wrong UBI profile selected")
    require("CONFIG_TARGET_MULTI_PROFILE=y" not in config, "Unexpected multi-profile build")
    require("5aef7c33b01455157c1373a14e2a3fa47d024c6c" in (ROOT / "package/system/procd/Makefile").read_text(), "procd signal fix was lost")
    require("PKG_RELEASE:=5" in (ROOT / "package/network/services/dnsmasq/Makefile").read_text(), "dnsmasq reload fixes were lost")
    require("PKG_RELEASE:=9" in (ROOT / "package/kernel/mac80211/Makefile").read_text(), "mac80211 baseline regressed")
    require("else if (flush && !sta->valid_links)" in (ROOT / "package/kernel/mt76/patches/0004-mt7996-flush-npu-for-non-mlo-station-removal.patch").read_text(), "Non-MLO cleanup patch missing")
    for line in (ROOT / "feeds.conf.default").read_text().splitlines():
        if not line.startswith("src-git "):
            continue
        fields = shlex.split(line)
        name, url = fields[-2:]
        require("^" in url, f"Feed is not pinned: {name}")
        expected_head = url.rsplit("^", 1)[1]
        actual_head = subprocess.check_output(["git", "-C", str(ROOT / "feeds" / name), "rev-parse", "HEAD"], text=True).strip()
        require(actual_head == expected_head, f"Feed moved: {name}: {actual_head} != {expected_head}")
    print("Source and effective profile checks passed")


def image_check(target):
    images = list(target.glob("*-gemtek_xr1710g-squashfs-sysupgrade.itb"))
    require(len(images) == 1, "Expected exactly one legacy XR1710G image")
    require(not list(target.glob("*-gemtek_xr1710g-ubi-squashfs-sysupgrade.itb")), "Unexpected UBI image")
    require(bool(list(target.glob("*sdk*.tar.*"))), "Matching SDK was not produced")
    image = images[0]
    data = image.read_bytes()
    nodes, total_size = fdt_nodes(data)
    output = ROOT / "validation"
    output.mkdir(exist_ok=True)
    report = {"image": image.name, "image_sha256": hashlib.sha256(data).hexdigest(), "hardware_runtime_validated": False, "payloads": {}}
    for name, destination in [("kernel-1", "kernel.bin"), ("fdt-1", "device.dtb"), ("rootfs-1", "rootfs.bin")]:
        path = "/images/" + name
        props = nodes[path]
        if "data" in props:
            payload = props["data"]
        else:
            offset = int.from_bytes(props.get("data-position", b""), "big")
            if "data-position" not in props:
                offset = ((total_size + 3) & ~3) + int.from_bytes(props["data-offset"], "big")
            size = int.from_bytes(props["data-size"], "big")
            require(offset + size <= len(data), f"Truncated {name}")
            payload = data[offset:offset + size]
        hashes = [v for k, v in nodes.items() if k.startswith(path + "/hash")]
        require(bool(hashes), f"Missing payload hash for {name}")
        for entry in hashes:
            algorithm = entry["algo"].rstrip(b"\0").decode()
            if algorithm == "crc32":
                actual = struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
            else:
                actual = hashlib.new(algorithm, payload).digest()
            require(actual == entry["value"], f"FIT {algorithm} mismatch for {name}")
        (output / destination).write_bytes(payload)
        report["payloads"][name] = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    kernel_props = nodes["/images/kernel-1"]
    require(int.from_bytes(kernel_props["load"], "big") == 0x80200000, "Unexpected kernel load address")
    compressed = (output / "kernel.bin").read_bytes()
    compression = kernel_props.get("compression", b"none\0").rstrip(b"\0")
    decompress = {b"lzma": lzma.decompress, b"gzip": gzip.decompress, b"none": lambda value: value}
    require(compression in decompress, f"Unsupported kernel compression: {compression!r}")
    kernel = decompress[compression](compressed)
    require(kernel[0x38:0x3c] == b"ARM\x64", "Invalid ARM64 kernel header")
    runtime_size = struct.unpack_from("<Q", kernel, 0x10)[0]
    require(max(len(kernel), runtime_size) < 0x3E00000, "Kernel overlaps reserved NPU memory")
    reference = json.loads((ROOT / "configs/xr1710g-n6-validation.json").read_text())
    digest = dtb_digest((output / "device.dtb").read_bytes())
    require(digest == reference["legacy_dtb_semantic_sha256"], "Hardware description differs from the boot-proven legacy release")
    report.update({"kernel_runtime_bytes": runtime_size, "dtb_semantic_sha256": digest, "fit_hashes_valid": True})
    (output / "image-validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


def rootfs_check(root):
    versions = {}
    for record in (root / "lib/apk/db/installed").read_text().split("\n\n"):
        fields = dict(line.split(":", 1) for line in record.splitlines() if ":" in line)
        if "P" in fields:
            versions[fields["P"]] = fields.get("V")
    for name, version in EXPECTED.items():
        require(versions.get(name) == version, f"Package regression: {name}: {versions.get(name)} != {version}")
    static = (root / "etc/dnsmasq.conf").read_text()
    require(not re.search(r"^\s*log-facility\s*=", static, re.M), "ROM static DNS template overrides UCI logging")
    require((root / "usr/libexec/immortalwrt/dnsmasq-logfacility").is_file(), "DNS migration helper missing")
    defaults = (root / "etc/uci-defaults/99-default-settings").read_text()
    require("/usr/libexec/immortalwrt/dnsmasq-logfacility" in defaults, "Initializer does not call migration helper")
    require('echo "log-facility=' not in defaults, "Initializer still forces duplicate logging")
    procd_dirs = list((ROOT / "build_dir/target-aarch64_cortex-a53_musl/procd-default").glob("procd-*"))
    require(len(procd_dirs) == 1, "Cannot identify prepared procd source")
    require("if (!in->has_jail)" in (procd_dirs[0] / "service/instance.c").read_text(), "Prepared procd lacks signal readiness fix")
    require("sigprocmask(SIG_UNBLOCK, &sigmask, NULL);" in (procd_dirs[0] / "jail/jail.c").read_text(), "Prepared ujail lacks signal readiness fix")
    mt76_dirs = list((ROOT / "build_dir/target-aarch64_cortex-a53_musl/linux-airoha_an7581").glob("mt76-*"))
    require(len(mt76_dirs) == 1, "Cannot identify prepared mt76 source")
    require("else if (flush && !sta->valid_links)" in (mt76_dirs[0] / "mt7996/main.c").read_text(), "Prepared mt7996 lacks non-MLO cleanup")
    reference = json.loads((ROOT / "configs/xr1710g-n6-validation.json").read_text())
    for relative, digest in reference.get("npu_firmware_sha256", {}).items():
        require(hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest, f"Unexpected NPU firmware: {relative}")
    report = {"critical_packages": {name: versions[name] for name in EXPECTED}, "package_count": len(versions), "prepared_fix_code_checked": True, "hardware_runtime_validated": False}
    (ROOT / "validation/component-validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "source":
        source_check()
    elif mode == "image":
        image_check(Path(sys.argv[2]))
    elif mode == "rootfs":
        rootfs_check(Path(sys.argv[2]))
    else:
        raise SystemExit("usage: verify-xr1710g-n6.py source|image <target-dir>|rootfs <root>")
