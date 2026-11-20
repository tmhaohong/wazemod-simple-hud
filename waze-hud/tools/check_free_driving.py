"""Render real firmware UI on the host; check ordering and update behavior."""
from pathlib import Path
import subprocess
import tempfile
import struct
import zlib

project = Path(__file__).resolve().parents[1]
output = project.parent / 'previews' / 'free_driving.png'
with tempfile.TemporaryDirectory(prefix='wazehud-ui-') as directory:
    tmp = Path(directory)
    (tmp / 'config').mkdir()
    config = (project / 'main/config/device_config.h').read_text()
    config = config[config.index('enum class UiTheme'):config.index('using HlpSendLine')]
    (tmp / 'config/device_config.h').write_text('#pragma once\n#include <cstdint>\nnamespace waze_hud {\n' + config + '}\n')
    (tmp / 'sdkconfig.h').write_text('#define CONFIG_WAZE_HUD_LCD_ILI9341 1\n')
    (tmp / 'esp_timer.h').write_text('#pragma once\n#include <cstdint>\nextern int64_t testTimeUs;\ninline int64_t esp_timer_get_time() { return testTimeUs; }\n')
    (tmp / 'esp_err.h').write_text('#pragma once\nusing esp_err_t = int;\n#define ESP_OK 0\n#define ESP_ERR_NO_MEM 1\n')
    (tmp / 'esp_log.h').write_text('#pragma once\n#define ESP_LOGE(...) ((void)0)\n#define ESP_LOGW(...) ((void)0)\n#define ESP_LOGI(...) ((void)0)\n#define ESP_LOGD(...) ((void)0)\n')
    (tmp / 'esp_heap_caps.h').write_text('#pragma once\n#include <cstdlib>\n#define MALLOC_CAP_SPIRAM 0\n#define MALLOC_CAP_8BIT 0\n#define MALLOC_CAP_DMA 0\n#define MALLOC_CAP_INTERNAL 0\ninline void *heap_caps_malloc(size_t n, int) { return malloc(n); }\n')
    (tmp / 'check.cpp').write_text(r'''
#include "display/hud_renderer.h"
#include "display/display_driver.h"
#include "display/colors.h"
#include "protocol/hlp_decoder.h"
#include <cassert>
#include <algorithm>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <vector>
using namespace waze_hud;
std::vector<uint16_t> frame;
int draws = 0;
int64_t testTimeUs = 0;
DisplayDriver &DisplayDriver::instance() { static DisplayDriver d; return d; }
esp_err_t DisplayDriver::setBrightness(uint8_t) { return ESP_OK; }
esp_err_t DisplayDriver::setOrientation(bool, bool) { return ESP_OK; }
esp_err_t DisplayDriver::drawRegion(const Rect &, const uint16_t *p) {
    frame.assign(p, p + layout::MaxRegionPixels); ++draws; return ESP_OK;
}
int main(int argc, char **argv) {
    HudRenderer renderer;
    assert(renderer.init() == ESP_OK);
    DeviceSettings settings;
    SystemStatusSnapshot status;
    HudState s;
    s.connected = s.hasProducerState = true;
    s.speedKmh = 48; s.speedLimitKmh = 50;
    std::strcpy(s.currentStreet.data(), "Market");
    s.nearestAlert = {AlertKind::Police, 350};
    s.upcomingAlertCount = 2;
    s.upcomingAlerts[0] = {AlertKind::SpeedCamera, 50};
    s.upcomingAlerts[1] = {AlertKind::Hazard, 200};
    renderer.render(s, settings, status);
    assert(frame[239 * 320 + 319] == colors::rgb565(10, 23, 27));
    const auto expected = frame;
    // Reordering input must produce the same distance-sorted pixels.
    s.nearestAlert = {AlertKind::SpeedCamera, 50};
    s.upcomingAlerts[0] = {AlertKind::Hazard, 200};
    s.upcomingAlerts[1] = {AlertKind::Police, 350};
    renderer.render(s, settings, status);
    assert(frame == expected);
    // Large and Small assets must be copied at their native sizes into each row.
    auto checkIcon = [&](const assets::ColorBitmap &icon, int row) {
        std::vector<uint16_t> iconPixels(layout::MaxRegionPixels, 0);
        Canvas expectedIcon(iconPixels.data(), layout::Width, layout::Height);
        const int x = 228 + (44 - icon.width) / 2;
        const int y = 10 + row * 50 + (50 - icon.height) / 2;
        expectedIcon.colorBitmap(x, y, icon);
        for (int iy = 0; iy < icon.height; ++iy)
            for (int ix = 0; ix < icon.width; ++ix)
                assert(frame[(y + iy) * 320 + x + ix] == iconPixels[(y + iy) * 320 + x + ix]);
    };
    checkIcon(assets::kAlertSpeedCameraLarge, 0);
    checkIcon(assets::kAlertHazardSmall, 1);
    checkIcon(assets::kAlertPoliceSmall, 2);
    auto dump = [&](const char *path) {
    FILE *file = std::fopen(path, "wb"); assert(file);
    std::fprintf(file, "P6\n320 240\n255\n");
    for (uint16_t p : frame) {
        unsigned char rgb[] = {static_cast<unsigned char>(((p >> 11) & 31) * 255 / 31),
                               static_cast<unsigned char>(((p >> 5) & 63) * 255 / 63),
                               static_cast<unsigned char>((p & 31) * 255 / 31)};
        std::fwrite(rgb, 1, 3, file);
    }
    std::fclose(file);
    };
    dump(argv[1]);
    int n = draws;
    renderer.render(s, settings, status); assert(draws == n);
    std::strcpy(s.currentStreet.data(), "Đường Nguyễn Thị Minh Khai");
    renderer.render(s, settings, status); assert(draws == ++n); assert(frame != expected);
    s.upcomingAlerts[1].distanceM = 100;
    renderer.render(s, settings, status); assert(draws == ++n);
    // Unknown distances follow known ones; no phantom or duplicate warnings.
    s.nearestAlert.distanceM = -1;
    renderer.render(s, settings, status);
    s.upcomingAlertCount = 0; s.nearestAlert = {};
    s.speedLimitKmh = 0; s.speedKmh = 128;
    renderer.render(s, settings, status);
    const auto divider = colors::rgb565(43, 61, 66);
    for (int y = 22; y < 152; ++y) assert(frame[y * 320 + 224] != divider);
    // Short street names are centered in the full free-drive street panel.
    std::strcpy(s.currentStreet.data(), "Market");
    renderer.render(s, settings, status);
    int streetLeft = 320, streetRight = 0, streetTop = 240, streetBottom = 0;
    for (int y = 171; y < 240; ++y)
        for (int x = 8; x < 312; ++x)
            if (frame[y * 320 + x] == colors::Foreground) {
                streetLeft = std::min(streetLeft, x); streetRight = std::max(streetRight, x);
                streetTop = std::min(streetTop, y); streetBottom = std::max(streetBottom, y);
            }
    assert(std::abs((streetLeft + streetRight) / 2 - 160) <= 2);
    assert(std::abs((streetTop + streetBottom) / 2 - 205) <= 3);
    std::puts("PASS: empty warning divider hidden; enlarged free-drive street centered");
    s.navigationActive = true;
    s.speedKmh = 48; s.speedLimitKmh = 50;
    std::strcpy(s.currentStreet.data(), "Market");
    std::strcpy(s.nextStreet.data(), "đường số 8");
    std::strcpy(s.eta.data(), "09:49");
    s.maneuver = Maneuver::Right; s.maneuverDistanceM = 150;
    s.remainingMinutes = 8; s.remainingMeters = 2300;
    s.nearestAlert = {AlertKind::SpeedCamera, 50};
    s.upcomingAlertCount = 2;
    s.upcomingAlerts[0] = {AlertKind::Hazard, 200};
    s.upcomingAlerts[1] = {AlertKind::Police, 350};
    s.laneCount = 3;
    s.lanes[0] = {0x05, 0x04};
    s.lanes[1] = {0x01, 0x01};
    s.lanes[2] = {0x30, 0x10};
    renderer.render(s, settings, status);
    dump(argv[2]);
    // Lane-only screenshot reference: slight-left + inactive straight,
    // straight, slight-right + inactive straight.
    auto referenceState = s;
    s.lanes[0] = {0x03, 0x02};
    s.lanes[1] = {0x01, 0x01};
    s.lanes[2] = {0x11, 0x10};
    renderer.render(s, settings, status);
    dump(argv[4]);
    const auto greenHead = colors::rgb565(66, 220, 112);
    // Both diagonal heads have wings below the tip, on opposite sides of
    // their shaft, rather than a sideways chevron with a wing above the tip.
    assert(frame[184 * 320 + 199] == greenHead);
    assert(frame[178 * 320 + 203] == greenHead);
    assert(frame[184 * 320 + 281] == greenHead);
    assert(frame[178 * 320 + 277] == greenHead);
    assert(frame[174 * 320 + 201] == colors::Black);
    assert(frame[174 * 320 + 279] == colors::Black);
    std::puts("PASS: mirrored diagonal lane heads follow their shafts, without sideways wings above the tips");
    s = referenceState;
    renderer.render(s, settings, status);
    for (int x : {225, 255}) {
        assert(frame[170 * 320 + x] == colors::Muted);
        assert(frame[174 * 320 + x] != colors::Muted);
    }
    for (int x = 166; x < 316; ++x)
        assert(frame[218 * 320 + x] != colors::rgb565(83, 224, 235));
    std::vector<uint16_t> expectedDistance(layout::MaxRegionPixels, 0);
    Canvas distanceCanvas(expectedDistance.data(), 320, 240);
    distanceCanvas.fontText(72, 176, "150 m", assets::kTextMedium, colors::White, 84, false);
    for (int y = 176; y < 198; ++y)
        for (int x = 72; x < 156; ++x)
            assert(frame[y * 320 + x] == expectedDistance[y * 320 + x]);
    std::puts("PASS: dashed lane separators, no underlines, white next-turn distance");
    std::vector<uint16_t> footer(layout::MaxRegionPixels, 0);
    Canvas footerCanvas(footer.data(), 320, 240);
    footerCanvas.fontText(8, 220, "8 phút - 2.3 km", assets::kFooterText, colors::Foreground, 202, false);
    for (int y = 220; y < 240; ++y)
        for (int x = 8; x < 210; ++x)
            assert(frame[y * 320 + x] == footer[y * 320 + x]);
    auto nav = frame;
    n = draws;
    s.lanes[0].selectedMask = 0x01;
    renderer.render(s, settings, status); assert(draws == ++n); assert(frame != nav);
    s.remainingMinutes = 7;
    renderer.render(s, settings, status); assert(draws == ++n);
    s.remainingMeters = 2200;
    renderer.render(s, settings, status); assert(draws == ++n);
    std::strcpy(s.eta.data(), "09:50");
    renderer.render(s, settings, status); assert(draws == ++n);
    std::strcpy(s.nextStreet.data(), "Đường Nguyễn Huệ");
    renderer.render(s, settings, status); assert(draws == ++n);
    s.laneCount = 0;
    renderer.render(s, settings, status); assert(draws == ++n);
    s.laneCount = 12;
    renderer.render(s, settings, status);
    // All six directions must be distinct; 0x08 specifically bends down for U-turn.
    std::vector<std::vector<uint16_t>> laneFrames;
    for (unsigned bit = 0; bit < 6; ++bit) {
        s.laneCount = 1;
        s.lanes[0] = {static_cast<uint8_t>(1U << bit), static_cast<uint8_t>(1U << bit)};
        renderer.render(s, settings, status);
        for (const auto &prior : laneFrames) assert(frame != prior);
        laneFrames.push_back(frame);
    }
    const auto green = colors::rgb565(66, 220, 112);
    assert(laneFrames[3][197 * 320 + 230] == green); // down-pointing U-turn end, inset from separator
    assert(laneFrames[2][194 * 320 + 228] == green); // full-length, lowered horizontal left end
    assert(laneFrames[5][194 * 320 + 252] == green); // full-length, lowered horizontal right end
    assert(laneFrames[5][190 * 320 + 240] == colors::Black); // no stem above standalone right elbow
    assert(laneFrames[2][190 * 320 + 240] == colors::Black); // symmetric standalone left
    dump(argv[5]); // current frame is standalone 0x20
    HlpDecoder decoder;
    HudState decodedLanes;
    auto *json = cJSON_Parse(R"({"ts":1,"nav":1,"lan":[[5,4],[33,0]]})");
    assert(json && decoder.decodeState(json, decodedLanes));
    cJSON_Delete(json);
    assert(decodedLanes.laneCount == 2);
    assert(decodedLanes.lanes[0].directionMask == 0x05 && decodedLanes.lanes[0].selectedMask == 0x04);
    assert(decodedLanes.lanes[1].directionMask == 0x21 && decodedLanes.lanes[1].selectedMask == 0x00);
    s.laneCount = decodedLanes.laneCount;
    s.lanes = decodedLanes.lanes;
    renderer.render(s, settings, status);
    dump(argv[6]);
    const auto twoLaneCase = frame;
    assert(frame[190 * 320 + 225] == colors::Muted); // straight above left elbow stays gray
    assert(frame[178 * 320 + 225] == colors::Muted); // straight arrowhead gray
    assert(frame[194 * 320 + 213] == green); // selected left tip
    assert(frame[206 * 320 + 225] == green); // selected shared lower stem
    assert(frame[178 * 320 + 255] == colors::Muted); // second lane straight gray
    assert(frame[194 * 320 + 267] == colors::Muted); // second lane right gray
    assert(frame[206 * 320 + 255] == colors::Muted); // second lane lower stem gray
    // Mirror the route selection: only the right branch is selected.
    s.lanes[0] = {0x05, 0x00};
    s.lanes[1] = {0x21, 0x20};
    renderer.render(s, settings, status);
    assert(frame[190 * 320 + 255] == colors::Muted);
    assert(frame[194 * 320 + 267] == green);
    assert(frame[206 * 320 + 255] == green);
    // Selecting straight leaves the horizontal branch unselected.
    s.lanes[0] = {0x05, 0x01};
    s.lanes[1] = {0x21, 0x01};
    renderer.render(s, settings, status);
    assert(frame[178 * 320 + 225] == green);
    assert(frame[194 * 320 + 213] == colors::Muted);
    assert(frame[178 * 320 + 255] == green);
    assert(frame[194 * 320 + 267] == colors::Muted);
    // Zero selectedMask never highlights the lane; selecting both highlights both.
    for (const uint8_t selected : {0x00, 0x05}) {
        s.lanes[0] = {0x05, selected};
        s.lanes[1] = {0x21, static_cast<uint8_t>(selected ? 0x21 : 0x00)};
        renderer.render(s, settings, status);
        const auto color = selected ? green : colors::Muted;
        assert(frame[178 * 320 + 225] == color);
        assert(frame[194 * 320 + 213] == color);
        assert(frame[178 * 320 + 255] == color);
        assert(frame[194 * 320 + 267] == color);
    }
    // Restoring the same masks must reproduce the identical frame.
    s.lanes[0] = {0x05, 0x04}; s.lanes[1] = {0x21, 0x00};
    renderer.render(s, settings, status); assert(frame == twoLaneCase);
    std::puts("PASS: exact two-lane masks, mirrored selection, straight-only, none/both selected, and selection redraws");
    const auto savedLanes = s;
    for (int count = 1; count <= 12; ++count) {
        for (int hole = -1; hole < count; ++hole) {
            decoder.resetSession();
            auto *root = cJSON_CreateObject();
            auto *lanes = cJSON_AddArrayToObject(root, "lan");
            for (int i = 0; i < count; ++i) {
                auto *entry = cJSON_CreateArray();
                cJSON_AddItemToArray(entry, cJSON_CreateNumber(i == hole ? 0 : (1U << (i % 6))));
                cJSON_AddItemToArray(entry, cJSON_CreateNumber(0));
                cJSON_AddItemToArray(lanes, entry);
            }
            assert(decoder.decodeState(root, decodedLanes)); cJSON_Delete(root);
            assert(decodedLanes.laneCount == count);
            for (int i = 0; i < count; ++i)
                assert(decodedLanes.lanes[i].directionMask == (i == hole ? 0 : (1U << (i % 6))));
            s.laneCount = decodedLanes.laneCount; s.lanes = decodedLanes.lanes;
            renderer.render(s, settings, status);
            if (hole >= 0) {
                const int width = std::min(30, 152 / count);
                const int left = 164 + (152 - width * count) / 2 + hole * width;
                const int center = left + width / 2;
                std::vector<uint16_t> placeholder(layout::MaxRegionPixels, 0);
                Canvas expected(placeholder.data(), 320, 240);
                expected.fontText(center - 5, 184, "?", assets::kTextSmall, colors::Muted, 10, true);
                bool visible = false;
                for (int y = 172; y < 218; ++y)
                    for (int x = left + 1; x < left + width; ++x) {
                        assert(frame[y * 320 + x] == placeholder[y * 320 + x]);
                        visible |= frame[y * 320 + x] != 0;
                    }
                assert(visible);
                if (count == 4 && hole == 1) dump(argv[7]);
                if (count == 2 && hole == 0) dump(argv[8]);
            }
        }
    }
    s = savedLanes; renderer.render(s, settings, status);
    std::puts("PASS: 1-12 lanes retain count/order, unknown slot at every position stays visible, no stale arrows");
    std::puts("PASS: six distinct procedural lane arrows including 0x08 U-turn");
    struct RoundaboutCase { Maneuver maneuver; int x; int y; };
    const RoundaboutCase roundabouts[] = {
        {Maneuver::Roundabout, 30, 30}, {Maneuver::RoundaboutLeft, 40, 23},
        {Maneuver::RoundaboutRight, 20, 23}, {Maneuver::RoundaboutStraight, 29, 32},
        {Maneuver::RoundaboutUTurn, 40, 22}
    };
    for (const auto &item : roundabouts) {
        s.maneuver = item.maneuver; s.roundaboutExit = 3;
        renderer.render(s, settings, status);
        int left = 64, right = 0, top = 214, bottom = 0;
        for (int y = 165; y < 213; ++y)
            for (int x = 16; x < 64; ++x)
                if (frame[y * 320 + x] == colors::White) {
                    left = std::min(left, x); right = std::max(right, x);
                    top = std::min(top, y); bottom = std::max(bottom, y);
                }
        assert(right >= left && bottom >= top);
        assert(std::abs((left + right) / 2 - (16 + item.x * 48 / 60)) <= 1);
        assert(std::abs((top + bottom) / 2 - (165 + item.y * 48 / 60)) <= 1);
        if (item.maneuver == Maneuver::RoundaboutRight) dump(argv[3]);
    }
    std::puts("PASS: free-drive bottom filled, footer hyphen, exit number centered in five roundabout assets");
    s.navigationActive = false;
    std::strcpy(s.currentStreet.data(), "Đường Nguyễn Thị Minh Khai - Phường Bến Thành - Thành phố Hồ Chí Minh");
    renderer.render(s, settings, status);
    assert(renderer.animationActive());
    const auto held = frame;
    testTimeUs += 800000;
    renderer.render(s, settings, status);
    assert(frame == held); // initial pause
    testTimeUs += 1400000;
    renderer.render(s, settings, status);
    assert(frame != held);
    for (int y = 175; y < 197; ++y) {
        for (int x = 0; x < 8; ++x) assert(frame[y * 320 + x] == held[y * 320 + x]);
        for (int x = 312; x < 320; ++x) assert(frame[y * 320 + x] == held[y * 320 + x]);
    }
    const auto scrolled = frame;
    std::strcpy(s.currentStreet.data(), "Market");
    renderer.render(s, settings, status);
    assert(!renderer.animationActive());
    assert(frame != scrolled);
    std::puts("PASS: long Vietnamese street scrolls after hold, clips to viewport, stops for short text");
    for (bool navigation : {false, true}) {
        s.navigationActive = navigation;
        s.nextStreet = {}; s.maneuver = Maneuver::Right;
        s.speedLimitKmh = 50; s.speedKmh = 51;
        settings.overspeedOffsetKmh = 0;
        renderer.render(s, settings, status);
        assert(renderer.animationActive());
        const auto brightBorder = frame;
        const int brightRed = (frame[0] >> 11) & 31;
        testTimeUs += 700000;
        renderer.render(s, settings, status);
        assert(((frame[0] >> 11) & 31) < brightRed);
        // The center of the UI is untouched by pulse animation.
        for (int y = 8; y < 232; ++y)
            for (int x = 8; x < 312; ++x)
                assert(frame[y * 320 + x] == brightBorder[y * 320 + x]);
        s.speedKmh = 50;
        renderer.render(s, settings, status);
        assert(!renderer.animationActive()); assert(frame[0] == colors::Black);
        s.speedKmh = 51; settings.overspeedOffsetKmh = 5;
        renderer.render(s, settings, status);
        assert(!renderer.animationActive());
        settings.overspeedOffsetKmh = 0; s.speedLimitKmh = 0;
        renderer.render(s, settings, status);
        assert(!renderer.animationActive());
    }
    s.speedLimitKmh = 50; s.speedKmh = 60; s.connected = false;
    renderer.render(s, settings, status); assert(!renderer.animationActive());
    s.connected = true; status.visible = true;
    renderer.render(s, settings, status); assert(!renderer.animationActive());
    status.visible = false; s.speedKmh = 48;
    std::puts("PASS: overspeed border pulses in both modes, preserves content, honors threshold and stops immediately");
    s.navigationActive = false;
    renderer.render(s, settings, status);
    std::puts("PASS: navigation text, lanes/ETA/remaining-only redraws, no lanes, 12 lanes and mode switching");
    std::puts("PASS: distance ordering, native Large/Small icons, street/alert redraws, unknown/no-alert states");
}
''')
    cjson = project / 'managed_components/espressif__cjson/cJSON'
    for source, obj in ((cjson / 'cJSON.c', tmp / 'cjson.o'),
                        (project / 'main/protocol/hlp_core.c', tmp / 'hlp_core.o')):
        subprocess.run(['cc', '-std=c99', '-I' + str(cjson), '-I' + str(project / 'main'),
                        '-c', str(source), '-o', str(obj)], check=True)
    subprocess.run(['c++', '-std=c++17', '-I' + str(tmp), '-I' + str(project / 'main'), '-I' + str(cjson),
                    str(tmp / 'check.cpp'), str(project / 'main/display/hud_renderer.cpp'),
                    str(project / 'main/display/bitmap_font.cpp'), str(project / 'main/assets/generated_assets.cpp'),
                    str(project / 'main/protocol/hlp_decoder.cpp'), str(tmp / 'cjson.o'), str(tmp / 'hlp_core.o'),
                    '-o', str(tmp / 'check')], check=True)
    subprocess.run([str(tmp / 'check'), str(tmp / 'frame.ppm'), str(tmp / 'navigation.ppm'), str(tmp / 'roundabout.ppm'), str(tmp / 'lane_reference.ppm'), str(tmp / 'right_lane.ppm'), str(tmp / 'two_lane_masks.ppm'), str(tmp / 'four_lanes_unknown.ppm'), str(tmp / 'two_lanes_unknown.ppm')], check=True)
    for name in ('frame', 'navigation', 'roundabout', 'lane_reference', 'right_lane', 'two_lane_masks', 'four_lanes_unknown', 'two_lanes_unknown'):
        rgb = (tmp / (name + '.ppm')).read_bytes().split(b'\n', 3)[3]
        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
        scanlines = b''.join(b'\0' + rgb[y * 960:(y + 1) * 960] for y in range(240))
        output = project.parent / 'previews' / ('free_driving.png' if name == 'frame' else name + '.png')
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 320, 240, 8, 2, 0, 0, 0))
                           + chunk(b'IDAT', zlib.compress(scanlines)) + chunk(b'IEND', b''))
        print(output)
