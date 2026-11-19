// place2mc LiDAR reader: real heights of the ground, every roof and every tree, from the USGS 3DEP laser scans
// (public domain, served as Entwine Point Tiles from the usgs-lidar-public bucket). Runs in a browser (or in Node
// for tests). It reads the scan points inside the place2mc square and boils them down to three small things:
//   dtm   - the bare ground, every `dtmStep` metres
//   roofs - the height of the roof above the ground for every 1 m cell inside a building footprint
//   trees - the top of every tree: position, height and crown radius
// The squares and cells are the same as place2mc's blocks: x east, z south, 0..size from the north-west corner.

const R = 6378137;                                       // Web Mercator sphere radius (the scans use EPSG:3857)

export function metresPerDegree(lat) {                   // the same formula as place2mc.metres_per_degree
  const phi = lat * Math.PI / 180;
  return [111132.92 - 559.82 * Math.cos(2 * phi) + 1.175 * Math.cos(4 * phi),
          111412.84 * Math.cos(phi) - 93.5 * Math.cos(3 * phi)];
}

export function toMercator(lat, lon) {
  return [R * lon * Math.PI / 180, R * Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360))];
}

// ----------------------------------------------------------------------------------------------- footprints

// building outlines from Overpass JSON (`out geom`), as rings of [x, z] in blocks
export function buildingRings(elements, lat0, lon0, size) {
  const [mLat, mLon] = metresPerDegree(lat0), half = size / 2;
  const px = (p) => [(p.lon - lon0) * mLon + half, (lat0 - p.lat) * mLat + half];
  const rings = [];
  for (const el of elements) {
    const tags = el.tags || {};
    if (!tags.building || tags.building === 'no') continue;
    if (el.type === 'way' && el.geometry && el.geometry.length >= 4) rings.push({ id: `way/${el.id}`, outer: [el.geometry.map(px)], inner: [] });
    else if (el.type === 'relation' && el.members) {
      const outer = joinRings(el.members.filter((m) => m.role !== 'inner' && m.geometry).map((m) => m.geometry.map(px)));
      const inner = joinRings(el.members.filter((m) => m.role === 'inner' && m.geometry).map((m) => m.geometry.map(px)));
      if (outer.length) rings.push({ id: `relation/${el.id}`, outer, inner });
    }
  }
  return rings;
}

function joinRings(lines) {
  const same = (a, b) => Math.abs(a[0] - b[0]) < 1e-6 && Math.abs(a[1] - b[1]) < 1e-6;
  const pool = lines.map((l) => l.slice()), out = [];
  while (pool.length) {
    let cur = pool.shift();
    for (let grew = true; grew && !same(cur[0], cur[cur.length - 1]);) {
      grew = false;
      for (let i = 0; i < pool.length; i++) {
        const l = pool[i];
        if (same(cur[cur.length - 1], l[0])) cur = cur.concat(l.slice(1));
        else if (same(cur[cur.length - 1], l[l.length - 1])) cur = cur.concat(l.slice(0, -1).reverse());
        else continue;
        pool.splice(i, 1); grew = true; break;
      }
    }
    if (cur.length >= 4) out.push(cur);
  }
  return out;
}

// cells whose centre lies inside the outline (even-odd, so courtyards stay open)
export function fillRings(grid, size, rings, value) {
  const all = rings.outer.concat(rings.inner);
  let zMin = Infinity, zMax = -Infinity;
  for (const r of all) for (const p of r) { zMin = Math.min(zMin, p[1]); zMax = Math.max(zMax, p[1]); }
  for (let z = Math.max(0, Math.floor(zMin)); z <= Math.min(size - 1, Math.ceil(zMax)); z++) {
    const y = z + 0.5, xs = [];
    for (const r of all) for (let i = 0; i < r.length - 1; i++) {
      const [x1, y1] = r[i], [x2, y2] = r[i + 1];
      if ((y1 > y) !== (y2 > y)) xs.push(x1 + (y - y1) * (x2 - x1) / (y2 - y1));
    }
    xs.sort((a, b) => a - b);
    for (let k = 0; k + 1 < xs.length; k += 2)
      for (let x = Math.max(0, Math.ceil(xs[k] - 0.5)); x <= Math.min(size - 1, Math.floor(xs[k + 1] - 0.5)); x++) grid[z * size + x] = value;
  }
}

// ----------------------------------------------------------------------------------------------- reading the scan

async function getJSON(fetchFn, url) {
  const r = await fetchFn(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

// every node of the scan's octree whose box overlaps [x0, y0, x1, y1] (Web Mercator)
async function nodesIn(fetchFn, base, ept, box) {
  const [bx0, by0, , bx1] = ept.bounds;
  const width = bx1 - bx0, found = [];
  async function walk(key) {
    const h = await getJSON(fetchFn, `${base}ept-hierarchy/${key}.json`);
    const subtrees = [];
    for (const [k, n] of Object.entries(h)) {
      const [d, x, y] = k.split('-').map(Number), w = width / 2 ** d;
      const nx0 = bx0 + x * w, ny0 = by0 + y * w;
      if (nx0 > box[2] || nx0 + w < box[0] || ny0 > box[3] || ny0 + w < box[1]) continue;
      if (n === -1) subtrees.push(k);
      else if (n > 0) found.push({ key: k, points: n });
    }
    for (const k of subtrees) if (k !== key) await walk(k);
  }
  await walk('0-0-0-0');
  return found;
}

function lasHeader(bytes) {
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return {
    format: dv.getUint8(104) & 0x3f, length: dv.getUint16(105, true),
    scale: [dv.getFloat64(131, true), dv.getFloat64(139, true), dv.getFloat64(147, true)],
    offset: [dv.getFloat64(155, true), dv.getFloat64(163, true), dv.getFloat64(171, true)],
  };
}

// decode one .laz file and hand every point to `visit(x, y, z, returnNumber, numberOfReturns, classification)`
export function decodeLaz(LazPerf, bytes, visit) {
  const head = lasHeader(bytes);
  const filePtr = LazPerf._malloc(bytes.byteLength);
  LazPerf.HEAPU8.set(bytes, filePtr);
  const laszip = new LazPerf.LASZip();
  let pointPtr = 0;
  try {
    laszip.open(filePtr, bytes.byteLength);
    const n = laszip.getCount(), len = laszip.getPointLength(), fmt = laszip.getPointFormat();
    pointPtr = LazPerf._malloc(len);
    const extended = fmt >= 6;
    for (let i = 0; i < n; i++) {
      laszip.getPoint(pointPtr);
      const dv = new DataView(LazPerf.HEAPU8.buffer, pointPtr, len);
      const x = dv.getInt32(0, true) * head.scale[0] + head.offset[0];
      const y = dv.getInt32(4, true) * head.scale[1] + head.offset[1];
      const z = dv.getInt32(8, true) * head.scale[2] + head.offset[2];
      const b = dv.getUint8(14);
      const rn = extended ? b & 15 : b & 7, nr = extended ? b >> 4 : (b >> 3) & 7;
      const cls = extended ? dv.getUint8(16) : dv.getUint8(15) & 31;
      visit(x, y, z, rn, nr, cls);
    }
    return n;
  } finally {
    if (pointPtr) LazPerf._free(pointPtr);
    LazPerf._free(filePtr);
    laszip.delete();
  }
}

// ----------------------------------------------------------------------------------------------- grids

// fill the cells that have no value (NaN) from coarser and coarser averages, so gaps (under buildings, in deep
// shade) get the ground around them
export function fillGaps(values, size) {
  const levels = [{ v: values, n: size }];
  while (levels[levels.length - 1].n > 1) {
    const { v, n } = levels[levels.length - 1], m = Math.ceil(n / 2), out = new Float32Array(m * m).fill(NaN);
    for (let z = 0; z < m; z++) for (let x = 0; x < m; x++) {
      let s = 0, c = 0;
      for (let dz = 0; dz < 2; dz++) for (let dx = 0; dx < 2; dx++) {
        const zz = 2 * z + dz, xx = 2 * x + dx;
        if (zz < n && xx < n && !Number.isNaN(v[zz * n + xx])) { s += v[zz * n + xx]; c++; }
      }
      if (c) out[z * m + x] = s / c;
    }
    levels.push({ v: out, n: m });
  }
  for (let l = levels.length - 2; l >= 0; l--) {
    const { v, n } = levels[l], up = levels[l + 1];
    for (let z = 0; z < n; z++) for (let x = 0; x < n; x++) {
      const i = z * n + x;
      if (!Number.isNaN(v[i])) continue;
      // bilinear from the coarser level (its cell centres sit at 2k + 0.5 in this level's cells)
      const fz = Math.min(Math.max((z - 0.5) / 2, 0), up.n - 1), fx = Math.min(Math.max((x - 0.5) / 2, 0), up.n - 1);
      const z0 = Math.min(Math.floor(fz), up.n - 1), x0 = Math.min(Math.floor(fx), up.n - 1);
      const z1 = Math.min(z0 + 1, up.n - 1), x1 = Math.min(x0 + 1, up.n - 1), az = fz - z0, ax = fx - x0;
      const g = (zz, xx) => up.v[zz * up.n + xx];
      v[i] = (g(z0, x0) * (1 - ax) + g(z0, x1) * ax) * (1 - az) + (g(z1, x0) * (1 - ax) + g(z1, x1) * ax) * az;
    }
  }
  return values;
}

// tree tops: the highest cell within a radius that grows with the tree, at least `minHeight` above the ground
export function findTrees(chm, size, minHeight = 3) {
  const trees = [];
  const at = (x, z) => (x < 0 || z < 0 || x >= size || z >= size ? 0 : chm[z * size + x]);
  for (let z = 0; z < size; z++) for (let x = 0; x < size; x++) {
    const h = chm[z * size + x];
    if (!(h >= minHeight)) continue;
    const r = Math.min(5, Math.max(1.5, 1 + 0.1 * h)), ri = Math.ceil(r);
    let top = true;
    for (let dz = -ri; dz <= ri && top; dz++) for (let dx = -ri; dx <= ri; dx++) {
      if ((dx || dz) && dx * dx + dz * dz <= r * r) {
        const o = at(x + dx, z + dz);
        // ties go to the first cell in reading order, so a flat-topped crown gives one top, not several
        if (o > h || (o === h && (dz < 0 || (dz === 0 && dx < 0)))) { top = false; break; }
      }
    }
    if (!top) continue;
    // crown radius: how far the canopy stays above 45% of the tree's height, in 8 directions (stopping where it
    // climbs again, which is the next tree)
    let sum = 0;
    for (const [ux, uz] of [[1, 0], [-1, 0], [0, 1], [0, -1], [0.7071, 0.7071], [-0.7071, 0.7071], [0.7071, -0.7071], [-0.7071, -0.7071]]) {
      let d = 0, last = h;
      for (let s = 1; s <= 14; s++) {
        const v = at(Math.round(x + ux * s), Math.round(z + uz * s));
        if (v < 0.45 * h || v > last + 1.5) break;
        d = s; last = Math.min(last, v);
      }
      sum += d + 0.5;
    }
    trees.push([x + 0.5, z + 0.5, h, sum / 8]);
  }
  return trees;
}

// ----------------------------------------------------------------------------------------------- packing

async function gzip(bytes) {
  const cs = new CompressionStream('gzip');
  const out = new Response(new Blob([bytes]).stream().pipeThrough(cs));
  return new Uint8Array(await out.arrayBuffer());
}

function b64(bytes) {
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

// ----------------------------------------------------------------------------------------------- the whole job

export async function extractLidar({ eptBase, lat0, lon0, size, elements, LazPerf, fetchFn = fetch, log = () => {},
                                     dtmStep = 2, roofStep = 0.5, parallel = 6, keepGrids = false, pointSource = null }) {
  const [mLat, mLon] = metresPerDegree(lat0), half = size / 2;

  const cells = size * size;
  const ground = new Float32Array(cells).fill(Infinity), single = new Float32Array(cells).fill(-Infinity);
  const first = new Float32Array(cells).fill(-Infinity), bclass = new Uint8Array(cells);
  const echoes = new Uint16Array(cells), singles = new Uint16Array(cells);   // above-ground echoes, and lone ones
  const classes = {}, returns = {};
  let used = 0, read = 0, done = 0;
  const visit = (x, y, z, rn, nr, cls) => {
    read++;
    const lon = x / R * 180 / Math.PI, lat = (2 * Math.atan(Math.exp(y / R)) - Math.PI / 2) * 180 / Math.PI;
    const cx = Math.floor((lon - lon0) * mLon + half), cz = Math.floor((lat0 - lat) * mLat + half);
    if (cx < 0 || cz < 0 || cx >= size || cz >= size) return;
    classes[cls] = (classes[cls] || 0) + 1;
    returns[nr] = (returns[nr] || 0) + 1;
    if (cls === 7 || cls === 18) return;                  // noise (birds, wires, glints)
    used++;
    const i = cz * size + cx;
    if (cls === 2 || cls === 9) { if (z < ground[i]) ground[i] = z; return; }   // bare ground and water
    if (cls === 6) bclass[i] = 1;
    if (cls === 17) return;                               // bridge decks are drawn from the map, not as buildings
    if (echoes[i] < 65535) { echoes[i]++; if (nr === 1) singles[i]++; }
    if (nr === 1 && z > single[i]) single[i] = z;         // one echo only: a hard surface such as a roof
    if (rn === 1 && z > first[i]) first[i] = z;           // the first echo: the top of whatever is there
  };
  if (pointSource) await pointSource(visit);             // tests feed made-up points here
  else {
    const ept = await getJSON(fetchFn, `${eptBase}ept.json`);
    const srs = (ept.srs && (ept.srs.horizontal || '')) + '';
    if (srs !== '3857') throw new Error(`the scan uses EPSG:${srs}; only EPSG:3857 (usgs-lidar-public) is read`);
    // the square in Web Mercator, with a few metres to spare
    const corners = [[-half - 5, -half - 5], [half + 5, half + 5]].map(([dx, dz]) => toMercator(lat0 - dz / mLat, lon0 + dx / mLon));
    const box = [corners[0][0], corners[1][1], corners[1][0], corners[0][1]];
    const nodes = await nodesIn(fetchFn, eptBase, ept, box);
    const total = nodes.reduce((s, n) => s + n.points, 0);
    log(`${nodes.length} pieces of the scan, ${total.toLocaleString()} points to read`);
    let next = 0;
    async function worker() {
      while (next < nodes.length) {
        const node = nodes[next++];
        const r = await fetchFn(`${eptBase}ept-data/${node.key}.laz`);
        if (!r.ok) throw new Error(`ept-data/${node.key}.laz: HTTP ${r.status}`);
        decodeLaz(LazPerf, new Uint8Array(await r.arrayBuffer()), visit);
        done++;
        if (done % 20 === 0 || done === nodes.length) log(`read ${done} of ${nodes.length} pieces`);
      }
    }
    await Promise.all(Array.from({ length: parallel }, worker));
  }
  log(`${used.toLocaleString()} points inside the square (${read.toLocaleString()} read)`);

  // the bare ground, gaps filled
  const dtm = new Float32Array(cells);
  let groundCells = 0;
  for (let i = 0; i < cells; i++) { dtm[i] = Number.isFinite(ground[i]) ? ground[i] : NaN; groundCells += Number.isFinite(ground[i]); }
  fillGaps(dtm, size);

  // building footprints from the map, one cell wider all round, so place2mc's own outline always finds a value
  const rings = buildingRings(elements, lat0, lon0, size);
  const inBuilding = new Uint8Array(cells);
  for (const r of rings) fillRings(inBuilding, size, r, 1);
  const grown = new Uint8Array(cells);
  for (let z = 0; z < size; z++) for (let x = 0; x < size; x++) {
    let on = 0;
    for (let dz = -1; dz <= 1 && !on; dz++) for (let dx = -1; dx <= 1; dx++) {
      const zz = z + dz, xx = x + dx;
      if (zz >= 0 && xx >= 0 && zz < size && xx < size && inBuilding[zz * size + xx]) { on = 1; break; }
    }
    grown[z * size + x] = on;
  }

  // roofs: the highest single echo (or first echo) above the ground, in steps of roofStep metres; 255 = no echo
  const roofCodes = [];
  let measured = 0;
  for (let i = 0; i < cells; i++) {
    if (!grown[i]) continue;
    const top = Number.isFinite(single[i]) ? single[i] : first[i];
    if (Number.isFinite(top)) { roofCodes.push(Math.max(0, Math.min(254, Math.round((top - dtm[i] + 2) / roofStep)))); measured++; }
    else roofCodes.push(255);
  }



  // buildings the map does not have: patches of at least 30 m2, at least 3 m up, where nearly every echo is a lone one
  // (a hard roof; trees give several echoes per pulse), away from the mapped buildings
  const hard = new Uint8Array(cells);
  for (let i = 0; i < cells; i++) {
    if (grown[i] || !Number.isFinite(single[i]) || single[i] - dtm[i] < 3) continue;
    if (bclass[i] || (echoes[i] >= 2 && singles[i] >= 0.75 * echoes[i])) hard[i] = 1;
  }
  const label = new Int32Array(cells);
  const stack = [];
  let nextLabel = 0;
  const extra = new Uint8Array(cells);
  for (let s0 = 0; s0 < cells; s0++) {
    if (!hard[s0] || label[s0]) continue;
    const members = [];
    label[s0] = ++nextLabel; stack.push(s0);
    while (stack.length) {
      const i = stack.pop(); members.push(i);
      const z = i / size | 0, x = i % size;
      for (const [dz, dx] of [[-1, 0], [1, 0], [0, -1], [0, 1]]) {
        const zz = z + dz, xx = x + dx, j = zz * size + xx;
        if (zz >= 0 && xx >= 0 && zz < size && xx < size && hard[j] && !label[j]) { label[j] = nextLabel; stack.push(j); }
      }
    }
    if (members.length >= 30) for (const i of members) extra[i] = 1;
  }
  // trees: the canopy is everything the first echoes hit above the ground, away from buildings
  const chm = new Float32Array(cells);
  for (let i = 0; i < cells; i++) {
    const z = i / size | 0, x = i % size;
    let nearBuilding = bclass[i] === 1 || extra[i] === 1;
    for (let dz = -1; dz <= 1 && !nearBuilding; dz++) for (let dx = -1; dx <= 1; dx++) {
      const zz = z + dz, xx = x + dx;
      if (zz >= 0 && xx >= 0 && zz < size && xx < size && (inBuilding[zz * size + xx] || extra[zz * size + xx])) { nearBuilding = true; break; }
    }
    chm[i] = nearBuilding || !Number.isFinite(first[i]) ? 0 : Math.max(0, first[i] - dtm[i]);
  }
  const trees = findTrees(chm, size);
  const extraCodes = [];
  let extraCount = 0;
  for (let i = 0; i < cells; i++) if (extra[i]) { extraCodes.push(Math.max(0, Math.min(254, Math.round((single[i] - dtm[i] + 2) / roofStep)))); extraCount++; }
  log(`${rings.length} buildings, ${measured.toLocaleString()} roof cells measured, ${trees.length.toLocaleString()} trees`);

  // pack: the ground every dtmStep metres in centimetres as differences along each row (they stay small, so they
  // pack well); building cells as runs (in, out, in, ...) in reading order; roof codes; trees in decimetres
  const n = Math.ceil(size / dtmStep);
  let lo = Infinity;
  for (let i = 0; i < cells; i++) lo = Math.min(lo, dtm[i]);
  const dtmRows = new Int16Array(n * n);
  for (let z = 0; z < n; z++) {
    let prev = 0;
    for (let x = 0; x < n; x++) {
      let s = 0, c = 0;
      for (let dz = 0; dz < dtmStep; dz++) for (let dx = 0; dx < dtmStep; dx++) {
        const zz = z * dtmStep + dz, xx = x * dtmStep + dx;
        if (zz < size && xx < size) { s += dtm[zz * size + xx]; c++; }
      }
      const v = Math.round((s / c - lo) * 100);
      dtmRows[z * n + x] = v - prev; prev = v;
    }
  }
  const runs = [];
  let state = 0, run = 0;
  for (let i = 0; i < cells; i++) {
    if (grown[i] !== state) { runs.push(run); run = 0; state = grown[i]; }
    run++;
  }
  runs.push(run);
  const runBytes = new Uint32Array(runs);
  const extraRuns = [];
  state = 0; run = 0;
  for (let i = 0; i < cells; i++) {
    if (extra[i] !== state) { extraRuns.push(run); run = 0; state = extra[i]; }
    run++;
  }
  extraRuns.push(run);
  const treeInts = new Int16Array(trees.length * 4);
  trees.forEach((t, k) => { for (let j = 0; j < 4; j++) treeInts[4 * k + j] = Math.round(t[j] * 10); });
  const pack = async (arr) => b64(await gzip(new Uint8Array(arr.buffer, arr.byteOffset, arr.byteLength)));
  const result = {
    v: 1, ept: eptBase, lat0, lon0, size, points: used, groundCells, classes, returns,
    dtm: { step: dtmStep, n, low: lo, unit: 0.01, rowDiffInt16: await pack(dtmRows) },
    roofs: { step: roofStep, offset: -2, runsUint32: await pack(runBytes), codesUint8: await pack(new Uint8Array(roofCodes)) },
    trees: { unit: 0.1, xzhrInt16: await pack(treeInts), count: trees.length },
    unmapped: { step: roofStep, offset: -2, cells: extraCount, runsUint32: await pack(new Uint32Array(extraRuns)),
                codesUint8: await pack(new Uint8Array(extraCodes)) },
  };
  if (keepGrids) result.grids = { dtm, single, first, chm, inBuilding, extra };
  return result;
}
