# ES3N28P firmware with ESP-IDF 6.1

Board: LCDWIKI ES3N28P (no touch), ESP32-S3 N16R8, ILI9341V 240x320 SPI LCD.

Verified pin source: https://www.lcdwiki.com/2.8inch_ESP32-S3_Display
Panel tuning: https://www.lcdwiki.com/res/ES3C28P/ILI9341V_Init.txt

LCD: SCLK GPIO12, MOSI GPIO11, CS GPIO10, DC GPIO46, backlight GPIO45
(active high). LCD reset shares CHIP_PU, so the driver uses software reset.
MISO GPIO13 is unused for this write-only display path. SPI runs at 20 MHz.

The free-driving HUD uses the full 320x240 landscape panel: current speed
on the left, an enlarged speed-limit sign in the center, up to three warnings
on the right sorted by measured distance, and current street below. The nearest
warning uses its native Large asset; the remaining warnings use Small assets.
Unknown warning distances sort last. Missing speed limits display a question mark.
Navigation uses a full-screen summary, street/instruction strip, turn and
lane-guidance row, and trip footer. Utility screens retain their previous
centered 320x170 layout.
RGB565 pixel bytes are swapped for SPI transmission. Brightness, mirror and
rotation remain available through BLE device configuration.

The original T-Display GPIO14 button and GPIO4 battery ADC are disabled
for this board. Battery percentage is unavailable in this profile.

Activate the installed ESP-IDF 6.1 environment, then run from `waze-hud`:

```sh
idf.py -B build-es3n28p -D SDKCONFIG=sdkconfig.es3n28p \
  -D 'SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.es3n28p.defaults' build
idf.py -B build-es3n28p -p /dev/cu.usbmodem3141201 flash
idf.py -B build-es3n28p -p /dev/cu.usbmodem3141201 monitor
```

Use the current USB serial port if it changes. The application BIN alone
belongs at offset 0x20000; `idf.py flash` handles all image offsets.
Production BLE firmware is the default; mock mode is disabled.

IDF 6.1 migration: cJSON is now a managed dependency; the original i80
driver also uses the current RGB element order and GPIO enum APIs.

## Validation on 2026-11-15

- Built with installed ESP-IDF tag `v6.1`, target `esp32s3`.
- Flashed over `/dev/cu.usbmodem3141201`; esptool verified written hashes.
- Device detected: ESP32-S3 rev v0.2, flash 16 MB, PSRAM 8 MB.
- Boot log confirms PSRAM memory test passed, ILI9341 initialized, first UI
  frame rendered, BLE advertising as `WazeHUD`, and a stable 10-second boot.
- A phone connected and established an HLP session with Waze 5.24.5.0
  during the first hardware check. Final boot check passed after correcting
  the startup GPIO-to-LEDC backlight handoff warning.
- Existing display-startup, minimum-speed and alert-rendering source guards pass.
- Physical LCD appearance, orientation and colors require visual confirmation.
- Existing NVS configuration was retained. A pre-existing coredump remains
  in flash; it is not evidence of a crash in this firmware.

Logs: `build-es3n28p-flash.log`, `build-es3n28p-boot.log`.
Final app SHA-256:
`b4a546ed049c238905d26eea77e5b7775cd207dc8715e311e94adbf31d42f252`

## Free-driving UI update

Run `python3 tools/check_free_driving.py` to exercise the actual C++ renderer
on the host. It verifies sorted-warning pixels, native Large/Small assets,
street-only and upcoming-alert-only redraws, and missing-data states.
The generated preview is `../previews/free_driving.png`; example data is
48 km/h, a 50 km/h limit, warnings at 50/200/350 m, and street Market.
Firmware flashed for hardware testing remains production mode, not mock mode.

Full-screen frame buffer is allocated in PSRAM; SPI transfers continue to use
the internal DMA stripe buffer. Final UI update flash passed hash verification;
boot log confirms full-screen LCD initialization and first-frame rendering.
UI update logs: `build-free-driving-flash.log`, `build-free-driving-boot.log`.

## Navigation UI update

When `nav` is active, the 320x240 screen shows speed/limit/warnings above
the current street (`st`) and a Vietnamese turn instruction derived from
`trn` and next street (`st2`). The next row uses embedded maneuver assets
for the next turn, its distance (`dst`), and optional lanes (`lan`).
Lane arrows reuse the existing embedded masks, with selected directions
in cyan and other directions muted. No external image assets are needed.
The footer shows `rmin`, `rm` (or legacy `rkm`), and producer ETA (`eta`).
The existing free-driving screen remains selected when navigation is inactive.

`tools/check_free_driving.py` also validates navigation-only field redraws,
missing/maximum lane counts, and mode switching. It generates
`../previews/navigation.png` from the actual C++ renderer using sample data.
Navigation update logs: `build-navigation-flash.log`, `build-navigation-boot.log`.

Lane guidance supports only flags 0x01 through 0x20 (combined mask 0x3F).
Each lane pair carries allowed directions and the selected subset. The
preview now uses `{0x05,0x04}`, `{0x01,0x01}`, `{0x30,0x10}`. Tests
check each of the six selected flags against the embedded arrow pixels.
Lane update logs: `build-lane-mask-flash.log`, `build-lane-mask-boot.log`.

## ES3N28P mock UI

Build and flash with the board defaults before the final mock override:

```sh
idf.py -B build-mock -D SDKCONFIG=sdkconfig.mock \
  -D 'SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.es3n28p.defaults;sdkconfig.mock.defaults' build
idf.py -B build-mock -p /dev/cu.usbmodem3141201 flash
```

The target is `esp32s3`, not `esp32`. Mock input replaces BLE/HLP input.
Each case lasts 5 seconds, except long-text cases 7 and 18 (12 seconds each).
The 20-case cycle repeats after 114 seconds:

| Log case | Scenario |
| --- | --- |
| 0 | Navigation U-turn and three mixed-direction lanes |
| 1 | Speed camera and police warnings |
| 2 | Police, hazard, and speed-drop warnings |
| 3 | Active no-passing zone |
| 4 | Roundabout, exit 3, 1.2 km to turn |
| 5 | Overspeed and sharp-left turn |
| 6 | Waiting/disconnected screen |
| 7 | Long current street and right exit |
| 8 | Roundabout exit 2 and phone camera |
| 9 | Railway, flood, and school zone |
| 10 | Traffic jams with different severities |
| 11 | Free driving: 48 km/h, limit 50, unsorted 350/50/200 m warnings |
| 12 | Free driving, stopped, no warnings |
| 13 | Free driving, unknown speed limit |
| 14 | Navigation right turn, 150 m, no lanes |
| 15 | Arrival, zero remaining distance/time |
| 16 | Six selected lane flags, 0x01 through 0x20 |
| 17 | Unknown/zero warning distances and unknown turn distance |
| 18 | Long next-street instruction, long trip, missing ETA |
| 19 | Free driving, three-digit overspeed and three warnings |

Return to live BLE firmware with:

```sh
idf.py -B build-es3n28p -p /dev/cu.usbmodem3141201 flash
```

Mock build/flash/boot logs: `build-mock.log`, `build-mock-flash.log`,
`build-mock-boot.log`.

Mock firmware was flashed with verified hashes and observed completing
scenarios 0 through 19, then restarting at 0, with no reported display
failure, panic, or reset during the full cycle. Mock mode was left running.

## UI refinements after mock testing

- Roundabout exit numbers are anchored to each source bitmap's ring center,
  rather than the complete icon's center. Host pixel checks cover all five
  roundabout assets.
- The free-driving street panel extends to the last screen row, removing
  the unused black strip.
- The footer uses an ASCII hyphen between remaining time and distance.
  The previous middle-dot character was absent from the embedded font and
  rendered as a question mark.
- Lane-arrow asset lookup/rendering is restored to the first navigation
  implementation; the main maneuver arrow is unchanged. The six supported
  HLP direction bits and selected-direction colors remain intact.
- Host checks verify footer pixels, bottom-panel coverage, and exit-number
  alignment. Preview: `../previews/roundabout.png`.
- Mock flash/boot logs: `build-ui-refinements-flash.log`,
  `build-ui-refinements-boot.log`.

## Barlow Condensed, marquee, and reference lanes

All embedded font families now use Barlow Condensed: SemiBold for text,
Bold for numbers, including Vietnamese glyphs. Source fonts and the OFL
license are checked in under `main/assets/fonts/`, downloaded from
https://github.com/google/fonts/tree/main/ofl/barlowcondensed.
Regenerate only fonts (preserving existing image assets) with:

```sh
python3 tools/generate_embedded_assets.py --fonts-only
```

Street and navigation-instruction text scroll independently when wider than
304 pixels. They pause for 1.2 seconds, scroll at one pixel per 45 ms, hold
the end for 0.9 seconds, and repeat. Changing text resets its scroll position.
Short text stays still. Pixels are clipped to the text viewport.

The old procedural lane style is restored with the reference flag mapping:
0x01 straight, 0x02 slight left, 0x04 left, 0x08 U-turn, 0x10 slight right,
0x20 right. Selected branches are cyan, other branches muted. The main
next-turn arrow continues to use its existing bitmap assets.

Host rendering tests cover marquee pause/advance/reset/clipping, six distinct
lane directions, and existing layout regressions. Updated mock logs:
`build-font-marquee-flash.log`, `build-font-marquee-boot.log`.

The final mock build was flashed with ESP-IDF 6.1 and verified write hashes.
Serial monitoring confirmed LCD initialization, the first frame, and continued
execution through scenario 11, including the full 12-second long-name scenario
7, without panic or display timeout. On-device appearance remains for user
testing.

UI refinement: current speed uses Barlow Condensed Regular at 50 px (previously
Bold at 62 px); limit-sign red ring increases from 6 to 8 raster pixels
(40% requested increase rounded to whole pixels). Warning panels have no
vertical or row dividers. Free-drive
street text uses SemiBold 26 px, centered horizontally and vertically when it
fits; overflowing names retain the clipped marquee. Host rendering checks
verify the empty divider and centered street alongside existing regressions.

Navigation instructions use a dedicated SemiBold 15 px font (previously 10 px),
with their origin moved up four pixels to fit the existing street section.
Long instructions retain their marquee. Lane arrows have no underline dashes.

Warning distances now use 13 px text (+30%), footer text uses 12 px (+20%),
and roundabout exit numerals use 14 px (12 px increased 20%, rounded).
Next-turn distance is white. Lane arrows are vertically centered in the
direction section with muted gray dashed vertical separators between lanes.
Roundabout mock scenarios 3/8/4 show exit 1 left / 2 straight / 3 right with
matching lane guidance. Previously roundabout scenarios inherited the shared
mock's selected left lane even when the maneuver icon pointed right; the
renderer continues to honor the independently supplied maneuver and exit
number because real roundabout geometry need not follow that mock mapping.

Lane strokes use the same 2.4 px antialiased width for selected and unselected
branches. The earlier 1.2 px inactive stroke kept a single-pixel core with only
faint edge coverage, so it still looked too thin. Selected lanes are green (RGB 66/220/112);
unselected branches and dashed lane separators retain their gray colors.

After UI approval, rebuilt and flashed `build-es3n28p` with mock mode disabled,
including all final UI changes. Verified flash hashes, BLE advertising as
`WazeHUD`, LCD first frame, and stable 10-second boot. The device now runs live
BLE/HLP firmware. Logs: `build-production-final-flash.log` and
`build-production-final-boot.log`.

2026-11-16 mock iteration: current-speed Regular font increased from 50 to
60 px (+20%). Lane geometry, including arrowhead height, is stretched vertically
20%, and separator height grows from 40 to 48 px. The taller lane group remains
centered in the direction row; stroke width stays 2.4 px. Mock flash/boot logs:
`build-taller-lanes-flash.log`, `build-taller-lanes-boot.log`.
The exported 2026-11-15 production package remains the prior approved version.

Lane group moved down 6 px, including separators, to reduce the gap above the
footer. Overspeed now animates a red perimeter (3 px border plus 5 px inner
glow) on a smooth 1.4-second cycle. It follows the existing speed-limit plus
configured offset threshold and stops immediately below it, without a limit,
on disconnect, or on the utility screen. Animation drives redraws even when
input is unchanged. Host checks verify both driving modes, unchanged center
content during the pulse, and threshold/stop behavior. Mock logs:
`build-overspeed-pulse-flash.log`, `build-overspeed-pulse-boot.log`.

Fixed slight-left/right lane arrowheads: diagonal vectors previously entered
the horizontal/vertical head branches, producing sideways heads. Diagonal
heads now derive their wings from the shaft direction and perpendicular,
using the same vertical stretch as the shaft. Cardinal arrows retain their
geometry. `previews/lane_reference.png` renders the user's comparison pattern
(slight-left + inactive straight, straight, slight-right + inactive straight).
Host tests check both wings and absence of the old above-tip wing pixels.

Horizontal left/right lane branches (0x04/0x20) retain their original full
width (12 px at the usual lane width). Their horizontal arms and heads are
lowered 8 raster pixels to expose other branches above. Arrowhead size and all
other lane directions retain their current geometry.

2026-11-16 production release: created merged binary and ZIP under
`firmware/WazeHUD-ES3N28P-production-2026-11-16/` and verified all constituent
images at their merged offsets. Flashed the same production build with
`idf.py flash` (mock disabled), verified write hashes, LCD first frame, BLE
advertising as `WazeHUD`, and stable 10-second boot. Logs:
`build-production-20261116.log`, `build-production-20261116-flash.log`,
`build-production-20261116-boot.log`. Merged image flashes at 0x0; the included
README documents the command and NVS reset behavior of the merged image.

Standalone 0x04/0x20 shape correction: shared stem now stops at the lowered
elbow for a lone horizontal turn, removing the upward spur. Mixed lanes retain
their shared stem and other branches. Both mirrored cases pass pixel checks.
Rebuilt/flashed production and refreshed the dated 2026-11-16 BIN, ZIP, and
SHA256SUMS together. Logs: `build-standalone-turns.log`,
`build-standalone-turns-flash.log`, `build-standalone-turns-boot.log`.

2026-11-17 mixed-lane validation: `[[5,4],[33,0]]` decodes as selected left
plus inactive straight in lane 1, and inactive straight/right in lane 2.
The shared stem used to be colored green up to the common junction above the
lowered horizontal elbow, bleeding into the inactive straight branch. Each
branch now paints its complete stem/path, inactive first and selected last,
so green stops at the selected path's junction. No changes to bit meanings.
The host harness runs the real HLP decoder and renderer for this exact case
and validates mirrored, straight-only, none/both-selected, and redraw cases.
Preview: `previews/two_lane_masks_corrected.png`.
Production build and merge passed; firmware is under
`firmware/WazeHUD-ES3N28P-production-2026-11-17/`, with matching ZIP/checksum.
Build log: `build-lane-mask-validation.log`. No serial port was detected, so
this update has not been flashed or verified on the physical device.

The subsequent exhaustive review supersedes that validation snapshot:
`tools/check_lane_arrows.py` covers all 728 supported direction/selection pairs
at counts 1–12 (8,736 render cases), all 65,536 byte-mask decoder pairs, malformed
and capacity cases. It also checks mirror geometry, unique selection states,
highlight containment, and arrow/separator boundaries. Found/fixed U-turn head
overflow and narrow-column slight-right overflow. The real decoder/renderer
regression harness verifies unknown slots at every position in counts 1–12.

Lane-count correction: every array entry now retains its slot, including zero,
unsupported or malformed direction guidance. Supported direction bits survive;
selected bits are intersected with allowed known directions. A gray `?` marks
unknown direction. Changed incomplete guidance emits a warning for diagnosis.
This fixes reproduced four-to-three and two-to-one cases, but the original field
payload was unavailable, and firmware cannot restore lanes omitted upstream.
Review evidence: `previews/lane_review/REVIEW.md`, `report.json`, and contact sheets.
Updated production BIN/ZIP/checksum under the existing 2026-11-17 release folder.
Build/flash/boot logs: `build-lane-count-review.log`,
`build-lane-count-review-flash.log`, `build-lane-count-review-boot.log`.

This reviewed production build was flashed with verified hashes. Serial logs
confirmed LCD first frame, BLE advertising as WazeHUD, and stable 10-second boot.
Live navigation guidance still requires user testing.

Warning-distance text increased from 13 to 14 px for the requested additional
10% increase (rounded to the nearest integer pixel). Rendering regression
checks pass. Updated production BIN/ZIP/checksum in the 2026-11-17 folder.
Logs: `build-warning-distance.log`, `build-warning-distance-flash.log`,
`build-warning-distance-boot.log`.
