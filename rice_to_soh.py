"""
Rice Texture Pack -> Ship of Harkinian (.o2r) Converter   (v2)
=============================================================
Converts Rice-format N64 texture packs (Mupen64/Project64) to SoH's
native .o2r mod format by reverse-engineering the Rice plugin's CRC.

QUICK START (no Python editing needed):
    python rice_to_soh.py "C:\\path\\to\\your\\Ship of Harkinian folder"

  ...or set SOH_DIR below once and just run:  python rice_to_soh.py

OPTIONS:
    --rice "<folder>"    Rice PNG folder   (default: ./celda_rice/THE LEGEND OF ZELDA)
    --out  "<file.o2r>"  output .o2r path  (default: <SoH>/mods/celda.o2r)
    --base "<file.o2r>"  EXTRA base archive to match against (repeatable). Extract one
                         from the ROM version the pack author actually used (e.g. the
                         PAL cart for European artists) for a real match-rate boost.
    --map  "<file.json>" Prebuilt filename->asset-path mapping. celda_mapping.json
                         (shipped with this repo, auto-loaded if next to the script)
                         gives Djipi's Celda pack its full 67% with NO extra ROMs.
    --no-map             Skip the auto-loaded mapping (pure CRC matching only).
    --dry-run            match + write the CSV reports only; skip building the big .o2r
                         (fast: use this while tuning / to see what will match)

REPORTS (written next to the output .o2r):
    unmatched_rice.csv   YOUR textures that found no home in the game archive
    matched.csv          rice_file -> game texture path  (audit trail)
    errors.csv           textures that failed to pack    (only if any)

Requirements:  Python 3.8+  and  Pillow  (pip install Pillow)

How it works:
    - Reads raw N64 texture data from SoH's base .o2r archive(s)
    - Computes Rice plugin CRC hashes (CalculateRDRAMCRC algorithm)
    - Matches CRCs against the Rice pack's filenames
    - Packs matched replacement PNGs into a new .o2r mod
    If BOTH oot.o2r and oot-mq.o2r are present, it matches against both
    (Master Quest + original) for maximum in-game coverage.
"""
import os, sys, struct, zipfile, time, csv, json

try:
    from PIL import Image
except ImportError:
    sys.exit("ERROR: Pillow is not installed.  Fix it with:  pip install Pillow")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# CONFIG  (or pass the SoH folder as the first command-line argument)
# ============================================================
SOH_DIR = r"PUT_YOUR_SOH_FOLDER_HERE"   # your Ship of Harkinian install folder
RICE_TEXTURES = os.path.join(SCRIPT_DIR, "celda_rice", "THE LEGEND OF ZELDA")
OUTPUT = None                            # None -> <SoH>/mods/celda.o2r
DRY_RUN = False
EXTRA_BASES = []                         # extra base .o2r archives (--base); e.g. one
                                         # extracted from the ROM version the pack
                                         # author dumped on -- big match-rate boost
MAP_FILE = None                          # --map; None = auto-load celda_mapping.json
USE_MAP = True                           # --no-map disables the auto-load
# ============================================================

MASK = 0xFFFFFFFF

# OTR TextureType -> (N64 format, N64 size enum). Size enum: 0=4bpp 1=8bpp 2=16bpp 3=32bpp
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

# When two Rice PNGs share a CRC, prefer the most complete variant (higher wins)
SUFFIX_PRIORITY = {"all": 4, "ciByRGBA": 3, "rgb": 2, "a": 1}


def parse_args(argv):
    """Tiny dependency-free CLI parser. First positional arg = SoH folder."""
    global SOH_DIR, RICE_TEXTURES, OUTPUT, DRY_RUN, MAP_FILE, USE_MAP
    positionals, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--rice" and i + 1 < len(argv):
            RICE_TEXTURES = argv[i + 1]; i += 2
        elif a == "--out" and i + 1 < len(argv):
            OUTPUT = argv[i + 1]; i += 2
        elif a == "--base" and i + 1 < len(argv):
            EXTRA_BASES.append(argv[i + 1]); i += 2
        elif a == "--map" and i + 1 < len(argv):
            MAP_FILE = argv[i + 1]; i += 2
        elif a == "--no-map":
            USE_MAP = False; i += 1
        elif a in ("--dry-run", "--report-only"):
            DRY_RUN = True; i += 1
        else:
            positionals.append(a); i += 1
    if positionals:
        SOH_DIR = positionals[0]


def suffix_of(filename):
    """Pull the trailing suffix token, e.g. 'all' from '...#0_all.png'."""
    stem = filename.rsplit('.', 1)[0]
    return stem.split('_', 1)[1] if '_' in stem else ''


def rice_crc(data_bytes, width, height, n64_size, pitch_bytes):
    """
    Rice plugin's CalculateRDRAMCRC hash (mupen64plus-video-rice FrameBuffer.cpp).
    Reads uint32 big-endian ('>I') because N64 RDRAM is big-endian.
    """
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
            crc = (((crc << 4) | (crc >> 28)) & MASK)  # ROL32 by 4
            crc = (crc + esi) & MASK
            x -= 4
        esi = (esi ^ y) & MASK
        crc = (crc + esi) & MASK
        pS += pitch_bytes
        y -= 1
    return crc


def make_otr_texture(png_path):
    """Create an OTR V1 RGBA32 texture resource from a PNG (LOAD_AS_IMG)."""
    img = Image.open(png_path).convert('RGBA')
    w, h = img.size
    pixels = img.tobytes()

    header = bytearray(64)
    header[0] = 0                                       # LE endianness
    header[1] = 1                                       # IsCustom = true
    struct.pack_into('<I', header, 4, 0x4F544558)       # Magic: OTEX
    struct.pack_into('<I', header, 8, 1)                # Version: 1
    struct.pack_into('<I', header, 12, 0xDEADBEEF)      # ID placeholder
    struct.pack_into('<I', header, 16, 0xDEADBEEF)

    body = bytearray()
    body += struct.pack('<I', 1)                # TextureType = RGBA32bpp
    body += struct.pack('<I', w)
    body += struct.pack('<I', h)
    body += struct.pack('<I', 0x02)             # Flags = TEX_FLAG_LOAD_AS_IMG
    body += struct.pack('<f', 1.0)              # HByteScale
    body += struct.pack('<f', 1.0)              # VPixelScale
    body += struct.pack('<I', len(pixels))
    body += pixels
    return bytes(header) + bytes(body)


def find_base_archives(soh_dir):
    """Return all present base archives (oot.o2r first, then oot-mq.o2r)."""
    found = []
    for name in ("oot.o2r", "oot-mq.o2r"):
        p = os.path.join(soh_dir, name)
        if os.path.exists(p):
            found.append(p)
    return found


def scan_rice(folder):
    """Recursively collect Rice PNGs, deduped by CRC with suffix priority.
    Returns (rice: {crc -> (full_path, filename)}, n_files, n_collisions)."""
    rice, n_files, collisions = {}, 0, 0
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if not f.lower().endswith('.png'):
                continue
            parts = f.split('#')
            if len(parts) < 4:
                continue
            crc = parts[1].upper()
            try:
                int(parts[2])
                int(parts[3].split('_')[0])
            except (ValueError, IndexError):
                continue
            n_files += 1
            full = os.path.join(root, f)
            if crc in rice:
                collisions += 1
                if SUFFIX_PRIORITY.get(suffix_of(f), 0) > SUFFIX_PRIORITY.get(suffix_of(rice[crc][1]), 0):
                    rice[crc] = (full, f)
            else:
                rice[crc] = (full, f)
    return rice, n_files, collisions


def write_reports(report_dir, rice, matched_crcs, matches):
    os.makedirs(report_dir, exist_ok=True)
    unmatched_path = os.path.join(report_dir, "unmatched_rice.csv")
    with open(unmatched_path, "w", newline="", encoding="utf-8") as fo:
        w = csv.writer(fo)
        w.writerow(["rice_file", "crc", "fmt", "siz", "suffix"])
        for crc, (full, fn) in sorted(rice.items()):
            if crc in matched_crcs:
                continue
            parts = fn.split('#')
            siz_suf = parts[3] if len(parts) > 3 else ""
            siz = siz_suf.split('_')[0]
            w.writerow([fn, crc, parts[2] if len(parts) > 2 else "", siz, suffix_of(fn)])
    matched_path = os.path.join(report_dir, "matched.csv")
    with open(matched_path, "w", newline="", encoding="utf-8") as fo:
        w = csv.writer(fo)
        w.writerow(["game_texture_path", "rice_file"])
        for path, (full, fn) in sorted(matches.items()):
            w.writerow([path, fn])
    return unmatched_path, matched_path


def main():
    parse_args(sys.argv[1:])
    print("=" * 60)
    print("Rice Texture Pack -> SoH (.o2r) Converter   v2")
    print("=" * 60)

    if SOH_DIR == "PUT_YOUR_SOH_FOLDER_HERE":
        print("\nYour Ship of Harkinian folder isn't set yet. Either:")
        print('   run:  python rice_to_soh.py "C:\\path\\to\\Ship of Harkinian"')
        print("   or edit SOH_DIR near the top of this script.")
        return
    if not os.path.isdir(SOH_DIR):
        print(f"\nERROR: That SoH folder doesn't exist:\n   {SOH_DIR}")
        return

    extra = []
    for p in EXTRA_BASES:
        if os.path.exists(p):
            extra.append(p)
        else:
            print(f"\nWARNING: --base archive not found, skipping:\n   {p}")
    # Extra bases go FIRST: if the pack was authored on that ROM version, its
    # archive should drive the matching; the install's archives fill in the rest.
    archives = extra + find_base_archives(SOH_DIR)
    if not archives:
        print(f"\nERROR: No oot.o2r or oot-mq.o2r found in:\n   {SOH_DIR}")
        print("Point this at your Ship of Harkinian install folder (the one with soh.exe).")
        return

    out_path = OUTPUT or os.path.join(SOH_DIR, "mods", "celda.o2r")
    report_dir = os.path.dirname(os.path.abspath(out_path)) or "."

    print(f"\nSoH folder     : {SOH_DIR}")
    print(f"Base archive(s): {', '.join(os.path.basename(a) for a in archives)}")
    print(f"Rice textures  : {RICE_TEXTURES}")
    print(f"Output         : {out_path}" + ("   (--dry-run: will NOT be written)" if DRY_RUN else ""))

    if not os.path.isdir(RICE_TEXTURES):
        print(f"\nERROR: Rice texture folder not found:\n   {RICE_TEXTURES}")
        print("Pass it with  --rice \"<folder of PNGs>\"  or fix RICE_TEXTURES at the top.")
        return

    # ---- 1. Scan the Rice pack ----
    print("\n[1/3] Scanning Rice texture pack...")
    rice, n_files, collisions = scan_rice(RICE_TEXTURES)
    print(f"  {n_files} PNG files -> {len(rice)} unique textures (by CRC)")
    if collisions:
        print(f"  ({collisions} files shared a CRC with another; kept the most complete variant)")
    if not rice:
        print("\nERROR: No Rice-format PNGs found.")
        print("Expected filenames like: GAMENAME#CRC#FORMAT#SIZE_suffix.png")
        return

    # ---- 2. Match against base archive(s) ----
    print("\n[2/3] Matching textures (computing Rice CRCs)...")
    matches = {}          # otr_path -> (full_png, rice_filename)
    matched_crcs = set()  # which of the artist's CRCs found a home
    base_paths = set()    # distinct typed base textures seen
    t0 = time.time()
    for arch in archives:
        with zipfile.ZipFile(arch, 'r') as z:
            names = [n for n in z.namelist() if not n.endswith('/') and n != 'version']
            for idx, name in enumerate(names):
                if name in matches:
                    continue  # already replaced (from an earlier archive)
                data = z.read(name)
                if len(data) < 0x54:
                    continue
                if struct.unpack_from('<I', data, 4)[0] != 0x4F544558:
                    continue
                tt = struct.unpack_from('<I', data, 0x40)[0]
                w  = struct.unpack_from('<I', data, 0x44)[0]
                h  = struct.unpack_from('<I', data, 0x48)[0]
                ds = struct.unpack_from('<I', data, 0x4C)[0]
                td = data[0x50:0x50 + ds]
                if tt not in TYPE_MAP or w == 0 or h == 0:
                    continue
                base_paths.add(name)

                _, n64_size = TYPE_MAP[tt]
                bpp = [4, 8, 16, 32][n64_size]
                pitch = max(8, (w * bpp + 63) // 64 * 8)
                bpl = ((w << n64_size) + 1) // 2

                crc = rice_crc(td, w, h, n64_size, pitch)
                if crc == 0:
                    continue  # degenerate (bpl<4); never use 0 as a match key
                ch = f"{crc:08X}"
                if ch not in rice and bpl != pitch:
                    crc = rice_crc(td, w, h, n64_size, bpl)  # try unaligned pitch
                    if crc:
                        ch = f"{crc:08X}"
                if ch in rice:
                    matches[name] = rice[ch]
                    matched_crcs.add(ch)

                if (idx + 1) % 4000 == 0:
                    print(f"  {os.path.basename(arch)}: {idx + 1}/{len(names)}...")
    elapsed = time.time() - t0

    # ---- 2b. Prebuilt mapping (fills matches CRC-hashing can't reach here) ----
    map_path = MAP_FILE or (os.path.join(SCRIPT_DIR, "celda_mapping.json") if USE_MAP else None)
    if map_path and os.path.exists(map_path):
        with open(map_path, encoding="utf-8") as f:
            mapping = json.load(f).get("map", {})
        by_name = {fn: (full, fn) for (full, fn) in rice.values()}
        crc_of = {fn: crc for crc, (_full, fn) in rice.items()}
        added_tex, added_paths = 0, 0
        for fn, paths in mapping.items():
            entry = by_name.get(fn)
            if not entry:
                continue
            used = False
            for path in paths:
                if path not in matches:
                    matches[path] = entry
                    base_paths.add(path)
                    added_paths += 1
                    used = True
            if used and crc_of.get(fn) not in matched_crcs:
                matched_crcs.add(crc_of[fn])
                added_tex += 1
        if added_paths:
            print(f"  + mapping ({os.path.basename(map_path)}): {added_tex} more of your textures, {added_paths} more game paths")

    pack_pct = len(matched_crcs) * 100 // max(len(rice), 1)
    cov_pct = len(matches) * 100 // max(len(base_paths), 1)
    print(f"  done in {elapsed:.1f}s")
    print(f"\n  >> Imported {len(matched_crcs)}/{len(rice)} of YOUR textures ({pack_pct}%)")
    print(f"     Game coverage: {len(matches)}/{len(base_paths)} base textures replaced ({cov_pct}%)")

    if not matches:
        print("\nERROR: No matches found. Check that the Rice pack is for this game/ROM.")
        return

    # ---- Reports ----
    unmatched_path, matched_path = write_reports(report_dir, rice, matched_crcs, matches)
    n_unmatched = len(rice) - len(matched_crcs)
    print(f"\n  {n_unmatched} of your textures found no home in this archive.")
    print(f"  -> see {os.path.basename(unmatched_path)} (and {os.path.basename(matched_path)}) in:")
    print(f"     {report_dir}")

    if DRY_RUN:
        print("\nDry run complete -- skipped building the .o2r.")
        print("Re-run without --dry-run to actually pack the textures.")
        return

    # ---- 3. Pack ----
    print(f"\n[3/3] Packing {len(matches)} textures into:\n   {out_path}")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    packed, errors = 0, []
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_STORED) as zout:
        for otr_path, (full, fn) in matches.items():
            try:
                zout.writestr(otr_path, make_otr_texture(full))
                packed += 1
            except Exception as e:
                errors.append((fn, str(e)))
            if packed % 1000 == 0 and packed:
                print(f"  {packed}/{len(matches)} packed...")
    if errors:
        with open(os.path.join(report_dir, "errors.csv"), "w", newline="", encoding="utf-8") as fo:
            w = csv.writer(fo)
            w.writerow(["rice_file", "error"])
            w.writerows(errors)

    size_mb = os.path.getsize(out_path) / 1024 / 1024
    print(f"\n{'=' * 60}")
    print(f"DONE! Packed {packed} textures ({size_mb:.0f} MB)")
    if errors:
        print(f"  {len(errors)} textures failed to pack -- see errors.csv")
    print(f"Output: {out_path}")
    print(f"{'=' * 60}")

    mods_dir = os.path.abspath(os.path.join(SOH_DIR, "mods"))
    if os.path.abspath(out_path).lower().startswith(mods_dir.lower()):
        print("\nIt's already in your mods/ folder. Next:")
        print("  SoH -> F1 -> Mods tab -> enable celda -> restart SoH.")
    else:
        print(f"\nNext steps:")
        print(f"  1. Copy this .o2r into {mods_dir}")
        print(f"  2. SoH -> F1 -> Mods tab -> enable it -> restart SoH")


if __name__ == '__main__':
    main()
