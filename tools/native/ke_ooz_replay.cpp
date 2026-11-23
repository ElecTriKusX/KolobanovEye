// Standalone local OOZ replay, using exactly the decoder linked into daKernel.
// No Dagor startup, no Windows dependencies except the C++ compiler.
// The input is a proprietary asset stream: keep it local; never commit it.
#include <cerrno>
#include <climits>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <vector>

extern int Kraken_Decompress(const unsigned char *src, size_t src_len,
                             unsigned char *dst, size_t dst_len);

int main(int argc, char **argv)
{
  if (argc < 3 || argc > 4)
  {
    std::fprintf(stderr, "Usage: ke-ooz-replay.exe failed-ooz.bin unpacked_bytes [output.raw]\n");
    return 2;
  }
  errno = 0;
  char *end = nullptr;
  unsigned long long expected = std::strtoull(argv[2], &end, 10);
  if (errno || end == argv[2] || *end || expected == 0 ||
      expected > 256ull * 1024 * 1024 || expected > INT_MAX)
  {
    std::fprintf(stderr, "Invalid decoded size (max 256 MiB)\n");
    return 2;
  }
  std::ifstream f(argv[1], std::ios::binary | std::ios::ate);
  if (!f)
  {
    std::fprintf(stderr, "Unable to open packed input\n");
    return 2;
  }
  const auto size = f.tellg();
  if (size < 2 || size > 16ll * 1024 * 1024)
  {
    std::fprintf(stderr, "Invalid packed input length\n");
    return 2;
  }
  size_t packed = static_cast<size_t>(size);
  std::vector<unsigned char> input(packed + 64, 0);
  std::vector<unsigned char> output(static_cast<size_t>(expected) + 64, 0);
  f.seekg(0, std::ios::beg);
  f.read(reinterpret_cast<char *>(input.data()), static_cast<std::streamsize>(packed));
  if (!f)
  {
    std::fprintf(stderr, "Incomplete packed input\n");
    return 2;
  }
  const int result = Kraken_Decompress(input.data(), packed, output.data(), static_cast<size_t>(expected));
  std::printf("[KE] OOZ_CPP_REPLAY packed=%zu expected=%llu result=%d\n", packed, expected, result);
  if (result != static_cast<int>(expected))
    return 1;
  if (argc == 4)
  {
    std::ofstream output_file(argv[3], std::ios::binary);
    output_file.write(reinterpret_cast<const char *>(output.data()), static_cast<std::streamsize>(expected));
    if (!output_file)
    {
      std::fprintf(stderr, "Unable to save decoded output\n");
      return 2;
    }
  }
  return 0;
}
