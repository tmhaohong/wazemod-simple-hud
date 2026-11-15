if(CONFIG_WAZE_HUD_LCD_ILI9341)
    set(HUD_DISPLAY_SOURCE "display/display_driver_ili9341.cpp")
else()
    set(HUD_DISPLAY_SOURCE "display/display_driver.cpp")
endif()

idf_component_register(
    SRCS
        "app_main.cpp"
        "bluetooth/ble_transport.cpp"
        "protocol/hlp_core.c"
        "protocol/hlp_protocol.cpp"
        "protocol/hlp_decoder.cpp"
        "state/hud_state_store.cpp"
        "${HUD_DISPLAY_SOURCE}"
        "display/hud_renderer.cpp"
        "display/bitmap_font.cpp"
        "assets/generated_assets.cpp"
        "config/device_config.cpp"
        "system/system_status.cpp"
    INCLUDE_DIRS "."
    REQUIRES bt esp_adc esp_lcd esp_driver_spi esp_driver_gpio esp_driver_ledc esp_hw_support nvs_flash
)

target_compile_options(${COMPONENT_LIB} PRIVATE
    $<$<COMPILE_LANGUAGE:CXX>:-std=gnu++17>
    -Wall -Wextra -Werror=return-type
)

target_compile_definitions(${COMPONENT_LIB} PRIVATE
    WAZE_HUD_FIRMWARE_VERSION="${PROJECT_VER}"
)
