# WazeHUD production firmware

Board: LCDWIKI ES3N28P, ESP32-S3 N16R8, 2.8-inch ILI9341V LCD.
Built with ESP-IDF 6.1. Mock mode disabled; live BLE/HLP enabled.
Includes all approved UI changes as of 2026-11-15.

The .bin is a complete merged flash image, including bootloader, partition
table, OTA initialization, and production application. Flash at offset 0x0.

With esptool v5 installed, run from this folder:

```sh
python -m esptool --chip esp32s3 --port /dev/cu.usbmodem3141201 --baud 460800 write-flash --flash-mode dio --flash-freq 80m --flash-size 16MB 0x0 WazeHUD-ES3N28P-production.bin
```

Replace the port with the connected device's serial port (for example COM3 on
Windows). If needed, hold BOOT while tapping RESET to enter download mode.
For a GUI flasher, choose ESP32-S3, this binary, and address 0x0.

Flashing this merged image resets saved settings in NVS. No separate erase
step is required. After boot, connect to BLE device WazeHUD.

The merged image's constituent files were byte-verified against the production
build. The same production build was previously flashed and boot-verified on
the connected board. The merged package has not been separately flashed.

Verify the binary against SHA256SUMS.txt before flashing.
