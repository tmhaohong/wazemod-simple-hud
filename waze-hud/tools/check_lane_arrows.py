"""Exhaustive host review of the real lane renderer and HLP decoder."""
from pathlib import Path
import json
import subprocess
import tempfile

project = Path(__file__).resolve().parents[1]
output = project.parent / "previews/lane_review"
output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="wazehud-lanes-") as directory:
    tmp = Path(directory)
    (tmp / "sdkconfig.h").write_text("#define CONFIG_WAZE_HUD_LCD_ILI9341 1\n")
    (tmp / "esp_err.h").write_text("#pragma once\nusing esp_err_t = int;\n#define ESP_OK 0\n#define ESP_ERR_NO_MEM 1\n")
    (tmp / "esp_log.h").write_text("#pragma once\n#define ESP_LOGE(...) ((void)0)\n#define ESP_LOGW(...) ((void)0)\n#define ESP_LOGI(...) ((void)0)\n#define ESP_LOGD(...) ((void)0)\n")
    (tmp / "esp_timer.h").write_text("#pragma once\n#include <cstdint>\ninline int64_t esp_timer_get_time() { return 0; }\n")
    (tmp / "esp_heap_caps.h").write_text("#pragma once\n#include <cstdlib>\n#define MALLOC_CAP_SPIRAM 0\n#define MALLOC_CAP_8BIT 0\ninline void *heap_caps_malloc(size_t n, int) { return malloc(n); }\n")
    (tmp / "config").mkdir()
    config = (project / "main/config/device_config.h").read_text()
    config = config[config.index("enum class UiTheme"):config.index("using HlpSendLine")]
    (tmp / "config/device_config.h").write_text("#pragma once\n#include <cstdint>\nnamespace waze_hud {\n" + config + "}\n")
    (tmp / "review.cpp").write_text(r'''
#include "display/hud_renderer.cpp"
#include "protocol/hlp_decoder.h"
#include <cassert>
#include <cstdio>
#include <vector>
using namespace waze_hud;
DisplayDriver &DisplayDriver::instance() { static DisplayDriver d; return d; }
esp_err_t DisplayDriver::setBrightness(uint8_t) { return ESP_OK; }
esp_err_t DisplayDriver::setOrientation(bool, bool) { return ESP_OK; }
esp_err_t DisplayDriver::drawRegion(const Rect &, const uint16_t *) { return ESP_OK; }
constexpr auto green = colors::rgb565(66, 220, 112);
constexpr int W = 64, H = 64;
using Pixels = std::vector<uint16_t>;
Pixels render(unsigned directions, unsigned selected, int width) {
    Pixels pixels(W * H, 0);
    Canvas canvas(pixels.data(), W, H);
    if (!directions) return pixels; // Empty selected-path mask has no highlighted geometry.
    drawLane(canvas, 32, 8, std::max(2, width / 2 - 3),
             {static_cast<uint8_t>(directions), static_cast<uint8_t>(selected)}, green);
    return pixels;
}
unsigned mirror(unsigned mask) {
    return (mask & 1) | ((mask & 2) << 3) | ((mask & 4) << 3) |
           ((mask & 16) >> 3) | ((mask & 32) >> 3);
}
int main(int argc, char **argv) {
    FILE *raw = std::fopen(argv[1], "wb"); assert(raw);
    unsigned cases = 0, boundsFailures = 0, supportFailures = 0, colorFailures = 0;
    unsigned uniquenessFailures = 0, coreFailures = 0, unselectedLeakFailures = 0;
    unsigned mirrorSupportFailures = 0, maxMirrorDelta = 0;
    unsigned distinctSelectionFailures = 0;
    for (unsigned directions = 1; directions <= 63; ++directions) {
        for (unsigned selected = 0; selected <= 63; ++selected) {
            if (selected & ~directions) continue;
            ++cases;
            const auto normal = render(directions, selected, 30);
            const unsigned char header[] = {static_cast<unsigned char>(directions), static_cast<unsigned char>(selected)};
            std::fwrite(header, 1, 2, raw);
            std::fwrite(normal.data(), sizeof(uint16_t), normal.size(), raw);
            for (int laneCount = 1; laneCount <= 12; ++laneCount) {
                const int width = std::min(30, 152 / laneCount);
                const auto pixels = render(directions, selected, width);
                const auto inactive = render(directions, 0, width);
                const auto selectedOnly = render(selected, selected, width);
                const int left = 32 - width / 2, right = left + width;
                for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) {
                    const auto pixel = pixels[y * W + x];
                    if (pixel && (x <= left || x >= right || y < 8 || y >= 48)) {
                        if (boundsFailures < 5) std::fprintf(stderr, "bounds dir=%02x sel=%02x lanes=%d width=%d x=%d y=%d left=%d right=%d\n", directions, selected, laneCount, width, x, y, left, right);
                        ++boundsFailures;
                    }
                    if ((pixel != 0) != (inactive[y * W + x] != 0)) ++supportFailures;
                    if (!selected && ((pixel >> 5 & 63) > (pixel >> 11 & 31) * 2 + 8)) ++colorFailures;
                    if (selectedOnly[y * W + x] == green && pixel != green) ++coreFailures;
                    if (!selectedOnly[y * W + x] && pixel != inactive[y * W + x]) ++unselectedLeakFailures;
                }
                // Without U-turn (which always hooks left), geometry and masks should mirror.
                if (!(directions & 8)) {
                    const auto mirrored = render(mirror(directions), mirror(selected), width);
                    for (int y = 0; y < H; ++y) for (int x = 1; x < W; ++x)
                        {
                            const unsigned a = pixels[y * W + x], b = mirrored[y * W + (64 - x)];
                            if ((a != 0) != (b != 0)) ++mirrorSupportFailures;
                            const unsigned delta = std::max({std::abs(int(a >> 11) - int(b >> 11)), std::abs(int((a >> 5) & 63) - int((b >> 5) & 63)), std::abs(int(a & 31) - int(b & 31))});
                            maxMirrorDelta = std::max(maxMirrorDelta, delta);
                        }
                }
            }
        }
    }
    std::fclose(raw);
    // The first 6 bits are independent flags; every direction set must be distinguishable.
    for (int laneCount = 1; laneCount <= 12; ++laneCount) {
        const int width = std::min(30, 152 / laneCount);
        std::vector<Pixels> supports;
        for (unsigned directions = 1; directions <= 63; ++directions) {
            const auto pixels = render(directions, directions, width);
            for (const auto &previous : supports) if (pixels == previous) ++uniquenessFailures;
            supports.push_back(pixels);
            std::vector<Pixels> selections;
            for (unsigned selected = 0; selected <= 63; ++selected) {
                if (selected & ~directions) continue;
                const auto variant = render(directions, selected, width);
                for (const auto &previous : selections) if (variant == previous) ++distinctSelectionFailures;
                selections.push_back(variant);
            }
        }
    }
    HlpDecoder decoder; HudState state; unsigned accepted = 0, decodeFailures = 0, ts = 0;
    for (unsigned directions = 0; directions <= 255; ++directions)
        for (unsigned selected = 0; selected <= 255; ++selected) {
            char json[128];
            std::snprintf(json, sizeof(json), "{\"ts\":%u,\"lan\":[[%u,%u]]}", ++ts, directions, selected);
            auto *root = cJSON_Parse(json); assert(root);
            assert(decoder.decodeState(root, state)); cJSON_Delete(root);
            if (state.laneCount != 1) ++decodeFailures;
            ++accepted;
            if (state.lanes[0].directionMask != (directions & 63) || state.lanes[0].selectedMask != (selected & directions & 63)) ++decodeFailures;
        }
    // Malformed guidance preserves its physical lane slot; selection cannot invent a direction.
    auto decode = [&](const char *text) {
        decoder.resetSession(); auto *root = cJSON_Parse(text); assert(root);
        assert(decoder.decodeState(root, state)); cJSON_Delete(root);
    };
    decode(R"({"lan":[null,[],[1],[1,0,0],["1",0],[1,true],[-1,0],[1,-1],[1.5,0],[1,0.5],[64,0],[1,2],[1e999,0],[1,1e999],[1,0]]})");
    assert(state.laneCount == 12);
    assert(state.lanes[0].directionMask == 0 && state.lanes[0].selectedMask == 0);
    assert(state.lanes[5].directionMask == 1 && state.lanes[5].selectedMask == 0);
    assert(state.lanes[7].directionMask == 1 && state.lanes[7].selectedMask == 0);
    assert(state.lanes[10].directionMask == 0 && state.lanes[10].selectedMask == 0);
    assert(state.lanes[11].directionMask == 1 && state.lanes[11].selectedMask == 0);
    decode(R"({"lan":[[1e999,0],[1,1e999],[1.5,0],[1,0.5]]})");
    assert(state.laneCount == 4 && state.lanes[0].directionMask == 0 && state.lanes[1].directionMask == 1 && state.lanes[1].selectedMask == 0);
    decode(R"({"lan":[[5,4],[0,0],[1,0],[33,0]]})");
    assert(state.laneCount == 4 && state.lanes[0].directionMask == 5 && state.lanes[1].directionMask == 0 && state.lanes[2].directionMask == 1 && state.lanes[3].directionMask == 33);
    decode(R"({"lan":[[64,0],[33,0]]})");
    assert(state.laneCount == 2 && state.lanes[0].directionMask == 0 && state.lanes[1].directionMask == 33);
    decode(R"({"lan":[[69,64],[33,2]]})");
    assert(state.laneCount == 2 && state.lanes[0].directionMask == 5 && state.lanes[0].selectedMask == 0 && state.lanes[1].directionMask == 33 && state.lanes[1].selectedMask == 0);
    decode(R"({"lan":[[1,0],[2,0],[4,0],[8,0],[16,0],[32,0],[1,1],[2,2],[4,4],[8,8],[16,16],[32,32],[63,63]]})");
    assert(state.laneCount == 12 && state.lanes[11].directionMask == 32);
    decode(R"({"lan":[]})"); assert(state.laneCount == 0);
    decode(R"({})"); assert(state.laneCount == 0);
    decode(R"({"lan":"invalid"})"); assert(state.laneCount == 0);
    // Empty lanes must not leave a ghost stem behind.
    for (auto pixel : render(0, 0, 30)) assert(pixel == 0);
    std::printf("{\"valid_masks\":%u,\"lane_count_cases\":%u,\"bounds_failures\":%u,\"support_failures\":%u,\"color_failures\":%u,\"selected_core_failures\":%u,\"unselected_highlight_leaks\":%u,\"mirror_support_failures\":%u,\"mirror_max_channel_delta\":%u,\"duplicate_direction_sets\":%u,\"duplicate_selection_states\":%u,\"decode_pairs\":65536,\"decode_preserved_pairs\":%u,\"decode_failures\":%u,\"malformed_and_capacity_checks\":true}\n", cases, cases*12, boundsFailures, supportFailures, colorFailures, coreFailures, unselectedLeakFailures, mirrorSupportFailures, maxMirrorDelta, uniquenessFailures, distinctSelectionFailures, accepted, decodeFailures);

}
''')
    cjson = project / "managed_components/espressif__cjson/cJSON"
    for source, obj in ((cjson / "cJSON.c", tmp / "cjson.o"), (project / "main/protocol/hlp_core.c", tmp / "hlp_core.o")):
        subprocess.run(["cc", "-std=c99", "-I" + str(cjson), "-I" + str(project / "main"), "-c", str(source), "-o", str(obj)], check=True)
    subprocess.run(["c++", "-O2", "-std=c++17", "-I" + str(tmp), "-I" + str(project / "main"), "-I" + str(cjson), str(tmp / "review.cpp"), str(project / "main/display/bitmap_font.cpp"), str(project / "main/assets/generated_assets.cpp"), str(project / "main/protocol/hlp_decoder.cpp"), str(tmp / "cjson.o"), str(tmp / "hlp_core.o"), "-o", str(tmp / "review")], check=True)
    run = subprocess.run([str(tmp / "review"), str(tmp / "all_masks.raw")], check=True, capture_output=True, text=True)
    if run.stderr:
        print(run.stderr)
    report = json.loads(run.stdout)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))

    assert report["valid_masks"] == 728
    assert report["decode_preserved_pairs"] == 65536
    for key in ("bounds_failures", "support_failures", "color_failures", "selected_core_failures", "unselected_highlight_leaks", "mirror_support_failures", "duplicate_direction_sets", "duplicate_selection_states", "decode_failures"):
        assert report[key] == 0, (key, report[key])
    assert report["mirror_max_channel_delta"] <= 1

    # Contact sheets show the actual renderer output, enlarged without smoothing.
    from PIL import Image, ImageDraw
    import struct
    raw = (tmp / "all_masks.raw").read_bytes()
    record_size = 2 + 64 * 64 * 2
    images = {}
    for offset in range(0, len(raw), record_size):
        directions, selected = raw[offset:offset + 2]
        pixels = struct.unpack("<4096H", raw[offset + 2:offset + record_size])
        rgb = bytes(channel for pixel in pixels for channel in (
            ((pixel >> 11) & 31) * 255 // 31,
            ((pixel >> 5) & 63) * 255 // 63,
            (pixel & 31) * 255 // 31))
        images[directions, selected] = Image.frombytes("RGB", (64, 64), rgb)
    atlas = Image.new("RGB", (12 * 76, ((len(images) + 11) // 12) * 86), (20, 24, 30))
    labels = ImageDraw.Draw(atlas)
    for index, (key, image) in enumerate(images.items()):
        x, y = index % 12 * 76, index // 12 * 86
        atlas.paste(image, (x + 6, y + 20))
        labels.text((x + 6, y + 4), f"{key[0]:02X}/{key[1]:02X}", fill="white")
    atlas.save(output / "all_728_masks.png")
    choices = [(1, 1), (2, 2), (4, 4), (8, 8), (16, 16), (32, 32), (5, 4), (33, 0),
               (3, 2), (17, 16), (12, 4), (12, 8), (10, 2), (10, 8), (63, 1), (63, 8)]
    sheet = Image.new("RGB", (8 * 144, 2 * 160), (20, 24, 30))
    labels = ImageDraw.Draw(sheet)
    for index, key in enumerate(choices):
        x, y = index % 8 * 144, index // 8 * 160
        sheet.paste(images[key].resize((128, 128), Image.Resampling.NEAREST), (x + 8, y + 24))
        labels.text((x + 8, y + 6), f"{key[0]:02X}/{key[1]:02X}", fill="white")
    sheet.save(output / "representative_corrected.png")
    print(output / "all_728_masks.png")
