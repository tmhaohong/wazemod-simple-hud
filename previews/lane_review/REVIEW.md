# Lane guidance review — 2026-11-17

## Fixed findings

1. Lane entries were dropped and later lanes shifted left when a direction was
   zero/unsupported or selection bits were inconsistent. The decoder now keeps
   each input position up to 12, masks to the six supported direction bits, and
   intersects selection with those directions. Unknown lanes use a gray `?`.
   Non-number, fractional, negative, non-finite, and malformed guidance cannot
   invent an arrow or highlight. Incomplete changed guidance is logged.
2. U-turn wings could cross the preceding lane boundary. Insetting the hook
   reserves space for the downward head and antialiasing inside the lane.
3. At 12 lanes a slight-right head could touch the separator. A smaller wing
   span is used for the 11–12-lane layouts.

The original missing-lane payload was unavailable. The decoder issue was
reproduced with four entries containing an unknown lane, and with two entries
containing an unsupported lane; this does not prove every field report shares
that cause. Firmware cannot restore lanes already omitted by the producer.

## Automated coverage

- All 728 valid nonempty direction/selection mask pairs at lane counts 1–12:
  8,736 rendering cases.
- No visible pixels cross the lane boundary or separator.
- Changing selection retains the same geometric support; selected cores stay
  green; unselected paths outside selected geometry are unchanged.
- No-selected lanes have no green highlighting. All 63 direction sets and all
  subset selections remain distinguishable at each tested lane width.
- Mirrored left/right shapes match. Antialiasing can differ by at most one
  RGB565 channel step; pixel support is identical.
- All 65,536 byte-mask pairs normalize to a retained lane slot. Unknown bits
  are stripped; unknown direction becomes a placeholder; selection is a subset.
- Malformed entries, empty/missing arrays, zero lanes, and capacity limiting.
- End-to-end real HLP decoder plus renderer: lane order/count preserved at
  counts 1–12, unknown at every possible index, old arrows cleared on redraw.
- Existing navigation, standalone/mixed directions, marquee, overspeed pulse,
  free-drive, footer and roundabout regressions pass.

`report.json` contains the exhaustive counters. `all_728_masks.png` shows all
normal-width cases, labeled `directionMask/selectedMask` in hex.
`representative_corrected.png` shows enlarged examples.
`../lane_count_preserved.png` shows four- and two-lane unknown-slot cases.

## Limits

A 320px screen necessarily crowds complex guidance at 11–12 lanes; automated
pixel containment is not a physical readability assessment. Decoder/renderer
checks cannot establish what the upstream navigation app actually sent.

Production build and flash passed with verified hashes. Serial logs confirm
LCD first frame, BLE advertising as WazeHUD, and stable 10-second boot.
Live navigation guidance has not been visually validated on the device.
