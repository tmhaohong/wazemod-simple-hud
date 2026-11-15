#include "display/hud_renderer.h"

#include "display/colors.h"
#include "display/display_driver.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>

namespace waze_hud {
namespace {
constexpr char kTag[] = "DISPLAY";

AlertState primaryAlert(const HudState &state) {
    if (state.noPassingZone) {
        AlertState alert{};
        alert.kind = AlertKind::NoPassing;
        alert.distanceM = state.noPassingRemainingM;
        return alert;
    }
    return state.nearestAlert;
}

bool hasAlert(const AlertState &alert) {
    return alert.kind != AlertKind::None;
}

bool hasManeuver(const HudState &state) {
    return state.navigationActive && state.maneuver != Maneuver::None &&
           state.maneuverDistanceM >= 0;
}

bool isDualSpeedLimit(const HudState &state) {
    return state.hasMinimumSpeed && state.minimumSpeedKmh > 0 && state.speedLimitKmh > 0;
}

bool isOverspeed(const HudState &state, const DeviceSettings &settings) {
    if (state.speedLimitKmh <= 0) return false;
    const int threshold = std::max(0, state.speedLimitKmh + static_cast<int>(settings.overspeedOffsetKmh));
    return state.speedKmh > threshold;
}

void formatDistance(int meters, char *output, size_t capacity) {
    if (meters < 0) { output[0] = 0; return; }
    if (meters < 1000) std::snprintf(output, capacity, "%dM", meters);
    else if (meters < 10000) std::snprintf(output, capacity, "%.1fKM", meters / 1000.0);
    else std::snprintf(output, capacity, "%dKM", (meters + 500) / 1000);
}

const assets::AlphaMask *maneuverAsset(Maneuver maneuver) {
    switch (maneuver) {
        case Maneuver::Continue: return &assets::kManeuverContinue;
        case Maneuver::Left: return &assets::kManeuverLeft;
        case Maneuver::Right: return &assets::kManeuverRight;
        case Maneuver::UTurn: return &assets::kManeuverUTurn;
        case Maneuver::Roundabout: return &assets::kManeuverRoundabout;
        case Maneuver::RoundaboutLeft: return &assets::kManeuverRoundaboutLeft;
        case Maneuver::RoundaboutRight: return &assets::kManeuverRoundaboutRight;
        case Maneuver::RoundaboutStraight: return &assets::kManeuverRoundaboutStraight;
        case Maneuver::RoundaboutUTurn: return &assets::kManeuverRoundaboutUTurn;
        case Maneuver::KeepLeft: return &assets::kManeuverExitLeft;
        case Maneuver::KeepRight: return &assets::kManeuverExitRight;
        case Maneuver::ExitLeft: return &assets::kManeuverExitLeft;
        case Maneuver::ExitRight: return &assets::kManeuverExitRight;
        case Maneuver::Arrive: return &assets::kManeuverArrive;
        default: return nullptr;
    }
}

void arrowHead(Canvas &canvas, int x, int y, int dx, int dy) {
    if (std::abs(dx) >= std::abs(dy)) {
        const int sign = dx >= 0 ? 1 : -1;
        canvas.line(x, y, x - sign * 9, y - 7, colors::White, 3);
        canvas.line(x, y, x - sign * 9, y + 7, colors::White, 3);
    } else {
        const int sign = dy >= 0 ? 1 : -1;
        canvas.line(x, y, x - 7, y - sign * 9, colors::White, 3);
        canvas.line(x, y, x + 7, y - sign * 9, colors::White, 3);
    }
}

void drawTurnFallback(Canvas &canvas, int x, int y, Maneuver maneuver) {
    // f205389 primitive geometry, translated into the current 60px icon slot.
    int endX, endY;
    switch (maneuver) {
        case Maneuver::SlightLeft: endX = 9; endY = 8; break;
        case Maneuver::SlightRight: endX = 51; endY = 8; break;
        case Maneuver::SharpLeft: endX = 2; endY = 39; break;
        case Maneuver::SharpRight: endX = 58; endY = 39; break;
        default: return;
    }
    canvas.line(x + 30, y + 58, x + 30, y + 35, colors::White, 5);
    canvas.line(x + 30, y + 35, x + endX, y + endY, colors::White, 5);
    arrowHead(canvas, x + endX, y + endY, endX - 30, endY - 35);
}

// Original procedural lane style, with the six HLP flags from the lane reference.
void drawLaneArrowHead(Canvas &canvas, int x, int y, int dx, int dy, uint16_t color, float thickness) {
    constexpr int size = 3;
    if (std::abs(dx) >= std::abs(dy)) {
        const int sign = dx >= 0 ? 1 : -1;
        canvas.smoothLine(x, y, x - sign * size, y - size, color, thickness);
        canvas.smoothLine(x, y, x - sign * size, y + size, color, thickness);
    } else {
        const int sign = dy >= 0 ? 1 : -1;
        canvas.smoothLine(x, y, x - size, y - sign * size, color, thickness);
        canvas.smoothLine(x, y, x + size, y - sign * size, color, thickness);
    }
}

void drawLane(Canvas &canvas, int x, int y, int branchWidth, const LaneState &lane, uint16_t selectedColor) {
    constexpr float thickness = 2.4F;
    canvas.smoothLine(x, y + 32, x, y + 15, lane.selectedMask ? selectedColor : colors::Muted,
                      thickness);
    for (int pass = 0; pass < 2; ++pass) {
        for (unsigned bit = 0; bit < 6; ++bit) {
            const unsigned flag = 1U << bit;
            const bool selected = (lane.selectedMask & flag) != 0;
            if (!(lane.directionMask & flag) || selected != (pass != 0)) continue;
            const auto color = selected ? selectedColor : colors::Muted;
            int endX = x, endY = y + 3;
            switch (flag) {
                case 0x02: endX -= branchWidth; endY = y + 5; break;
                case 0x04:
                    endX -= branchWidth; endY = y + 12;
                    canvas.smoothLine(x, y + 15, x, endY, color, thickness);
                    canvas.smoothLine(x, endY, endX, endY, color, thickness);
                    drawLaneArrowHead(canvas, endX, endY, -1, 0, color, thickness);
                    continue;
                case 0x08:
                    // Up, around, and down: U-turn, not another left branch.
                    endX -= branchWidth; endY = y + 21;
                    canvas.smoothLine(x, y + 15, x, y + 7, color, thickness);
                    canvas.smoothLine(x, y + 7, x - branchWidth / 2, y + 3, color, thickness);
                    canvas.smoothLine(x - branchWidth / 2, y + 3, endX, y + 7, color, thickness);
                    canvas.smoothLine(endX, y + 7, endX, endY, color, thickness);
                    drawLaneArrowHead(canvas, endX, endY, 0, 1, color, thickness);
                    continue;
                case 0x10: endX += branchWidth; endY = y + 5; break;
                case 0x20:
                    endX += branchWidth; endY = y + 12;
                    canvas.smoothLine(x, y + 15, x, endY, color, thickness);
                    canvas.smoothLine(x, endY, endX, endY, color, thickness);
                    drawLaneArrowHead(canvas, endX, endY, 1, 0, color, thickness);
                    continue;
                default: break;
            }
            canvas.smoothLine(x, y + 15, endX, endY, color, thickness);
            drawLaneArrowHead(canvas, endX, endY, endX - x, endY - y - 15, color, thickness);
        }
    }
}

const assets::ColorBitmap *alertAsset(AlertKind kind, bool dominant) {
    const uint8_t code = static_cast<uint8_t>(kind);
    for (std::size_t index = 0; index < assets::kAlertAssetCount; ++index) {
        const assets::AlertAssetSet &entry = assets::kAlertAssets[index];
        if (entry.code == code) return dominant ? entry.large : entry.small;
    }
    return nullptr;
}

const assets::ColorBitmap *trafficJamAsset(uint8_t severity, bool dominant) {
    switch (severity) {
        case 1: return dominant ? &assets::kAlertTrafficJam1Large : &assets::kAlertTrafficJam1Small;
        case 2: return dominant ? &assets::kAlertTrafficJam2Large : &assets::kAlertTrafficJam2Small;
        case 4: case 5: return dominant ? &assets::kAlertTrafficJam4Large : &assets::kAlertTrafficJam4Small;
        default: return alertAsset(AlertKind::TrafficJam, dominant);
    }
}

const assets::ColorBitmap *alertBitmap(const AlertState &alert, bool dominant) {
    if (alert.kind == AlertKind::SpeedDrop) {
        for (std::size_t index = 0; index < assets::kSpeedLimitAssetCount; ++index) {
            const auto &entry = assets::kSpeedLimitAssets[index];
            if (entry.value != alert.valueKmh) continue;
            return dominant ? entry.alertLarge : entry.alertSmall;
        }
        return nullptr;
    }
    const auto *bitmap = alert.kind == AlertKind::TrafficJam
        ? trafficJamAsset(alert.trafficSeverity, dominant) : alertAsset(alert.kind, dominant);
    return bitmap ? bitmap : alertAsset(AlertKind::Hazard, dominant);
}

void drawAlertIcon(Canvas &canvas, int x, int y, const AlertState &alert, bool dominant) {
    const auto *bitmap = alertBitmap(alert, dominant);
    if (bitmap && bitmap->pixels && bitmap->alpha) {
        canvas.colorBitmap(x, y, *bitmap);
        return;
    }
    const int radius = dominant ? 22 : 13;
    const int cx = x + (bitmap ? bitmap->width / 2 : radius);
    const int cy = y + (bitmap ? bitmap->height / 2 : radius);
    if (alert.kind == AlertKind::SpeedDrop) {
        canvas.fillCircle(cx, cy, radius, colors::White);
        canvas.circle(cx, cy, radius, colors::Red, dominant ? 3 : 2);
        char value[12];
        std::snprintf(value, sizeof(value), "%d", alert.valueKmh);
        const auto &font = dominant ? assets::kNumberMedium : assets::kNumberSmall;
        canvas.fontText(cx - radius, cy - font.lineHeight / 2, value,
                        font, colors::Black, 2 * radius, true);
        return;
    }
    canvas.triangle(cx, cy - radius, cx - radius, cy + radius,
                    cx + radius, cy + radius, colors::Amber);
    canvas.fontText(cx - radius, cy - assets::kTextSmall.lineHeight / 2,
                    "!", assets::kTextSmall, colors::Amber, 2 * radius, true);
}

uint16_t bleSignalColor(const SystemStatusSnapshot &status) {
    if (!status.bleConnected) return colors::Muted;
    if (status.bleRssiDbm >= -60) return colors::Green;
    if (status.bleRssiDbm >= -75) return colors::Blue;
    if (status.bleRssiDbm >= -85) return colors::Amber;
    return colors::Red;
}
}  // namespace

esp_err_t HudRenderer::init() {
#if CONFIG_WAZE_HUD_LCD_ILI9341
    // SPI transfers use the driver's internal DMA stripe buffer. Keep the
    // full 320x240 frame in PSRAM to avoid fragmenting BLE's internal heap.
    constexpr int bufferCaps = MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT;
#else
    constexpr int bufferCaps = MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL;
#endif
    buffer_ = static_cast<uint16_t *>(heap_caps_malloc(layout::MaxRegionPixels * sizeof(uint16_t),
                                                       bufferCaps));
    if (!buffer_) {
        ESP_LOGE(kTag, "Unable to allocate %d-byte frame buffer",
                 layout::MaxRegionPixels * static_cast<int>(sizeof(uint16_t)));
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

void HudRenderer::renderWaiting(Canvas &canvas) {
    canvas.clear(colors::Black);
    if (assets::kBootIcon.pixels && assets::kBootIcon.alpha) {
        canvas.colorBitmap(layout::Width / 2 - assets::kBootIcon.width / 2, 40, assets::kBootIcon);
    }
    canvas.fontText(0, 115, "CHỜ KẾT NỐI...", assets::kTextMedium, colors::White, layout::Width, true);
}

void HudRenderer::renderSystemStatus(Canvas &canvas, const SystemStatusSnapshot &systemStatus,
                                     const DeviceSettings &settings) {
    canvas.clear(colors::Black);
    canvas.fontText(0, 7, "TRẠNG THÁI", assets::kTextMedium, colors::White, layout::Width, true);

    constexpr int batteryX = 25;
    constexpr int batteryY = 47;
    constexpr int batteryWidth = 52;
    constexpr int batteryHeight = 27;
    const uint16_t batteryColor = systemStatus.batteryPresent
        ? (systemStatus.batteryPercent <= 15 ? colors::Red
           : systemStatus.batteryPercent <= 35 ? colors::Amber : colors::Green)
        : colors::Muted;
    canvas.fillRect(batteryX, batteryY, batteryWidth, batteryHeight, batteryColor);
    canvas.fillRect(batteryX + 3, batteryY + 3, batteryWidth - 6, batteryHeight - 6, colors::Black);
    canvas.fillRect(batteryX + batteryWidth, batteryY + 8, 5, batteryHeight - 16, batteryColor);
    if (systemStatus.batteryPresent) {
        const int fillWidth = (batteryWidth - 10) * systemStatus.batteryPercent / 100;
        canvas.fillRect(batteryX + 5, batteryY + 5, fillWidth, batteryHeight - 10, batteryColor);
    } else {
        canvas.line(batteryX + 8, batteryY + 6, batteryX + batteryWidth - 8, batteryY + batteryHeight - 6, colors::Red, 3);
        canvas.line(batteryX + batteryWidth - 8, batteryY + 6, batteryX + 8, batteryY + batteryHeight - 6, colors::Red, 3);
    }
    char batteryText[24];
    if (systemStatus.batteryPresent)
        std::snprintf(batteryText, sizeof(batteryText), "PIN %u%%",
                      static_cast<unsigned>(systemStatus.batteryPercent));
    else
        std::snprintf(batteryText, sizeof(batteryText), "KHÔNG CÓ PIN");
    canvas.fontText(95, 49, batteryText, assets::kTextMedium, batteryColor, 215, false);

    constexpr int bluetoothX = 49;
    constexpr int bluetoothTop = 92;
    constexpr int bluetoothBottom = 132;
    const uint16_t bluetoothColor = bleSignalColor(systemStatus);
    canvas.line(bluetoothX, bluetoothTop, bluetoothX, bluetoothBottom, bluetoothColor, 3);
    canvas.line(bluetoothX, bluetoothTop, bluetoothX + 13, 103, bluetoothColor, 3);
    canvas.line(bluetoothX + 13, 103, bluetoothX - 10, 122, bluetoothColor, 3);
    canvas.line(bluetoothX - 10, 101, bluetoothX + 13, 122, bluetoothColor, 3);
    canvas.line(bluetoothX + 13, 122, bluetoothX, bluetoothBottom, bluetoothColor, 3);

    int signalBars = 0;
    if (systemStatus.bleConnected) {
        signalBars = systemStatus.bleRssiDbm >= -55 ? 4 :
                     systemStatus.bleRssiDbm >= -67 ? 3 :
                     systemStatus.bleRssiDbm >= -78 ? 2 :
                     systemStatus.bleRssiDbm >= -90 ? 1 : 0;
    }
    constexpr int barBaseX = 95;
    constexpr int barBottom = 132;
    for (int bar = 0; bar < 4; ++bar) {
        const int height = 10 + bar * 8;
        const uint16_t color = bar < signalBars ? bluetoothColor : colors::Muted;
        canvas.fillRect(barBaseX + bar * 11, barBottom - height, 6, height, color);
    }
    char rssiText[32];
    if (systemStatus.bleConnected)
        std::snprintf(rssiText, sizeof(rssiText), "BLE %d dBm", systemStatus.bleRssiDbm);
    else
        std::snprintf(rssiText, sizeof(rssiText), "CHƯA KẾT NỐI BLE");
    canvas.fontText(150, 102, rssiText, assets::kTextMedium, bluetoothColor, 160, false);
}

void HudRenderer::renderSpeedLimitSign(Canvas &canvas, int centerX, int centerY, int radius, int speedLimit) {
    if (speedLimit <= 0) return;
    const int innerRadius = radius * 74 / 100;
    canvas.fillCircle(centerX, centerY, radius, colors::Red);
    canvas.fillCircle(centerX, centerY, innerRadius, colors::White);
    char limitStr[12];
    std::snprintf(limitStr, sizeof(limitStr), "%d", speedLimit);
    const int fontW = radius * 14 / 10;
    canvas.fontText(centerX - fontW / 2, centerY - assets::kNumberLarge.lineHeight / 2,
                    limitStr, assets::kNumberLarge, colors::Black, fontW, true);
}

void HudRenderer::renderMinSpeedSign(Canvas &canvas, int centerX, int centerY, int radius, int minSpeed) {
    if (minSpeed <= 0) return;
    canvas.fillCircle(centerX, centerY, radius, colors::Blue);
    char limitStr[12];
    std::snprintf(limitStr, sizeof(limitStr), "%d", minSpeed);
    const int fontW = radius * 14 / 10;
    canvas.fontText(centerX - fontW / 2, centerY - assets::kNumberLarge.lineHeight / 2,
                    limitStr, assets::kNumberLarge, colors::White, fontW, true);
}

void HudRenderer::renderAlertSide(Canvas &canvas, const AlertState &alert, int maxZoneWidth) {
    const assets::ColorBitmap *bitmap = alertBitmap(alert, true);

    char distanceStr[16];
    formatDistance(alert.distanceM, distanceStr, sizeof(distanceStr));
    const int textW = canvas.fontTextWidth(distanceStr, assets::kTextLarge);

    const int iconW = bitmap ? bitmap->width : 44;
    const int iconH = bitmap ? bitmap->height : 44;
    const int gap = 12;
    const int totalW = iconW + gap + textW;
    const int startX = std::max(6, (maxZoneWidth - totalW) / 2);

    drawAlertIcon(canvas, startX, 85 - iconH / 2, alert, true);
    canvas.fontText(startX + iconW + gap, 85 - assets::kTextLarge.lineHeight / 2,
                    distanceStr, assets::kTextLarge, colors::White, textW + 10, false);
}

void HudRenderer::renderManeuverSide(Canvas &canvas, Maneuver maneuver, int exit, int distanceM, int maxZoneWidth) {
    const assets::AlphaMask *mask = maneuverAsset(maneuver);

    char distanceStr[16];
    formatDistance(distanceM, distanceStr, sizeof(distanceStr));
    const int textW = canvas.fontTextWidth(distanceStr, assets::kTextLarge);

    const int iconW = mask ? mask->width : 60;
    const int iconH = mask ? mask->height : 60;
    const int gap = 10;
    const int totalW = iconW + gap + textW;
    const int startX = std::max(4, (maxZoneWidth - totalW) / 2);
    const int iconY = 85 - iconH / 2;

    if (mask && mask->alpha) {
        canvas.alphaMask(startX, iconY, *mask, colors::White);
        if (exit > 0) {
            char exitStr[16];
            std::snprintf(exitStr, sizeof(exitStr), "%d", std::clamp(exit, 0, 99));
            canvas.fontText(startX + iconW / 2 - 12, 85 - assets::kNumberSmall.lineHeight / 2,
                            exitStr, assets::kNumberSmall, colors::White, 24, true);
        }
    } else {
        drawTurnFallback(canvas, startX, iconY, maneuver);
    }
    canvas.fontText(startX + iconW + gap, 85 - assets::kTextLarge.lineHeight / 2,
                    distanceStr, assets::kTextLarge, colors::White, textW + 10, false);
}

void HudRenderer::renderSpeedSide(Canvas &canvas, int speedKmh, bool overspeed, int centerX, int centerY) {
    char speedStr[12];
    std::snprintf(speedStr, sizeof(speedStr), "%d", std::clamp(speedKmh, 0, 999));
    const uint16_t color = overspeed ? colors::Red : colors::White;
    canvas.fontText(centerX - 45, centerY - 22, speedStr, assets::kCurrentSpeed, color, 90, true);
    canvas.fontText(centerX - 30, centerY + 18, "km/h", assets::kTextSmall, colors::Muted, 60, true);
}

void HudRenderer::renderStreetText(Canvas &canvas, int slot, int x, int y, const char *text,
                                    const assets::BitmapFont &font, uint16_t color, int width, bool centered) {
    const auto now = static_cast<uint64_t>(esp_timer_get_time() / 1000);
    auto &stored = marqueeText_[slot];
    if (std::strcmp(stored.data(), text) != 0) {
        std::snprintf(stored.data(), stored.size(), "%s", text);
        marqueeEpochMs_[slot] = now;
    }
    const int overflow = canvas.fontTextWidth(text, font) - width;
    if (overflow <= 0) {
        canvas.fontTextViewport(x, y, text, font, color, width,
                                centered ? overflow / 2 : 0);
        return;
    }
    marqueeActive_ = true;
    constexpr uint64_t startHold = 1200, endHold = 900, msPerPixel = 45;
    const uint64_t scroll = static_cast<uint64_t>(overflow) * msPerPixel;
    const uint64_t elapsed = (now - marqueeEpochMs_[slot]) % (startHold + scroll + endHold);
    const int offset = elapsed < startHold ? 0 :
        std::min(overflow, static_cast<int>((elapsed - startHold) / msPerPixel));
    canvas.fontTextViewport(x, y, text, font, color, width, offset);
}

void HudRenderer::renderFreeDriving(Canvas &canvas, const HudState &state,
                                   const DeviceSettings &settings) {
    // Keep the original 320x170 boards supported while using the full ES3N28P panel.
    const int streetTop = layout::Height == 240 ? 170 : 138;
    renderDrivingSummary(canvas, state, settings, streetTop);
    canvas.fillRect(0, streetTop, layout::Width, layout::Height - streetTop,
                    colors::rgb565(10, 23, 27));
    canvas.line(0, streetTop, 319, streetTop, colors::rgb565(43, 61, 66));
    if (settings.showStreet)
        renderStreetText(canvas, 0, 8,
                         streetTop + (layout::Height - streetTop - assets::kStreetName.lineHeight) / 2,
                         state.currentStreet.data(), assets::kStreetName,
                         colors::Foreground, 304, true);
}

void HudRenderer::renderDrivingSummary(Canvas &canvas, const HudState &state,
                                       const DeviceSettings &settings, int streetTop) {
    const int centerY = streetTop / 2;
    const uint16_t warningColor = colors::rgb565(255, 216, 104);
    renderSpeedSide(canvas, state.speedKmh, isOverspeed(state, settings), 46, centerY);

    // No heading: devote the center column to a large, readable limit sign.
    const int radius = std::min(62, streetTop / 2 - 8);
    canvas.fillCircle(157, centerY, radius, colors::Red);
    canvas.fillCircle(157, centerY, radius - 8, colors::White);
    char limit[12];
    if (state.speedLimitKmh > 0)
        std::snprintf(limit, sizeof(limit), "%d", state.speedLimitKmh);
    else
        std::snprintf(limit, sizeof(limit), "?");
    canvas.fontText(157 - radius + 8, centerY - assets::kNumberLarge.lineHeight / 2,
                    limit, assets::kNumberLarge, colors::Black, 2 * radius - 16, true);

    std::array<AlertState, kMaxAlerts + 2> alerts{};
    std::size_t count = 0;
    auto append = [&](const AlertState &candidate) {
        if (!hasAlert(candidate)) return;
        for (std::size_t i = 0; i < count; ++i)
            if (alerts[i] == candidate) return;
        if (count < alerts.size()) alerts[count++] = candidate;
    };
    append(primaryAlert(state));
    append(state.nearestAlert);
    for (std::size_t i = 0; i < std::min<std::size_t>(state.upcomingAlertCount, state.upcomingAlerts.size()); ++i)
        append(state.upcomingAlerts[i]);
    // Unknown distances follow measured distances. Stable ties retain producer order.
    std::stable_sort(alerts.begin(), alerts.begin() + count, [](const AlertState &a, const AlertState &b) {
        if (a.distanceM < 0) return false;
        return b.distanceM < 0 || a.distanceM < b.distanceM;
    });
    const bool compact = streetTop <= 112;
    const int rowTop = compact ? 2 : 10;
    const int rowHeight = (streetTop - 20) / 3;
    for (std::size_t i = 0; i < std::min<std::size_t>(count, 3); ++i) {
        const bool large = i == 0;
        const auto *bitmap = alertBitmap(alerts[i], large);
        const int width = bitmap ? bitmap->width : (large ? 44 : 26);
        const int height = bitmap ? bitmap->height : (large ? 44 : 26);
        const int heightForRow = compact ? (large ? 44 : 30) : rowHeight;
        const int y = compact ? (i == 0 ? rowTop : rowTop + 44 + (i - 1) * 30) : rowTop + i * rowHeight;
        drawAlertIcon(canvas, 228 + (44 - width) / 2, y + (heightForRow - height) / 2, alerts[i], large);
        char distance[20];
        if (alerts[i].distanceM < 0) std::snprintf(distance, sizeof(distance), "--");
        else if (alerts[i].distanceM < 1000) std::snprintf(distance, sizeof(distance), "%d m", alerts[i].distanceM);
        else std::snprintf(distance, sizeof(distance), "%.1f km", alerts[i].distanceM / 1000.0);
        const auto &font = assets::kWarningDistance;
        canvas.fontText(276, y + (heightForRow - font.lineHeight) / 2,
                        distance, font, warningColor, 43, false);
    }

}

void HudRenderer::renderNavigation(Canvas &canvas, const HudState &state,
                                  const DeviceSettings &settings) {
    constexpr int streetTop = 112;
    constexpr int directionsTop = 160;
    const auto cyan = colors::rgb565(83, 224, 235);
    const auto divider = colors::rgb565(43, 61, 66);
    renderDrivingSummary(canvas, state, settings, streetTop);
    canvas.fillRect(0, streetTop, 320, directionsTop - streetTop, colors::rgb565(10, 23, 27));
    canvas.line(0, streetTop, 319, streetTop, divider);
    canvas.line(0, directionsTop, 319, directionsTop, divider);
    if (settings.showStreet)
        renderStreetText(canvas, 0, 8, streetTop + 3, state.currentStreet.data(), assets::kTextMedium,
                          colors::Foreground, 304);

    const char *instruction = "Tiếp tục";
    switch (state.maneuver) {
        case Maneuver::Left: instruction = "Rẽ trái"; break;
        case Maneuver::Right: instruction = "Rẽ phải"; break;
        case Maneuver::SlightLeft: instruction = "Chếch trái"; break;
        case Maneuver::SlightRight: instruction = "Chếch phải"; break;
        case Maneuver::SharpLeft: instruction = "Rẽ ngoặt trái"; break;
        case Maneuver::SharpRight: instruction = "Rẽ ngoặt phải"; break;
        case Maneuver::KeepLeft: case Maneuver::ExitLeft: instruction = "Đi bên trái"; break;
        case Maneuver::KeepRight: case Maneuver::ExitRight: instruction = "Đi bên phải"; break;
        case Maneuver::UTurn: instruction = "Quay đầu"; break;
        case Maneuver::Arrive: instruction = "Đến nơi"; break;
        case Maneuver::Roundabout: case Maneuver::RoundaboutLeft:
        case Maneuver::RoundaboutRight: case Maneuver::RoundaboutStraight:
        case Maneuver::RoundaboutUTurn: instruction = "Vào vòng xuyến"; break;
        default: break;
    }
    const bool roundabout = state.maneuver == Maneuver::Roundabout ||
        state.maneuver == Maneuver::RoundaboutLeft || state.maneuver == Maneuver::RoundaboutRight ||
        state.maneuver == Maneuver::RoundaboutStraight || state.maneuver == Maneuver::RoundaboutUTurn;
    char instructionText[256];
    if (state.roundaboutExit > 0 && roundabout)
        std::snprintf(instructionText, sizeof(instructionText), "%s, lối ra %d", instruction, state.roundaboutExit);
    else
        std::snprintf(instructionText, sizeof(instructionText), "%s%s%s", instruction,
                      state.nextStreet[0] ? " vào " : "", state.nextStreet.data());
    renderStreetText(canvas, 1, 8, streetTop + 25, instructionText, assets::kNavigationText, colors::Muted, 304);

    const auto *turn = maneuverAsset(state.maneuver);
    if (turn) canvas.alphaMaskScaled(16, directionsTop + 5, 48, 48, *turn, cyan);
    else drawTurnFallback(canvas, 10, directionsTop + 1, state.maneuver);
    if (state.roundaboutExit > 0 && turn && roundabout) {
        char exit[12];
        std::snprintf(exit, sizeof(exit), "%d", state.roundaboutExit);
        // The ring is not centered in the full bitmap when an exit arm extends
        // to one side. Anchor the number to each source asset's ring center.
        int ringX = 30, ringY = 30;
        switch (state.maneuver) {
            case Maneuver::RoundaboutLeft: ringX = 40; ringY = 23; break;
            case Maneuver::RoundaboutRight: ringX = 20; ringY = 23; break;
            case Maneuver::RoundaboutStraight: ringX = 29; ringY = 32; break;
            case Maneuver::RoundaboutUTurn: ringX = 40; ringY = 22; break;
            default: break;
        }
        const int centerX = 16 + ringX * 48 / turn->width;
        const int centerY = directionsTop + 5 + ringY * 48 / turn->height;
        const int numberWidth = canvas.fontTextWidth(exit, assets::kRoundaboutExit);
        canvas.fontText(centerX - numberWidth / 2,
                        centerY - assets::kRoundaboutExit.lineHeight / 2,
                        exit, assets::kRoundaboutExit, colors::White, numberWidth, false);
    }
    char turnDistance[24];
    if (state.maneuverDistanceM < 0) std::snprintf(turnDistance, sizeof(turnDistance), "--");
    else if (state.maneuverDistanceM < 1000)
        std::snprintf(turnDistance, sizeof(turnDistance), "%d m", state.maneuverDistanceM);
    else std::snprintf(turnDistance, sizeof(turnDistance), "%.1f km", state.maneuverDistanceM / 1000.0);
    canvas.fontText(72, directionsTop + 16, turnDistance, assets::kTextMedium, colors::White, 84, false);

    const std::size_t laneCount = std::min<std::size_t>(state.laneCount, state.lanes.size());
    if (laneCount) {
        const int laneWidth = std::min(30, 152 / static_cast<int>(laneCount));
        const int startX = 164 + (152 - laneWidth * laneCount) / 2;
        for (std::size_t lane = 0; lane < laneCount; ++lane) {
            const int x = startX + lane * laneWidth;
            // 30px arrow centered vertically in the 56px direction section.
            drawLane(canvas, x + laneWidth / 2, directionsTop + 10,
                     std::max(2, laneWidth / 2 - 3), state.lanes[lane], colors::rgb565(66, 220, 112));
            if (lane + 1 < laneCount) {
                const int separatorX = x + laneWidth;
                for (int y = directionsTop + 8; y < directionsTop + 48; y += 7)
                    canvas.line(separatorX, y, separatorX, std::min(y + 3, directionsTop + 47),
                                colors::Muted);
            }
        }
    }

    char remaining[64];
    const double km = state.remainingMeters > 0 ? state.remainingMeters / 1000.0 : state.remainingKm;
    if (km >= 1.0) std::snprintf(remaining, sizeof(remaining), "%d phút - %.1f km", state.remainingMinutes, km);
    else std::snprintf(remaining, sizeof(remaining), "%d phút - %d m", state.remainingMinutes,
                        std::max(0, static_cast<int>(std::lround(km * 1000))));
    canvas.fontText(8, 220, remaining, assets::kFooterText, colors::Foreground, 202, false);
    char arrival[24];
    std::snprintf(arrival, sizeof(arrival), "Đến %s", state.eta[0] ? state.eta.data() : "--:--");
    canvas.fontText(218, 220, arrival, assets::kFooterText, colors::Foreground, 94, true);
}

void HudRenderer::render(const HudState &state, const DeviceSettings &settings,
                         const SystemStatusSnapshot &systemStatus) {
    const bool changed = firstFrame_ || marqueeActive_ ||
        state.connected != previous_.connected ||
        state.hasProducerState != previous_.hasProducerState ||
        state.speedKmh != previous_.speedKmh ||
        state.speedLimitKmh != previous_.speedLimitKmh ||
        state.hasMinimumSpeed != previous_.hasMinimumSpeed ||
        state.minimumSpeedKmh != previous_.minimumSpeedKmh ||
        !(state.nearestAlert == previous_.nearestAlert) ||
        state.upcomingAlertCount != previous_.upcomingAlertCount ||
        state.upcomingAlerts != previous_.upcomingAlerts ||
        state.currentStreet != previous_.currentStreet ||
        state.nextStreet != previous_.nextStreet ||
        state.eta != previous_.eta ||
        state.remainingMinutes != previous_.remainingMinutes ||
        state.remainingMeters != previous_.remainingMeters ||
        state.remainingKm != previous_.remainingKm ||
        state.laneCount != previous_.laneCount ||
        state.lanes != previous_.lanes ||
        state.noPassingZone != previous_.noPassingZone ||
        state.noPassingRemainingM != previous_.noPassingRemainingM ||
        state.maneuver != previous_.maneuver ||
        state.maneuverDistanceM != previous_.maneuverDistanceM ||
        state.roundaboutExit != previous_.roundaboutExit ||
        state.navigationActive != previous_.navigationActive ||
        systemStatus.visible != previousSystemStatus_.visible ||
        settings.overspeedOffsetKmh != previousSettings_.overspeedOffsetKmh ||
        settings.offsetX != previousSettings_.offsetX ||
        settings.offsetY != previousSettings_.offsetY ||
        settings.revision != previousSettings_.revision;

    if (!changed) return;

    if (firstFrame_ || settings.revision != previousSettings_.revision) {
        auto &display = DisplayDriver::instance();
        if (display.setBrightness(settings.brightness) != ESP_OK) {
            ESP_LOGE(kTag, "Failed to apply brightness");
            return;
        }
        if (display.setOrientation(settings.mirrorHud, settings.rotateDisplay) != ESP_OK) {
            ESP_LOGE(kTag, "Failed to apply orientation");
            return;
        }
    }

    if (state.navigationActive != previous_.navigationActive) {
        for (auto &text : marqueeText_) text[0] = 0;
    }
    marqueeActive_ = false;
    firstFrame_ = false;
    previous_ = state;
    previousSettings_ = settings;
    previousSystemStatus_ = systemStatus;

    Canvas canvas(buffer_, layout::Width, layout::Height);
    canvas.clear(colors::Black);
    canvas.setTranslation(settings.offsetX, settings.offsetY);

    // Preserve the previous vertical placement for utility screens.
    if (layout::Height > 170 && (systemStatus.visible || !state.connected ||
        !state.hasProducerState))
        canvas.setTranslation(settings.offsetX, settings.offsetY + (layout::Height - 170) / 2);

    if (systemStatus.visible) {
        renderSystemStatus(canvas, systemStatus, settings);
        DisplayDriver::instance().drawRegion(layout::Full, buffer_);
        return;
    }

    if (!state.connected || !state.hasProducerState) {
        renderWaiting(canvas);
        DisplayDriver::instance().drawRegion(layout::Full, buffer_);
        return;
    }

    if (!state.navigationActive) {
        renderFreeDriving(canvas, state, settings);
        DisplayDriver::instance().drawRegion(layout::Full, buffer_);
        return;
    }

    if (layout::Height == 240) {
        renderNavigation(canvas, state, settings);
        DisplayDriver::instance().drawRegion(layout::Full, buffer_);
        return;
    }

    const bool overspeed = isOverspeed(state, settings);
    const AlertState alert = primaryAlert(state);

    if (isDualSpeedLimit(state)) {
        // Case 3: Dual speed limits (Max limit on left, Min limit on right, Alert/Maneuver in center)
        renderSpeedLimitSign(canvas, 65, 85, 55, state.speedLimitKmh);
        renderMinSpeedSign(canvas, 255, 85, 55, state.minimumSpeedKmh);

        if (hasAlert(alert)) {
            const assets::ColorBitmap *bitmap = alertBitmap(alert, false);
            drawAlertIcon(canvas, 160 - (bitmap ? bitmap->width / 2 : 13), 48,
                          alert, false);
            char distanceStr[16];
            formatDistance(alert.distanceM, distanceStr, sizeof(distanceStr));
            canvas.fontText(120, 84, distanceStr, assets::kTextSmall, colors::White, 80, true);
        } else if (hasManeuver(state)) {
            const assets::AlphaMask *mask = maneuverAsset(state.maneuver);
            if (mask && mask->alpha) {
                canvas.alphaMask(160 - mask->width / 2, 40, *mask, colors::White);
            } else {
                drawTurnFallback(canvas, 130, 40, state.maneuver);
            }
            char distanceStr[16];
            formatDistance(state.maneuverDistanceM, distanceStr, sizeof(distanceStr));
            canvas.fontText(120, 105, distanceStr, assets::kTextSmall, colors::White, 80, true);
        } else {
            // Cruising dual limits: show current speed in center
            char speedStr[12];
            std::snprintf(speedStr, sizeof(speedStr), "%d", std::clamp(state.speedKmh, 0, 999));
            const uint16_t color = overspeed ? colors::Red : colors::White;
            canvas.fontText(120, 58, speedStr, assets::kTextLarge, color, 80, true);
            canvas.fontText(120, 92, "km/h", assets::kTextSmall, colors::Muted, 80, true);
        }
    } else {
        // Standard 2-Zone Mode (Case 1, Case 2, Cruising)
        const bool alertActive = hasAlert(alert);
        const bool maneuverActive = hasManeuver(state);

        // Right Zone: Speed limit sign or current speed
        if (state.speedLimitKmh > 0) {
            renderSpeedLimitSign(canvas, 236, 85, 66, state.speedLimitKmh);
        } else if (alertActive || maneuverActive) {
            // No speed limit sign, but left zone is occupied: right zone shows speed
            renderSpeedSide(canvas, state.speedKmh, false, 240, 85);
        }

        // Left Zone: Priority: Alert -> Maneuver -> Current Speed
        if (alertActive) {
            // Case 1: Alert + Distance
            renderAlertSide(canvas, alert, 160);
        } else if (maneuverActive) {
            // Case 2: Maneuver + Distance
            renderManeuverSide(canvas, state.maneuver, state.roundaboutExit,
                               state.maneuverDistanceM, 160);
        } else {
            // Cruising: No alert, no maneuver
            if (state.speedLimitKmh > 0) {
                renderSpeedSide(canvas, state.speedKmh, overspeed, 80, 85);
            } else {
                // Neither speed limit nor alert: center the vehicle speed
                renderSpeedSide(canvas, state.speedKmh, false, 160, 85);
            }
        }
    }

    DisplayDriver::instance().drawRegion(layout::Full, buffer_);
}

}  // namespace waze_hud
