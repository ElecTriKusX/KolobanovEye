"""Safely install frame screen-position offsets into patched Dagor capture source.

This is applied *after* the existing patch stack. Marker-based, fail-closed and
idempotent for the long-lived Windows checkout; never resets Dagor sources.
"""
from pathlib import Path

MARKER = "// [KE] SCREEN_COMPOSITION_V1"


def install_screen_composition(source: Path) -> bool:
    raw = source.read_bytes()
    eol = b"\r\n" if b"\r\n" in raw else b"\n"
    text = raw.replace(b"\r\n", b"\n").decode("utf-8")
    if MARKER in text:
        required = ("screen_bin_planned", "screen_offset_uv", "keScreenOffset",
                    "keShiftedLookAt", "look_at", "screen_offset_%d")
        if not all(x in text for x in required):
            raise ValueError(f"Partial screen composition in {source}")
        return False

    def change(before, after):
        nonlocal text
        count = text.count(before)
        if count != 1:
            raise ValueError(f"Ambiguous Dagor composition insertion ({count} hits): {before[:90]!r} in {source}")
        text = text.replace(before, after, 1)

    # Requested 0.5 means center; signed UV shifts are relative to frame center.
    # Camera position is unchanged: adjust only the world-space look-at target.
    change(
        "  const Point3 aim = world * ((bounds[0] + bounds[1]) * 0.5f);",
        """  const Point3 aim = world * ((bounds[0] + bounds[1]) * 0.5f);
  // [KE] SCREEN_COMPOSITION_V1
  char keScreenOffsetKey[48], keScreenBinKey[48];
  snprintf(keScreenOffsetKey, sizeof(keScreenOffsetKey), "screen_offset_%d", frameIndex);
  snprintf(keScreenBinKey, sizeof(keScreenBinKey), "screen_bin_%d", frameIndex);
  const Point2 keScreenOffset = config.getPoint2(keScreenOffsetKey, Point2(0.f, 0.f));
  const char *keScreenBin = config.getStr(keScreenBinKey, "center");
  if (!std::isfinite(keScreenOffset.x) || !std::isfinite(keScreenOffset.y) ||
      fabsf(keScreenOffset.x) > 0.5f || fabsf(keScreenOffset.y) > 0.5f)
    return fail("CONFIG", "Invalid normalized screen-position offset");
  auto keShiftedLookAt = [&](const Point3 &candidateEye) -> Point3 {
    const Point3 toward = normalize(aim - candidateEye);
    const Point3 right = normalize(Point3(0, 1, 0) % toward);
    const Point3 up = toward % right;
    const float distance = length(aim - candidateEye);
    const float halfWidth = tanf(fov * DEG_TO_RAD * 0.5f) * distance;
    const float halfHeight = halfWidth * float(height) / width;
    // Positive U moves the tank to the right; positive V moves it down.
    return aim - right * (2.f * keScreenOffset.x * halfWidth) +
      up * (2.f * keScreenOffset.y * halfHeight);
  };""")
    change(
        "    const Point3 candidateForward = normalize(aim - candidateEye);",
        "    const Point3 candidateForward = normalize(keShiftedLookAt(candidateEye) - candidateEye);")
    change(
        "  const Point3 forward = normalize(aim - eye);",
        "  const Point3 lookAt = keShiftedLookAt(eye);\n  const Point3 forward = normalize(lookAt - eye);")
    change(
        '  result.setPoint3("look_at", aim);',
        """  result.setPoint3("look_at", lookAt);
  result.setStr("screen_bin_planned", keScreenBin);
  result.setPoint2("screen_offset_uv", keScreenOffset);""")
    updated = text.encode("utf-8").replace(b"\n", eol)
    source.write_bytes(updated)
    print("[KE] Installed normalized per-frame screen composition into Dagor", flush=True)
    return True
