#include "display/display_driver.h"

#include "driver/gpio.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_lcd_ili9341.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "sdkconfig.h"
#include <algorithm>

namespace waze_hud {
namespace {
constexpr char kTag[] = "DISPLAY";
#ifdef CONFIG_WAZE_HUD_LCD_INVERT
constexpr bool kInvert = true;
#else
constexpr bool kInvert = false;
#endif
#ifdef CONFIG_WAZE_HUD_LCD_BGR
constexpr bool kBgr = true;
#else
constexpr bool kBgr = false;
#endif
#ifdef CONFIG_WAZE_HUD_LCD_BACKLIGHT_ACTIVE_LOW
constexpr bool kBacklightActiveLow = true;
#else
constexpr bool kBacklightActiveLow = false;
#endif
constexpr int kPanelHeight = 240;
constexpr int kHudTop = (kPanelHeight - layout::Height) / 2;
constexpr int kStripeRows = 10;
uint16_t *transferBuffer = nullptr;

// ES3N28P IPS panel settings from LCDWIKI's ILI9341V_Init.txt.
const ili9341_lcd_init_cmd_t kInitCommands[] = {
    {0xCF, (uint8_t[]){0x00, 0xC1, 0x30}, 3, 0},
    {0xED, (uint8_t[]){0x64, 0x03, 0x12, 0x81}, 4, 0},
    {0xE8, (uint8_t[]){0x85, 0x00, 0x78}, 3, 0},
    {0xCB, (uint8_t[]){0x39, 0x2C, 0x00, 0x34, 0x02}, 5, 0},
    {0xF7, (uint8_t[]){0x20}, 1, 0},
    {0xEA, (uint8_t[]){0x00, 0x00}, 2, 0},
    {0xC0, (uint8_t[]){0x13}, 1, 0},
    {0xC1, (uint8_t[]){0x13}, 1, 0},
    {0xC5, (uint8_t[]){0x22, 0x35}, 2, 0},
    {0xC7, (uint8_t[]){0xBD}, 1, 0},
    {0xB6, (uint8_t[]){0x0A, 0xA2}, 2, 0},
    {0xF6, (uint8_t[]){0x01, 0x30}, 2, 0},
    {0xB1, (uint8_t[]){0x00, 0x1B}, 2, 0},
    {0xF2, (uint8_t[]){0x00}, 1, 0},
    {0x26, (uint8_t[]){0x01}, 1, 0},
    {0xE0, (uint8_t[]){0x0F,0x35,0x31,0x0B,0x0E,0x06,0x49,0xA7,0x33,0x07,0x0F,0x03,0x0C,0x0A,0x00}, 15, 0},
    {0xE1, (uint8_t[]){0x00,0x0A,0x0F,0x04,0x11,0x08,0x36,0x58,0x4D,0x07,0x10,0x0C,0x32,0x34,0x0F}, 15, 0},
    {0x11, nullptr, 0, 120},
};

bool onTransferDone(esp_lcd_panel_io_handle_t, esp_lcd_panel_io_event_data_t *, void *ctx) {
    BaseType_t wake = pdFALSE;
    xSemaphoreGiveFromISR(static_cast<SemaphoreHandle_t>(ctx), &wake);
    return wake == pdTRUE;
}

esp_err_t sendStripe(esp_lcd_panel_handle_t panel, SemaphoreHandle_t done,
                     int x, int y, int width, int rows) {
    ESP_RETURN_ON_ERROR(esp_lcd_panel_draw_bitmap(panel, x, y, x + width, y + rows,
                                                transferBuffer), kTag, "LCD transfer failed");
    ESP_RETURN_ON_FALSE(xSemaphoreTake(done, pdMS_TO_TICKS(500)) == pdTRUE,
                        ESP_ERR_TIMEOUT, kTag, "LCD transfer timed out");
    return ESP_OK;
}
}  // namespace

DisplayDriver &DisplayDriver::instance() {
    static DisplayDriver driver;
    return driver;
}

esp_err_t DisplayDriver::init() {
    ESP_LOGI(kTag, "Initializing ILI9341 SPI LCD (ES3N28P), HUD centered at 320x170");
    ESP_RETURN_ON_FALSE(CONFIG_WAZE_HUD_LCD_SCLK >= 0 && CONFIG_WAZE_HUD_LCD_MOSI >= 0 &&
                        CONFIG_WAZE_HUD_LCD_CS >= 0 && CONFIG_WAZE_HUD_LCD_DC >= 0,
                        ESP_ERR_INVALID_ARG, kTag, "LCD GPIO wiring is not configured");
    const auto backlight = static_cast<gpio_num_t>(CONFIG_WAZE_HUD_LCD_BACKLIGHT);
    if (backlight >= 0) {
        gpio_config_t config{};
        config.pin_bit_mask = 1ULL << backlight;
        config.mode = GPIO_MODE_OUTPUT;
        ESP_RETURN_ON_ERROR(gpio_config(&config), kTag, "Backlight GPIO failed");
        ESP_RETURN_ON_ERROR(gpio_set_level(backlight, kBacklightActiveLow),
                            kTag, "Backlight off failed");
    }
    auto done = xSemaphoreCreateBinary();
    ESP_RETURN_ON_FALSE(done, ESP_ERR_NO_MEM, kTag, "Transfer semaphore allocation failed");
    transferDone_ = done;
    transferBuffer = static_cast<uint16_t *>(heap_caps_malloc(
        layout::Width * kStripeRows * sizeof(uint16_t), MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL));
    ESP_RETURN_ON_FALSE(transferBuffer, ESP_ERR_NO_MEM, kTag, "SPI buffer allocation failed");

    spi_bus_config_t bus{};
    bus.sclk_io_num = static_cast<gpio_num_t>(CONFIG_WAZE_HUD_LCD_SCLK);
    bus.mosi_io_num = static_cast<gpio_num_t>(CONFIG_WAZE_HUD_LCD_MOSI);
    bus.miso_io_num = GPIO_NUM_NC;
    bus.quadwp_io_num = GPIO_NUM_NC;
    bus.quadhd_io_num = GPIO_NUM_NC;
    bus.max_transfer_sz = layout::Width * kStripeRows * sizeof(uint16_t);
    ESP_RETURN_ON_ERROR(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO), kTag, "SPI bus failed");

    esp_lcd_panel_io_spi_config_t ioConfig{};
    ioConfig.cs_gpio_num = static_cast<gpio_num_t>(CONFIG_WAZE_HUD_LCD_CS);
    ioConfig.dc_gpio_num = static_cast<gpio_num_t>(CONFIG_WAZE_HUD_LCD_DC);
    ioConfig.pclk_hz = 20 * 1000 * 1000;
    ioConfig.trans_queue_depth = 1;
    ioConfig.lcd_cmd_bits = 8;
    ioConfig.lcd_param_bits = 8;
    ioConfig.on_color_trans_done = onTransferDone;
    ioConfig.user_ctx = done;
    esp_lcd_panel_io_handle_t io = nullptr;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_spi(SPI2_HOST, &ioConfig, &io), kTag, "SPI LCD IO failed");
    io_ = io;

    ili9341_vendor_config_t vendor{};
    vendor.init_cmds = kInitCommands;
    vendor.init_cmds_size = sizeof(kInitCommands) / sizeof(kInitCommands[0]);
    esp_lcd_panel_dev_config_t config{};
    config.reset_gpio_num = static_cast<gpio_num_t>(CONFIG_WAZE_HUD_LCD_RESET);
    config.rgb_ele_order = kBgr ? LCD_RGB_ELEMENT_ORDER_BGR : LCD_RGB_ELEMENT_ORDER_RGB;
    config.bits_per_pixel = 16;
    config.vendor_config = &vendor;
    esp_lcd_panel_handle_t panel = nullptr;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_ili9341(io, &config, &panel), kTag, "ILI9341 driver failed");
    panel_ = panel;
    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(panel), kTag, "LCD reset failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(panel), kTag, "LCD init failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_invert_color(panel, kInvert), kTag, "LCD inversion failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_swap_xy(panel, true), kTag, "LCD landscape failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_mirror(panel, true, false), kTag, "LCD mirror failed");
    std::fill(transferBuffer, transferBuffer + layout::Width * kStripeRows, uint16_t{0});
    for (int y = 0; y < kPanelHeight; y += kStripeRows)
        ESP_RETURN_ON_ERROR(sendStripe(panel, done, 0, y, layout::Width, kStripeRows),
                            kTag, "LCD clear failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_disp_on_off(panel, true), kTag, "LCD display on failed");

    if (backlight >= 0) {
        // Release the startup GPIO output reservation before LEDC takes ownership.
        ESP_RETURN_ON_ERROR(gpio_reset_pin(backlight), kTag, "Backlight handoff failed");
        ledc_timer_config_t timer{};
        timer.speed_mode = LEDC_LOW_SPEED_MODE;
        timer.timer_num = LEDC_TIMER_0;
        timer.duty_resolution = LEDC_TIMER_10_BIT;
        timer.freq_hz = 5000;
        timer.clk_cfg = LEDC_AUTO_CLK;
        ESP_RETURN_ON_ERROR(ledc_timer_config(&timer), kTag, "Backlight timer failed");
        ledc_channel_config_t channel{};
        channel.gpio_num = backlight;
        channel.speed_mode = LEDC_LOW_SPEED_MODE;
        channel.channel = LEDC_CHANNEL_0;
        channel.timer_sel = LEDC_TIMER_0;
        channel.duty = kBacklightActiveLow ? 1023 : 0;
        ESP_RETURN_ON_ERROR(ledc_channel_config(&channel), kTag, "Backlight PWM failed");
    }
    ready_ = true;
    ESP_LOGI(kTag, "Display ready at 320x240 landscape, HUD y=%d", kHudTop);
    return ESP_OK;
}

esp_err_t DisplayDriver::drawRegion(const Rect &region, const uint16_t *pixels) {
    ESP_RETURN_ON_FALSE(ready_ && panel_ && pixels, ESP_ERR_INVALID_STATE, kTag, "Display unavailable");
    ESP_RETURN_ON_FALSE(region.x >= 0 && region.y >= 0 && region.width > 0 && region.height > 0 &&
                        region.x + region.width <= layout::Width && region.y + region.height <= layout::Height,
                        ESP_ERR_INVALID_ARG, kTag, "Invalid HUD region");
    for (int row = 0; row < region.height; row += kStripeRows) {
        const int rows = std::min(kStripeRows, region.height - row);
        const uint16_t *source = pixels + row * region.width;
        // Renderer buffers are host-endian RGB565; ILI9341 SPI expects MSB first.
        for (int i = 0; i < rows * region.width; ++i)
            transferBuffer[i] = __builtin_bswap16(source[i]);
        ESP_RETURN_ON_ERROR(sendStripe(static_cast<esp_lcd_panel_handle_t>(panel_),
                                        static_cast<SemaphoreHandle_t>(transferDone_),
                                        region.x, region.y + row + kHudTop, region.width, rows),
                            kTag, "HUD region transfer failed");
    }
    return ESP_OK;
}

esp_err_t DisplayDriver::setBrightness(uint8_t percent) {
    ESP_RETURN_ON_FALSE(ready_, ESP_ERR_INVALID_STATE, kTag, "Display unavailable");
    if (CONFIG_WAZE_HUD_LCD_BACKLIGHT < 0) return ESP_OK;
    uint32_t duty = 1023U * std::min<uint8_t>(percent, 100) / 100U;
    if (kBacklightActiveLow) duty = 1023U - duty;
    ESP_RETURN_ON_ERROR(ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, duty), kTag, "Brightness failed");
    return ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
}

esp_err_t DisplayDriver::setOrientation(bool mirrored, bool rotated180) {
    ESP_RETURN_ON_FALSE(ready_, ESP_ERR_INVALID_STATE, kTag, "Display unavailable");
    return esp_lcd_panel_mirror(static_cast<esp_lcd_panel_handle_t>(panel_),
                                !rotated180, rotated180 != mirrored);
}
}  // namespace waze_hud
