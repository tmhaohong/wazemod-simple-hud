#pragma once

#include <cstdint>
#include "sdkconfig.h"

namespace waze_hud {

struct Rect {
    int16_t x;
    int16_t y;
    int16_t width;
    int16_t height;
};

namespace layout {
constexpr int Width = 320;
#if CONFIG_WAZE_HUD_LCD_ILI9341
constexpr int Height = 240;
#else
constexpr int Height = 170;
#endif
constexpr Rect Full{0, 0, Width, Height};
constexpr int MaxRegionPixels = Width * Height;
}  // namespace layout

}  // namespace waze_hud
