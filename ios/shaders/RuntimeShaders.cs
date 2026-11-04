// On-device form of script/compile_runtime_shaders.py for hosts that cannot start the Mac tools (iOS): indexes new
// runtime captures, translates pending variant requests with the same translator (Program.cs, linked in), and builds
// their MSL libraries with the checks of script/build_shaders.py. The libraries directory names the prepared chain;
// tests/runtime_shaders compares this port with the Python pipeline over every prepared shader.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

static class RuntimeShaders
{
    const int PointSizeLimit = 0x610, TexBase = 0x168, SampBase = 0x568, ImgBase = 0x120;
    static readonly Dictionary<string, string> Labels = new() { ["vs"] = "vertex", ["ps"] = "pixel", ["cs"] = "compute", ["hs"] = "hull", ["ds"] = "domain", ["gs"] = "geometry" };
    static readonly string[] Stages = { "vs", "ps", "gs", "hs", "ds", "cs" };
    static readonly (int, int)[] Bank0 = { (0x110, 0x210), (0x310, 0x410), (0x510, 0x610) },
                                 Bank2 = { (0x20, 0x120), (0x168, 0x568), (0x568, 0x968) };
    static readonly JsonSerializerOptions Indented = new() { WriteIndented = true },
        Compact = new() { Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping };

    [DllImport("zelda_spirv", EntryPoint = "zelda_spirv_msl")]
    static extern unsafe int SpirvMsl(byte* spirv, nuint bytes, out nint output);
    [DllImport("zelda_spirv", EntryPoint = "zelda_spirv_free")]
    static extern void SpirvFree(nint text);

    [UnmanagedCallersOnly(EntryPoint = "zelda_runtime_shaders")]
    static unsafe int Entry(byte* shaders, byte* library)
    {
        try
        {
            Compile(Marshal.PtrToStringUTF8((nint)shaders), Marshal.PtrToStringUTF8((nint)library));
            return 0;
        }
        catch (Exception e)
        {
            Console.Error.WriteLine($"Runtime shader preparation failed: {e.GetType().Name}: {e.Message}");
            return 1;
        }
    }

    // compile_pending() with an unchanged translator. Returns whether any requested shader stayed blocked.
    public static bool Compile(string shaders, string library)
    {
        var records = (JsonArray)JsonNode.Parse(File.ReadAllText(Path.Combine(shaders, "index.json")));
        var entries = Requested(shaders, library, records, IndexCaptures(shaders, records));
        Console.WriteLine($"Pending shaders: {entries.Count}");
        if (entries.Count == 0) return false;
        string failuresPath = Path.Combine(shaders, "build-failures.json");
        var previous = File.Exists(failuresPath) ? (JsonArray)JsonNode.Parse(File.ReadAllText(failuresPath)) : new JsonArray();
        Translate(shaders, entries);
        var current = Build(shaders, library, entries);
        // The failure list keeps earlier entries for shaders this run did not process.
        var processed = entries.Select(e => (string)e["key"]).ToHashSet();
        foreach (var failure in previous)
            if (!processed.Contains((string)failure["key"])) current.Add(failure.DeepClone());
        WriteAtomic(failuresPath, current.ToJsonString(Indented) + "\n");
        return entries.Count > 0 && current.Any(f => processed.Contains((string)f["key"]));
    }

    static void WriteAtomic(string path, string text)
    {
        string temporary = path + ".tmp";
        File.WriteAllText(temporary, text);
        File.Move(temporary, path, true);
    }

    static string Hex(ReadOnlySpan<byte> data) => Convert.ToHexString(data).ToLowerInvariant();

    // index_shaders.stage() for a checked runtime record.
    static (JsonObject, byte[]) Stage(byte[] control, byte[] code)
    {
        uint U(byte[] data, int offset) => offset >= 0 && offset + 4 <= data.Length ? BitConverter.ToUInt32(data, offset) : throw new InvalidDataException("shader archive range outside file");
        if (U(control, 0) != 0x98761234 || U(control, 4) != 1 || (U(control, 8) != 9 && U(control, 8) != 16) ||
            U(control, 0x20) < 0x748 || U(control, 0x20) > control.Length)
            throw new InvalidDataException("unsupported NVN shader control header");
        control = control[..(int)U(control, 0x20)];
        long programSize = U(control, 0x6f8), c1Size = U(control, 0x6fc), c1Offset = U(control, 0x700), total = U(control, 0x704),
             offset = U(control, 0x708), kind = U(control, 0x714);
        if (total != code.Length || kind >= Stages.Length || offset != (kind == 5 ? 0x100 : 0x30) || c1Offset < offset + programSize)
            throw new InvalidDataException("invalid shader bytecode layout");
        if (U(code, 0) != (kind == 5 ? 0x12345679u : 0x12345678u)) throw new InvalidDataException("invalid shader bytecode header");
        if (c1Offset + c1Size > code.Length || c1Offset > code.Length) throw new InvalidDataException("shader archive range outside file");
        byte[] c1 = code[(int)c1Offset..(int)(c1Offset + c1Size)], program = code[(int)offset..(int)c1Offset];
        var result = new JsonObject
        {
            ["key"] = Hex(SHA256.HashData(control.Concat(program).Concat(c1).ToArray()))[..16], ["control_sha256"] = Hex(SHA256.HashData(control)),
            ["stage"] = Stages[kind], ["control_bytes"] = control.Length, ["c1"] = Hex(c1),
        };
        if (kind == 5)
        {
            long x = U(control, 0x748), y = U(control, 0x74c), z = U(control, 0x750), shared = U(control, 0x754);
            if (Math.Min(x, Math.Min(y, z)) < 1 || x * y * z > 1024 || shared > 49152) throw new InvalidDataException("invalid compute shader workgroup");
            result["local"] = new JsonArray(x, y, z); result["shared"] = shared;
        }
        return (result, program);
    }

    // compile_runtime_shaders.index_captures(): append records of new runtime captures.
    static HashSet<string> IndexCaptures(string shaders, JsonArray records)
    {
        var known = new Dictionary<string, JsonObject>();
        foreach (JsonObject entry in records) known[(string)entry["key"]] = entry;
        var sources = records.Select(r => (string)r["source"]).ToHashSet();
        var added = new List<string>();
        var captures = Directory.Exists(Path.Combine(shaders, "source")) ? Directory.GetFiles(Path.Combine(shaders, "source"), "runtime-*.nvn") : [];
        foreach (string path in captures.OrderBy(p => p, StringComparer.Ordinal))
        {
            string name = Path.GetFileName(path);
            if (sources.Contains(name)) continue;
            byte[] data = File.ReadAllBytes(path);
            if (data.Length < 4 || BitConverter.ToUInt32(data, 0) != 0x98761234) throw new InvalidDataException("unsupported shader archive");
            if (data.Length < 0x24) throw new InvalidDataException("shader archive range outside file");
            int length = (int)BitConverter.ToUInt32(data, 0x20);
            if (length < 0x748) throw new InvalidDataException("invalid captured NVN shader");
            if (length > data.Length) throw new InvalidDataException("shader archive range outside file");
            var (entry, program) = Stage(data[..length], data[length..]);
            entry["code_sha256"] = Hex(SHA256.HashData(program));
            entry["source"] = name;
            string key = (string)entry["key"];
            if (known.TryGetValue(key, out var prior))
            {
                if (new[] { "stage", "code_sha256", "c1" }.Any(k => (string)prior[k] != (string)entry[k])) throw new InvalidDataException("shader control hash collision");
                continue;
            }
            File.WriteAllBytes(Path.Combine(shaders, "code", key + ".bin"), program);
            known[key] = entry;
            records.Add((JsonNode)entry);
            added.Add(key);
        }
        if (added.Count > 0)
        {
            WriteAtomic(Path.Combine(shaders, "index.json"), records.ToJsonString(Indented) + "\n");
            WriteAtomic(Path.Combine(shaders, "shader_map.tsv"), "package\tindex\tstage\tcontrol\n" + string.Concat(
                records.Select(e => $"{(string)e["source"]}\t0\t{Labels[(string)e["stage"]]}\t{(string)e["key"]}\n")));
        }
        Console.WriteLine($"Runtime records added: {added.Count}");
        return added.ToHashSet();
    }

    // build_shaders.shader_entries() over the index and the named variant requests.
    public static List<JsonObject> ShaderEntries(JsonArray records, string variants, IEnumerable<string> names)
    {
        var entries = records.Cast<JsonObject>().ToList();
        var bases = new Dictionary<string, JsonObject>();
        foreach (var entry in entries) bases[(string)entry["key"]] = entry;
        foreach (string stem in names.OrderBy(n => n, StringComparer.Ordinal))
        {
            var request = (JsonObject)JsonNode.Parse(File.ReadAllText(Path.Combine(variants, stem + ".json")));
            string baseKey = (string)request["base"];
            string stage = bases.TryGetValue(baseKey, out var b) ? (string)b["stage"] : null;
            int slots = stage == "vs" ? 16 : 8;
            JsonNode next = request["next"];
            bool integral = request["mask"] is JsonValue m && m.GetValueKind() == JsonValueKind.Number && m.TryGetValue(out long _);
            long mask = integral ? (long)request["mask"] : 0;
            if ((stage != "vs" && stage != "ps") || !integral || !((next != null ? 0 : 1) <= mask && mask < 1L << (2 * slots)) ||
                Enumerable.Range(0, slots).Any(i => ((mask >> (2 * i)) & 3) == 3) ||
                (next != null && (next.GetValueKind() != JsonValueKind.String || stage != "vs" ||
                                  !(bases.TryGetValue((string)next, out var n) && (string)n["stage"] == "ps"))))
                throw new InvalidDataException("invalid shader interface variant");
            string name = $"{baseKey}-{mask.ToString("x" + slots / 2)}";
            if (next != null) name = Hex(SHA256.HashData(Encoding.UTF8.GetBytes($"{baseKey}-{mask:x8}-{(string)next}")))[..16];
            if (stem != name || bases.ContainsKey(name)) throw new InvalidDataException("shader variant identity mismatch");
            var types = new JsonObject();
            for (int i = 0; i < slots; i++)
                if (((mask >> (2 * i)) & 3) != 0) types[i.ToString()] = ((mask >> (2 * i)) & 3) == 1 ? "uint" : "sint";
            var variant = (JsonObject)bases[baseKey].DeepClone();
            variant["key"] = stem; variant["base"] = baseKey; variant[stage == "vs" ? "attr" : "fout"] = types;
            if (next != null) variant["next"] = bases[(string)next].DeepClone();
            entries.Add(variant);
        }
        return entries;
    }

    // compile_runtime_shaders.requested(): variant requests without a library, and new records.
    static List<JsonObject> Requested(string shaders, string library, JsonArray records, HashSet<string> added)
    {
        var built = Directory.Exists(library) ? Directory.GetFiles(library, "*_b.metallib").Select(p => Path.GetFileName(p)[..^"_b.metallib".Length]).ToHashSet() : [];
        var variants = Directory.GetFiles(Path.Combine(shaders, "variants"), "*.json").Select(p => Path.GetFileName(p)[..^5]).ToList();
        string failures = Path.Combine(shaders, "build-failures.json");
        var blocked = File.Exists(failures) ? ((JsonArray)JsonNode.Parse(File.ReadAllText(failures)))
            .Where(f => (string)f["error"] != "SPIR-V translation missing").Select(f => (string)f["key"]).ToHashSet() : [];
        var missing = variants.Where(k => !built.Contains(k) && !blocked.Contains(k)).Concat(added.Where(k => !built.Contains(k))).ToHashSet();
        return ShaderEntries(records, Path.Combine(shaders, "variants"), missing.Where(k => !added.Contains(k)))
            .Where(e => missing.Contains((string)e["key"])).ToList();
    }

    static bool Valid(JsonObject entry, string output)
    {
        try
        {
            byte[] data = File.ReadAllBytes(Path.Combine(output, (string)entry["key"] + ".spv"));
            var metadata = JsonNode.Parse(File.ReadAllText(Path.Combine(output, (string)entry["key"] + ".res.json")));
            return data.Length >= 20 && data[0] == 3 && data[1] == 2 && data[2] == 0x23 && data[3] == 7 &&
                   (string)metadata["key"] == (string)entry["key"] && (string)metadata["stage"] == (string)entry["stage"];
        }
        catch (Exception e) when (e is IOException or JsonException or InvalidOperationException or NullReferenceException or UnauthorizedAccessException) { return false; }
    }

    // translate_shaders.translate() for the given entries, running the translator in process.
    public static void Translate(string shaders, List<JsonObject> entries)
    {
        string output = Path.Combine(shaders, "spv"), code = Path.Combine(shaders, "code");
        Directory.CreateDirectory(output);
        var jobs = new JsonArray();
        foreach (var entry in entries.Where(e => !Valid(e, output)))
        {
            var job = (JsonObject)entry.DeepClone();
            job["code"] = Path.Combine(code, ((string)entry["base"] ?? (string)entry["key"]) + ".bin");
            job["debug"] = true;
            if (job["next"] is JsonObject next) next["code"] = Path.Combine(code, (string)next["key"] + ".bin");
            jobs.Add((JsonNode)job);
        }
        foreach (var job in jobs)
            foreach (string suffix in new[] { ".spv", ".res.json" }) File.Delete(Path.Combine(output, (string)job["key"] + suffix));
        string jobFile = Path.Combine(shaders, "jobs.json");
        if (jobs.Count > 0)
        {
            File.WriteAllText(jobFile, jobs.ToJsonString() + "\n");
            int result = SwshXlate.Main([jobFile, output]);
            if (result != 0 && result != 1) throw new InvalidOperationException("shader translator could not run");
            int invalid = jobs.Count(j => !Valid((JsonObject)j, output));
            if (invalid > 0) throw new InvalidOperationException($"{invalid} shaders could not be translated");
        }
        if (!entries.All(e => Valid(e, output))) throw new InvalidOperationException("SPIR-V output validation failed");
        File.Delete(jobFile);
        Console.WriteLine($"Validated SPIR-V shaders: {entries.Count}");
    }

    static unsafe (bool, string) Msl(byte[] spirv)
    {
        fixed (byte* data = spirv)
        {
            int status = SpirvMsl(data, (nuint)spirv.Length, out nint text);
            try { return (status == 0, Marshal.PtrToStringUTF8(text)); }
            finally { SpirvFree(text); }
        }
    }

    // build_shaders.build() for the given entries; returns this run's failures.
    public static JsonArray Build(string shaders, string library, List<JsonObject> entries)
    {
        string version = Path.GetFileName(library.TrimEnd('/'))[2..], spv = Path.Combine(shaders, "spv");
        Directory.CreateDirectory(library);
        var failures = new JsonArray();
        foreach (var entry in entries)
            if (Finish(entry, library, spv, version) is JsonObject failure) failures.Add((JsonNode)failure);
        Console.WriteLine($"MSL libraries: {entries.Count - failures.Count} blocked: {failures.Count} chain: {version}");
        return failures;
    }

    static JsonObject Finish(JsonObject entry, string target, string spv, string version)
    {
        string key = (string)entry["key"], stage = (string)entry["stage"];
        string metadata = Path.Combine(target, key + "_b.json"), library = Path.Combine(target, key + "_b.metallib");
        if (File.Exists(metadata) && File.Exists(library)) return null;
        string resultFile = Path.Combine(spv, key + ".res.json");
        if (!File.Exists(resultFile)) return new JsonObject { ["key"] = key, ["error"] = "SPIR-V translation missing" };
        byte[] module = File.ReadAllBytes(Path.Combine(spv, key + ".spv"));
        var (ok, source) = Msl(module);
        if (!ok && stage == "vs" && source.Contains("Subgroup builtins are not available"))
        {
            // Metal vertex functions have no lane builtins; ballot-only use lowers to a one-invocation subgroup.
            byte[] lowered = SpirvSubgroup.Lower(module);
            if (lowered != null) (ok, source) = Msl(lowered);
        }
        if (!ok) return new JsonObject { ["key"] = key, ["error"] = source.Length > 500 ? source[..500] : source };
        if (stage == "vs")
        {
            try { source = PointSizeUniform(source); }
            catch (InvalidDataException error) { return new JsonObject { ["key"] = key, ["error"] = error.Message }; }
        }
        var resources = (JsonObject)JsonNode.Parse(File.ReadAllText(resultFile));
        var bindings = PointerStorage(Reflect(stage, source, resources), resources);
        string[] resolved = { "tex", "samp", "samp_tex", "img", "tbuf", "sbuf", "ubo_ptr" };
        var missing = bindings.Cast<JsonObject>().Where(b => (string)b["kind"] == "?" || (resolved.Contains((string)b["kind"]) && b["reg"] is null))
            .Select(b => (JsonNode)(string)b["msl_name"]).ToArray();
        if (missing.Length > 0) return new JsonObject { ["key"] = key, ["error"] = "unresolved resource bindings", ["bindings"] = new JsonArray(missing) };
        var function = Regex.Match(source, @"(vertex|fragment|kernel)\s+\S+\s+(\w+)\s*\(");
        if (!function.Success) return new JsonObject { ["key"] = key, ["error"] = "Metal entry point missing" };
        var reads = DriverReads(source);
        var unfillable = Unfillable(reads, stage);
        if (Regex.IsMatch(source, $@"_c0\)\._m0\[{PointSizeLimit / 16}u?\](?!\.x\b)")) unfillable.Add("c0+0x610 beyond the point-size limit component");
        if (unfillable.Count > 0) return new JsonObject { ["key"] = key, ["error"] = "unsupported driver constant reads", ["reads"] = new JsonArray(unfillable.Select(r => (JsonNode)r).ToArray()) };
        var data = new JsonObject
        {
            ["key"] = key + "_b", ["route"] = "B", ["chain"] = version, ["stage"] = stage, ["source"] = entry["source"]?.DeepClone(),
            ["entry"] = function.Groups[2].Value, ["msl_entry_kind"] = function.Groups[1].Value, ["bindings"] = bindings, ["c1"] = entry["c1"]?.DeepClone(),
            ["threadgroup_size"] = entry["local"]?.DeepClone(), ["notes"] = resources["notes"]?.DeepClone(), ["drv_reads"] = reads, ["drv_unfillable"] = new JsonArray(),
            ["vertex_inputs"] = new JsonArray(Regex.Matches(source, @"(\w+)\s+\w+\s*\[\[attribute\((\d+)\)\]\]")
                .Select(m => (JsonNode)new JsonObject { ["location"] = int.Parse(m.Groups[2].Value), ["msl_type"] = m.Groups[1].Value }).ToArray()),
            ["raster"] = RasterFacts(stage, source), ["chain_metal"] = "macOS runtime source compiler",
        };
        // Metadata first: the game treats an existing library as ready.
        WriteAtomic(metadata, data.ToJsonString(Compact) + "\n");
        WriteAtomic(library, source);
        return null;
    }

    static string PointSizeUniform(string source)
    {
        // The translator's default point size becomes the NVN fixed size; a later programmable write still overrides it.
        const string assignment = "out.gl_PointSize = 1.0;";
        var entry = Regex.Match(source, @"vertex\s+\w+\s+\w+\s*\(");
        if (Count(source, assignment) != 1 || !entry.Success || source.Contains("[[buffer(15)]]"))
            throw new InvalidDataException("unsupported vertex point-size layout");
        int offset = entry.Index + entry.Length;
        string parameter = "constant float& zelda_point_size [[buffer(15)]]";
        if (source[offset..].TrimStart()[0] != ')') parameter += ", ";
        source = source[..offset] + parameter + source[offset..];
        return source.Replace(assignment, "out.gl_PointSize = zelda_point_size;");
    }

    static int Count(string text, string part)
    {
        int count = 0;
        for (int i = text.IndexOf(part, StringComparison.Ordinal); i >= 0; i = text.IndexOf(part, i + part.Length, StringComparison.Ordinal)) count++;
        return count;
    }

    static JsonArray PointerStorage(JsonArray bindings, JsonObject resources)
    {
        // Read-only global memory whose base address the shader loads from a game uniform buffer (sbuf_cb).
        var ordered = resources["sbufs"].AsArray().Cast<JsonObject>().OrderBy(b => (long)b["binding"]).ToList();
        foreach (JsonObject binding in bindings)
        {
            if ((string)binding["kind"] != "sbuf" || binding["reg"] is not null || (long)binding["index"] >= ordered.Count) continue;
            var source = ordered[(int)(long)binding["index"]];
            long slot = (long)source["cb_slot"], offset = (long)source["cb_offset"] * 4;
            if (3 <= slot && slot <= 16 && ((long)source["flags"] & 1) == 0 && offset % 8 == 0 && offset + 8 <= 0x10000)
            {
                binding["kind"] = "sbuf_cb"; binding["reg"] = slot - 3; binding["offset"] = offset; binding["rw"] = false;
            }
        }
        return bindings;
    }

    // dmsh_build.decode_tex() with build_shaders.reflection_helpers()'s bindless uniform-table addition.
    static (long?, long?, long)? DecodeTexture(string name)
    {
        (long?, long?, long)? Separate()
        {
            var m = Regex.Match(name, @"^[a-z]+_[ti]\w*?_(?:cb(\d+)|tcb)_([0-9A-F]+)");
            if (!m.Success) return null;
            long slots = m.Groups[1].Success ? long.Parse(m.Groups[1].Value) : -1, h = Convert.ToInt64(m.Groups[2].Value, 16);
            long tb = slots >= 0 ? slots & 0xffff : 2, sb = slots >= 0 && (slots >> 16) != 0 ? (slots >> 16) - 1 : tb;
            long o0 = h & 0x3fff, o1 = (h >> 14) & 0x3fff, ty = h >> 28;
            long? Slot(long word, long baseOffset, long cb) => cb == 2 && word * 4 >= baseOffset && (word * 4 - baseOffset) % 8 == 0 ? (word * 4 - baseOffset) / 8 : null;
            var words = ty != 0 ? new[] { o0, o1 } : new[] { o0 };
            if (Regex.IsMatch(name, @"^[a-z]+_i"))
                return (words.Where(x => ImgBase <= x * 4 && x * 4 < TexBase && Slot(x, ImgBase, tb) != null).Select(x => Slot(x, ImgBase, tb)).FirstOrDefault(), null, ty);
            long? t = words.Where(x => Slot(x, TexBase, tb) != null && x * 4 < SampBase).Select(x => Slot(x, TexBase, tb)).FirstOrDefault();
            long? s = words.Where(x => x * 4 >= SampBase && Slot(x, SampBase, sb) != null).Select(x => Slot(x, SampBase, sb)).FirstOrDefault();
            return (t, s, ty);
        }
        var result = Separate();
        var table = Regex.Match(name, @"^[a-z]+_t\w*?_(?:cb2|tcb)_([0-9A-F]+)\z");
        if (result is { } r && r.Item1 == null && r.Item3 == 0 && table.Success)
        {
            long offset = Convert.ToInt64(table.Groups[1].Value, 16) * 4;
            if (0x20 <= offset && offset < 0x120 && (offset - 0x20) % 8 == 0) return ((offset - 0x20) / 8, null, 0);
        }
        return result;
    }

    static JsonNode Reg(long? value) => value is long v ? JsonValue.Create(v) : null;

    // dmsh_build.reflect(): argument buffer members → replay reflection (storage buffers marked rw by flag bit 0).
    static JsonArray Reflect(string stage, string msl, JsonObject res)
    {
        var body = Regex.Match(msl, @"struct spvDescriptorSetBuffer0\s*\{(.*?)\};", RegexOptions.Singleline);
        var binds = new JsonArray();
        foreach (Match m in Regex.Matches(body.Success ? body.Groups[1].Value : "", @"^\s*(.+?)\s+(\w+)\s*\[\[id\((\d+)\)\]\];", RegexOptions.Multiline))
        {
            string type = m.Groups[1].Value.Trim(), name = m.Groups[2].Value;
            var e = new JsonObject { ["msl_name"] = name, ["msl_type"] = type, ["arg_id"] = long.Parse(m.Groups[3].Value) };
            Match number;
            if (name == "support_buffer") e["kind"] = "support";
            else if ((number = Regex.Match(name, @"^[a-z]+_c(\d+)\z")).Success)
            {
                long n = long.Parse(number.Groups[1].Value);
                if (3 <= n && n <= 16) { e["kind"] = "cbuf"; e["reg"] = n - 3; }
                else { e["kind"] = n == 1 ? "c1" : "drvcb"; e["bank"] = n; }
            }
            else if ((number = Regex.Match(name, @"^[a-z]+_s(\d+)\z")).Success) { e["kind"] = "sbuf"; e["index"] = long.Parse(number.Groups[1].Value); }
            else if (name.EndsWith("Smplr", StringComparison.Ordinal))
            {
                // Handle type 0 combines texture and sampler: the sampler index comes from the texture slot's handle.
                var (t, s, ty) = DecodeTexture(name[..^5]) ?? throw new InvalidDataException($"cannot decode sampler {name}");
                if (ty != 0) { e["kind"] = "samp"; e["reg"] = Reg(s); } else { e["kind"] = "samp_tex"; e["reg"] = Reg(t); }
            }
            else if (DecodeTexture(name) is { } decoded)
            {
                e["kind"] = Regex.IsMatch(name, @"^[a-z]+_i") ? "img" : type.Contains("texture_buffer") ? "tbuf" : "tex";
                e["reg"] = Reg(decoded.Item1);
            }
            else e["kind"] = "?";
            binds.Add((JsonNode)e);
        }
        // Storage buffers: SPIR-V binding → c0 offset → NVN storage buffer slot (c0 base: vertex 0x110, compute 0x310, pixel 0x510).
        long? baseOffset = stage switch { "vs" => 0x110, "cs" => 0x310, "ps" => 0x510, _ => null };
        var sorted = res["sbufs"].AsArray().Cast<JsonObject>().OrderBy(b => (long)b["binding"]).ToList();
        foreach (JsonObject e in binds)
        {
            if ((string)e["kind"] != "sbuf") continue;
            long index = (long)e["index"];
            var b = index < sorted.Count ? sorted[(int)index] : null;
            long? off = b != null && (long)b["cb_slot"] == 0 ? (long)b["cb_offset"] * 4 : null;
            if (stage == "cs" && off is long o && 0x210 <= o && o < 0x310 && (o - 0x210) % 16 == 0) { e["kind"] = "ubo_ptr"; e["reg"] = (o - 0x210) / 16; }
            else e["reg"] = off is long p && baseOffset is long q && 0 <= p - q && p - q < 0x100 && (p - q) % 16 == 0 ? (p - q) / 16 : null;
            e["rw"] = b != null && ((long)b["flags"] & 1) != 0;
        }
        return binds;
    }

    // dmsh_build.drv_reads() over any stage prefix: constant c0 / c2 indices as byte offsets, others as dynamic.
    static JsonObject DriverReads(string source)
    {
        var output = new JsonObject();
        foreach (int bank in new[] { 0, 2 })
        {
            var offsets = new SortedSet<long>(); bool dynamic = false;
            foreach (Match m in Regex.Matches(source, $@"[a-z]+_c{bank}\)\._m0\[([^\]]+)\]"))
            {
                string index = m.Groups[1].Value.TrimEnd('u');
                if (index.Length > 0 && index.All(char.IsDigit)) offsets.Add(long.Parse(index) * 16);
                else dynamic = true;
            }
            if (offsets.Count > 0 || dynamic)
                output[bank.ToString()] = new JsonObject { ["vec4_offsets"] = new JsonArray(offsets.Select(o => (JsonNode)o).ToArray()), ["dynamic"] = dynamic };
        }
        return output;
    }

    static List<string> Unfillable(JsonObject reads, string stage)
    {
        var bad = new List<string>();
        foreach (var (bank, value) in reads)
        {
            // Compute uniform-table addresses and the vertex point-size limit are filled by the host.
            if ((bool)value["dynamic"]) bad.Add($"c{bank} 动态下标");
            foreach (long o in value["vec4_offsets"].AsArray().Select(v => (long)v))
            {
                if ((stage == "cs" && bank == "0" && 0x210 <= o && o < 0x310) || (stage == "vs" && bank == "0" && o == PointSizeLimit)) continue;
                if (!(bank == "0" ? Bank0 : Bank2).Any(r => r.Item1 <= o && o < r.Item2)) bad.Add($"c{bank}+0x{o:x}");
            }
        }
        return bad;
    }

    // dmsh_build.raster_facts(): static facts for the full-coverage first-draw proof.
    static JsonObject RasterFacts(string stage, string src)
    {
        if (stage == "ps")
        {
            var output = Regex.Match(src, @"struct main0_out\s*\{(.*?)\};", RegexOptions.Singleline);
            string members = output.Success ? output.Groups[1].Value : "";
            return new JsonObject
            {
                ["discard"] = src.Contains("discard_fragment") ? 1 : 0, ["sample_mask"] = members.Contains("sample_mask") ? 1 : 0,
                ["colors"] = new JsonArray(new SortedSet<long>(Regex.Matches(members, @"\[\[color\((\d+)\)\]\]").Select(m => long.Parse(m.Groups[1].Value))).Select(c => (JsonNode)c).ToArray()),
            };
        }
        if (stage != "vs") return new JsonObject();
        var body = Regex.Match(src, @"\bvertex\s+\w+\s+\w+\s*\(.*?\)\s*\{(.*)\}\s*$", RegexOptions.Singleline);
        if (!body.Success || Regex.IsMatch(body.Groups[1].Value, @"\b(if|for|while|switch|do)\b|clip_distance|ClipDistance")) return new JsonObject { ["pos_attr"] = -1 };
        string b = body.Groups[1].Value;
        var attr = new Dictionary<string, long>();
        foreach (Match m in Regex.Matches(src, @"(\w+)\s+(\w+)\s*\[\[attribute\((\d+)\)\]\]")) attr[m.Groups[2].Value] = long.Parse(m.Groups[3].Value);
        var variable = new Dictionary<string, (string, string)>();
        foreach (Match m in Regex.Matches(b, @"float\s+(_\w+)\s*=\s*in\.(\w+)\.([xyzw]);")) variable[m.Groups[1].Value] = (m.Groups[2].Value, m.Groups[3].Value);
        var last = new Dictionary<string, string>();
        foreach (Match m in Regex.Matches(b, @"out\.gl_Position\.([xyzw])\s*=\s*([^;]+);")) last[m.Groups[1].Value] = m.Groups[2].Value.Trim();
        (string, string)? SourceOf(string component) => last.TryGetValue(component, out var v) && variable.TryGetValue(v, out var s) ? s : null;
        var m0 = SourceOf("x");
        if (m0 is not { } position || !attr.ContainsKey(position.Item1) || "xyz".Any(c => SourceOf(c.ToString()) != (position.Item1, c.ToString()))) return new JsonObject { ["pos_attr"] = -1 };
        string a0 = position.Item1, w;
        if (last.TryGetValue("w", out var lw) && lw == "1.0") w = "one";
        else if (SourceOf("w") == (a0, "w")) w = "attr";
        else return new JsonObject { ["pos_attr"] = -1 };
        // uv pass-through and affine uv (merge B "this-pixel addressing" proof).
        var outputs = Regex.Match(src, @"struct main0_out\s*\{(.*?)\};", RegexOptions.Singleline);
        var locn = new Dictionary<string, long>(); var order = new List<string>();
        foreach (Match m in Regex.Matches(outputs.Success ? outputs.Groups[1].Value : "", @"(m_\d+) \[\[user\(locn(\d+)\)\]\]"))
        {
            if (!locn.ContainsKey(m.Groups[1].Value)) order.Add(m.Groups[1].Value);
            locn[m.Groups[1].Value] = long.Parse(m.Groups[2].Value);
        }
        var olast = new Dictionary<(string, string), string>();
        foreach (Match m in Regex.Matches(b, @"out\.(m_\d+)\.([xyzw])\s*=\s*([^;]+);")) olast[(m.Groups[1].Value, m.Groups[2].Value)] = m.Groups[3].Value.Trim();
        string Last(string member, string component) => olast.TryGetValue((member, component), out var v) ? v : "";
        ((string, string), JsonArray, JsonArray)? Fma(string value)
        {
            var m = Regex.Match(b, @"float " + Regex.Escape(value) + @" = fma\((_\w+), \(\*spvDescriptorSet0\.vp_c(\d+)\)\._m0\[(\d+)\]\.([xyzw]), \(\*spvDescriptorSet0\.vp_c(\d+)\)\._m0\[(\d+)\]\.([xyzw])\);");
            if (!m.Success || !variable.TryGetValue(m.Groups[1].Value, out var v)) return null;
            JsonArray Part(int g) => new JsonArray(long.Parse(m.Groups[g].Value) - 3, long.Parse(m.Groups[g + 1].Value), (long)"xyzw".IndexOf(m.Groups[g + 2].Value, StringComparison.Ordinal));
            return (v, Part(2), Part(5));
        }
        var uv = new JsonObject(); var affine = new JsonObject();
        foreach (string member in order)
        {
            string k = locn[member].ToString();
            (string, string)? sx = variable.TryGetValue(Last(member, "x"), out var vx) ? vx : null, sy = variable.TryGetValue(Last(member, "y"), out var vy) ? vy : null;
            if (sx is { } x && sy is { } y && x.Item1 == y.Item1 && attr.ContainsKey(x.Item1) && x.Item2 == "x" && y.Item2 == "y") { uv[k] = attr[x.Item1]; continue; }
            var fx = Fma(Last(member, "x")); var fy = Fma(Last(member, "y"));
            if (fx is { } ax && fy is { } ay && ax.Item1.Item1 == ay.Item1.Item1 && attr.ContainsKey(ax.Item1.Item1) && ax.Item1.Item2 == "x" && ay.Item1.Item2 == "y" &&
                new[] { ax.Item2, ax.Item3, ay.Item2, ay.Item3 }.Min(p => (long)p[0]) >= 0)
                affine[k] = new JsonObject { ["attr"] = attr[ax.Item1.Item1], ["sx"] = ax.Item2, ["ox"] = ax.Item3, ["sy"] = ay.Item2, ["oy"] = ay.Item3 };
        }
        return new JsonObject { ["pos_attr"] = attr[a0], ["pos_w"] = w, ["uv_from_attr"] = uv, ["uv_affine"] = affine };
    }
}

// script/spirv_subgroup.py: lower ballot-only subgroup use in vertex SPIR-V to a subgroup of one invocation.
static class SpirvSubgroup
{
    const uint OpCapability = 17, OpExtension = 10, OpEntryPoint = 15, OpName = 5, OpDecorate = 71;
    const uint OpTypeInt = 21, OpTypeVector = 23, OpConstant = 43, OpConstantComposite = 44;
    const uint OpFunction = 54, OpVariable = 59, OpLoad = 61, OpSelect = 169, OpCopyObject = 83;
    const uint Ballot = 339, BallotKhr = 4421, BuiltIn = 11;
    static readonly HashSet<uint> SubgroupCapabilities = [61, 64, 4423];
    static readonly Dictionary<uint, uint[]> BuiltinValues = new()
    {
        [36] = [1], [41] = [0], [4416] = [1, 0, 0, 0], [4417] = [1, 0, 0, 0], [4418] = [0, 0, 0, 0], [4419] = [1, 0, 0, 0], [4420] = [0, 0, 0, 0],
    };
    static bool GroupOp(uint op) => (op >= 333 && op < 367) || (op >= 4421 && op < 4433);

    static List<(int index, uint op, uint[] ops)> Instructions(uint[] words)
    {
        var listing = new List<(int, uint, uint[])>();
        for (int i = 5; i < words.Length;)
        {
            int count = (int)(words[i] >> 16);
            if (count == 0 || i + count > words.Length) throw new InvalidDataException("malformed SPIR-V instruction stream");
            listing.Add((i, words[i] & 0xffff, words[(i + 1)..(i + count)]));
            i += count;
        }
        return listing;
    }

    static uint[] Encode(uint op, IEnumerable<uint> operands) { var o = operands.ToArray(); return [(uint)(o.Length + 1) << 16 | op, .. o]; }

    static int StringWords(uint[] operands)
    {
        for (int n = 0; n < operands.Length; n++)
            if (new[] { 0, 8, 16, 24 }.Any(shift => ((operands[n] >> shift) & 0xff) == 0)) return n + 1;
        throw new InvalidDataException("unterminated SPIR-V string");
    }

    // Returns rewritten SPIR-V, or null when the module has nothing to lower or uses other group operations.
    public static byte[] Lower(byte[] data)
    {
        if (data.Length % 4 != 0 || data.Length < 20) throw new InvalidDataException("SPIR-V size");
        uint[] words = MemoryMarshal.Cast<byte, uint>(data).ToArray();
        if (words[0] != 0x07230203) throw new InvalidDataException("SPIR-V magic");
        var listing = Instructions(words);
        var builtins = new Dictionary<uint, uint>();
        foreach (var (_, op, ops) in listing) if (op == OpDecorate && ops.Length >= 3 && ops[1] == BuiltIn) builtins[ops[0]] = ops[2];
        var lane = builtins.Where(kv => BuiltinValues.ContainsKey(kv.Value)).ToDictionary(kv => kv.Key, kv => kv.Value);
        var group = listing.Where(l => GroupOp(l.op)).Select(l => l.op).ToList();
        if (lane.Count == 0 && group.Count == 0) return null;
        if (group.Any(op => op != Ballot && op != BallotKhr)) return null;
        var types = new Dictionary<uint, (uint, uint[])>();
        foreach (var (_, op, ops) in listing) if (op == OpTypeInt || op == OpTypeVector) types[ops[0]] = (op, ops);
        var loads = new Dictionary<uint, (uint, uint)>();
        foreach (var (_, op, ops) in listing)
        {
            if (op == OpLoad && lane.ContainsKey(ops[2])) loads[ops[1]] = (ops[0], lane[ops[2]]);
            else if (op != OpDecorate && op != OpName && op != OpEntryPoint && op != OpVariable && op != OpLoad && ops.Skip(2).Any(lane.ContainsKey)) return null;
        }
        uint bound = words[3];
        var constants = new Dictionary<string, uint>();
        var added = new List<uint>();
        uint Constant(uint type, uint[] value, bool vector)
        {
            string key = $"{type}:{(vector ? "(" : "")}{string.Join(",", value)}";
            if (constants.TryGetValue(key, out uint id)) return id;
            types.TryGetValue(type, out var t);
            if (t.Item1 == OpTypeInt && !vector)
            {
                constants[key] = bound; added.AddRange(Encode(OpConstant, [type, bound, value[0]])); bound++;
            }
            else if (t.Item1 == OpTypeVector && vector && t.Item2[2] == value.Length)
            {
                var parts = value.Select(v => Constant(t.Item2[1], [v], false)).ToArray();
                constants[key] = bound; added.AddRange(Encode(OpConstantComposite, [type, bound, .. parts])); bound++;
            }
            else throw new InvalidDataException("unsupported subgroup value type");
            return constants[key];
        }
        var replaced = new Dictionary<int, uint[]>();
        foreach (var (index, op, ops) in listing)
        {
            if (op == Ballot || op == BallotKhr)
            {
                uint one = Constant(ops[0], [1, 0, 0, 0], true), zero = Constant(ops[0], [0, 0, 0, 0], true);
                replaced[index] = Encode(OpSelect, [ops[0], ops[1], ops[^1], one, zero]);
            }
            else if (op == OpLoad && loads.TryGetValue(ops[1], out var load))
            {
                uint[] value = BuiltinValues[load.Item2];
                replaced[index] = Encode(OpCopyObject, [load.Item1, ops[1], Constant(load.Item1, value, value.Length > 1)]);
            }
        }
        var output = new List<uint>(words[..5]);
        int firstFunction = listing.First(l => l.op == OpFunction).index;
        foreach (var (index, op, original) in listing)
        {
            var ops = original;
            if (index == firstFunction) output.AddRange(added);
            if (replaced.TryGetValue(index, out var replacement)) { output.AddRange(replacement); continue; }
            if (op == OpCapability && SubgroupCapabilities.Contains(ops[0])) continue;
            if (op == OpExtension)
            {
                byte[] text = MemoryMarshal.AsBytes(ops.AsSpan()).ToArray();
                int end = Array.IndexOf(text, (byte)0);
                if (Encoding.ASCII.GetString(text, 0, end < 0 ? text.Length : end) == "SPV_KHR_shader_ballot") continue;
            }
            if ((op == OpDecorate || op == OpName) && lane.ContainsKey(ops[0])) continue;
            if (op == OpVariable && lane.ContainsKey(ops[1])) continue;
            if (op == OpEntryPoint)
            {
                int name = StringWords(ops[2..]);
                ops = [.. ops[..(2 + name)], .. ops[(2 + name)..].Where(v => !lane.ContainsKey(v))];
            }
            output.AddRange(Encode(op, ops));
        }
        output[3] = bound;
        return MemoryMarshal.AsBytes(output.ToArray().AsSpan()).ToArray();
    }
}
