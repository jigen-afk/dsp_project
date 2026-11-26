#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <compressapi.h>

#include <array>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <memory>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>

namespace {
namespace fs = std::filesystem;
using Bytes = std::span<const unsigned char>;

Bytes take(Bytes& source, size_t count) {
    if (source.size() < count) {
        throw std::runtime_error("Truncated application archive.");
    }
    const auto value = source.first(count);
    source = source.subspan(count);
    return value;
}

std::uint32_t read_size(Bytes& source) {
    std::uint32_t size;
    const auto bytes = take(source, sizeof(size));
    std::memcpy(&size, bytes.data(), sizeof(size));
    return size;
}

struct Payload {
    Bytes compressed;
    size_t size;
    std::wstring cache_id;
};

Payload payload() {
    const auto module = GetModuleHandleW(nullptr);
    const auto resource = FindResourceW(module, MAKEINTRESOURCEW(1), RT_RCDATA);
    const auto memory = resource ? LoadResource(module, resource) : nullptr;
    const auto content = memory ? LockResource(memory) : nullptr;
    if (!content) {
        throw std::runtime_error("Embedded application archive is missing.");
    }
    Bytes bytes(static_cast<const unsigned char*>(content), SizeofResource(module, resource));
    const auto magic = take(bytes, 8);
    if (std::memcmp(magic.data(), "DSPSFX01", magic.size()) != 0) {
        throw std::runtime_error("Unrecognized application archive.");
    }
    const auto size = read_size(bytes);
    std::wstring cache_id;
    for (const auto digit : take(bytes, 32)) {
        if (!(digit >= '0' && digit <= '9') && !(digit >= 'a' && digit <= 'f')) {
            throw std::runtime_error("Invalid application archive identifier.");
        }
        cache_id += static_cast<wchar_t>(digit);
    }
    return {bytes, size, cache_id};
}

fs::path executable_directory() {
    std::array<wchar_t, 32768> path;
    const auto count = GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
    if (!count || count == path.size()) {
        throw std::runtime_error("Cannot locate DSPStudio.exe.");
    }
    return fs::path(std::wstring_view(path.data(), count)).parent_path();
}

void unpack(const Payload& archive, const fs::path& directory) {
    auto contents = std::make_unique_for_overwrite<unsigned char[]>(archive.size);
    DECOMPRESSOR_HANDLE decompressor = nullptr;
    if (!CreateDecompressor(COMPRESS_ALGORITHM_LZMS, nullptr, &decompressor)) {
        throw std::runtime_error("Cannot open the Windows decompressor.");
    }
    size_t decoded = 0;
    const auto ok = Decompress(
        decompressor, archive.compressed.data(), archive.compressed.size(), contents.get(), archive.size, &decoded);
    CloseDecompressor(decompressor);
    if (!ok || decoded != archive.size) {
        throw std::runtime_error("Cannot unpack the application archive.");
    }

    Bytes records(contents.get(), decoded);
    fs::path previous_parent;
    while (!records.empty()) {
        const auto name_size = read_size(records);
        const auto content_size = read_size(records);
        const auto name = take(records, name_size);
        const auto file = take(records, content_size);
        const fs::path relative{std::u8string(name.begin(), name.end())};
        if (relative.empty() || relative.has_root_path() || relative.native().find(L':') != std::wstring::npos) {
            throw std::runtime_error("Invalid archive filename.");
        }
        for (const auto& part : relative) {
            if (part == L".." || part == L".") {
                throw std::runtime_error("Invalid archive filename.");
            }
        }
        const auto destination = directory / relative;
        const auto parent = destination.parent_path();
        if (parent != previous_parent) {
            fs::create_directories(parent);
            previous_parent = parent;
        }
        std::ofstream output(destination, std::ios::binary);
        output.write(reinterpret_cast<const char*>(file.data()), static_cast<std::streamsize>(file.size()));
        output.close();
        if (!output) {
            throw std::runtime_error("Cannot write the application cache.");
        }
    }
}

bool ready(const fs::path& directory) {
    return fs::is_regular_file(directory / L".complete") && fs::is_regular_file(directory / L"DSPStudio.exe");
}

struct StagingDirectory {
    fs::path path;

    explicit StagingDirectory(const fs::path& root) :
        path(root / (L".unpacking-" + std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(GetTickCount64()))) {
        if (!fs::create_directory(path)) {
            throw std::runtime_error("Cannot create the application staging directory.");
        }
    }

    ~StagingDirectory() {
        std::error_code ignored;
        fs::remove_all(path, ignored);
    }
};

fs::path prepare_cache(const Payload& archive, const fs::path& data_root) {
    const auto runtime = data_root / L"runtime";
    const auto directory = runtime / archive.cache_id;
    if (!ready(directory)) {
        fs::create_directories(runtime);
        const StagingDirectory staging(runtime);
        unpack(archive, staging.path);
        std::ofstream marker(staging.path / L".complete");
        marker.close();
        if (!marker || !ready(staging.path)) {
            throw std::runtime_error("Cannot finish writing the application cache.");
        }
        // A second launch may have published the same complete cache while we unpacked.
        std::error_code error;
        fs::rename(staging.path, directory, error);
        if (error && !ready(directory)) {
            throw std::runtime_error("Cannot activate the application cache.");
        }
    }
    return directory / L"DSPStudio.exe";
}

int launch(const fs::path& app, const fs::path& launcher_dir, std::wstring_view arguments) {
    if (!SetEnvironmentVariableW(L"DSPSTUDIO_PORTABLE_DIR", launcher_dir.c_str())) {
        throw std::runtime_error("Cannot set the application data location.");
    }
    std::wstring command = L"\"" + app.native() + L"\"";
    if (!arguments.empty()) {
        command += L" ";
        command += arguments;
    }
    STARTUPINFOW start{};
    start.cb = sizeof(start);
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(app.c_str(), command.data(), nullptr, nullptr, FALSE, 0, nullptr, nullptr, &start, &process)) {
        throw std::runtime_error("Cannot start the cached application.");
    }
    CloseHandle(process.hThread);
    const auto wait = WaitForSingleObject(process.hProcess, INFINITE);
    DWORD code = 1;
    const auto exited = GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hProcess);
    return wait == WAIT_OBJECT_0 && exited ? static_cast<int>(code) : 1;
}
}  // namespace

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR command_line, int) {
    const std::wstring_view arguments(command_line);
    try {
        const auto launcher_dir = executable_directory();
        const auto root = _wcsicmp(launcher_dir.filename().c_str(), L"app") == 0
            ? launcher_dir.parent_path() : launcher_dir;
        const auto app = prepare_cache(payload(), root / L"data");
        return launch(app, launcher_dir, arguments);
    } catch (const std::exception&) {
        if (!arguments.starts_with(L"--self-test")) {
            MessageBoxW(nullptr, L"启动失败。请确认软件目录可写，并有空间展开运行文件。", L"DSP Studio", MB_OK | MB_ICONERROR);
        }
        return 1;
    }
}
