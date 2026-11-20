#!/usr/bin/env python3
"""install_addon.py

Installs the AlfredBridge WoW AddOn and dynamically generated Pin OnDemand slots
into the World of Warcraft Forever client directory, and/or packages a release zip.
"""

import argparse
import os
import shutil
import sys
import zipfile
from pathlib import Path

DEFAULT_WOW_FOREVER_DIR = r"C:\Program Files (x86)\World of Warcraft\_forever_"
DEFAULT_SLOTS = 20
VERSION = "1.0.0"


def generate_pin_toc(slot_num: int) -> str:
    return (
        f"## Interface: 16001\n"
        f"## Title: AlfredBridge Pin {slot_num}\n"
        f"## Dependencies: AlfredBridge\n"
        f"## Group: AlfredBridge\n"
        f"## X-Part-Of: AlfredBridge\n"
        f"## LoadOnDemand: 1\n\n"
        f"pin.lua\n"
    )


def generate_pin_lua() -> str:
    return "_G.AlfredBridgePendingPin = { seq = 0 }\n"


def install_addon(wow_dir: Path, slots: int, dry_run: bool = False) -> None:
    addon_source_dir = Path(__file__).resolve().parent.parent / "addon" / "AlfredBridge"
    if not addon_source_dir.exists():
        raise FileNotFoundError(f"Source addon directory not found: {addon_source_dir}")

    target_addons_dir = wow_dir / "Interface" / "AddOns"
    print(f"Target AddOns directory: {target_addons_dir}")

    if not dry_run:
        target_addons_dir.mkdir(parents=True, exist_ok=True)

    # 1. Install AlfredBridge main addon
    main_target = target_addons_dir / "AlfredBridge"
    print(f"{'[DRY RUN] Would copy' if dry_run else 'Copying'} {addon_source_dir} -> {main_target}")
    if not dry_run:
        main_target.mkdir(parents=True, exist_ok=True)
        for item in addon_source_dir.iterdir():
            if item.is_file():
                shutil.copy2(item, main_target / item.name)

    # 2. Generate Pin slots
    for slot in range(1, slots + 1):
        pin_dir = target_addons_dir / f"AlfredBridge_Pin{slot}"
        print(f"{'[DRY RUN] Would create' if dry_run else 'Creating'} pin slot {slot} at {pin_dir}")
        if not dry_run:
            pin_dir.mkdir(parents=True, exist_ok=True)
            toc_file = pin_dir / f"AlfredBridge_Pin{slot}.toc"
            lua_file = pin_dir / "pin.lua"
            toc_file.write_text(generate_pin_toc(slot), encoding="utf-8")
            lua_file.write_text(generate_pin_lua(), encoding="utf-8")

    print(f"Successfully {'[DRY RUN] simulated' if dry_run else 'installed'} AlfredBridge with {slots} pin slots.")


def build_distribution_zip(dist_dir: Path, slots: int) -> Path:
    addon_source_dir = Path(__file__).resolve().parent.parent / "addon" / "AlfredBridge"
    if not addon_source_dir.exists():
        raise FileNotFoundError(f"Source addon directory not found: {addon_source_dir}")

    dist_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dist_dir / f"AlfredBridge-v{VERSION}.zip"
    print(f"Packaging distribution zip to: {zip_path}")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add main addon files
        for item in sorted(addon_source_dir.iterdir()):
            if item.is_file():
                arcname = f"AlfredBridge/{item.name}"
                zf.write(item, arcname)

        # Add generated Pin slots
        for slot in range(1, slots + 1):
            pin_prefix = f"AlfredBridge_Pin{slot}"
            toc_content = generate_pin_toc(slot)
            lua_content = generate_pin_lua()
            zf.writestr(f"{pin_prefix}/{pin_prefix}.toc", toc_content)
            zf.writestr(f"{pin_prefix}/pin.lua", lua_content)

    print(f"Distribution package created ({zip_path.stat().st_size} bytes).")
    return zip_path


def main():
    parser = argparse.ArgumentParser(description="Install AlfredBridge WoW AddOn")
    parser.add_argument(
        "--wow-dir",
        default=os.environ.get("WOW_FOREVER_DIR"),
        help="Path to World of Warcraft _forever_ directory",
    )
    parser.add_argument(
        "--slots",
        type=int,
        default=DEFAULT_SLOTS,
        help="Number of AlfredBridge_Pin on-demand slots to create (default 20)",
    )
    parser.add_argument(
        "--build-zip",
        action="store_true",
        help="Package dist/AlfredBridge-v1.0.0.zip containing AddOn and all Pin slots",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without modifying files",
    )

    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    dist_dir = project_root / "dist"

    if args.build_zip:
        build_distribution_zip(dist_dir, args.slots)

    # Determine wow_dir
    wow_dir_str = args.wow_dir
    if not wow_dir_str and Path(DEFAULT_WOW_FOREVER_DIR).exists():
        wow_dir_str = DEFAULT_WOW_FOREVER_DIR

    if wow_dir_str:
        wow_dir = Path(wow_dir_str)
        install_addon(wow_dir, args.slots, dry_run=args.dry_run)
    else:
        if not args.build_zip:
            print("No --wow-dir provided and default directory not found. Use --build-zip or specify --wow-dir.", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
