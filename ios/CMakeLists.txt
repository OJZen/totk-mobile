cmake_minimum_required(VERSION 3.24)
project(ZeldaMobile LANGUAGES C OBJC ASM)
get_filename_component(ROOT "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
if(NOT ZELDA_IOS_TEAM)
  message(FATAL_ERROR "Use script/ios.sh with ZELDA_IOS_TEAM set")
endif()
set(CORE "${ROOT}/core")
set(GFX "${CORE}/gfx")
set(HOST "${ROOT}/host")
set(GEN "${ROOT}/work/build")
set(IOS_WORK "${ROOT}/work/ios")
set(VPX "${IOS_WORK}/deps/install")
set(ZELDA_IOS_BUNDLE "local.zelda-mobile.ios" CACHE STRING "Development bundle identifier")
add_executable(ZeldaMobile MACOSX_BUNDLE
  main.m "${IOS_WORK}/embed.S"
  "${HOST}/probe.c" "${HOST}/codec.c" "${HOST}/kernel.c" "${HOST}/threads.c"
  "${HOST}/timeline.c" "${HOST}/trace_writer.c" "${HOST}/services.c"
  "${HOST}/audio.c" "${HOST}/audio_mixer.c" "${HOST}/filesystem.c"
  "${HOST}/shader_prep.m" "${HOST}/input.c" "${HOST}/vibration.c" "${HOST}/swkbd.c"
  "${HOST}/movie.c" "${HOST}/movie_video.c" "${HOST}/font_watch.c"
  "${HOST}/input_script.c" "${HOST}/gamepad.m" "${HOST}/performance.c"
  "${HOST}/game_timing.c" "${HOST}/settings.m" "${HOST}/display.c"
  "${CORE}/host/loader.c" "${CORE}/host/entry.S"
  "${GEN}/hle_nv.c" "${GEN}/hle_nvn.c"
  "${GFX}/nvn/nvn_mtl.m" "${GFX}/nvn/nvn_refl.m" "${GFX}/nvn/nvn_tex.c"
  "${HOST}/nvn_dirty.c" "${GFX}/nvn/nvn_thunks.S" "${GFX}/nvn/nvn_entry.S"
  "${GFX}/tex/nvn_fmt_mtl.c" "${GFX}/tex/nvn_bcdec.c"
  "${GFX}/tex/nvn_deswizzle.c" "${GFX}/tex/nvn_deswizzle_gpu.m")
set_source_files_properties("${HOST}/probe.c" PROPERTIES COMPILE_DEFINITIONS "main=zelda_main")
target_include_directories(ZeldaMobile PRIVATE "${HOST}" "${GEN}" "${CORE}/host"
  "${GFX}" "${GFX}/nvn" "${GFX}/nvn/include" "${VPX}/include" "${VPX}/include/opus")
target_compile_options(ZeldaMobile PRIVATE -O2 -g -Wall -Wextra -Wno-deprecated-declarations
  -mcpu=apple-m1 -ivfsoverlay "${GEN}/graphics-overlay.json"
  "$<$<COMPILE_LANGUAGE:OBJC>:-fobjc-arc>")
target_link_libraries(ZeldaMobile PRIVATE "${VPX}/lib/libvpx.a" "${VPX}/lib/libopus.a"
  "-framework UIKit" "-framework Foundation" "-framework QuartzCore" "-framework CoreGraphics"
  "-framework CoreVideo" "-framework Metal" "-framework MetalFX"
  "-framework ImageIO" "-framework UniformTypeIdentifiers" "-framework AudioToolbox"
  "-framework AVFAudio" "-framework GameController" "-framework CoreHaptics")
foreach(MODULE main subsdk0 sdk)
  target_link_options(ZeldaMobile PRIVATE "-Wl,-segprot,__DM${MODULE}W,rw,rw")
endforeach()
file(GLOB MODULE_TABLES "${ROOT}/work/patched/*.tsv")
target_sources(ZeldaMobile PRIVATE ${MODULE_TABLES})
set_source_files_properties(${MODULE_TABLES} PROPERTIES MACOSX_PACKAGE_LOCATION "Resources/patched")
configure_file("/System/Library/Fonts/Supplemental/AppleGothic.ttf" "${IOS_WORK}/shared.ttf" COPYONLY)
target_sources(ZeldaMobile PRIVATE "${IOS_WORK}/shared.ttf")
set_source_files_properties("${IOS_WORK}/shared.ttf" PROPERTIES MACOSX_PACKAGE_LOCATION Resources)
set_target_properties(ZeldaMobile PROPERTIES
  MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_LIST_DIR}/Info.plist"
  MACOSX_BUNDLE_GUI_IDENTIFIER "${ZELDA_IOS_BUNDLE}"
  XCODE_ATTRIBUTE_PRODUCT_BUNDLE_IDENTIFIER "${ZELDA_IOS_BUNDLE}"
  XCODE_ATTRIBUTE_DEVELOPMENT_TEAM "${ZELDA_IOS_TEAM}"
  XCODE_ATTRIBUTE_CODE_SIGN_STYLE Automatic
  XCODE_ATTRIBUTE_CODE_SIGN_ENTITLEMENTS "${CMAKE_CURRENT_LIST_DIR}/ZeldaMobile.entitlements"
  XCODE_ATTRIBUTE_TARGETED_DEVICE_FAMILY "1,2"
  XCODE_ATTRIBUTE_DEAD_CODE_STRIPPING NO
  XCODE_ATTRIBUTE_ENABLE_USER_SCRIPT_SANDBOXING NO
  XCODE_ATTRIBUTE_ENABLE_BITCODE NO
  XCODE_ATTRIBUTE_CLANG_ENABLE_OBJC_ARC YES)
