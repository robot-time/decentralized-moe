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
    Produces icon.ico (Windows) and icon.png (macOS/Linux).
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("Pillow not found -- skipping icon generation")
        return None

    SIZE = 256
    img  = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d    = ImageDraw.Draw(img)

    # Background circle
    d.ellipse([4, 4, SIZE - 4, SIZE - 4], fill=(30, 41, 59, 255))

    # Network nodes
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

    png_path = assets / "icon.png"
    img.save(png_path)

    # Windows .ico (multiple sizes)
    ico_path = assets / "icon.ico"
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icons = [img.resize(s, Image.LANCZOS) for s in sizes]
    icons[0].save(ico_path, format="ICO", sizes=sizes, append_images=icons[1:])

    # macOS .icns via iconutil (only available on macOS)
    if sys.platform == "darwin":
        _make_icns(img, assets)

    print(f"Icons generated in {assets}/")
    return ico_path if sys.platform == "win32" else png_path


def _make_icns(img: "Image.Image", assets: Path) -> None:
    """Create a .icns file for macOS using the iconutil command."""
    iconset = assets / "icon.iconset"
    iconset.mkdir(exist_ok=True)
    for size in [16, 32, 64, 128, 256, 512, 1024]:
        for scale, suffix in [(1, ""), (2, "@2x")]:
            px = size * scale
            resized = img.resize((px, px), Image.LANCZOS)
            resized.save(iconset / f"icon_{size}x{size}{suffix}.png")
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(assets / "icon.icns")],
        check=False,
    )


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

        # Bundle the default expert YAML templates
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

        # Entry point
        "main.py",
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
