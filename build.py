"""
build.py -- Build the distributable app
========================================
Bundles everything into a single executable using PyInstaller.
No Python install required on the target machine.

Output:
  dist/MoE-Network.exe          (Windows)
  dist/MoE-Network.app          (macOS -- drag to Applications)
  dist/MoE-Network              (Linux)

Usage:
    pip install pyinstaller
    python build.py

The build is fully self-contained and installs to the user's home directory.
No admin permissions required to run the output executable.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def generate_icon() -> Path:
    """
    Generate the app icon from code (no separate icon file needed).
    Produces icon.png (all platforms) and icon.ico (Windows only).
    macOS uses the png directly -- no .icns required.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("Pillow not found -- skipping icon generation")
        return None

    resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS

    SIZE = 256
    img  = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d    = ImageDraw.Draw(img)

    # Background circle
    d.ellipse([4, 4, SIZE - 4, SIZE - 4], fill=(30, 41, 59, 255))

    # Network nodes (one per expert)
    cx, cy = SIZE // 2, SIZE // 2
    r = 72
    positions = [
        (cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy),
    ]
    colors = [
        (59, 130, 246), (34, 197, 94),
        (249, 115, 22), (168, 85, 247),
    ]
    for pos in positions:
        d.line([cx, cy, pos[0], pos[1]], fill=(255, 255, 255, 80), width=6)
    for pos, col in zip(positions, colors):
        x, y = pos
        d.ellipse([x - 22, y - 22, x + 22, y + 22], fill=(*col, 255))
    d.ellipse([cx - 18, cy - 18, cx + 18, cy + 18], fill=(255, 255, 255, 230))

    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)

    # Save PNG (used on macOS and Linux)
    png_path = assets / "icon.png"
    img.save(png_path)

    # Save ICO (Windows -- multiple sizes baked in)
    ico_path = assets / "icon.ico"
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icons = [img.resize(s, resample) for s in sizes]
    icons[0].save(ico_path, format="ICO", sizes=sizes, append_images=icons[1:])

    print(f"Icons generated in {assets}/")
    return ico_path if sys.platform == "win32" else png_path


def _patch_macos_plist(app_path: Path) -> None:
    """
    Add LSUIElement=1 to the macOS app bundle's Info.plist.

    Without this the OS treats the app as a regular GUI application that
    should have a dock icon.  When no windows are open it can be killed or
    behave unexpectedly.  LSUIElement=1 marks it as a background/menu-bar
    agent -- exactly what a tray-only app needs.
    """
    import plistlib

    plist_path = app_path / "Contents" / "Info.plist"
    if not plist_path.exists():
        print(f"  Warning: Info.plist not found at {plist_path}")
        return

    with open(plist_path, "rb") as f:
        plist = plistlib.load(f)

    plist["LSUIElement"] = True
    # Also ensure the bundle doesn't show in the Force-Quit panel
    plist.setdefault("NSHighResolutionCapable", True)

    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)

    print("  macOS: patched Info.plist (LSUIElement=1)")


def build() -> None:
    print("=" * 56)
    print("  Building MoE Network distributable")
    print("=" * 56)
    print()

    # Generate icon
    icon_path = generate_icon()

    # PyInstaller arguments
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",         # single executable (easiest to share)
        "--windowed",        # no terminal/console window on Windows + macOS
        "--name", "MoE-Network",

        # Bundle the default expert YAML templates and settings UI script
        f"--add-data=experts{os.pathsep}experts",

        # Hidden imports PyInstaller misses for these libraries
        "--hidden-import=kademlia",
        "--hidden-import=kademlia.network",
        "--hidden-import=kademlia.protocol",
        "--hidden-import=aiohttp",
        "--hidden-import=pystray",
        "--hidden-import=pystray._win32",
        "--hidden-import=pystray._darwin",
        "--hidden-import=pystray._gtk",
        "--hidden-import=PIL",
        "--hidden-import=PIL.Image",
        "--hidden-import=PIL.ImageDraw",
        "--hidden-import=settings_ui",

        # Entry point
        "main.py",
    ]

    # macOS: pystray's darwin backend requires pyobjc (AppKit / Foundation)
    if sys.platform == "darwin":
        args += [
            "--hidden-import=AppKit",
            "--hidden-import=Foundation",
            "--hidden-import=objc",
            "--hidden-import=Cocoa",
            "--osx-bundle-identifier=com.moe-network.tray",
        ]

    # Add platform-specific icon
    if icon_path and icon_path.exists():
        if sys.platform == "win32":
            args += ["--icon", str(ROOT / "assets" / "icon.ico")]
        elif sys.platform == "darwin":
            icns = ROOT / "assets" / "icon.icns"
            if icns.exists():
                args += ["--icon", str(icns)]

    print("Running PyInstaller...")
    result = subprocess.run(args, cwd=ROOT)

    if result.returncode == 0:
        dist = ROOT / "dist"

        # macOS: patch Info.plist so the app runs as a menu-bar-only agent.
        # LSUIElement=1 hides the dock icon and lets pystray own the status bar.
        if sys.platform == "darwin":
            _patch_macos_plist(dist / "MoE-Network.app")

        print()
        print("=" * 56)
        print("  Build successful!")
        print(f"  Output: {dist}/")
        print()
        if sys.platform == "win32":
            print("  Share dist/MoE-Network.exe with your friends.")
            print("  They double-click it -- no install needed.")
        elif sys.platform == "darwin":
            print("  Share dist/MoE-Network.app with your friends.")
            print("  They drag it to Applications -- no install needed.")
        else:
            print("  Share dist/MoE-Network with your friends.")
            print("  They chmod +x it and run it.")
        print("=" * 56)
    else:
        print()
        print("Build failed. Make sure PyInstaller is installed:")
        print("  pip install pyinstaller")
        sys.exit(1)


if __name__ == "__main__":
    build()
