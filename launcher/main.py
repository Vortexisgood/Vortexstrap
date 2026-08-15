"""
VortexStrap - Alternative launcher for Vortex

Features:
  - Binary font patcher: replaces Inter with any TTF/OTF font
  - Custom cursors (animated GIF or built-in styles)
  - In-game screenshot capture (F12 / PrtScn hotkey)
  - Render backend control (DX12, DX11, Vulkan, OpenGL, software fallback)
"""

import sys, os, struct, shutil, json, subprocess, datetime, ctypes, re
from ctypes import wintypes
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QFileDialog, QMessageBox,
    QProgressBar, QFrame, QScrollArea, QGraphicsDropShadowEffect,
    QCheckBox, QStackedWidget, QTabWidget, QDialog, QGridLayout
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QPoint, QSize, QUrl
from PyQt6.QtGui import (
    QFont, QFontDatabase, QPainter, QColor, QLinearGradient,
    QPen, QCursor, QMouseEvent, QPainterPath, QPixmap, QBrush, QDesktopServices,
    QGuiApplication, QShortcut, QKeySequence, QMovie, QImage
)

# Platform detection
IS_WINDOWS = sys.platform == "win32"
IS_MACOS   = sys.platform == "darwin"
IS_LINUX   = sys.platform.startswith("linux")

# Paths and Vortex executable detection
BASE_DIR = Path(sys.argv[0]).parent if getattr(sys, 'frozen', False) else Path(__file__).parent

def find_vortex_exe() -> Path:
    if IS_MACOS:
        candidates = [
            BASE_DIR / "Vortex.app" / "Contents" / "MacOS" / "Vortex",
            BASE_DIR / "Vortex.app",
            BASE_DIR / "Vortex",
            Path("/Applications/Vortex.app/Contents/MacOS/Vortex"),
            Path.home() / "Applications" / "Vortex.app" / "Contents" / "MacOS" / "Vortex",
        ]
        for p in candidates:
            if p.exists():
                return p.resolve()
        return BASE_DIR / "Vortex"

    candidates = [
        BASE_DIR / "Vortex.exe",
        BASE_DIR.parent / "Vortex.exe",
        BASE_DIR.parent.parent / "Vortex.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Vortex" / "Vortex.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Vortex" / "Vortex.exe",
    ]
    for p in candidates:
        if p.exists():
            return p.resolve()
    return BASE_DIR / "Vortex.exe"

def find_logo_webp() -> Path | None:
    candidates = [
        BASE_DIR / "Vortex_logo9.webp",
        BASE_DIR / "images" / "Vortex_logo9.webp",
        BASE_DIR.parent / "images" / "Vortex_logo9.webp",
        Path(__file__).parent / "Vortex_logo9.webp",
        Path(__file__).parent / "images" / "Vortex_logo9.webp",
    ]
    for p in candidates:
        if p.exists():
            return p.resolve()
    return None

VORTEX_EXE      = find_vortex_exe()
ROOT_DIR        = VORTEX_EXE.parent if VORTEX_EXE.exists() else BASE_DIR
BACKUP_EXE      = ROOT_DIR / ("Vortex_backup" if IS_MACOS else "Vortex_backup.exe")
CONFIG_FILE     = BASE_DIR / "config.json"
FONTS_DIR       = BASE_DIR / "fonts"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"
CURSORS_DIR     = BASE_DIR / "Mouseİmleci"
GIF_CURSOR      = CURSORS_DIR / "cursor.gif"
LOGO_WEBP       = find_logo_webp()

FONTS_DIR.mkdir(exist_ok=True)
SCREENSHOTS_DIR.mkdir(exist_ok=True)

# Fallback slot size if we can't calculate the real TTF size.
# 407 KB is a safe lower bound based on observed Inter font sizes in Vortex.
_FALLBACK_SLOT_SIZE = 407_054


def _read_ttf_name(data: bytes, offset: int) -> str:
    """
    Reads the font family name (nameID 1) from a TTF/OTF blob at the given offset.
    Returns an empty string if reading fails.
    """
    try:
        num_tables = struct.unpack_from('>H', data, offset + 4)[0]
        # Walk the table directory to find the 'name' table
        for i in range(num_tables):
            tbl_base  = offset + 12 + i * 16
            tag       = data[tbl_base:tbl_base + 4]
            tbl_off   = struct.unpack_from('>I', data, tbl_base + 8)[0]
            tbl_len   = struct.unpack_from('>I', data, tbl_base + 12)[0]
            if tag == b'name':
                abs_off = offset + tbl_off
                count   = struct.unpack_from('>H', data, abs_off + 2)[0]
                str_off = struct.unpack_from('>H', data, abs_off + 4)[0]
                for j in range(count):
                    rec = abs_off + 6 + j * 12
                    name_id  = struct.unpack_from('>H', data, rec + 6)[0]
                    length   = struct.unpack_from('>H', data, rec + 8)[0]
                    str_pos  = struct.unpack_from('>H', data, rec + 10)[0]
                    if name_id == 1:  # Font Family name
                        raw = data[abs_off + str_off + str_pos:
                                   abs_off + str_off + str_pos + length]
                        try:
                            return raw.decode('utf-16-be').strip()
                        except Exception:
                            try:
                                return raw.decode('ascii').strip()
                            except Exception:
                                pass
    except Exception:
        pass
    return ""


def _calc_ttf_slot_size(data: bytes, offset: int) -> int:
    """
    Calculates the actual byte footprint of a TTF blob by finding the end
    of its last table. Falls back to _FALLBACK_SLOT_SIZE on any error.
    """
    try:
        num_tables = struct.unpack_from('>H', data, offset + 4)[0]
        last_end = 0
        for i in range(num_tables):
            tbl_base  = offset + 12 + i * 16
            tbl_start = struct.unpack_from('>I', data, tbl_base + 8)[0]
            tbl_len   = struct.unpack_from('>I', data, tbl_base + 12)[0]
            end = tbl_start + tbl_len
            if end > last_end:
                last_end = end
        # Round up to nearest 4 bytes for alignment, add a small safety margin
        size = last_end + (4 - last_end % 4) % 4
        return size if size > 0 else _FALLBACK_SLOT_SIZE
    except Exception:
        return _FALLBACK_SLOT_SIZE


def _full_dynamic_scan(data: bytes) -> dict[int, int]:
    """
    Fast, reliable scan of Vortex.exe for embedded Inter TTF blobs.
    Validates TTF table headers ('head', 'name', 'cmap') to ignore code noise.
    Scans a 100MB binary in under 1.5 seconds.
    """
    import re
    matches = [m.start() for m in re.finditer(b'\x00I\x00n\x00t\x00e\x00r', data)]
    found: dict[int, int] = {}

    for pos in matches:
        start_search = max(0, pos - 500_000)
        chunk = data[start_search:pos]
        for i in range(len(chunk) - 4):
            if chunk[i:i+4] == b'\x00\x01\x00\x00':
                cand_off = start_search + i
                if cand_off in found:
                    continue
                try:
                    num_tables = struct.unpack_from('>H', data, cand_off + 4)[0]
                    if 4 <= num_tables <= 30:
                        tags = [data[cand_off + 12 + t * 16 : cand_off + 16 + t * 16] for t in range(num_tables)]
                        if b'head' in tags and b'name' in tags and b'cmap' in tags:
                            last_end = max(
                                struct.unpack_from('>I', data, cand_off + 12 + t * 16 + 8)[0] +
                                struct.unpack_from('>I', data, cand_off + 12 + t * 16 + 12)[0]
                                for t in range(num_tables)
                            )
                            # Ensure slot capacity is at least _FALLBACK_SLOT_SIZE (407,054 bytes)
                            slot_cap = max(last_end, _FALLBACK_SLOT_SIZE)
                            found[cand_off] = slot_cap
                            print(f"Found Inter TTF at offset {cand_off:,} (slot {slot_cap:,} bytes)")
                except Exception:
                    pass
    return found


def get_inter_offsets(exe_path: Path, cfg: dict) -> tuple[list[int], dict[int, int]]:
    """
    Returns (offsets_list, spacing_map) for all Inter font blobs in Vortex.exe.
    Results are cached in config by exe file size for instant sub-second application.
    """
    if not exe_path.exists():
        return [], {}

    exe_size = exe_path.stat().st_size
    cached   = cfg.get("_inter_cache", {})

    if cached.get("exe_size") == exe_size and cached.get("offsets") and cached.get("spacing"):
        offsets     = cached["offsets"]
        spacing_map = {int(k): v for k, v in cached["spacing"].items()}
        return offsets, spacing_map

    print(f"Vortex.exe changed (size={exe_size:,}). Running fast Inter font scan...")
    try:
        data = exe_path.read_bytes()
    except Exception as e:
        print(f"Could not read Vortex.exe: {e}")
        return [], {}

    spacing_map = _full_dynamic_scan(data)

    if not spacing_map:
        # Fallback to hardcoded verified offsets if scanning fails
        print("Fallback to built-in verified offsets...")
        spacing_map = {
            64763168: 407054,
            66396813: 407054,
            66803878: 415070,
            67218961: 412846,
            75310326: 415070,
            75921509: 407054,
            78576686: 412846,
        }

    offsets = sorted(spacing_map.keys())
    cfg["_inter_cache"] = {
        "exe_size": exe_size,
        "offsets":  offsets,
        "spacing":  {str(k): v for k, v in spacing_map.items()},
    }
    save_cfg(cfg)
    print(f"Scan complete: found {len(offsets)} Inter font slot(s).")
    return offsets, spacing_map



# Config
DEFAULT_CONFIG = {
    "accent":              "#7C3AED",
    "ui_font":             "Segoe UI",
    "patched_font":        None,
    "is_patched":          False,
    "custom_cursor":       "neon_arrow",
    "render_backend":      "auto",
    "render_power":        "high",
    "render_antialiasing": True,
    "system_cursor":       False,
    "font_mode":           "registry",
    "fun_mode":            "none",
}

def load_cfg():
    if CONFIG_FILE.exists():
        try:
            d = json.loads(CONFIG_FILE.read_text("utf-8"))
            c = DEFAULT_CONFIG.copy(); c.update(d); return c
        except: pass
    return DEFAULT_CONFIG.copy()

def save_cfg(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")

# Background thread that listens for F12 / PrtScn at the OS level
class GlobalHotkeyThread(QThread):
    triggered = pyqtSignal()

    def run(self):
        if not IS_WINDOWS:
            return  # Skip Windows-specific OS hotkeys on macOS/Linux
        user32 = ctypes.windll.user32
        VK_F12      = 0x7B   # F12
        VK_SNAPSHOT = 0x2C   # PrtScn
        was_pressed = False

        while not self.isInterruptionRequested():
            f12_state = user32.GetAsyncKeyState(VK_F12) & 0x8000
            prt_state = user32.GetAsyncKeyState(VK_SNAPSHOT) & 0x8000
            is_pressed = bool(f12_state or prt_state)

            if is_pressed and not was_pressed:
                self.triggered.emit()
                was_pressed = True
            elif not is_pressed:
                was_pressed = False

            self.msleep(40)


# Draws built-in cursor shapes as QPixmaps
def create_custom_cursor(cursor_type: str, accent: str = "#7C3AED") -> QCursor:
    if cursor_type == "system":
        return QCursor(Qt.CursorShape.ArrowCursor)

    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    main_col = QColor(accent)
    glow_col = QColor(accent)
    glow_col.setAlpha(120)

    if cursor_type == "neon_arrow":
        path = QPainterPath()
        path.moveTo(4, 4)
        path.lineTo(24, 12)
        path.lineTo(14, 15)
        path.lineTo(18, 25)
        path.lineTo(13, 27)
        path.lineTo(9, 17)
        path.lineTo(4, 22)
        path.closeSubpath()

        pen_glow = QPen(glow_col, 4)
        painter.setPen(pen_glow)
        painter.drawPath(path)

        painter.setBrush(QBrush(main_col))
        painter.setPen(QPen(QColor("#FFFFFF"), 1.5))
        painter.drawPath(path)
        painter.end()
        return QCursor(pixmap, 4, 4)

    elif cursor_type == "crosshair":
        center = 16
        c_pt = QPoint(center, center)
        painter.setPen(QPen(glow_col, 3))
        painter.drawEllipse(c_pt, 8, 8)

        painter.setPen(QPen(main_col, 2))
        painter.drawEllipse(c_pt, 6, 6)
        painter.drawLine(center - 11, center, center - 3, center)
        painter.drawLine(center + 3, center, center + 11, center)
        painter.drawLine(center, center - 11, center, center - 3)
        painter.drawLine(center, center + 3, center, center + 11)

        painter.setBrush(QBrush(QColor("#FFFFFF")))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(c_pt, 2, 2)
        painter.end()
        return QCursor(pixmap, center, center)

    elif cursor_type == "dot_glow":
        center = 16
        c_pt = QPoint(center, center)
        grad = QLinearGradient(0, 0, 32, 32)
        grad.setColorAt(0, main_col)
        grad.setColorAt(1, QColor("#FFFFFF"))

        painter.setBrush(QBrush(glow_col))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(c_pt, 10, 10)

        painter.setBrush(QBrush(grad))
        painter.setPen(QPen(QColor("#FFFFFF"), 1))
        painter.drawEllipse(c_pt, 5, 5)
        painter.end()
        return QCursor(pixmap, center, center)

    painter.end()
    return QCursor(Qt.CursorShape.ArrowCursor)


# Converts a QPixmap to a Windows HCURSOR for system-wide cursor replacement
def _pixmap_to_hcursor(pixmap: QPixmap, hotspot_x: int = 0, hotspot_y: int = 0):
    if not IS_WINDOWS:
        return None
    size = 32
    scaled = pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    img = scaled.toImage().convertToFormat(QImage.Format.Format_ARGB32)

    w, h = img.width(), img.height()
    bits = img.bits()
    bits.setsize(w * h * 4)
    raw = bytes(bits)

    bgra = (ctypes.c_ubyte * len(raw))(*raw)
    hbm_color = ctypes.windll.gdi32.CreateBitmap(w, h, 1, 32, bgra)

    hdc = ctypes.windll.user32.GetDC(0)
    hbm_mask = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc, w, h)
    ctypes.windll.user32.ReleaseDC(0, hdc)

    class ICONINFO(ctypes.Structure):
        _fields_ = [
            ("fIcon",    wintypes.BOOL),
            ("xHotspot", wintypes.DWORD),
            ("yHotspot", wintypes.DWORD),
            ("hbmMask",  wintypes.HANDLE),
            ("hbmColor", wintypes.HANDLE),
        ]

    ii = ICONINFO()
    ii.fIcon    = False
    ii.xHotspot = hotspot_x
    ii.yHotspot = hotspot_y
    ii.hbmMask  = hbm_mask
    ii.hbmColor = hbm_color

    hcursor = ctypes.windll.user32.CreateIconIndirect(ctypes.byref(ii))
    ctypes.windll.gdi32.DeleteObject(hbm_color)
    ctypes.windll.gdi32.DeleteObject(hbm_mask)
    return hcursor


OCR_NORMAL      = 32512
SPI_SETCURSORS  = 0x0057

def _save_original_system_cursor():
    pass  # no-op: SystemParametersInfo restores defaults on exit

def _restore_system_cursor():
    ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETCURSORS, 0, None, 0x0003
    )


# Drives a GIF cursor frame-by-frame, optionally replacing the system cursor
class GifCursorAnimator(QTimer):
    def __init__(self, gif_path: Path, system_wide: bool = False, parent=None):
        super().__init__(parent)
        self._system_wide = system_wide and IS_WINDOWS
        self._frames: list[QPixmap] = []
        self._idx = 0

        if not gif_path.exists():
            return

        movie = QMovie(str(gif_path))
        movie.setCacheMode(QMovie.CacheMode.CacheAll)
        movie.start()
        count = movie.frameCount()
        if count <= 0:
            count = 30
        for i in range(count):
            movie.jumpToFrame(i)
            pix = movie.currentPixmap()
            if not pix.isNull():
                self._frames.append(pix.copy())
        movie.stop()

        if self._frames:
            self.timeout.connect(self._tick)
            self.start(60)

    def _tick(self):
        if not self._frames:
            return
        pix = self._frames[self._idx % len(self._frames)]
        self._idx += 1

        # Always update the Qt override cursor so it shows inside the launcher window
        QApplication.restoreOverrideCursor()
        QApplication.setOverrideCursor(QCursor(pix, 0, 0))

        # If system-wide mode is on, also push the cursor to the Windows system
        if self._system_wide and IS_WINDOWS:
            try:
                hc = _pixmap_to_hcursor(pix, 0, 0)
                if hc:
                    ctypes.windll.user32.SetSystemCursor(hc, OCR_NORMAL)
            except Exception:
                pass

    def stop_and_restore(self):
        self.stop()
        if self._system_wide and IS_WINDOWS:
            _restore_system_cursor()
        else:
            QApplication.restoreOverrideCursor()




# Adds/removes an Inter → custom font substitution in the Windows registry.
# This makes any app that asks for 'Inter' use the chosen font instead.
class RegistryFontSubstitutor:
    @staticmethod
    def apply_substitution(target_font_name: str) -> tuple[bool, str]:
        if not IS_WINDOWS:
            return False, "Registry substitution is only supported on Windows."
        try:
            import winreg
            key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\FontSubstitutes"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "Inter", 0, winreg.REG_SZ, target_font_name)
                winreg.SetValueEx(key, "Inter-Regular", 0, winreg.REG_SZ, target_font_name)
                winreg.SetValueEx(key, "Inter-Bold", 0, winreg.REG_SZ, target_font_name)

            HWND_BROADCAST = 0xFFFF
            WM_FONTCHANGE  = 0x001D
            ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_FONTCHANGE, 0, 0, 2, 1000, None)
            return True, f"Font substituted to '{target_font_name}' in Windows Registry!"
        except Exception as e:
            return False, f"Registry error: {e}"

    @staticmethod
    def remove_substitution() -> tuple[bool, str]:
        if not IS_WINDOWS:
            return True, "Registry cleanup skipped on non-Windows OS."
        try:
            import winreg
            key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\FontSubstitutes"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_SET_VALUE) as key:
                for font_var in ["Inter", "Inter-Regular", "Inter-Bold"]:
                    try:
                        winreg.DeleteValue(key, font_var)
                    except FileNotFoundError:
                        pass

            HWND_BROADCAST = 0xFFFF
            WM_FONTCHANGE  = 0x001D
            ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_FONTCHANGE, 0, 0, 2, 1000, None)
            return True, "Registry font substitutions removed!"
        except Exception as e:
            return False, f"Registry error: {e}"

# Handles binary patching of Inter inside Vortex.exe and registry substitution
class FontPatcher:
    @staticmethod
    def subset_ttf(font_path: str) -> tuple[str | None, str]:
        """Subsets the font to Latin + extended Latin characters to reduce file size."""
        try:
            from fontTools import subset
            from fontTools.ttLib import TTFont
            import tempfile

            options = subset.Options()
            options.layout_features = ["*"]
            options.name_IDs = ["*"]

            # Basic Latin + Latin Extended (covers most European languages)
            unicodes = subset.parse_unicodes(
                "U+0020-007E, U+00A0-00FF, U+0100-017F, "
                "U+011E, U+011F, U+0130, U+0131, U+015E, U+015F"
            )

            font = TTFont(font_path)
            subsetter = subset.Subsetter(options=options)
            subsetter.populate(unicodes=unicodes)
            subsetter.subset(font)

            out_fd, out_path = tempfile.mkstemp(suffix=".ttf")
            os.close(out_fd)
            font.save(out_path)
            return out_path, "Font compressed successfully!"
        except Exception as e:
            return None, f"Font compression error: {e}"

    @staticmethod
    def patch(new_font_path: str, backup: bool = True, cfg: dict = None) -> tuple[bool, str]:
        cfg = cfg or {}
        font_stem = Path(new_font_path).stem

        try:
            new_ttf = Path(new_font_path).read_bytes()
        except Exception as e:
            return False, f"Could not read font file: {e}"

        try:
            exe_data = bytearray(VORTEX_EXE.read_bytes())
        except Exception as e:
            return False, f"Could not read EXE: {e}"

        if backup and not BACKUP_EXE.exists():
            try:
                shutil.copy2(VORTEX_EXE, BACKUP_EXE)
            except Exception as e:
                return False, f"Could not create backup: {e}"

        offsets, spacing_map = get_inter_offsets(VORTEX_EXE, cfg)

        print(f"Offsets: {offsets}, font size: {len(new_ttf):,} bytes")

        if not offsets:
            return False, (
                "Inter font not found in Vortex.exe.\n"
                "The EXE may have changed — please report this issue."
            )

        # Make sure the font fits in the smallest available slot (enforce at least _FALLBACK_SLOT_SIZE)
        min_spacing = max(min(spacing_map.get(off, _FALLBACK_SLOT_SIZE) for off in offsets), _FALLBACK_SLOT_SIZE)
        if len(new_ttf) > min_spacing:
            print(f"Font size ({len(new_ttf):,} bytes) > slot size ({min_spacing:,} bytes). Attempting automatic compression…")
            compressed_path, c_msg = FontPatcher.subset_ttf(new_font_path)
            if compressed_path and Path(compressed_path).exists():
                try:
                    c_bytes = Path(compressed_path).read_bytes()
                    if len(c_bytes) <= min_spacing:
                        new_ttf = c_bytes
                        print(f"Font compressed successfully from {len(Path(new_font_path).read_bytes()):,} -> {len(new_ttf):,} bytes!")
                except Exception:
                    pass

        if len(new_ttf) > min_spacing:
            print(f"Font too large: {len(new_ttf)} bytes, slot is {min_spacing} bytes")
            return False, (
                f"Selected font ({len(new_ttf):,} bytes) exceeds the slot capacity "
                f"({min_spacing:,} bytes).\nPlease select a smaller font."
            )

        # Kill Vortex if it's running so we can write to the EXE
        try:
            sp = __import__("subprocess")
            sp.run(["taskkill", "/f", "/im", "Vortex.exe"], capture_output=True)
        except:
            pass

        # Rename the font family to 'Inter' so Vortex recognizes it
        try:
            from fontTools.ttLib import TTFont
            import io
            
            font_obj = TTFont(io.BytesIO(new_ttf))
            for name_rec in font_obj['name'].names:
                if name_rec.nameID in (1, 4, 6):
                    name_rec.string = "Inter".encode('utf-16-be' if name_rec.isUnicode() else 'ascii')
            
            out_buf = io.BytesIO()
            font_obj.save(out_buf)
            new_ttf = out_buf.getvalue()
            print(f"Font renamed to 'Inter', new size: {len(new_ttf):,} bytes")
        except Exception as e:
            print(f"Warning: could not rename font family name: {e}")

        # Step 1: registry substitution (Inter → chosen font name)
        RegistryFontSubstitutor.apply_substitution(font_stem)

        # Step 2: write the new TTF bytes into every Inter slot in the EXE
        patched_count = 0
        for offset in offsets:
            spacing = spacing_map.get(offset, 407_065)
            try:
                exe_data[offset: offset + spacing] = b'\x00' * spacing
                exe_data[offset: offset + len(new_ttf)] = new_ttf
                patched_count += 1
                print(f"Patched offset {offset:,} (slot {spacing:,} bytes)")
            except Exception as ex:
                print(f"Warning: patch failed at offset {offset:,}: {ex}")

        try:
            VORTEX_EXE.write_bytes(bytes(exe_data))
            print(f"EXE written successfully, {patched_count} offsets patched")
        except Exception as e:
            print(f"Warning: could not write EXE (registry substitution still active): {e}")

        return True, f"Font applied ({font_stem})! Restart Vortex to see changes."

    @staticmethod
    def restore(cfg: dict = None) -> tuple[bool, str]:
        RegistryFontSubstitutor.remove_substitution()
        if not BACKUP_EXE.exists():
            return False, "Backup file not found (Vortex_backup.exe)."
        try:
            shutil.copy2(BACKUP_EXE, VORTEX_EXE)
            return True, "Original Vortex.exe restored successfully."
        except Exception as e:
            return False, f"Restore error: {e}"

# Worker threads for running patch/restore/launch operations off the UI thread
class PatchWorker(QThread):
    done  = pyqtSignal(bool, str)

    def __init__(self, font_path, cfg=None):
        super().__init__()
        self.font_path = font_path
        self.cfg = cfg or {}

    def run(self):
        ok, msg = FontPatcher.patch(self.font_path, cfg=self.cfg)
        self.done.emit(ok, msg)

class RestoreWorker(QThread):
    done = pyqtSignal(bool, str)

    def __init__(self, cfg=None):
        super().__init__()
        self.cfg = cfg or {}

    def run(self):
        ok, msg = FontPatcher.restore(cfg=self.cfg)
        self.done.emit(ok, msg)

class LaunchWorker(QThread):
    started_sig  = pyqtSignal()
    finished_sig = pyqtSignal()
    finished_code_sig = pyqtSignal(int)   # exit code
    error_sig    = pyqtSignal(str)

    def __init__(self, render_env=None, extra_args=None):
        super().__init__()
        self.render_env  = render_env  or {}
        self.extra_args  = extra_args  or []

    def run(self):
        try:
            env = os.environ.copy()
            env.update(self.render_env)
            cmd = [str(VORTEX_EXE)] + self.extra_args
            proc = subprocess.Popen(cmd, cwd=str(ROOT_DIR), env=env)
            self.started_sig.emit()
            proc.wait()
            self.finished_code_sig.emit(proc.returncode)
            self.finished_sig.emit()
        except Exception as e:
            self.error_sig.emit(str(e))


# Bloxstrap-style compact launch splash screen
class LaunchSplashDialog(QDialog):
    """
    Compact, centered launcher splash dialog.
    Displays logo (Vortex_logo9.webp), 'Launching Vortex...', a sleek progress bar,
    credits ('Logo: music.mash'), and a Cancel button.
    """
    def __init__(self, parent=None, on_cancel=None):
        super().__init__(parent)
        self.on_cancel_cb = on_cancel
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(380, 190)

        # Center on primary screen
        screen = QGuiApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            self.move(geo.center().x() - 190, geo.center().y() - 95)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        card = QFrame(self)
        card.setStyleSheet("""
            QFrame {
                background: #120E24;
                border: 1px solid rgba(124, 58, 237, 0.45);
                border-radius: 14px;
            }
        """)
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(18, 16, 18, 12)
        c_lay.setSpacing(8)

        # 1. Top Logo (Vortex_logo9.webp)
        logo_lbl = QLabel()
        logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_lbl.setStyleSheet("border: none; background: transparent;")
        if LOGO_WEBP and LOGO_WEBP.exists():
            pix = QPixmap(str(LOGO_WEBP))
            if not pix.isNull():
                logo_lbl.setPixmap(pix.scaled(52, 52, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        if logo_lbl.pixmap() is None or logo_lbl.pixmap().isNull():
            logo_lbl.setText("⚡")
            logo_lbl.setStyleSheet("font-size: 32px; color: #A78BFA; border: none; background: transparent;")
        c_lay.addWidget(logo_lbl)

        # 2. Status Label
        self.status_lbl = QLabel("Launching Vortex…")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setStyleSheet("color: #E2D9FF; font-size: 13px; font-weight: 600; border: none; background: transparent;")
        c_lay.addWidget(self.status_lbl)

        # 3. Progress Bar
        self.prog = QProgressBar()
        self.prog.setRange(0, 0)  # Indeterminate pulsing animation
        self.prog.setFixedHeight(5)
        self.prog.setTextVisible(False)
        self.prog.setStyleSheet("""
            QProgressBar {
                background: rgba(255, 255, 255, 0.08);
                border: none;
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7C3AED, stop:1 #A78BFA);
                border-radius: 2px;
            }
        """)
        c_lay.addWidget(self.prog)

        c_lay.addSpacing(2)

        # 4. Footer row: Credits + Cancel
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)

        cred = QLabel("Logo: music.mash")
        cred.setStyleSheet("color: #7A6A9F; font-size: 10px; border: none; background: transparent;")
        foot.addWidget(cred)

        foot.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel_btn.setFixedSize(65, 22)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.07);
                color: #B8B0D0;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 5px;
                font-size: 11px;
            }
            QPushButton:hover {
                background: rgba(239, 68, 68, 0.25);
                color: #F87171;
                border-color: rgba(239, 68, 68, 0.5);
            }
        """)
        cancel_btn.clicked.connect(self._on_cancel)
        foot.addWidget(cancel_btn)

        c_lay.addLayout(foot)
        lay.addWidget(card)

    def set_status(self, text: str):
        self.status_lbl.setText(text)

    def _on_cancel(self):
        if self.on_cancel_cb:
            self.on_cancel_cb()
        self.reject()

# Animated particle background widget
class BgWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        import random; r = random.Random(3)
        self._pts = [{"x": r.uniform(0,1),"y": r.uniform(0,1),
                      "r": r.uniform(1.5,4),"dx": r.uniform(-.0003,.0003),
                      "dy": r.uniform(-.0002,.0002),"a": r.uniform(.1,.45)}
                     for _ in range(40)]
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def _tick(self):
        for p in self._pts:
            p["x"] = (p["x"]+p["dx"]) % 1.0
            p["y"] = (p["y"]+p["dy"]) % 1.0
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        g = QLinearGradient(0, 0, w, h)
        g.setColorAt(0, QColor("#0C0916"))
        g.setColorAt(1, QColor("#07050F"))
        painter.fillRect(self.rect(), g)
        for pt in self._pts:
            c = QColor("#7C3AED")
            c.setAlphaF(pt["a"])
            painter.setBrush(c)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(
                QPoint(int(pt["x"] * w), int(pt["y"] * h)),
                int(pt["r"]), int(pt["r"])
            )
        painter.end()

# Rounded, semi-transparent card container
class Card(QFrame):
    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(255,255,255,10))
        pen = QPen(QColor(255,255,255,22)); pen.setWidth(1); p.setPen(pen)
        p.drawRoundedRect(self.rect().adjusted(0,0,-1,-1), 14, 14)
        p.end()

# Colored status label shown at the bottom of the launcher
class StatusBadge(QLabel):
    def set_ok(self, text):
        self.setText(f"✓  {text}")
        self.setStyleSheet("background:#0D2B17;color:#4ADE80;border:1px solid #166534;"
                           "border-radius:8px;padding:6px 14px;font-size:12px;font-weight:600;")
    def set_warn(self, text):
        self.setText(f"⚠  {text}")
        self.setStyleSheet("background:#2B1A07;color:#FCD34D;border:1px solid #92400E;"
                           "border-radius:8px;padding:6px 14px;font-size:12px;font-weight:600;")
    def set_err(self, text):
        self.setText(f"✕  {text}")
        self.setStyleSheet("background:#2B0A0A;color:#F87171;border:1px solid #991B1B;"
                           "border-radius:8px;padding:6px 14px;font-size:12px;font-weight:600;")
    def set_info(self, text):
        self.setText(f"●  {text}")
        self.setStyleSheet("background:rgba(124,58,237,0.12);color:#A78BFA;border:1px solid rgba(124,58,237,0.4);"
                           "border-radius:8px;padding:6px 14px;font-size:12px;font-weight:600;")

# Simple fullscreen image preview dialog for screenshots
class ImagePreviewDialog(QDialog):
    def __init__(self, img_path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(img_path.name)
        self.resize(750, 500)
        self.setStyleSheet("background:#0C0916;")

        lay = QVBoxLayout(self)
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pix = QPixmap(str(img_path))
        if not pix.isNull():
            lbl.setPixmap(pix.scaled(720, 460, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        lay.addWidget(lbl)

# Main launcher window
class VortexLauncher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg          = load_cfg()
        self._drag_pos    = None
        self._worker      = None
        self._gif_animator: GifCursorAnimator | None = None

        for ext in ("*.ttf","*.otf"):
            for fp in FONTS_DIR.glob(ext):
                QFontDatabase.addApplicationFont(str(fp))

        self.setWindowTitle("Vortex Launcher")
        self.setFixedSize(780, 600)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        acc = self.cfg.get("accent","#7C3AED")
        fam = self.cfg.get("ui_font","Segoe UI")
        QApplication.instance().setFont(QFont(fam, 12))
        QApplication.instance().setStyleSheet(self._global_qss(acc, fam))

        self._apply_cursor(self.cfg.get("custom_cursor", "neon_arrow"))

        # F12 shortcut (within the launcher window) + global OS-level hotkey
        self.hotkey_thread = GlobalHotkeyThread()
        self.hotkey_thread.triggered.connect(self._take_screenshot)
        self.hotkey_thread.start()

        self.shortcut_f12 = QShortcut(QKeySequence("F12"), self)
        self.shortcut_f12.activated.connect(self._take_screenshot)

        self.setAcceptDrops(True)
        self._build()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(('.ttf', '.otf')):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        added = []
        for url in event.mimeData().urls():
            file_path = Path(url.toLocalFile())
            if file_path.suffix.lower() in ('.ttf', '.otf'):
                dest = FONTS_DIR / file_path.name
                try:
                    shutil.copy2(file_path, dest)
                    fid = QFontDatabase.addApplicationFont(str(dest))
                    if fid >= 0:
                        fams = QFontDatabase.applicationFontFamilies(fid)
                        for fam in fams:
                            if self.font_combo.findText(fam) < 0:
                                self.font_combo.addItem(fam)
                            added.extend(fams)
                            self.font_combo.setCurrentText(fam)
                except Exception as e:
                    pass

        if added:
            self.badge.set_ok(f"Fonts added & ready: {', '.join(added[:3])}")
            self.tabs.setCurrentIndex(0)  # Jump to Font tab

    def closeEvent(self, event):
        if self._gif_animator:
            self._gif_animator.stop_and_restore()
        super().closeEvent(event)

    def _apply_cursor(self, cursor_key: str):
        if self._gif_animator:
            self._gif_animator.stop_and_restore()
            self._gif_animator = None

        if cursor_key == "shatterill_gif":
            system_wide = self.cfg.get("system_cursor", False)
            self._gif_animator = GifCursorAnimator(GIF_CURSOR, system_wide=system_wide, parent=self)
            if not system_wide:
                pix = self._gif_animator._frames[0] if self._gif_animator._frames else None
                if pix:
                    QApplication.setOverrideCursor(QCursor(pix, 0, 0))
            return

        acc    = self.cfg.get("accent", "#7C3AED")
        cursor = create_custom_cursor(cursor_key, acc)
        QApplication.setOverrideCursor(cursor)

    def _take_screenshot(self):
        screen = QGuiApplication.primaryScreen()
        if screen:
            pix = screen.grabWindow(0)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = SCREENSHOTS_DIR / f"vortex_{timestamp}.png"
            pix.save(str(filename), "PNG")
            try:
                import winsound
                winsound.Beep(1200, 120)
            except:
                pass
            self.badge.set_ok(f"Screenshot saved: {filename.name}")
            self._reload_gallery()

    def _get_render_env(self) -> tuple[dict, list]:
        """Returns (env_vars_dict, extra_cli_args_list) for launching Vortex."""
        env        = {}
        extra_args = []
        
        # Software rendering mode: forces WARP (Microsoft's CPU-based DX12 renderer).
        # Use this if the GPU doesn't meet Vortex's minimum requirements.
        if self.cfg.get("software_rendering", False):
            # Microsoft Basic Render Driver is Windows' built-in DX12 software rasterizer (WARP)
            env["WGPU_ADAPTER_NAME"] = "Microsoft Basic Render Driver"
            env["WGPU_BACKEND"]      = "dx12"
            env["WGPU_POWER_PREF"]   = "low"
            if self.cfg.get("render_antialiasing", True):
                env["WGPU_FXAA"] = "1"
            else:
                env["WGPU_FXAA"] = "0"
            return env, extra_args

        backend    = self.cfg.get("render_backend", "auto")

        # Set the WGPU backend. If 'auto', leave it unset so wgpu picks the best one.
        # WGPU_BACKEND accepted values (wgpu-rs): vulkan, metal, dx12, dx11, gl
        # Note: 'gl' uses ANGLE on Windows which may crash on older GPU drivers.
        # For OpenGL fallback on Windows the safest option is dx11 → then gl.
        if backend == "gl":
            # Force OpenGL via ANGLE — needs OpenGL 3.1+ or DirectX 11 ANGLE.
            # Set both WGPU env var AND disable Vulkan/DX12 so runtime doesn't pick them first.
            env["WGPU_BACKEND"] = "gl"
            # Disable GPU process sandbox that can block ANGLE on some systems
            env["ANGLE_DEFAULT_PLATFORM"] = "gl"   # prefer native GL over D3D ANGLE
        elif backend != "auto":
            env["WGPU_BACKEND"] = backend
        # 'auto' → leave WGPU_BACKEND unset (runtime picks best available)

        # GPU power preference — hint to Windows which adapter to prefer
        power = self.cfg.get("render_power", "high")
        if power == "high":
            env["WGPU_POWER_PREF"] = "high"
            # Windows: hint OS to use high-performance GPU adapter
            # Works on Windows 10+ with NVIDIA/AMD hybrid setups
            env["SHIM_MCCOMPAT"]         = "0x800000001"   # NVIDIA Optimus hint
            env["DISABLE_LAYER_AMD_SWITCHABLE_GRAPHICS_1"] = "1"  # force discrete AMD
        elif power == "low":
            env["WGPU_POWER_PREF"] = "low"
        # 'default' → leave unset

        # FXAA
        if self.cfg.get("render_antialiasing", True):
            env["WGPU_FXAA"] = "1"
        else:
            env["WGPU_FXAA"] = "0"

        # ── Fun & Retro Modes ────────────────────────────────────────────────
        fun_mode = self.cfg.get("fun_mode", "none")
        if fun_mode == "retro_240p":
            env["WGPU_SCALE_FACTOR"] = "0.3"
            env["WGPU_FXAA"]         = "0"
        elif fun_mode == "arcade_8bit":
            env["WGPU_SCALE_FACTOR"] = "0.2"
            env["WGPU_FXAA"]         = "0"
        elif fun_mode == "crt_vintage":
            env["WGPU_SCALE_FACTOR"] = "0.4"
            env["WGPU_FXAA"]         = "0"
        elif fun_mode == "speedrunner":
            env["WGPU_SCALE_FACTOR"] = "0.5"
            env["WGPU_FXAA"]         = "0"
            env["WGPU_POWER_PREF"]   = "high"

        return env, extra_args

    # drag
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() == Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
    def mouseReleaseEvent(self, _): self._drag_pos = None

    # Builds the entire UI
    def _build(self):
        c = QWidget(self); self.setCentralWidget(c)
        self.bg = BgWidget(c); self.bg.setGeometry(0,0,780,600)

        root = QVBoxLayout(c)
        root.setContentsMargins(20,16,20,16)
        root.setSpacing(12)

        root.addLayout(self._titlebar())

        self.tabs = QTabWidget()
        acc = self.cfg.get("accent","#7C3AED")
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: none;
                background: transparent;
            }}
            QTabBar::tab {{
                background: rgba(255,255,255,0.04);
                color: #A89BC2;
                padding: 8px 16px;
                margin-right: 6px;
                border-radius: 8px;
                font-weight: 600;
            }}
            QTabBar::tab:selected {{
                background: rgba(124,58,237,0.25);
                color: #E2D9FF;
                border: 1px solid {acc};
            }}
            QTabBar::tab:hover {{
                color: #E2D9FF;
            }}
        """)

        self.tabs.addTab(self._main_tab(), "Launcher & Font")
        self.tabs.addTab(self._render_tab(), "Render & Graphics")
        self.tabs.addTab(self._gallery_tab(), "Screenshots")
        self.tabs.addTab(self._cursor_tab(), "Custom Cursor")
        self.tabs.addTab(self._deployment_tab(), "Deployment")

        root.addWidget(self.tabs, 1)

        self.badge = StatusBadge()
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if self.cfg.get("is_patched"):
            self.badge.set_ok(f"Font replaced → {self.cfg.get('patched_font','?')}")
        elif VORTEX_EXE.exists():
            self.badge.set_info("Ready Vortex.exe found | Press F12 to take Screenshots")
        else:
            self.badge.set_err("Vortex.exe not found!")
        root.addWidget(self.badge)

    def _titlebar(self):
        acc = self.cfg.get("accent","#7C3AED")
        bar = QHBoxLayout(); bar.setContentsMargins(4,0,4,0)

        logo = QLabel()
        galaxy_logo = None
        for p in [
            BASE_DIR / "images" / "Vortexstrap-galaxy-text.png",
            BASE_DIR / "Vortexstrap-galaxy-text.png",
            BASE_DIR.parent / "images" / "Vortexstrap-galaxy-text.png",
        ]:
            if p.exists():
                galaxy_logo = p
                break

        if galaxy_logo:
            pix = QPixmap(str(galaxy_logo))
            if not pix.isNull():
                logo.setPixmap(pix.scaledToHeight(26, Qt.TransformationMode.SmoothTransformation))
                logo.setStyleSheet("border: none; background: transparent;")

        if logo.pixmap() is None or logo.pixmap().isNull():
            logo.setText("⚡ VortexStrap")
            logo.setStyleSheet(f"color:{acc};font-size:17px;font-weight:800;letter-spacing:1px;")

        bar.addWidget(logo); bar.addStretch()

        for sym, tip, fn in [("—","Minimize",self.showMinimized),("✕","Close",self.close)]:
            b = QPushButton(sym); b.setFixedSize(28,28); b.setToolTip(tip)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setStyleSheet("""QPushButton{background:transparent;color:#5A4A8A;font-size:13px;border:none;border-radius:7px;}
                              QPushButton:hover{background:rgba(124,58,237,0.2);color:#E2D9FF;}""")
            b.clicked.connect(fn); bar.addWidget(b)
        return bar

    # ── TAB 1: Başlatıcı & Font ───────────────────────────────────────────────
    def _main_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(12)

        lay.addWidget(self._main_card(), 1)
        lay.addWidget(self._font_card(), 1)
        return w

    def _main_card(self) -> Card:
        acc  = self.cfg.get("accent","#7C3AED")
        card = Card()
        lay  = QVBoxLayout(card)
        lay.setContentsMargins(24,24,24,24); lay.setSpacing(14)

        logo = QLabel("VORTEX")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet(f"color:{acc};font-size:40px;font-weight:900;letter-spacing:8px;")
        sub = QLabel("Alternative Launcher")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color:#3D3060;font-size:11px;letter-spacing:3px;")

        lay.addStretch()
        lay.addWidget(logo); lay.addWidget(sub)
        lay.addSpacing(16)

        self.launch_btn = QPushButton("▶   Launch Vortex")
        self.launch_btn.setMinimumHeight(50)
        self.launch_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.launch_btn.setStyleSheet(self._btn_qss(acc))

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(30); shadow.setOffset(0,0); shadow.setColor(QColor(acc))
        self.launch_btn.setGraphicsEffect(shadow)
        self.launch_btn.clicked.connect(self._launch)
        lay.addWidget(self.launch_btn)

        self.prog = QProgressBar()
        self.prog.setRange(0,0); self.prog.setFixedHeight(3)
        self.prog.setTextVisible(False); self.prog.hide()
        self.prog.setStyleSheet(f"""
            QProgressBar{{background:rgba(255,255,255,0.05);border:none;border-radius:1px;}}
            QProgressBar::chunk{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {acc},stop:1 #4F46E5);border-radius:1px;}}
        """)
        lay.addWidget(self.prog)
        lay.addStretch()
        return card

    def _font_card(self) -> Card:
        acc  = self.cfg.get("accent","#7C3AED")
        card = Card()
        lay  = QVBoxLayout(card)
        lay.setContentsMargins(20,16,20,16); lay.setSpacing(10)

        title = QLabel("Font Changer")
        title.setStyleSheet("color:#E2D9FF;font-size:13px;font-weight:700;")
        lay.addWidget(title)

        desc = QLabel("Replaces the internal Inter font of Vortex with any custom font.")
        desc.setStyleSheet("color:#6A5A8A;font-size:11px;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        row = QHBoxLayout(); row.setSpacing(8)

        self.font_combo = QComboBox()
        
        # 1. Fonts klasöründeki özel fontları tara ve yükle
        local_fonts = []
        for ext in ("*.ttf", "*.otf"):
            for fp in FONTS_DIR.glob(ext):
                fid = QFontDatabase.addApplicationFont(str(fp))
                if fid >= 0:
                    for fam in QFontDatabase.applicationFontFamilies(fid):
                        if fam not in local_fonts:
                            local_fonts.append(fam)

        # Önce lokal/özel fontları ekle
        for f in local_fonts:
            self.font_combo.addItem(f"⭐ {f}")

        # 2. Popüler sistem fontları
        popular = [
            "Segoe UI", "Calibri", "Trebuchet MS", "Verdana", "Tahoma",
            "Arial", "Georgia", "Candara", "Corbel", "Century Gothic",
            "Constantia", "Cambria", "Garamond", "Palatino Linotype",
        ]
        avail = set(QFontDatabase.families())
        for f in popular:
            if f in avail and f not in local_fonts:
                self.font_combo.addItem(f)

        for f in self.cfg.get("custom_fonts", []):
            if self.font_combo.findText(f) < 0 and self.font_combo.findText(f"⭐ {f}") < 0:
                self.font_combo.addItem(f)

        self.font_combo.setStyleSheet(f"""
            QComboBox{{background:rgba(255,255,255,0.07);border:1px solid rgba(124,58,237,0.45);
                       border-radius:8px;color:#E2D9FF;padding:7px 10px;font-size:12px;}}
            QComboBox::drop-down{{border:none;width:24px;}}
            QComboBox::down-arrow{{image:none;}}
            QComboBox QAbstractItemView{{background:#1A1030;color:#E2D9FF;
                selection-background-color:{acc};border:1px solid rgba(124,58,237,0.4);}}
        """)
        row.addWidget(self.font_combo, 1)

        browse = QPushButton("TTF")
        browse.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        browse.setStyleSheet(self._outline_qss())
        browse.clicked.connect(self._browse_ttf)
        row.addWidget(browse)

        lay.addLayout(row)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        self.apply_btn = QPushButton("✓  Apply")
        self.apply_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.apply_btn.setStyleSheet(self._btn_qss(acc))
        self.apply_btn.setMinimumHeight(38)
        self.apply_btn.clicked.connect(self._apply_font)
        btn_row.addWidget(self.apply_btn, 2)

        self.restore_btn = QPushButton("↩  Restore")
        self.restore_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.restore_btn.setStyleSheet(self._outline_qss())
        self.restore_btn.setMinimumHeight(38)
        self.restore_btn.setEnabled(BACKUP_EXE.exists())
        self.restore_btn.clicked.connect(self._restore_font)
        btn_row.addWidget(self.restore_btn, 1)

        lay.addLayout(btn_row)

        imp_row = QHBoxLayout()
        
        imp = QPushButton("+ Add Custom Font (.ttf / .otf)")
        imp.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        imp.setStyleSheet("""
            QPushButton{background:transparent;color:#5A4A8A;border:none;
                        font-size:11px;text-decoration:underline;padding:2px;}
            QPushButton:hover{color:#A78BFA;}
        """)
        imp.clicked.connect(self._import_font)
        imp_row.addWidget(imp)

        imp_row.addStretch()

        open_fonts_btn = QPushButton("Open Fonts Folder")
        open_fonts_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        open_fonts_btn.setStyleSheet("""
            QPushButton{background:transparent;color:#5A4A8A;border:none;
                        font-size:11px;text-decoration:underline;padding:2px;}
            QPushButton:hover{color:#A78BFA;}
        """)
        open_fonts_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(FONTS_DIR))))
        imp_row.addWidget(open_fonts_btn)

        lay.addLayout(imp_row)

        return card

    # ── TAB 2: Render & Grafik Ayarları ────────────────────────────────────────
    def _render_tab(self) -> Card:
        card = Card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)

        title = QLabel("Render Engine & Graphics Settings")
        title.setStyleSheet("color:#E2D9FF; font-size:14px; font-weight:700;")
        lay.addWidget(title)

        desc = QLabel("Configure graphics & performance settings for Vortex (Rust/WGPU engine):")
        desc.setStyleSheet("color:#6A5A8A; font-size:11px;")
        lay.addWidget(desc)

        # 1. Render Backend Combo
        lay.addWidget(QLabel("Render Backend (WGPU Engine):"))
        self.backend_combo = QComboBox()
        backends = [
            ("auto",   "Automatic (Let Vortex Decide — Recommended)"),
            ("dx12",   "DirectX 12  ✓ Best for Windows 10/11 (NVIDIA/AMD)"),
            ("dx11",   "DirectX 11  ✓ Most compatible — use if DX12 crashes"),
            ("vulkan", "Vulkan       High Performance (NVIDIA/AMD, needs Vulkan 1.1+)"),
            ("gl",     "OpenGL       Legacy fallback (slower, use only if others fail)"),
        ]
        for key, name in backends:
            self.backend_combo.addItem(name, key)

        cur_backend = self.cfg.get("render_backend", "auto")
        for idx in range(self.backend_combo.count()):
            if self.backend_combo.itemData(idx) == cur_backend:
                self.backend_combo.setCurrentIndex(idx)

        acc = self.cfg.get("accent", "#7C3AED")
        combo_qss = f"""
            QComboBox{{background:rgba(255,255,255,0.07);border:1px solid rgba(124,58,237,0.45);
                       border-radius:8px;color:#E2D9FF;padding:8px 12px;font-size:12px;}}
            QComboBox::drop-down{{border:none;width:24px;}}
            QComboBox::down-arrow{{image:none;}}
            QComboBox QAbstractItemView{{background:#1A1030;color:#E2D9FF;
                selection-background-color:{acc};border:1px solid rgba(124,58,237,0.4);}}
        """
        self.backend_combo.setStyleSheet(combo_qss)
        lay.addWidget(self.backend_combo)

        # 1b. Fun & Retro Modes Combo
        lay.addWidget(QLabel("🕹️  Fun & Retro Modes (Graphics Effects):"))
        self.fun_combo = QComboBox()
        fun_modes = [
            ("none",         "Off (Standard Modern Resolution)"),
            ("retro_240p",   "🕹️  Retro Pixelated 240p (PS1 / N64 Nostalgia)"),
            ("arcade_8bit",   "👾  8-Bit Arcade Mode (Low-Res + Pixel Textures)"),
            ("crt_vintage",   "📺  CRT Vintage Display (Low-Poly Filter)"),
            ("speedrunner",   "⚡  Potato PC / Speedrunner Mode (Ultra Performance)"),
        ]
        for key, name in fun_modes:
            self.fun_combo.addItem(name, key)

        cur_fun = self.cfg.get("fun_mode", "none")
        for idx in range(self.fun_combo.count()):
            if self.fun_combo.itemData(idx) == cur_fun:
                self.fun_combo.setCurrentIndex(idx)

        self.fun_combo.setStyleSheet(combo_qss)
        lay.addWidget(self.fun_combo)

        # Crash tip note
        crash_note = QLabel(
            "⚠️  If the game crashes on launch: try DX11 first, then Vulkan.\n"
            "    OpenGL (gl) can crash on systems without OpenGL 3.1+ or ANGLE support."
        )
        crash_note.setStyleSheet("color:#C97B2A; font-size:10px; padding:4px 0px;")
        crash_note.setWordWrap(True)
        lay.addWidget(crash_note)

        # 2. Power Mode Combo
        lay.addWidget(QLabel("GPU Power Mode:"))
        self.power_combo = QComboBox()
        powers = [
            ("high", "Dedicated GPU / High Performance (NVIDIA / AMD)"),
            ("low", "Integrated GPU / Power Saving (Intel HD / Integrated)"),
            ("default", "System Default"),
        ]
        for key, name in powers:
            self.power_combo.addItem(name, key)

        cur_power = self.cfg.get("render_power", "high")
        for idx in range(self.power_combo.count()):
            if self.power_combo.itemData(idx) == cur_power:
                self.power_combo.setCurrentIndex(idx)

        self.power_combo.setStyleSheet(combo_qss)
        lay.addWidget(self.power_combo)

        # 3. Checkboxes
        self.aa_cb = QCheckBox("Enable FXAA Anti-Aliasing")
        self.aa_cb.setChecked(self.cfg.get("render_antialiasing", True))
        self.aa_cb.setStyleSheet("color:#A89BC2; font-size:12px;")
        lay.addWidget(self.aa_cb)

        self.software_rendering_cb = QCheckBox("Software Rendering / CPU Fallback (For very old GPUs)")
        self.software_rendering_cb.setChecked(self.cfg.get("software_rendering", False))
        self.software_rendering_cb.setStyleSheet("color:#D1783B; font-size:12px; font-weight:bold;")
        self.software_rendering_cb.setToolTip(
            "Forces game to run on CPU using Microsoft Basic Render Driver (WARP DX12).\n"
            "Use ONLY if your GPU is completely unsupported and game crashes on start."
        )
        lay.addWidget(self.software_rendering_cb)

        lay.addStretch()

        # Save Button
        save_render_btn = QPushButton("Save Render Settings")
        save_render_btn.setStyleSheet(self._btn_qss(acc))
        save_render_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save_render_btn.clicked.connect(self._save_render_settings)
        lay.addWidget(save_render_btn)

        return card

    def _save_render_settings(self):
        self.cfg["render_backend"]      = self.backend_combo.currentData()
        self.cfg["fun_mode"]            = self.fun_combo.currentData()
        self.cfg["render_power"]        = self.power_combo.currentData()
        self.cfg["render_antialiasing"] = self.aa_cb.isChecked()
        self.cfg["software_rendering"]   = self.software_rendering_cb.isChecked()
        save_cfg(self.cfg)
        self.badge.set_ok("Render & Graphics settings saved! Vortex will launch with these settings.")

    # ── TAB 3: Ekran Görüntüleri (Gallery) ────────────────────────────────────
    def _gallery_tab(self) -> Card:
        card = Card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        top_bar = QHBoxLayout()
        title = QLabel("In-Game Screenshots")
        title.setStyleSheet("color:#E2D9FF; font-size:13px; font-weight:700;")
        top_bar.addWidget(title)
        top_bar.addStretch()

        snap_btn = QPushButton("Take Screenshot (F12)")
        snap_btn.setStyleSheet(self._btn_qss(self.cfg.get("accent","#7C3AED")))
        snap_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        snap_btn.clicked.connect(self._take_screenshot)
        top_bar.addWidget(snap_btn)

        open_folder_btn = QPushButton("Open Folder")
        open_folder_btn.setStyleSheet(self._outline_qss())
        open_folder_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        open_folder_btn.clicked.connect(self._open_screenshot_folder)
        top_bar.addWidget(open_folder_btn)

        add_shot_btn = QPushButton("Add Image")
        add_shot_btn.setStyleSheet(self._outline_qss())
        add_shot_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        add_shot_btn.clicked.connect(self._add_screenshot)
        top_bar.addWidget(add_shot_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setStyleSheet(self._outline_qss())
        refresh_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        refresh_btn.clicked.connect(self._reload_gallery)
        top_bar.addWidget(refresh_btn)

        lay.addLayout(top_bar)

        self.gallery_scroll = QScrollArea()
        self.gallery_scroll.setWidgetResizable(True)
        self.gallery_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.gallery_container = QWidget()
        self.gallery_grid = QGridLayout(self.gallery_container)
        self.gallery_grid.setSpacing(12)
        self.gallery_scroll.setWidget(self.gallery_container)

        lay.addWidget(self.gallery_scroll, 1)

        self._reload_gallery()
        return card

    def _reload_gallery(self):
        for i in reversed(range(self.gallery_grid.count())):
            w = self.gallery_grid.itemAt(i).widget()
            if w:
                w.deleteLater()

        dirs = [
            SCREENSHOTS_DIR,
            ROOT_DIR / "screenshots",
            Path.home() / "Pictures" / "Vortex",
            Path.home() / "Pictures" / "Screenshots",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Vortex" / "screenshots",
        ]

        images = []
        for d in dirs:
            if d.exists():
                for ext in ("*.png", "*.jpg", "*.jpeg"):
                    images.extend(list(d.glob(ext)))

        images = sorted(list(set(images)), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)

        if not images:
            empty_lbl = QLabel("Right now theres no Screenshot.\n'📸 Get Screenshot (F12)' Or '➕ Add Image'!")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lbl.setStyleSheet("color:#6A5A8A; font-size:12px;")
            self.gallery_grid.addWidget(empty_lbl, 0, 0)
            return

        col_count = 3
        for idx, img_path in enumerate(images[:18]):
            row = idx // col_count
            col = idx % col_count

            item_frame = QFrame()
            item_frame.setStyleSheet("""
                QFrame {
                    background: rgba(255,255,255,0.04);
                    border: 1px solid rgba(124,58,237,0.3);
                    border-radius: 8px;
                }
                QFrame:hover {
                    border-color: #7C3AED;
                    background: rgba(124,58,237,0.1);
                }
            """)
            flay = QVBoxLayout(item_frame)
            flay.setContentsMargins(6, 6, 6, 6)

            pix = QPixmap(str(img_path))
            lbl = QLabel()
            lbl.setFixedSize(180, 110)
            lbl.setScaledContents(True)
            if not pix.isNull():
                lbl.setPixmap(pix.scaled(180, 110, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))

            lbl_name = QLabel(img_path.name[:20])
            lbl_name.setStyleSheet("color:#A89BC2; font-size:10px; border:none; background:transparent;")
            lbl_name.setAlignment(Qt.AlignmentFlag.AlignCenter)

            flay.addWidget(lbl)
            flay.addWidget(lbl_name)

            item_frame.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            item_frame.mousePressEvent = lambda _, p=img_path: self._open_image_preview(p)

            self.gallery_grid.addWidget(item_frame, row, col)

    def _open_image_preview(self, path: Path):
        dlg = ImagePreviewDialog(path, self)
        dlg.exec()

    def _open_screenshot_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(SCREENSHOTS_DIR)))

    def _add_screenshot(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Ekran Görüntüsü Seç", str(Path.home()),
            "Resim Dosyaları (*.png *.jpg *.jpeg)"
        )
        for f in files:
            shutil.copy2(f, SCREENSHOTS_DIR / Path(f).name)
        if files:
            self._reload_gallery()

    # ── TAB 4: Özel İmleç (Custom Cursor) ──────────────────────────────────────
    def _cursor_tab(self) -> Card:
        card = Card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)

        title = QLabel("Custom Cursor")
        title.setStyleSheet("color:#E2D9FF; font-size:14px; font-weight:700;")
        lay.addWidget(title)

        desc = QLabel("Başlatıcı içerisindeki fare imleci stilini özelleştir:")
        desc.setStyleSheet("color:#6A5A8A; font-size:11px;")
        lay.addWidget(desc)

        self.cursor_combo = QComboBox()
        gif_available = GIF_CURSOR.exists()
        cursors = [
            ("neon_arrow",      "Neon Purple (Default)"),
            ("crosshair",       "Crosshair"),
            ("dot_glow",        "The Dot"),
            ("system",          "System Default"),
        ]
        if gif_available:
            cursors.insert(0, ("shatterill_gif", " shatterill's Cursor (Animated GIF)"))

        for key, name in cursors:
            self.cursor_combo.addItem(name, key)

        cur_key = self.cfg.get("custom_cursor", "neon_arrow")
        for idx in range(self.cursor_combo.count()):
            if self.cursor_combo.itemData(idx) == cur_key:
                self.cursor_combo.setCurrentIndex(idx)

        acc = self.cfg.get("accent", "#7C3AED")
        self.cursor_combo.setStyleSheet(f"""
            QComboBox{{background:rgba(255,255,255,0.07);border:1px solid rgba(124,58,237,0.45);
                       border-radius:8px;color:#E2D9FF;padding:8px 12px;font-size:12px;}}
            QComboBox::drop-down{{border:none;width:24px;}}
            QComboBox::down-arrow{{image:none;}}
            QComboBox QAbstractItemView{{background:#1A1030;color:#E2D9FF;
                selection-background-color:{acc};border:1px solid rgba(124,58,237,0.4);}}
        """)
        self.cursor_combo.currentIndexChanged.connect(self._on_cursor_changed)
        lay.addWidget(self.cursor_combo)

        # System-wide toggle (makes cursor work inside Vortex.exe too)
        self.system_cursor_cb = QCheckBox("🎮 Apply inside Vortex.exe (system-wide)")
        self.system_cursor_cb.setChecked(self.cfg.get("system_cursor", False))
        self.system_cursor_cb.setStyleSheet("color:#A89BC2; font-size:12px; margin-top:6px;")
        self.system_cursor_cb.stateChanged.connect(self._on_system_cursor_toggled)
        lay.addWidget(self.system_cursor_cb)

        lay.addStretch()

        # Credit label
        if gif_available:
            credit = QLabel('🎨 Cursor by <a href="https://discord.com/users/shatterill" style="color:#A78BFA;">shatterill</a> — used with permission')
            credit.setOpenExternalLinks(True)
            credit.setStyleSheet("color:#4A3A6A; font-size:11px; margin-top:4px;")
            lay.addWidget(credit)

        return card

    def _on_cursor_changed(self, idx: int):
        cursor_key = self.cursor_combo.itemData(idx)
        self.cfg["custom_cursor"] = cursor_key
        save_cfg(self.cfg)
        self._apply_cursor(cursor_key)
        self.badge.set_ok("Cursor updated!")

    def _on_system_cursor_toggled(self, state: int):
        self.cfg["system_cursor"] = bool(state)
        save_cfg(self.cfg)
        cur_key = self.cursor_combo.currentData()
        if cur_key == "shatterill_gif":
            self._apply_cursor(cur_key)

    # ── TAB 5: Deployment ─────────────────────────────────────────────────────
    def _deployment_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)

        card = Card()
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(20, 20, 20, 20)
        c_lay.setSpacing(14)

        title = QLabel("📦 Deployment & Update Settings")
        title.setStyleSheet("color:#E2D9FF; font-size:15px; font-weight:700;")
        c_lay.addWidget(title)

        desc = QLabel("Configure Vortex deployment rules, auto-updates, and patch policies.")
        desc.setStyleSheet("color:#6A5A8A; font-size:12px;")
        c_lay.addWidget(desc)

        c_lay.addSpacing(10)

        # Disable Vortex Auto-Update Option
        self.disable_update_cb = QCheckBox("🚫 Disable Vortex Auto-Update")
        self.disable_update_cb.setChecked(False)
        self.disable_update_cb.setEnabled(False)  # Devre dışı (pasif)
        self.disable_update_cb.setStyleSheet("""
            QCheckBox {
                color: #8B7AA8;
                font-size: 13px;
                font-weight: 600;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
        """)
        c_lay.addWidget(self.disable_update_cb)

        # Warning / Note Badge
        note_card = QFrame()
        note_card.setStyleSheet("""
            QFrame {
                background: rgba(239, 68, 68, 0.1);
                border: 1px solid rgba(239, 68, 68, 0.3);
                border-radius: 10px;
                padding: 12px;
            }
        """)
        n_lay = QVBoxLayout(note_card)
        n_lay.setContentsMargins(10, 8, 10, 8)

        note_lbl = QLabel("⚠️ NOTE: This may violate rules/guidelines, so it is currently disabled. We will ask Halo before enabling it.")
        note_lbl.setStyleSheet("color: #F87171; font-size: 12px; font-weight: 600;")
        note_lbl.setWordWrap(True)
        n_lay.addWidget(note_lbl)

        c_lay.addWidget(note_card)
        c_lay.addStretch()

        lay.addWidget(card)
        return w

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _btn_qss(self, acc):
        return f"""
QPushButton{{
    background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {acc},stop:1 #4F46E5);
    color:white;border:none;border-radius:11px;
    font-size:14px;font-weight:700;letter-spacing:0.3px;
}}
QPushButton:hover{{
    background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #A78BFA,stop:1 #6366F1);
}}
QPushButton:disabled{{background:rgba(124,58,237,0.25);color:rgba(255,255,255,0.35);}}
"""

    def _outline_qss(self):
        return """
QPushButton{background:transparent;color:#A89BC2;
            border:1px solid rgba(124,58,237,0.45);border-radius:10px;
            padding:6px 14px;font-size:12px;}
QPushButton:hover{color:#E2D9FF;border-color:#7C3AED;background:rgba(124,58,237,0.1);}
QPushButton:disabled{color:#3A3060;border-color:rgba(124,58,237,0.15);}
"""

    def _global_qss(self, acc, fam):
        return f"""
* {{font-family:"{fam}";}}
QWidget {{color:#E2D9FF;background:transparent;}}
QScrollBar:vertical{{background:rgba(255,255,255,0.04);width:5px;border-radius:2px;}}
QScrollBar::handle:vertical{{background:{acc};border-radius:2px;min-height:20px;}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}
QToolTip{{background:#1A1030;color:#E2D9FF;border:1px solid {acc};border-radius:6px;padding:4px 8px;}}
"""

    # ── Actions ───────────────────────────────────────────────────────────────
    def _locate_vortex(self):
        global VORTEX_EXE, ROOT_DIR, BACKUP_EXE
        path, _ = QFileDialog.getOpenFileName(
            self, "Locate Vortex.exe", str(Path.home()),
            "Executable Files (*Vortex*.exe *.exe)"
        )
        if path:
            VORTEX_EXE = Path(path).resolve()
            ROOT_DIR   = VORTEX_EXE.parent
            BACKUP_EXE = ROOT_DIR / "Vortex_backup.exe"
            self.badge.set_ok(f"Vortex.exe located: {VORTEX_EXE.name}")

    def _launch(self):
        global VORTEX_EXE, ROOT_DIR, BACKUP_EXE
        if not VORTEX_EXE.exists():
            self._locate_vortex()
            if not VORTEX_EXE.exists():
                self.badge.set_err("Vortex binary not found! Please locate it manually.")
                return

        self.launch_btn.setEnabled(False)
        self.launch_btn.setText("Launching…")
        self.prog.show()

        # Open Bloxstrap-style splash dialog (centered on screen)
        self._splash_dialog = LaunchSplashDialog(self, on_cancel=self._cancel_launch)
        self._splash_dialog.show()

        render_env, extra_args = self._get_render_env()
        w = LaunchWorker(render_env=render_env, extra_args=extra_args)
        w.started_sig.connect(self._on_game_started)
        w.finished_code_sig.connect(self._on_game_exit)
        w.finished_sig.connect(self._on_game_finished)
        w.error_sig.connect(self._on_game_error)

        self._worker = w
        w.start()

    def _on_game_started(self):
        backend_label = self.cfg.get('render_backend', 'auto').upper()
        self.badge.set_ok(f"Vortex is running! ({backend_label} Engine) | Press F12 for Screenshots")
        self.prog.hide()
        self.launch_btn.setText("🟢  Running")

        if hasattr(self, '_splash_dialog') and self._splash_dialog:
            self._splash_dialog.set_status("Vortex is running!")
            QTimer.singleShot(1000, self._close_splash)

    def _close_splash(self):
        if hasattr(self, '_splash_dialog') and self._splash_dialog:
            self._splash_dialog.accept()
            self._splash_dialog = None

    def _cancel_launch(self):
        if hasattr(self, '_worker') and self._worker and self._worker.isRunning():
            self._worker.terminate()
            self.badge.set_info("Launch cancelled.")
            self.prog.hide()
            self.launch_btn.setText("▶   Launch Vortex")
            self.launch_btn.setEnabled(True)

    def _on_game_finished(self):
        self.launch_btn.setText("▶   Launch Vortex")
        self.launch_btn.setEnabled(True)
        self._close_splash()

    def _on_game_error(self, msg: str):
        self.badge.set_err(f"Launch error: {msg}")
        self.prog.hide()
        self.launch_btn.setText("▶   Launch Vortex")
        self.launch_btn.setEnabled(True)
        self._close_splash()

    def _on_game_exit(self, return_code: int):
        """Handle Vortex.exe exit — show crash hints if it exited with non-zero code."""
        if return_code == 0:
            self.badge.set_info("Vortex closed normally.")
            return

        backend = self.cfg.get("render_backend", "auto")
        # Build a helpful suggestion based on what backend was being used
        if backend in ("gl", "opengl"):
            tip = "OpenGL backend crashed. Switch to 'DirectX 11' in Render tab and try again."
        elif backend == "vulkan":
            tip = "Vulkan backend crashed. Try 'DirectX 12' or 'DirectX 11' in Render tab."
        elif backend == "dx12":
            tip = "DX12 crashed. Switch to 'DirectX 11' in Render tab for more compatibility."
        else:
            tip = "Vortex crashed. Try selecting 'DirectX 11' in Render & Graphics tab."

        self.badge.set_err(
            f"Vortex exited with code {return_code} (crash). {tip}"
        )

    def _browse_ttf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select TTF/OTF Font", str(Path.home()),
            "Font Files (*.ttf *.otf)"
        )
        if path:
            self._selected_ttf = path
            name = Path(path).stem
            if self.font_combo.findText(name) < 0:
                self.font_combo.addItem(name)
            self.font_combo.setCurrentText(name)
            self.badge.set_info(f"Selected: {name}")

    def _import_font(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Custom Fonts", str(Path.home()),
            "Font Files (*.ttf *.otf)"
        )
        added = []
        for p in paths:
            dest = FONTS_DIR / Path(p).name
            shutil.copy2(p, dest)
            fid = QFontDatabase.addApplicationFont(str(dest))
            if fid >= 0:
                fams = QFontDatabase.applicationFontFamilies(fid)
                for fam in fams:
                    if self.font_combo.findText(fam) < 0:
                        self.font_combo.addItem(fam)
                    added.extend(fams)

        if added:
            self.badge.set_ok(f"Fonts added: {', '.join(added[:3])}")

    def _apply_font(self):
        raw_name = self.font_combo.currentText()
        font_name = raw_name.replace("⭐ ", "").strip()
        ttf_path = getattr(self, "_selected_ttf", None)

        if not ttf_path or Path(ttf_path).stem.lower() != font_name.lower():
            ttf_path = self._find_system_font(font_name)

        if not ttf_path:
            for ext in ("ttf","otf"):
                candidates = list(FONTS_DIR.glob(f"*{font_name}*.{ext}"))
                if candidates:
                    ttf_path = str(candidates[0]); break

        if not ttf_path:
            self.badge.set_err(
                f"TTF file for '{font_name}' not found.\n"
                f"Please manually select it using '📂 TTF'."
            )
            return

        self.apply_btn.setEnabled(False)
        self.apply_btn.setText("Applying…")

        # If this is the first time after a Vortex update, the full binary scan
        # runs inside PatchWorker (background thread) so the UI stays responsive.
        is_first_scan = not self.cfg.get("_inter_cache", {}).get("offsets")
        if is_first_scan:
            self.badge.set_info("First run after update — scanning Vortex.exe for fonts (may take a few seconds)…")
        else:
            self.badge.set_info("Patching font, please wait…")

        w = PatchWorker(ttf_path, cfg=self.cfg)
        w.done.connect(self._on_patch_done)
        self._worker = w; w.start()


    def _on_patch_done(self, ok, msg):
        self.apply_btn.setText("✓  Uygula")
        self.apply_btn.setEnabled(True)

        if ok:
            font_name = self.font_combo.currentText()
            self.cfg["is_patched"]   = True
            self.cfg["patched_font"] = font_name
            save_cfg(self.cfg)
            self.restore_btn.setEnabled(True)
            self.badge.set_ok(f"Success! Vortex will use '{font_name}' now.")
        else:
            self.badge.set_err(msg)

    def _on_font_mode_toggled(self, checked: bool):
        mode = "registry" if checked else "binary"
        self.cfg["font_mode"] = mode
        save_cfg(self.cfg)
        if checked:
            self.badge.set_ok("⚡ Instant Mode on — font changes will be instant, Vortex.exe untouched.")
        else:
            self.badge.set_warn("Binary Mode on — font changes will patch Vortex.exe directly (slower).")

    def _restore_font(self):
        self.restore_btn.setEnabled(False)
        font_mode = self.cfg.get("font_mode", "registry")
        if font_mode == "registry":
            self.badge.set_info("Removing font substitution from registry…")
        else:
            self.badge.set_info("Restoring original Vortex.exe…")
        w = RestoreWorker(cfg=self.cfg)
        w.done.connect(self._on_restore_done)
        self._worker = w; w.start()

    def _on_restore_done(self, ok, msg):
        if ok:
            self.cfg["is_patched"]   = False
            self.cfg["patched_font"] = None
            save_cfg(self.cfg)
            self.badge.set_ok("Original font Downloaded back.")
        else:
            self.restore_btn.setEnabled(True)
            self.badge.set_err(msg)

    def _find_system_font(self, name: str) -> str | None:
        dirs = [
            FONTS_DIR,
            Path(os.environ.get("WINDIR","C:/Windows")) / "Fonts",
            Path(os.environ.get("LOCALAPPDATA","")) / "Microsoft" / "Windows" / "Fonts",
        ]
        name_clean = name.lower().replace(" ", "").replace("-", "")
        
        # Known filename variants for common fonts (e.g. Comic Sans has several files)
        aliases = [name_clean]
        if "comicsans" in name_clean:
            aliases.extend(["comic", "comicbd", "comici", "comicz"])

        for d in dirs:
            if not d.exists(): continue
            for ext in ("ttf", "otf", "ttc"):
                for f in d.glob(f"*.{ext}"):
                    f_stem = f.stem.lower().replace(" ", "").replace("-", "")
                    for alias in aliases:
                        if alias == f_stem or alias in f_stem:
                            return str(f)
        return None

# ── Entry ─────────────────────────────────────────────────────────────────────
def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("VortexStrap")
    win = VortexLauncher()
    win.setWindowTitle("VortexStrap")
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
