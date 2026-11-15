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
    (tmp / 'esp_log.h').write_text('#pragma once\n#define ESP_LOGE(...) ((void)0)\n')
    (tmp / 'esp_heap_caps.h').write_text('#pragma once\n#include <cstdlib>\n#define MALLOC_CAP_SPIRAM 0\n#define MALLOC_CAP_8BIT 0\n#define MALLOC_CAP_DMA 0\n#define MALLOC_CAP_INTERNAL 0\ninline void *heap_caps_malloc(size_t n, int) { return malloc(n); }\n')
    (tmp / 'check.cpp').write_text(r'''
#include "display/hud_renderer.h"
#include "display/display_driver.h"
#include "display/colors.h"
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
    for (int x : {225, 255}) {
        assert(frame[168 * 320 + x] == colors::Muted);
        assert(frame[172 * 320 + x] != colors::Muted);
    }
    for (int x = 166; x < 316; ++x)
        assert(frame[208 * 320 + x] != colors::rgb565(83, 224, 235));
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
    assert(laneFrames[3][191 * 320 + 228] == green); // down-pointing U-turn end
    assert(laneFrames[2][182 * 320 + 228] == green); // horizontal left end
    assert(laneFrames[5][182 * 320 + 252] == green); // horizontal right end
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
    s.navigationActive = false;
    renderer.render(s, settings, status);
    std::puts("PASS: navigation text, lanes/ETA/remaining-only redraws, no lanes, 12 lanes and mode switching");
    std::puts("PASS: distance ordering, native Large/Small icons, street/alert redraws, unknown/no-alert states");
}
''')
    subprocess.run(['c++', '-std=c++17', '-I' + str(tmp), '-I' + str(project / 'main'),
                    str(tmp / 'check.cpp'), str(project / 'main/display/hud_renderer.cpp'),
                    str(project / 'main/display/bitmap_font.cpp'), str(project / 'main/assets/generated_assets.cpp'),
                    '-o', str(tmp / 'check')], check=True)
    subprocess.run([str(tmp / 'check'), str(tmp / 'frame.ppm'), str(tmp / 'navigation.ppm'), str(tmp / 'roundabout.ppm')], check=True)
    for name in ('frame', 'navigation', 'roundabout'):
        rgb = (tmp / (name + '.ppm')).read_bytes().split(b'\n', 3)[3]
        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
        scanlines = b''.join(b'\0' + rgb[y * 960:(y + 1) * 960] for y in range(240))
        output = project.parent / 'previews' / ('free_driving.png' if name == 'frame' else name + '.png')
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 320, 240, 8, 2, 0, 0, 0))
                           + chunk(b'IDAT', zlib.compress(scanlines)) + chunk(b'IEND', b''))
        print(output)
