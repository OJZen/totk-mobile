// SPIRV-Cross's own command line built into the app: compile_iteration() with the arguments script/build_shaders.py
// passes (--msl --msl-version 20300 --msl-argument-buffers --msl-texture-buffer-native --msl-pad-fragment-output), so
// libraries prepared on the device match the Mac tool's output.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

// The command line exits on a few malformed inputs; inside the game those fail the one compile instead.
#define exit(code) throw std::runtime_error("SPIRV-Cross command line stopped")
#define main spirv_cross_command_line
#include "main.cpp"
#undef main
#undef exit

extern "C" int zelda_spirv_msl(const void *spirv, size_t bytes, char **output)
{
    std::string text;
    int status = 0;
    try
    {
        // read_spirv_file() keeps whole words and stops on an empty module.
        std::vector<uint32_t> words(bytes / sizeof(uint32_t));
        if (words.empty()) throw std::runtime_error("empty SPIR-V module");
        memcpy(words.data(), spirv, words.size() * sizeof(uint32_t));
        CLIArguments args;
        args.msl = true;
        args.msl_version = 20300;
        args.set_msl_version = true;
        args.msl_argument_buffers = true;
        args.msl_texture_buffer_native = true;
        args.msl_pad_fragment_output = true;
        text = compile_iteration(args, std::move(words));
    }
    catch (const std::exception &e)
    {
        text = std::string("SPIRV-Cross threw an exception: ") + e.what() + "\n";
        status = 1;
    }
    *output = strdup(text.c_str());
    return *output ? status : 1;
}

extern "C" void zelda_spirv_free(char *text) { free(text); }
