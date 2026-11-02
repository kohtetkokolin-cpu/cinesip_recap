# Fairuse Presets (Zippy Cut v1.0.2)

Source: five `.zippy` files supplied by the user on 2026-10-30. Each file is
JSON despite the `.zippy` extension.

| Level | File |
|---|---|
| 30% | `fairuse_30.json` |
| 60% | `fairuse_60.json` |
| 90% | `fairuse_90.json` |
| 120% | `fairuse_120.json` |
| 150% | `fairuse_150.json` |

## Shared settings

All five presets have identical `fx`; only `name`, `at` and `fairUse` differ.

- **caption**: on, `#ffe600`, black outline, box background `#000000` at 0.7,
  size `0.044`, y `0.8`, length `mid`
- **color**: preset `none`, b/c/s `1`, warm/sepia/gray `0`
- **logo**: same embedded PNG in every preset; extracted to
  `../../assets/preset_logo.png`; x `0.84`, y `0.1`, w `0.11`, opacity `1`,
  shape `circle`, ring `gold`
- **frame**: style `18`
- **canvas**: ratio `16:9`, zoom `1.2`, x/y `0.5`, flip `true`,
  background `blur`, color `#000000`, image `null`
- **texts**: empty list

## Open question

The exact meaning of `fairUse` (30/60/90/120/150) is **not defined inside the
file**. It is an app-level value. It may mean source-usage ratio, playback
speed, or transformation strength. This must be confirmed before implementing
the fairuse engine.
