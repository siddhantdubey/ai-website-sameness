// Colour maths and the hue-bucket rules. The README copies these thresholds;
// if you change a number here, change it there.

export function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

export function rgbToHex({ r, g, b }) {
  const h = (v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0');
  return `#${h(r)}${h(g)}${h(b)}`;
}

// HSL with h in degrees [0,360), s and l in [0,1]; also chroma = max-min in [0,1].
export function rgbToHsl({ r, g, b }) {
  const R = r / 255, G = g / 255, B = b / 255;
  const max = Math.max(R, G, B), min = Math.min(R, G, B);
  const l = (max + min) / 2;
  const c = max - min;
  let h = 0, s = 0;
  if (c > 0) {
    s = c / (1 - Math.abs(2 * l - 1));
    if (max === R) h = ((G - B) / c) % 6;
    else if (max === G) h = (B - R) / c + 2;
    else h = (R - G) / c + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  return { h, s, l, c };
}

// WCAG relative luminance Y (0..1) and CIE L* / 100 ("perceived lightness", 0..1).
export function luminance({ r, g, b }) {
  const lin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const Y = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  const Lstar = Y <= 216 / 24389 ? Y * (24389 / 27) : 116 * Math.cbrt(Y) - 16;
  return { Y, lightness: Lstar / 100 };
}

// Pure hue family for any colour with HSL S >= 0.12 (otherwise 'neutral').
export const HUE_RANGES = [
  // [name, from (inclusive), to (exclusive)] in degrees
  ['red', 345, 360],
  ['red', 0, 15],
  ['orange', 15, 40],
  ['yellow', 40, 65],
  ['green', 65, 165],
  ['teal/cyan', 165, 200],
  ['blue', 200, 235],
  ['purple/indigo', 235, 290],
  ['pink/magenta', 290, 345],
];

export function hueFamily(hex) {
  const rgb = hexToRgb(hex);
  if (!rgb) return 'na';
  const { h, s, l, c } = rgbToHsl(rgb);
  if (s < 0.12 || (c < 0.04 && l >= 0.85) || l <= 0.08 || l >= 0.99) return 'neutral';
  for (const [name, a, b] of HUE_RANGES) if (h >= a && h < b) return name;
  return 'na';
}

// Bucket rules, applied in this order (first match wins):
//  1 black        L <= 0.08
//  2 white        L >= 0.95 and chroma <= 0.06
//  3 cream/beige  L >= 0.80, hue in [20,65), 0.03 < chroma <= 0.16
//  4 white/gray   chroma < 0.04 and L >= 0.85: white if L >= 0.93, else gray   (v1.2.0: near-whites like #f5f3ee
//                 have an inflated HSL S and were landing in 'yellow'/'orange')
//  4b gray        S < 0.12
//  5 dark-navy    L <= 0.22, hue in [195,235)
//  6 dark-tinted  L <= 0.22 (any other hue)
//  7 hue family from HUE_RANGES
export function hueBucket(hex) {
  const rgb = hexToRgb(hex);
  if (!rgb) return 'na';
  const { h, s, l, c } = rgbToHsl(rgb);
  if (l <= 0.08) return 'black';
  if (l >= 0.95 && c <= 0.06) return 'white';
  if (l >= 0.80 && h >= 20 && h < 65 && c > 0.03 && c <= 0.16) return 'cream/beige';
  if (c < 0.04 && l >= 0.85) return l >= 0.93 ? 'white' : 'gray';
  if (s < 0.12) return 'gray';
  if (l <= 0.22 && h >= 195 && h < 235) return 'dark-navy';
  if (l <= 0.22) return 'dark-tinted';
  for (const [name, a, b] of HUE_RANGES) if (h >= a && h < b) return name;
  return 'na';
}

export function isNeutralBucket(b) {
  return b === 'white' || b === 'black' || b === 'gray';
}
