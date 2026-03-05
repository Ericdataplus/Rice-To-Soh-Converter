# Rice to SoH Texture Pack Converter

👉 **[Launch the Web Version Here!](https://ericdataplus.github.io/Rice-To-Soh-Converter/)** 👈

A straightforward python utility designed for mapping N64 Rice format texture packs (such as **Djipi's Celda Cellshade** and others) to the **Ship of Harkinian (.o2r)** mod format.

Although initially created specifically to help convert Djipi's Ocarina of Time cel-shaded pack directly into SoH formats, this tool functions via CRC hashing matching and will happily convert other Rice N64 texture replacements provided the base archives match up.

## Features Let's you:
- Auto-scans subdirectories for valid `GAMENAME#CRC#FORMAT#SIZE_suffix.png` Rice images.
- Unpacks, reads, hashes, and compares textures against the provided `.o2r` internal game dump.
- Matches N64 big-endian memory hashes against standard Rice CRC matching.
- **Accurately handles Alpha channels (IA8, IA16, CI4)** so transparent textures won't render as black!
- Injects missing non-replaced base files to ensure the entire world is properly mapped without texture holes.
- Easy to use GUI without requiring python knowledge if distributed via executable.

## How To Run

If running from python source:
```bash
pip install Pillow
python rice_to_soh_gui.py
```
*(Or use `python rice_to_soh_cli.py` for command-line access)*

### Making an Executable (For Sharing)
You can easily bundle this for friends without Python:
```bash
pip install pyinstaller Pillow
pyinstaller --onefile --windowed --name "Rice to SoH Converter" rice_to_soh_gui.py
```

## How to Use
1. **SoH Base Archive**: This must be the `oot.o2r` (or `oot-mq.o2r`) file located in your Ship of Harkinian directory.
2. **Rice Texture Folder**: The folder containing all your extracted high-res PNG textures.
3. **Output File**: Choose where to save your newly created `.o2r` mod file (e.g., `Desktop\texture_pack.o2r`).
4. Wait for it to scan and pack.
5. Drop your output file into the SoH `mods/` folder and enable it in the F1 Menu!

## Open Source
Built with help from python community tools and reverse-engineering of OTR resource files. Feel free to clone, edit, or utilize this for any game modding efforts.
