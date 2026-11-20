# WazeHUD production firmware - 2026-11-17

LCDWIKI ES3N28P / ESP32-S3 N16R8 / 2.8-inch ILI9341V LCD.
ESP-IDF 6.1. Mock mode disabled; BLE/HLP input enabled.

Includes all approved UI changes and reviewed lane-arrow rendering:
- Correct selected-path highlighting and mirrored diagonal heads.
- Full-length lowered horizontal turns, with no spur on standalone turns.
- U-turn head inset to stay inside its column; smaller head wings in 11-12 lane layouts.
- Every reported lane slot retained, in order, up to 12 lanes.
- Unknown directions shown as gray '?' rather than deleting a lane.
- Supported direction bits retained; selection masked to known allowed directions.
- Incomplete guidance produces a diagnostic warning when lane content changes.

Validation: all 728 valid masks at 12 lane counts (8,736 cases), all 65,536
byte-pair decoder inputs, malformed/capacity cases, and end-to-end count/order
checks for 1-12 lanes with unknown slots at every position. Complex arrows are
necessarily crowded at 11-12 lanes on this 320px display; physical readability
still needs user testing. The original disappearing-lane payload was unavailable,
so confirmed decoder reproductions do not prove the cause of every field report.

Merged image includes bootloader, partition table, OTA initialization, and app.
Flash at address 0x0. With esptool v5 installed, from this folder:

```sh
python -m esptool --chip esp32s3 --port /dev/cu.usbmodem3141201 --baud 460800 write-flash --flash-mode dio --flash-freq 80m --flash-size 16MB 0x0 WazeHUD-ES3N28P-production.bin
```

Replace the port as needed (for example COM3 on Windows).
If needed, hold BOOT and tap RESET to enter download mode.
A GUI flasher must use ESP32-S3 and offset 0x0 for this binary.

Flashing the merged image resets saved NVS settings. No separate erase is
required. Connect to BLE device WazeHUD after boot.

Each constituent image was byte-verified at its merged offset. Verify the
binary against SHA256SUMS.txt before use. The same build can be flashed with
idf.py flash to preserve current NVS settings.

Warning-distance font increased from 13 to 14 px (requested +10%, rounded
to the nearest whole pixel). Production image retains all reviewed lane fixes.
