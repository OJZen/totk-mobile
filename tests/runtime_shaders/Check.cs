// Runs the on-device shader compiler over a scratch copy of work/shaders; tests/runtime_shaders/compare.py then
// compares its output with the libraries the Python pipeline prepared.
//   all <shaders> <scratch>      translate and build every indexed shader and variant request
//   captures <shaders> <scratch> re-index the runtime captures from a copy of the index without them
//   pending <scratch> <library>  the runtime entry point over a prepared scratch copy
using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.Json.Nodes;

static class RuntimeShadersCheck
{
    static int Main(string[] args)
    {
        string bridge = Path.GetFullPath("work/build/runtime-shaders/libzelda_spirv.dylib");
        NativeLibrary.SetDllImportResolver(typeof(RuntimeShaders).Assembly, (name, assembly, path) =>
            name == "zelda_spirv" ? NativeLibrary.Load(bridge) : IntPtr.Zero);
        var compiler = typeof(RuntimeShaders);
        T Call<T>(string name, params object[] arguments) =>
            (T)compiler.GetMethod(name, BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic).Invoke(null, arguments);
        switch (args[0])
        {
            case "all":
            {
                string shaders = args[1], scratch = args[2];
                var records = (JsonArray)JsonNode.Parse(File.ReadAllText(Path.Combine(shaders, "index.json")));
                var names = Directory.GetFiles(Path.Combine(shaders, "variants"), "*.json").Select(p => Path.GetFileNameWithoutExtension(p));
                var entries = RuntimeShaders.ShaderEntries(records, Path.Combine(shaders, "variants"), names);
                Directory.CreateDirectory(scratch);
                Directory.CreateSymbolicLink(Path.Combine(scratch, "code"), Path.GetFullPath(Path.Combine(shaders, "code")));
                RuntimeShaders.Translate(scratch, entries);
                var failures = RuntimeShaders.Build(scratch, Path.Combine(scratch, "b-check"), entries);
                File.WriteAllText(Path.Combine(scratch, "build-failures.json"), failures.ToJsonString() + "\n");
                return 0;
            }
            case "captures":
            {
                string shaders = args[1], scratch = args[2];
                var records = (JsonArray)JsonNode.Parse(File.ReadAllText(Path.Combine(shaders, "index.json")));
                var kept = new JsonArray(records.Where(r => !((string)r["source"]).StartsWith("runtime-")).Select(r => r.DeepClone()).ToArray());
                Directory.CreateDirectory(Path.Combine(scratch, "code"));
                Directory.CreateDirectory(Path.Combine(scratch, "source"));
                foreach (string capture in Directory.GetFiles(Path.Combine(shaders, "source"), "runtime-*.nvn"))
                    File.Copy(capture, Path.Combine(scratch, "source", Path.GetFileName(capture)));
                var added = Call<System.Collections.Generic.HashSet<string>>("IndexCaptures", scratch, kept);
                Console.WriteLine($"captures re-indexed: {added.Count}");
                File.WriteAllText(Path.Combine(scratch, "index.json"), kept.ToJsonString() + "\n");
                return 0;
            }
            case "pending":
                Console.WriteLine($"blocked: {RuntimeShaders.Compile(args[1], args[2])}");
                return 0;
        }
        return 2;
    }
}
