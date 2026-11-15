#include <util/dag_globDef.h>
#include <ioSys/dag_oodleIo.h>
#include <fstream>
#include <vector>
#include <algorithm>
#include <cassert>
#include <cstdlib>
#include <cstring>

void logmessage_fmt(int, const char *fmt, const DagorSafeArg *, int) { fprintf(stderr, "%s\n", fmt); }
void _core_fatal_fmt(const char *, int, const char *, const DagorSafeArg *, int) { std::abort(); }
void OodleLoadCB::issueFatal() { std::abort(); }

struct Input : IGenLoad
{
  std::vector<char> data;
  int pos = 0;
  explicit Input(std::vector<char> d) : data(std::move(d)) {}
  void read(void *, int) override { std::abort(); }
  int tryRead(void *ptr, int size) override
  {
    const int count = std::min({size, 997, static_cast<int>(data.size()) - pos});
    if (count > 0)
    {
      std::memcpy(ptr, data.data() + pos, count);
      pos += count;
    }
    return count;
  }
  int tell() override { return pos; }
  void seekto(int n) override { pos = n; }
  void seekrel(int n) override { pos += n; }
  const char *getTargetName() override { return "fixture"; }
  int beginBlock(unsigned *) override { std::abort(); }
  void endBlock() override { std::abort(); }
  int getBlockLength() override { std::abort(); }
  int getBlockRest() override { std::abort(); }
  int getBlockLevel() override { return 0; }
};

struct Output : IGenSave
{
  std::vector<char> data;
  void write(const void *ptr, int size) override
  {
    const auto *bytes = static_cast<const char *>(ptr);
    data.insert(data.end(), bytes, bytes + size);
  }
  int tell() override { return data.size(); }
  void seekto(int) override { std::abort(); }
  void seektoend(int) override { std::abort(); }
  const char *getTargetName() override { return "fixture-output"; }
  void beginBlock() override { std::abort(); }
  void endBlock(unsigned) override { std::abort(); }
  int getBlockLevel() override { return 0; }
  void flush() override {}
};

int main(int argc, char **argv)
{
  std::vector<char> expected;
  for (int i = 1; i < argc; ++i)
  {
    std::ifstream file(argv[i], std::ios::binary);
    std::vector<char> packed((std::istreambuf_iterator<char>(file)), {});
    assert(packed.size() >= 8);
    constexpr int size = 10192446;
    const int prefix = (packed[4] == 0 && packed[5] == 0 && packed[6] == 0 && packed[7] == 0) ? 8 : 4;
    packed.erase(packed.begin(), packed.begin() + prefix);
    const int packed_size = static_cast<int>(packed.size());
    std::vector<char> decoded(size);
    assert(oodle_decompress(decoded.data(), size, packed.data(), packed_size) == size);
    if (expected.empty())
      expected = decoded;
    assert(expected == decoded);
    Input src(packed);
    OodleLoadCB reader(src, packed_size, size);
    assert(src.tell() == 0);
    assert(reader.ceaseReading() && src.tell() == 0);
    reader.open(src, packed_size, size);
    reader.seekrel(12);
    char sample[256];
    reader.read(sample, sizeof(sample));
    assert(std::memcmp(sample, decoded.data() + 12, sizeof(sample)) == 0);
    assert(src.tell() == packed_size);
    reader.seekrel(size);
    assert(reader.decodedBytes() == size && reader.tryRead(sample, 1) == 0);
    assert(reader.ceaseReading() && reader.tryRead(sample, 1) == 0);
    reader.close();
    src.seekto(0);
    Output output;
    assert(oodle_decompress_data(output, src,  packed_size, size) == size);
    assert(output.data == decoded);
    src.seekto(0);
    reader.open(src, packed_size + 1, size);
    assert(reader.tryRead(sample, 1) == 0 && reader.decodedBytes() == 0);
    assert(reader.ceaseReading() && src.tell() == packed_size + 1);
    if ((packed[1] & 0x7f) == 12)
    {
      const char tail[] = {0, 1, 2, 3, 4, 5};
      packed.insert(packed.end(), tail, tail + sizeof(tail));
      const int envelope_size = static_cast<int>(packed.size());
      std::vector<char> with_tail(size);
      assert(oodle_decompress(with_tail.data(), size, packed.data(), envelope_size) == size);
      assert(with_tail == decoded);
      packed.push_back('!');
      Input padded_src(std::move(packed));
      Output padded_output;
      assert(oodle_decompress_data(padded_output, padded_src, envelope_size, size) == size);
      assert(padded_output.data == decoded);
      assert(padded_src.tell() == envelope_size);
      assert(padded_src.tryRead(sample, 1) == 1 && sample[0] == '!');
    }
    fprintf(stdout, "%s OK %d bytes\n", argv[i], size);
  }
  const unsigned char checksum_stream[] = {0x8C, 0x86, 0, 0};
  char result[] = {'x'};
  assert(oodle_decompress(result, sizeof(result), checksum_stream, sizeof(checksum_stream)) == 0 && result[0] == 'x');
  assert(oodle_decompress(result, 256u * 1024u * 1024u + 1, checksum_stream, sizeof(checksum_stream)) == 0 && result[0] == 'x');
  std::ofstream out("dickens.decoded", std::ios::binary);
  out.write(expected.data(), expected.size());
}
