"""
Rice to SoH Texture Pack Converter - GUI Version
==================================================
Drag-and-drop or click-to-browse GUI for converting Rice-format N64 texture
packs to Ship of Harkinian's .o2r mod format.

No command line needed. Just run this and click buttons.

Build standalone exe:
  pip install pyinstaller Pillow
  pyinstaller --onefile --windowed --name "Rice to SoH Converter" rice_to_soh_gui.py
"""
import os
import sys
import struct
import zipfile
import time
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image

MASK = 0xFFFFFFFF

TYPE_MAP = {
    1: (0, 3),  # RGBA32
    2: (0, 2),  # RGBA16
    3: (2, 0),  # CI4
    4: (2, 1),  # CI8
    5: (4, 0),  # I4
    6: (4, 1),  # I8
    7: (3, 0),  # IA4
    8: (3, 1),  # IA8
    9: (3, 2),  # IA16
}


def rice_crc(data_bytes, width, height, n64_size, pitch_bytes):
    crc = 0
    bpl = ((width << n64_size) + 1) // 2
    if bpl < 4 or height == 0:
        return 0
    pS = 0
    y = height - 1
    esi = 0
    while y >= 0:
        x = bpl - 4
        while x >= 0:
            if pS + x + 4 <= len(data_bytes):
                esi = struct.unpack_from('>I', data_bytes, pS + x)[0]
            else:
                esi = 0
            esi = (esi ^ x) & MASK
            crc = (((crc << 4) | (crc >> 28)) & MASK)
            crc = (crc + esi) & MASK
            x -= 4
        esi = (esi ^ y) & MASK
        crc = (crc + esi) & MASK
        pS += pitch_bytes
        y -= 1
    return crc


def make_otr_texture(png_path, original_type=1):
    """
    Create an OTR V1 texture resource.
    original_type: the TextureType from the base .o2r (preserves blend mode for
                   transparency types like IA8=8, IA16=9, CI4=3, CI8=4, etc.)
    """
    img = Image.open(png_path).convert('RGBA')

    # N64 Intensity (I4/I8) textures use their single color channel for BOTH Color and Alpha.
    # When PIL converts a grayscale image to RGBA, it hardcodes the Alpha channel to 255 (opaque).
    # This caused fences, windows, and shadows to render as solid black/white blocks.
    # By forcing the Alpha channel to match the color (Luminance), transparency is restored.
    if original_type in [5, 6]:  # 5 = I4, 6 = I8
        r, g, b, _ = img.split()
        img = Image.merge('RGBA', (r, g, b, r))

    w, h = img.size
    pixels = img.tobytes()
    header = bytearray(64)
    header[0] = 0
    header[1] = 1
    struct.pack_into('<I', header, 4, 0x4F544558)
    struct.pack_into('<I', header, 8, 1)
    struct.pack_into('<I', header, 12, 0xDEADBEEF)
    struct.pack_into('<I', header, 16, 0xDEADBEEF)
    body = bytearray()
    body += struct.pack('<I', original_type)   # preserve original type — DO NOT hardcode 1
    body += struct.pack('<I', w)
    body += struct.pack('<I', h)
    body += struct.pack('<I', 0x02)             # TEX_FLAG_LOAD_AS_IMG = raw RGBA pixels
    body += struct.pack('<f', 1.0)
    body += struct.pack('<f', 1.0)
    body += struct.pack('<I', len(pixels))
    body += pixels
    return bytes(header) + bytes(body)


def find_rice_folder(start_dir):
    """Try to auto-detect the Rice texture folder."""
    for item in os.listdir(start_dir):
        full = os.path.join(start_dir, item)
        if os.path.isdir(full):
            pngs = [f for f in os.listdir(full) if f.lower().endswith('.png') and '#' in f]
            if len(pngs) > 10:
                return full
            # Check one level deeper
            for sub in os.listdir(full):
                subfull = os.path.join(full, sub)
                if os.path.isdir(subfull):
                    pngs = [f for f in os.listdir(subfull) if f.lower().endswith('.png') and '#' in f]
                    if len(pngs) > 10:
                        return subfull
    return None


def find_o2r(start_dir):
    """Try to auto-detect the .o2r base archive."""
    for item in os.listdir(start_dir):
        if item.lower() in ('oot.o2r', 'oot-mq.o2r'):
            return os.path.join(start_dir, item)
    return None


class ConverterApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Rice to SoH Texture Pack Converter")
        self.root.geometry("700x580")
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1a2e")

        self.converting = False

        # Try auto-detect paths
        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        auto_o2r = find_o2r(exe_dir) or ""
        auto_rice = find_rice_folder(exe_dir) or ""

        self._build_ui(auto_o2r, auto_rice, exe_dir)

    def _build_ui(self, auto_o2r, auto_rice, exe_dir):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TButton', font=('Segoe UI', 10), padding=6)
        style.configure('TLabel', font=('Segoe UI', 10), background="#1a1a2e", foreground="#e0e0e0")
        style.configure('Header.TLabel', font=('Segoe UI', 16, 'bold'), background="#1a1a2e", foreground="#00d4ff")
        style.configure('Sub.TLabel', font=('Segoe UI', 9), background="#1a1a2e", foreground="#888")
        style.configure('TCheckbutton', background="#1a1a2e", foreground="#e0e0e0", font=('Segoe UI', 10))

        # Header
        ttk.Label(self.root, text="🎨 Rice to SoH Converter", style='Header.TLabel').pack(pady=(15, 2))
        ttk.Label(self.root, text="Convert N64 Rice texture packs to Ship of Harkinian .o2r format", style='Sub.TLabel').pack(pady=(0, 15))

        # --- O2R Path ---
        frame1 = tk.Frame(self.root, bg="#1a1a2e")
        frame1.pack(fill='x', padx=20, pady=5)
        ttk.Label(frame1, text="SoH Base Archive (.o2r):").pack(anchor='w')

        row1 = tk.Frame(frame1, bg="#1a1a2e")
        row1.pack(fill='x', pady=2)
        self.o2r_var = tk.StringVar(value=auto_o2r)
        self.o2r_entry = tk.Entry(row1, textvariable=self.o2r_var, font=('Segoe UI', 9), bg="#16213e", fg="#e0e0e0", insertbackground="#e0e0e0", relief='flat', bd=2)
        self.o2r_entry.pack(side='left', fill='x', expand=True, ipady=4)
        ttk.Button(row1, text="Browse...", command=self._browse_o2r).pack(side='right', padx=(5, 0))

        # --- Rice Folder ---
        frame2 = tk.Frame(self.root, bg="#1a1a2e")
        frame2.pack(fill='x', padx=20, pady=5)
        ttk.Label(frame2, text="Rice Texture Folder (folder of PNGs):").pack(anchor='w')

        row2 = tk.Frame(frame2, bg="#1a1a2e")
        row2.pack(fill='x', pady=2)
        self.rice_var = tk.StringVar(value=auto_rice)
        self.rice_entry = tk.Entry(row2, textvariable=self.rice_var, font=('Segoe UI', 9), bg="#16213e", fg="#e0e0e0", insertbackground="#e0e0e0", relief='flat', bd=2)
        self.rice_entry.pack(side='left', fill='x', expand=True, ipady=4)
        ttk.Button(row2, text="Browse...", command=self._browse_rice).pack(side='right', padx=(5, 0))

        # --- Output ---
        frame3 = tk.Frame(self.root, bg="#1a1a2e")
        frame3.pack(fill='x', padx=20, pady=5)
        ttk.Label(frame3, text="Output .o2r File:").pack(anchor='w')

        row3 = tk.Frame(frame3, bg="#1a1a2e")
        row3.pack(fill='x', pady=2)
        default_out = os.path.join(exe_dir, "texture_pack.o2r")
        self.out_var = tk.StringVar(value=default_out)
        self.out_entry = tk.Entry(row3, textvariable=self.out_var, font=('Segoe UI', 9), bg="#16213e", fg="#e0e0e0", insertbackground="#e0e0e0", relief='flat', bd=2)
        self.out_entry.pack(side='left', fill='x', expand=True, ipady=4)
        ttk.Button(row3, text="Browse...", command=self._browse_output).pack(side='right', padx=(5, 0))

        # --- Options ---
        frame4 = tk.Frame(self.root, bg="#1a1a2e")
        frame4.pack(fill='x', padx=20, pady=10)
        self.fallback_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame4, text="Include base textures for unmatched assets (recommended)", variable=self.fallback_var).pack(anchor='w')
        self.altfolder_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame4, text="Also export all textures to an alt folder (loose PNGs)", variable=self.altfolder_var).pack(anchor='w', pady=(4, 0))

        # --- Convert Button ---
        self.convert_btn = tk.Button(
            self.root, text="Convert!", font=('Segoe UI', 14, 'bold'),
            bg="#00d4ff", fg="#1a1a2e", activebackground="#00a8cc", activeforeground="#1a1a2e",
            relief='flat', bd=0, padx=30, pady=8, command=self._start_convert
        )
        self.convert_btn.pack(pady=10)

        # --- Progress ---
        self.progress = ttk.Progressbar(self.root, mode='determinate', length=660)
        self.progress.pack(padx=20, pady=5)

        self.status_var = tk.StringVar(value="Ready. Set your paths and click Convert!")
        ttk.Label(self.root, textvariable=self.status_var, style='Sub.TLabel').pack(pady=2)

        # --- Log ---
        log_frame = tk.Frame(self.root, bg="#1a1a2e")
        log_frame.pack(fill='both', expand=True, padx=20, pady=(5, 15))
        self.log = tk.Text(log_frame, height=8, font=('Consolas', 9), bg="#0f0f23", fg="#00ff88",
                           insertbackground="#00ff88", relief='flat', bd=2, state='disabled')
        self.log.pack(fill='both', expand=True)

    def _log(self, msg):
        self.log.config(state='normal')
        self.log.insert('end', msg + "\n")
        self.log.see('end')
        self.log.config(state='disabled')
        self.root.update_idletasks()

    def _browse_o2r(self):
        path = filedialog.askopenfilename(filetypes=[("O2R/OTR Files", "*.o2r *.otr"), ("All Files", "*.*")])
        if path:
            self.o2r_var.set(path)

    def _browse_rice(self):
        path = filedialog.askdirectory(title="Select folder containing Rice-format PNGs")
        if path:
            self.rice_var.set(path)

    def _browse_output(self):
        path = filedialog.asksaveasfilename(defaultextension=".o2r", filetypes=[("O2R Files", "*.o2r")])
        if path:
            self.out_var.set(path)

    def _start_convert(self):
        if self.converting:
            return
        o2r_path = self.o2r_var.get().strip()
        rice_path = self.rice_var.get().strip()
        out_path = self.out_var.get().strip()

        if not o2r_path or not os.path.exists(o2r_path):
            messagebox.showerror("Error", "Please select a valid .o2r base archive (oot.o2r or oot-mq.o2r)")
            return
        if not rice_path or not os.path.isdir(rice_path):
            messagebox.showerror("Error", "Please select a valid Rice texture folder (folder of PNGs)")
            return
        if not out_path:
            messagebox.showerror("Error", "Please set an output path")
            return

        self.converting = True
        self.convert_btn.config(state='disabled', text="Converting...")
        thread = threading.Thread(target=self._do_convert, args=(o2r_path, rice_path, out_path), daemon=True)
        thread.start()

    def _do_convert(self, o2r_path, rice_path, out_path):
        try:
            self._run_conversion(o2r_path, rice_path, out_path)
        except Exception as e:
            self.root.after(0, lambda: self._log(f"\nERROR: {e}"))
            self.root.after(0, lambda: self.status_var.set(f"Error: {e}"))
            self.root.after(0, lambda: messagebox.showerror("Error", str(e)))
        finally:
            self.root.after(0, lambda: self.convert_btn.config(state='normal', text="Convert!"))
            self.converting = False

    def _run_conversion(self, o2r_path, rice_path, out_path):
        def log(msg):
            self.root.after(0, lambda m=msg: self._log(m))

        def status(msg):
            self.root.after(0, lambda m=msg: self.status_var.set(m))

        def progress(val):
            self.root.after(0, lambda v=val: self.progress.configure(value=v))

        log("=" * 50)
        log("Rice to SoH Converter")
        log("=" * 50)

        # Step 1: Parse Rice filenames (scan subfolders too)
        status("Step 1/3: Scanning Rice textures...")
        log("\n[1/3] Scanning Rice texture pack (including subfolders)...")
        rice_textures = {}   # CRC -> full file path (best match: _all preferred)
        rice_palette = {}    # CRC -> {palette_hash: full_path} for ciByRGBA variants
        total_pngs = 0
        for dirpath, dirnames, filenames in os.walk(rice_path):
            for f in filenames:
                if not f.lower().endswith('.png'):
                    continue
                parts = f.split('#')
                if len(parts) < 4:
                    continue
                crc_str = parts[1].upper()
                try:
                    int(parts[2])
                    int(parts[3].split('_')[0])
                except (ValueError, IndexError):
                    continue
                total_pngs += 1
                full_path = os.path.join(dirpath, f)

                if len(parts) >= 5 and '_ciByRGBA' in parts[4]:
                    # Palette-specific variant: GAME#CRC#FMT#SIZE#PALHASH_ciByRGBA.png
                    pal_hash = parts[4].split('_')[0].upper()
                    if crc_str not in rice_palette:
                        rice_palette[crc_str] = {}
                    rice_palette[crc_str][pal_hash] = full_path
                    # Also set as default if no _all exists
                    if crc_str not in rice_textures:
                        rice_textures[crc_str] = full_path
                else:
                    # Universal _all variant — always preferred as default
                    rice_textures[crc_str] = full_path
        palette_crcs = sum(1 for v in rice_palette.values() if len(v) > 1)
        log(f"  Found {total_pngs} Rice textures ({len(rice_textures)} unique CRCs)")
        if palette_crcs:
            log(f"  {palette_crcs} CRCs have multiple palette variants")

        if not rice_textures:
            raise Exception("No Rice-format PNGs found in the selected folder.\n\n"
                            "Expected filenames like:\nGAMENAME#CRC#FORMAT#SIZE_suffix.png")

        # Step 2: Compute CRCs and match
        status("Step 2/3: Matching textures (this takes a minute)...")
        log("\n[2/3] Matching textures...")
        matches = {}
        fallbacks = []
        total = 0
        t0 = time.time()

        with zipfile.ZipFile(o2r_path, 'r') as z:
            names = [n for n in z.namelist() if not n.endswith('/') and n != 'version']
            for idx, name in enumerate(names):
                data = z.read(name)
                if len(data) < 0x54:
                    continue
                if struct.unpack_from('<I', data, 4)[0] != 0x4F544558:
                    continue

                tt = struct.unpack_from('<I', data, 0x40)[0]
                w = struct.unpack_from('<I', data, 0x44)[0]
                h = struct.unpack_from('<I', data, 0x48)[0]
                ds = struct.unpack_from('<I', data, 0x4C)[0]
                td = data[0x50:0x50 + ds]

                if tt not in TYPE_MAP or w == 0 or h == 0:
                    continue

                total += 1
                _, n64_size = TYPE_MAP[tt]
                bpp = [4, 8, 16, 32][n64_size]
                pitch = max(8, (w * bpp + 63) // 64 * 8)
                bpl = ((w << n64_size) + 1) // 2

                matched = False
                crc = rice_crc(td, w, h, n64_size, pitch)
                crc_hex = f"{crc:08X}"
                if crc_hex in rice_textures:
                    matches[name] = (rice_textures[crc_hex], tt)  # (full_path, original_type)
                    matched = True
                elif bpl != pitch:
                    crc = rice_crc(td, w, h, n64_size, bpl)
                    crc_hex = f"{crc:08X}"
                    if crc_hex in rice_textures:
                        matches[name] = (rice_textures[crc_hex], tt)  # (full_path, original_type)
                        matched = True

                if not matched:
                    fallbacks.append(name)

                if (idx + 1) % 500 == 0:
                    pct_done = (idx + 1) * 50 // len(names)
                    progress(pct_done)
                    status(f"Step 2/3: Processed {idx + 1}/{len(names)} assets...")

        elapsed = time.time() - t0
        pct = len(matches) * 100 // max(total, 1)
        log(f"  Matched {len(matches)}/{total} textures ({pct}%) in {elapsed:.1f}s")
        if self.fallback_var.get():
            log(f"  {len(fallbacks)} textures will use base assets as fallback")
        progress(50)

        if not matches:
            raise Exception("No matches found!\n\nMake sure:\n- The .o2r is from Ship of Harkinian\n"
                            "- The Rice pack is for the same game (Ocarina of Time)")

        # Step 3: Pack
        use_fallback = self.fallback_var.get()
        total_to_pack = len(matches) + (len(fallbacks) if use_fallback else 0)
        status(f"Step 3/3: Packing {total_to_pack} textures...")
        log(f"\n[3/3] Packing {total_to_pack} textures...")
        packed = 0
        fallback_packed = 0
        errors = 0

        use_altfolder = self.altfolder_var.get()
        alt_dir = None
        if use_altfolder:
            alt_dir = os.path.splitext(out_path)[0] + '_textures'
            os.makedirs(alt_dir, exist_ok=True)
            log(f"  Alt folder: {alt_dir}")

        os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)

        with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_STORED) as zout:
            for otr_path, (rice_png, orig_type) in matches.items():  # unpack (path, type) tuple
                try:
                    resource_data = make_otr_texture(rice_png, orig_type)  # pass original type!
                    zout.writestr(otr_path, resource_data)
                    packed += 1

                    # Export to alt folder if enabled
                    if use_altfolder:
                        alt_file = os.path.join(alt_dir, otr_path.replace('/', os.sep))
                        os.makedirs(os.path.dirname(alt_file) or '.', exist_ok=True)
                        # Copy the source Rice PNG
                        import shutil
                        alt_png = os.path.splitext(alt_file)[0] + '.png'
                        shutil.copy2(rice_png, alt_png)

                except Exception as e:
                    errors += 1
                    if errors <= 3:
                        log(f"  Warning: {otr_path}: {e}")

                if packed % 200 == 0:
                    pct_done = 50 + (packed * 40 // max(len(matches), 1))
                    progress(pct_done)
                    status(f"Step 3/3: Packed {packed}/{len(matches)} celda textures...")

            if use_fallback and fallbacks:
                log(f"  Adding {len(fallbacks)} base-texture fallbacks...")
                with zipfile.ZipFile(o2r_path, 'r') as zbase:
                    for i, otr_path in enumerate(fallbacks):
                        try:
                            zout.writestr(otr_path, zbase.read(otr_path))
                            fallback_packed += 1
                        except Exception:
                            errors += 1

                        if (i + 1) % 200 == 0:
                            pct_done = 90 + ((i + 1) * 10 // len(fallbacks))
                            progress(pct_done)

        progress(100)
        size_mb = os.path.getsize(out_path) / 1024 / 1024

        log(f"\n{'=' * 50}")
        log(f"DONE! {size_mb:.0f} MB")
        log(f"  Replacements: {packed}")
        if use_fallback:
            log(f"  Base fallbacks: {fallback_packed}")
        log(f"  Total: {packed + fallback_packed}")
        if errors:
            log(f"  Errors skipped: {errors}")
        log(f"\nOutput: {out_path}")
        log(f"\nNext: Put the .o2r in your SoH mods/ folder")
        log(f"      F1 -> Mods -> Enable -> Restart SoH")
        log(f"{'=' * 50}")

        status(f"Done! {packed + fallback_packed} textures packed ({size_mb:.0f} MB)")
        self.root.after(0, lambda: messagebox.showinfo("Done!",
            f"Conversion complete!\n\n"
            f"Packed {packed + fallback_packed} textures ({size_mb:.0f} MB)\n\n"
            f"Put {os.path.basename(out_path)} in your SoH mods/ folder,\n"
            f"then F1 → Mods → Enable → Restart SoH"))

    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    app = ConverterApp()
    app.run()
