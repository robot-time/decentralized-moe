"""
build.py -- Build the distributable MoE Network app
=====================================================
Three-step build:
  1. npm run build  ->  ui/dist/          (React frontend)
  2. PyInstaller    ->  dist/backend.exe  (Python API server, bundles ui/dist)
  3. go build       ->  dist/moe.exe      (Go shell: tray + WebView2 window)

Output layout (zip this up for distribution):
  dist/
    moe.exe         <- users double-click this
    backend.exe     <- started automatically by moe.exe
    experts/        <- default expert YAML templates

Usage:
    pip install pyinstaller
    python build.py
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / "dist"


# ── Step 0: icon generation ───────────────────────────────────────────────────

def generate_icon() -> Path | None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("Pillow not found -- skipping icon generation")
        return None

    resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
    SIZE = 256
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)

    d.ellipse([4, 4, SIZE - 4, SIZE - 4], fill=(30, 41, 59, 255))
    cx, cy, r = SIZE // 2, SIZE // 2, 72
    positions = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    colors    = [(59, 130, 246), (34, 197, 94), (249, 115, 22), (168, 85, 247)]
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
    ico_path = assets / "icon.ico"
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icons = [img.resize(s, resample) for s in sizes]
    icons[0].save(ico_path, format="ICO", sizes=sizes, append_images=icons[1:])
    print(f"  Icons generated: {assets}/")
    return ico_path if sys.platform == "win32" else png_path


# ── Step 1: React UI ──────────────────────────────────────────────────────────

def build_ui() -> None:
    print("\n[1/3] Building React UI...")
    ui_dir = ROOT / "ui"
    if not ui_dir.exists():
        print("  ERROR: ui/ directory not found")
        sys.exit(1)

    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    run([npm, "install"], cwd=ui_dir)
    run([npm, "run", "build"], cwd=ui_dir)

    dist = ui_dir / "dist"
    if not dist.exists():
        print("  ERROR: ui/dist/ not created by npm build")
        sys.exit(1)
    print(f"  React build complete -> {dist}")


# ── Step 2: Python backend (PyInstaller) ──────────────────────────────────────

def build_backend(icon_path: Path | None) -> None:
    print("\n[2/3] Bundling Python backend with PyInstaller...")

    sep = os.pathsep
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--console",             # keep console for now; switch to --windowed once stable
        "--name", "backend",

        # Bundle the React UI so the server can serve it
        f"--add-data=ui/dist{sep}ui/dist",
        # Bundle default expert configs
        f"--add-data=experts{sep}experts",

        # Hidden imports
        "--hidden-import=kademlia",
        "--hidden-import=kademlia.network",
        "--hidden-import=kademlia.protocol",
        "--hidden-import=aiohttp",
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.loops",
        "--hidden-import=uvicorn.loops.auto",
        "--hidden-import=uvicorn.protocols",
        "--hidden-import=uvicorn.protocols.http",
        "--hidden-import=uvicorn.protocols.http.auto",
        "--hidden-import=uvicorn.protocols.websockets",
        "--hidden-import=uvicorn.protocols.websockets.auto",
        "--hidden-import=uvicorn.lifespan",
        "--hidden-import=uvicorn.lifespan.on",
        "--hidden-import=fastapi",
        "--hidden-import=pystray",
        "--hidden-import=PIL",
        "--hidden-import=PIL.Image",
        "--hidden-import=PIL.ImageDraw",

        # Entry point
        "api_server.py",
    ]

    if icon_path and icon_path.exists() and sys.platform == "win32":
        args += ["--icon", str(ROOT / "assets" / "icon.ico")]

    run(args, cwd=ROOT)

    backend = DIST / ("backend.exe" if sys.platform == "win32" else "backend")
    if not backend.exists():
        print(f"  ERROR: {backend} not found after PyInstaller")
        sys.exit(1)
    print(f"  Backend bundle -> {backend}")


# ── Step 3: Go shell ──────────────────────────────────────────────────────────

def build_shell(icon_path: Path | None) -> None:
    print("\n[3/3] Building Go shell...")
    app_dir = ROOT / "app"
    if not app_dir.exists():
        print("  ERROR: app/ directory not found")
        sys.exit(1)

    run(["go", "mod", "tidy"], cwd=app_dir)

    out_name = "moe.exe" if sys.platform == "win32" else "moe"
    out_path = DIST / out_name

    ldflags = "-H windowsgui" if sys.platform == "win32" else ""
    args = ["go", "build", "-o", str(out_path)]
    if ldflags:
        args += ["-ldflags", ldflags]
    args += ["."]

    run(args, cwd=app_dir)

    if not out_path.exists():
        print(f"  ERROR: {out_path} not created")
        sys.exit(1)
    print(f"  Go shell -> {out_path}")


# ── Step 4: Copy extras & report ─────────────────────────────────────────────

def copy_extras() -> None:
    # Copy default experts so users get them in the zip
    experts_dst = DIST / "experts"
    experts_dst.mkdir(exist_ok=True)
    for f in (ROOT / "experts").glob("*.yaml"):
        shutil.copy2(f, experts_dst / f.name)
    print(f"\n  Extras copied -> {DIST}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def run(args: list, cwd: Path = ROOT) -> None:
    result = subprocess.run(args, cwd=cwd)
    if result.returncode != 0:
        print(f"\nERROR: command failed: {' '.join(str(a) for a in args)}")
        sys.exit(1)


# ── Entry point ───────────────────────────────────────────────────────────────

def build() -> None:
    print("=" * 56)
    print("  MoE Network -- Full Build")
    print("=" * 56)

    DIST.mkdir(exist_ok=True)
    icon_path = generate_icon()

    build_ui()
    build_backend(icon_path)
    build_shell(icon_path)
    copy_extras()

    launcher = "moe.exe" if sys.platform == "win32" else "./moe"
    print()
    print("=" * 56)
    print("  Build complete!")
    print(f"  Launch: dist/{launcher}")
    print("=" * 56)


if __name__ == "__main__":
    build()
