#pragma once
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cassert>
#include <immintrin.h>
using byte = unsigned char;
using uint8 = uint8_t;
using uint16 = uint16_t;
using uint32 = uint32_t;
using uint64 = uint64_t;
using int16 = int16_t;
using int32 = int32_t;
using int64 = int64_t;
using uint = unsigned int;
#define __forceinline inline __attribute__((always_inline))
#define _byteswap_ushort __builtin_bswap16
#define _byteswap_ulong __builtin_bswap32
#define _byteswap_uint64 __builtin_bswap64
inline bool _BitScanForward(unsigned long *index, uint32 n) { if (!n) return false; *index = __builtin_ctz(n); return true; }
inline bool _BitScanReverse(unsigned long *index, uint32 n) { if (!n) return false; *index = 31-__builtin_clz(n); return true; }
