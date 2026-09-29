// Public, deterministic PNG for LINE Flex. Input contains only published prices; no user IDs or tokens.
const WIDTH = 480, HEIGHT = 180;
const COLORS = { rise: [191, 52, 50], fall: [29, 126, 79], flat: [55, 111, 151] };
const TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let i = 0; i < 8; i++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function u32(n) { return Uint8Array.of(n >>> 24, n >>> 16 & 255, n >>> 8 & 255, n & 255); }
function crc(bytes) { let c = 0xffffffff; for (const b of bytes) c = TABLE[(c ^ b) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; }
function join(parts) { const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0)); let i = 0; for (const p of parts) { out.set(p, i); i += p.length; } return out; }
function chunk(name, data) { const inner = join([new TextEncoder().encode(name), data]); return join([u32(data.length), inner, u32(crc(inner))]); }
function png(rows) {
  const raw = new Uint8Array(HEIGHT * (WIDTH * 4 + 1));
  for (let y = 0; y < HEIGHT; y++) { raw[y * (WIDTH * 4 + 1)] = 0; raw.set(rows.subarray(y * WIDTH * 4, (y + 1) * WIDTH * 4), y * (WIDTH * 4 + 1) + 1); }
  const blocks = [Uint8Array.of(0x78, 0x01)];
  for (let i = 0; i < raw.length; i += 65535) {
    const part = raw.subarray(i, i + 65535), n = part.length;
    blocks.push(Uint8Array.of(i + n >= raw.length ? 1 : 0, n & 255, n >>> 8, ~n & 255, ~n >>> 8), part);
  }
  let a = 1, b = 0;
  for (const x of raw) { a = (a + x) % 65521; b = (b + a) % 65521; }
  blocks.push(u32((b << 16) | a));
  return join([Uint8Array.of(137, 80, 78, 71, 13, 10, 26, 10),
    chunk('IHDR', join([u32(WIDTH), u32(HEIGHT), Uint8Array.of(8, 6, 0, 0, 0)])),
    chunk('IDAT', join(blocks)), chunk('IEND', new Uint8Array())]);
}
function paint(values, reference) {
  const pixels = new Uint8Array(WIDTH * HEIGHT * 4);
  for (let i = 0; i < pixels.length; i += 4) { pixels[i] = 246; pixels[i + 1] = 248; pixels[i + 2] = 247; pixels[i + 3] = 255; }
  const low = Math.min(...values, reference), high = Math.max(...values, reference), span = Math.max(high - low, Math.abs(high) * 0.002, 0.01);
  const lo = low - span * 0.12, hi = high + span * 0.12;
  const yOf = v => Math.max(10, Math.min(HEIGHT - 10, Math.round(HEIGHT - 10 - (v - lo) / (hi - lo) * (HEIGHT - 20))));
  const dot = (x, y, color, size = 1) => {
    for (let yy = -size + 1; yy < size; yy++) for (let xx = -size + 1; xx < size; xx++) {
      const a = x + xx, b = y + yy;
      if (a < 0 || b < 0 || a >= WIDTH || b >= HEIGHT) continue;
      const i = (b * WIDTH + a) * 4; pixels[i] = color[0]; pixels[i + 1] = color[1]; pixels[i + 2] = color[2];
    }
  };
  const line = (x0, y0, x1, y1, color, size = 1) => {
    let dx = Math.abs(x1 - x0), sx = x0 < x1 ? 1 : -1, dy = -Math.abs(y1 - y0), sy = y0 < y1 ? 1 : -1, err = dx + dy;
    for (;;) { dot(x0, y0, color, size); if (x0 === x1 && y0 === y1) break; const e = 2 * err; if (e >= dy) { err += dy; x0 += sx; } if (e <= dx) { err += dx; y0 += sy; } }
  };
  const refY = yOf(reference);
  for (let x = 12; x < WIDTH - 12; x += 9) line(x, refY, Math.min(x + 4, WIDTH - 12), refY, [153, 163, 157]);
  for (let i = 1; i < values.length; i++) {
    const x0 = Math.round(12 + (i - 1) / (values.length - 1) * (WIDTH - 24));
    const x1 = Math.round(12 + i / (values.length - 1) * (WIDTH - 24));
    const a = values[i - 1], b = values[i];
    const ca = a > reference ? COLORS.rise : a < reference ? COLORS.fall : COLORS.flat;
    const cb = b > reference ? COLORS.rise : b < reference ? COLORS.fall : COLORS.flat;
    if ((a - reference) * (b - reference) < 0) {
      const crossX = Math.round(x0 + (x1 - x0) * (reference - a) / (b - a));
      line(x0, yOf(a), crossX, refY, ca, 2);
      line(crossX, refY, x1, yOf(b), cb, 2);
    } else line(x0, yOf(a), x1, yOf(b), cb, 2);
  }
  dot(WIDTH - 12, yOf(values.at(-1)), values.at(-1) > reference ? COLORS.rise : values.at(-1) < reference ? COLORS.fall : COLORS.flat, 4);
  return png(pixels);
}
export function chartResponse(request) {
  const url = new URL(request.url), raw = url.searchParams.get('v') || '', ref = Number(url.searchParams.get('ref'));
  if (raw.length > 600 || !/^[\d.,-]+$/.test(raw) || !url.searchParams.has('ref') || !Number.isFinite(ref) || ref <= 0) return new Response('invalid chart', { status: 400 });
  const values = raw.split(',').map(Number);
  if (values.length < 3 || values.length > 32 || values.some(v => !Number.isFinite(v) || v <= 0 || v > 1e9)) return new Response('invalid series', { status: 400 });
  const bytes = paint(values, ref);
  return new Response(bytes, { headers: { 'Content-Type': 'image/png', 'Cache-Control': 'public, max-age=300', 'X-Content-Type-Options': 'nosniff' } });
}
