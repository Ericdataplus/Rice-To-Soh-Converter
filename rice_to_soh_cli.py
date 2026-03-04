"""
Rice Texture Pack to Ship of Harkinian (.o2r) Converter
========================================================
Converts Rice-format N64 texture packs (like Djipi's Celda Cellshade)
to Ship of Harkinian's native .o2r mod format.

Requirements:
  pip install Pillow

Usage:
  python rice_to_soh_share.py --o2r "path/to/oot.o2r" --rice "path/to/rice/pngs" --output "path/to/soh/mods/celda.o2r"

Options:
  --alt-folder    Also export all matched textures to a folder of loose PNGs

How to get the texture pack:
  1. Download Djipi's "Zelda Cellshade 2016" from:
     https://emulationking.com/nintendo/n64/games/zeldaocarinaoftime/texturepacks/djipi2016cellshade/
  2. Extract it - you'll get a folder called "THE LEGEND OF ZELDA" full of PNGs
  3. Point --rice at that folder

How to use the output:
  1. Put the generated .o2r file in your SoH "mods/" folder
  2. Launch SoH -> F1 -> Mods tab -> Enable it -> Restart SoH

Credits:
  - Djipi: Original cel-shaded texture artwork
  - Rice/Mupen64Plus: CRC algorithm (FrameBuffer.cpp)
  - Ship of Harkinian: OTR/O2R resource format
"""
import os, struct, zipfile, time, argparse, shutil
from PIL import Image

MASK = 0xFFFFFFFF

# OTR TextureType -> (N64 format, N64 size enum)
# Size enum: 0=4bpp, 1=8bpp, 2=16bpp, 3=32bpp
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
    """
    Rice plugin's CalculateRDRAMCRC hash algorithm.
    From mupen64plus-video-rice FrameBuffer.cpp.

    CRITICAL: Must read uint32 as big-endian ('>I') because N64 RDRAM
    is big-endian and the x86 CRC code does raw pointer dereferences.
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


def make_otr_texture(png_path, original_type=1):
    """
    Create an OTR V1 texture resource from a PNG file.

    original_type: the TextureType from the base .o2r (preserves blend mode for
                   transparency types like IA8=8, IA16=9, CI4=3, CI8=4, etc.)
                   Hardcoding 1 (RGBA32) broke alpha textures — they showed black.

    Format:
      [64-byte header] [Type:u32] [W:u32] [H:u32] [Flags:u32]
      [HByteScale:f32] [VPixelScale:f32] [DataSize:u32] [RGBA pixels]
    """
    img = Image.open(png_path).convert('RGBA')
    w, h = img.size
    pixels = img.tobytes()

    # 64-byte OTR resource header
    header = bytearray(64)
    header[0] = 0                                       # LE endianness
    header[1] = 1                                       # IsCustom = true
    struct.pack_into('<I', header, 4, 0x4F544558)       # Magic: OTEX
    struct.pack_into('<I', header, 8, 1)                # Version: 1
    struct.pack_into('<I', header, 12, 0xDEADBEEF)      # ID placeholder
    struct.pack_into('<I', header, 16, 0xDEADBEEF)      # ID placeholder

    # V1 texture body
    body = bytearray()
    body += struct.pack('<I', original_type)    # preserve original type — DO NOT hardcode 1
    body += struct.pack('<I', w)                # Width
    body += struct.pack('<I', h)                # Height
    body += struct.pack('<I', 0x02)             # Flags = TEX_FLAG_LOAD_AS_IMG
    body += struct.pack('<f', 1.0)              # HByteScale
    body += struct.pack('<f', 1.0)              # VPixelScale
    body += struct.pack('<I', len(pixels))      # ImageDataSize
    body += pixels                              # Raw RGBA32 pixel data

    return bytes(header) + bytes(body)


def main():
    parser = argparse.ArgumentParser(
        description="Convert Rice-format N64 texture packs to Ship of Harkinian .o2r format",
        epilog="Example: python rice_to_soh_share.py --o2r oot.o2r --rice \"THE LEGEND OF ZELDA\" --output mods/celda.o2r"
    )
    parser.add_argument('--o2r', required=True, help='Path to your SoH base archive (oot.o2r or oot-mq.o2r)')
    parser.add_argument('--rice', required=True, help='Path to folder containing Rice-format PNGs (scans subfolders too)')
    parser.add_argument('--output', default='celda.o2r', help='Output .o2r file path (default: celda.o2r)')
    parser.add_argument('--alt-folder', action='store_true', help='Also export matched textures to a folder of loose PNGs')
    args = parser.parse_args()

    print("=" * 60)
    print("Rice Texture Pack -> SoH (.o2r) Converter")
    print("(with base-texture fallback for unmatched assets)")
    print("=" * 60)

    if not os.path.exists(args.o2r):
        print(f"\nERROR: Base archive not found: {args.o2r}")
        return

    if not os.path.isdir(args.rice):
        print(f"\nERROR: Rice texture folder not found: {args.rice}")
        return

    # ---- Step 1: Parse Rice filenames (scan subfolders too) ----
    print("\n[1/3] Scanning Rice texture pack (including subfolders)...")
    rice_textures = {}   # CRC -> full file path (best match: _all preferred)
    rice_palette = {}    # CRC -> {palette_hash: full_path} for ciByRGBA variants
    total_pngs = 0
    for dirpath, dirnames, filenames in os.walk(args.rice):
        for f in filenames:
            if not f.lower().endswith('.png'):
                continue
            parts = f.split('#')
            if len(parts) < 4:
                continue
            crc_str = parts[1].upper()
            try:
                int(parts[2])                       # format
                int(parts[3].split('_')[0])          # size
            except (ValueError, IndexError):
                continue
            total_pngs += 1
            full_path = os.path.join(dirpath, f)

            if len(parts) >= 5 and '_ciByRGBA' in parts[4]:
                # Palette-specific variant
                pal_hash = parts[4].split('_')[0].upper()
                if crc_str not in rice_palette:
                    rice_palette[crc_str] = {}
                rice_palette[crc_str][pal_hash] = full_path
                if crc_str not in rice_textures:
                    rice_textures[crc_str] = full_path
            else:
                # Universal _all variant — always preferred as default
                rice_textures[crc_str] = full_path
    palette_crcs = sum(1 for v in rice_palette.values() if len(v) > 1)
    print(f"  Found {total_pngs} Rice textures ({len(rice_textures)} unique CRCs)")
    if palette_crcs:
        print(f"  {palette_crcs} CRCs have multiple palette variants")

    if not rice_textures:
        print("\nERROR: No Rice-format PNGs found in the selected folder.")
        print("Expected filenames like: GAMENAME#CRC#FORMAT#SIZE_suffix.png")
        return

    # ---- Step 2: Compute CRCs and match ----
    print("\n[2/3] Matching textures (computing Rice CRCs)...")
    matches = {}        # otr_path -> (rice_full_path, original_type) tuple
    fallbacks = []      # otr_paths with no celda match (will use base texture)
    total = 0
    t0 = time.time()

    with zipfile.ZipFile(args.o2r, 'r') as z:
        names = [n for n in z.namelist() if not n.endswith('/') and n != 'version']
        for idx, name in enumerate(names):
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

            total += 1
            _, n64_size = TYPE_MAP[tt]
            bpp = [4, 8, 16, 32][n64_size]
            pitch = max(8, (w * bpp + 63) // 64 * 8)
            bpl = ((w << n64_size) + 1) // 2

            matched = False
            # Try aligned pitch first
            crc = rice_crc(td, w, h, n64_size, pitch)
            crc_hex = f"{crc:08X}"
            if crc_hex in rice_textures:
                matches[name] = (rice_textures[crc_hex], tt)  # (full_path, original_type)
                matched = True
            elif bpl != pitch:
                # Try unaligned pitch
                crc = rice_crc(td, w, h, n64_size, bpl)
                crc_hex = f"{crc:08X}"
                if crc_hex in rice_textures:
                    matches[name] = (rice_textures[crc_hex], tt)  # (full_path, original_type)
                    matched = True

            # No celda match - fall back to base texture
            if not matched:
                fallbacks.append(name)

            if (idx + 1) % 3000 == 0:
                print(f"  Processed {idx + 1}/{len(names)}...")

    elapsed = time.time() - t0
    pct = len(matches) * 100 // max(total, 1)
    print(f"  Matched {len(matches)}/{total} textures ({pct}%) in {elapsed:.1f}s")
    print(f"  {len(fallbacks)} textures will use base assets as fallback")

    if not matches:
        print("\nERROR: No matches found! Check that your paths are correct")
        print("and that the Rice pack is for the same game as the .o2r.")
        return

    # ---- Step 3: Pack into .o2r (celda + base fallbacks) ----
    print(f"\n[3/3] Packing {len(matches)} celda + {len(fallbacks)} base textures into {args.output}...")
    packed = 0
    fallback_packed = 0
    errors = 0

    # Alt folder setup
    alt_dir = None
    if args.alt_folder:
        alt_dir = os.path.splitext(args.output)[0] + '_textures'
        os.makedirs(alt_dir, exist_ok=True)
        print(f"  Alt folder: {alt_dir}")

    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)

    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_STORED) as zout:
        # Pack celda replacements
        for otr_path, (rice_png, orig_type) in matches.items():  # unpack (path, type) tuple
            try:
                resource_data = make_otr_texture(rice_png, orig_type)  # pass original type!
                zout.writestr(otr_path, resource_data)
                packed += 1

                # Export to alt folder if enabled
                if alt_dir:
                    alt_file = os.path.join(alt_dir, otr_path.replace('/', os.sep))
                    os.makedirs(os.path.dirname(alt_file) or '.', exist_ok=True)
                    alt_png = os.path.splitext(alt_file)[0] + '.png'
                    shutil.copy2(rice_png, alt_png)

            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"  Warning: {otr_path}: {e}")

            if packed % 1000 == 0 and packed > 0:
                print(f"  {packed}/{len(matches)} celda textures packed...")

        # Pack base fallbacks for unmatched textures
        print(f"  Packing {len(fallbacks)} base-texture fallbacks...")
        with zipfile.ZipFile(args.o2r, 'r') as zbase:
            for otr_path in fallbacks:
                try:
                    base_data = zbase.read(otr_path)
                    zout.writestr(otr_path, base_data)
                    fallback_packed += 1
                except Exception as e:
                    errors += 1
                    if errors <= 6:
                        print(f"  Warning (fallback): {otr_path}: {e}")

    size_mb = os.path.getsize(args.output) / 1024 / 1024

    print(f"\n{'=' * 60}")
    print(f"DONE! Complete texture pack: {size_mb:.0f} MB")
    print(f"  Celda replacements: {packed}")
    print(f"  Base fallbacks:     {fallback_packed}")
    print(f"  Total textures:     {packed + fallback_packed}")
    if errors:
        print(f"  Errors (skipped):   {errors}")
    if alt_dir:
        print(f"  Alt folder:         {alt_dir}")
    print(f"Output: {args.output}")
    print(f"{'=' * 60}")
    print(f"\nNext steps:")
    print(f"  1. Put {os.path.basename(args.output)} in your SoH mods/ folder")
    print(f"  2. Open SoH -> F1 -> Mods tab -> Enable it")
    print(f"  3. Restart SoH and enjoy!")


if __name__ == '__main__':
    main()

