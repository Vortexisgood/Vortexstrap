import sys
import os
import subprocess
from pathlib import Path
from PIL import Image

BASE_DIR = Path(__file__).parent
PNG_ICON = BASE_DIR / "vortexstraplogo.png"
ICO_ICON = BASE_DIR / "vortexstraplogo.ico"
MAIN_PY  = BASE_DIR / "launcher" / "main.py"

def convert_png_to_ico():
    if PNG_ICON.exists():
        print(f"Converting {PNG_ICON.name} to {ICO_ICON.name}...")
        img = Image.open(PNG_ICON)
        img.save(ICO_ICON, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
        print("Icon created successfully!")
    else:
        print(f"Warning: {PNG_ICON.name} not found. Build will proceed without custom icon.")

def build():
    convert_png_to_ico()

    # Eğer VortexStrap.exe çalışıyorsa kapat (PermissionError önlemek için)
    import subprocess as sp
    sp.run(["taskkill", "/f", "/im", "VortexStrap.exe"], capture_output=True)

    # Eski build klasörünü temizle
    import shutil
    build_dir = BASE_DIR / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)

    cmd = [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name=VortexStrap",
    ]

    if ICO_ICON.exists():
        cmd.append(f"--icon={ICO_ICON}")

    cmd.append(str(MAIN_PY))

    print("\nStarting PyInstaller build...")
    print("Command:", " ".join(cmd))
    
    result = subprocess.run(cmd, cwd=str(BASE_DIR))
    if result.returncode == 0:
        dist_dir = BASE_DIR / 'dist' / 'VortexStrap'
        
        # Gerekli kaynak klasörlerini dist içine kopyala
        import shutil
        for folder_name in ["Mouseİmleci", "fonts", "screenshots", "images"]:
            src_folder = BASE_DIR / "launcher" / folder_name
            if not src_folder.exists():
                src_folder = BASE_DIR / folder_name
            
            if src_folder.exists():
                dst_folder = dist_dir / folder_name
                if dst_folder.exists():
                    shutil.rmtree(dst_folder)
                shutil.copytree(src_folder, dst_folder)
                print(f"Copied resource folder: {folder_name}")

        logo_src = BASE_DIR / "images" / "Vortex_logo9.webp"
        if not logo_src.exists():
            logo_src = BASE_DIR / "launcher" / "Vortex_logo9.webp"
        if logo_src.exists():
            shutil.copy2(logo_src, dist_dir / "Vortex_logo9.webp")
            print("Copied Vortex_logo9.webp to dist")

        # Copy icon files to dist
        for icon_f in [PNG_ICON, ICO_ICON]:
            if icon_f.exists():
                shutil.copy2(icon_f, dist_dir / icon_f.name)
                print(f"Copied {icon_f.name} to dist")

        print("\n=======================================================")
        print("SUCCESS! VortexStrap.exe has been created inside:")
        print(f" -> {dist_dir / 'VortexStrap.exe'}")
        print("=======================================================")
    else:
        print("\nBuild failed with exit code:", result.returncode)

if __name__ == "__main__":
    build()
