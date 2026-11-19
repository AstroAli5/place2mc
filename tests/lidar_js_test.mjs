// Run from tests/: npm install laz-perf@0.0.6, put PDAL's test/data/ept/lone-star-laszip in ept-test/, then node lidar_js_test.mjs
// Tests for app/lidar.js. 1) the LAZ reader on PDAL's sample scan; 2) the whole job on a made-up scan with a
// sloping ground, a gabled house, a flat block with a tower and three trees, written to synthetic.json for the
// Python side to check.
import { readFile, writeFile } from 'node:fs/promises';
import lp from 'laz-perf';
const createLazPerf = lp.createLazPerf || lp.default || lp;
import * as L from '../app/lidar.js';

const LazPerf = await createLazPerf();
const base = process.argv[2] || 'ept-test/';
const ept = JSON.parse(await readFile(base + 'ept.json'));
const hier = JSON.parse(await readFile(base + 'ept-hierarchy/0-0-0-0.json'));
let count = 0, bad = 0;
const cls = {}, rets = {};
for (const key of Object.keys(hier)) {
  const bytes = new Uint8Array(await readFile(base + `ept-data/${key}.laz`));
  L.decodeLaz(LazPerf, bytes, (x, y, z, rn, nr, c) => {
    count++;
    if (x < ept.bounds[0] - 1 || x > ept.bounds[3] + 1 || y < ept.bounds[1] - 1 || y > ept.bounds[4] + 1 || z < ept.bounds[2] - 1 || z > ept.bounds[5] + 1) bad++;
    if (rn < 1 || rn > nr) bad++;
    cls[c] = (cls[c] || 0) + 1; rets[nr] = (rets[nr] || 0) + 1;
  });
}
console.log(`sample: ${count} points (ept.json: ${ept.points}) ${count === ept.points ? 'OK' : 'MISMATCH'}; ${bad} outside the bounds or with a bad return number`);
console.log('  classes', JSON.stringify(cls), 'returns', JSON.stringify(rets));

// ---- made-up scan
const lat0 = 40.0465, lon0 = -76.32, size = 160, half = size / 2;
const [mLat, mLon] = L.metresPerDegree(lat0);
const toLL = (x, z) => [lat0 - (z - half) / mLat, lon0 + (x - half) / mLon];
let seed = 7;
const rnd = () => {           // mulberry32
  seed = (seed + 0x6D2B79F5) | 0;
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};
const groundAt = (x, z) => 100 + 0.03 * x - 0.01 * z;
const house = { x0: 40, x1: 60, z0: 40, z1: 52, eave: 6, ridge: 10 };      // ridge along x at z = 46
const block = { x0: 90, x1: 115, z0: 30, z1: 55, top: 12, tower: [100, 104, 38, 42, 21] };
const trees = [[40, 110, 15, 5], [70, 120, 9, 3], [120, 110, 20, 6]];       // x, z, height, crown radius
function surfaceAt(x, z) {
  if (x >= house.x0 && x < house.x1 && z >= house.z0 && z < house.z1)
    return ['roof', groundAt(x, z) + house.eave + (house.ridge - house.eave) * (1 - Math.abs(z - 46) / 6)];
  if (x >= block.x0 && x < block.x1 && z >= block.z0 && z < block.z1) {
    const [tx0, tx1, tz0, tz1, th] = block.tower;
    return ['roof', groundAt(x, z) + (x >= tx0 && x < tx1 && z >= tz0 && z < tz1 ? th : block.top)];
  }
  if (x >= 130 && x < 140 && z >= 70 && z < 78) return ['roof', groundAt(x, z) + 7];   // a shed the map lacks
  if (x >= 115 && x < 119 && z >= 40 && z < 45) return ['roof', groundAt(x, z) + 26];  // a tower bay the outline leaves out
  for (const [tx, tz, h, r] of trees) {
    const d = Math.hypot(x - tx, z - tz);
    if (d < r) return ['tree', groundAt(x, z) + h * (1 - 0.5 * (d / r) ** 2)];
  }
  return ['ground', groundAt(x, z)];
}
const points = [];
for (let i = 0; i < size * size * 4; i++) {
  const x = rnd() * size, z = rnd() * size;
  const [kind, top] = surfaceAt(x, z);
  const [lat, lon] = toLL(x, z);
  const [mx, my] = L.toMercator(lat, lon);
  if (kind === 'ground') points.push([mx, my, top, 1, 1, 2]);
  else if (kind === 'roof') points.push([mx, my, top, 1, 1, 1]);
  else {                                  // a tree: a first echo on the canopy, a last echo on the ground below
    points.push([mx, my, top - rnd() * 0.3, 1, 2, 1]);
    points.push([mx, my, groundAt(x, z), 2, 2, 2]);
  }
}
points.push([...L.toMercator(...toLL(10, 10)), 400, 1, 1, 7]);     // a bird: noise, must be ignored
const elements = [
  { type: 'way', id: 1, tags: { building: 'house' }, geometry: [[40, 40], [60, 40], [60, 52], [40, 52], [40, 40]].map(([x, z]) => { const [lat, lon] = toLL(x, z); return { lat, lon }; }) },
  { type: 'way', id: 2, tags: { building: 'university' }, geometry: [[90, 30], [115, 30], [115, 55], [90, 55], [90, 30]].map(([x, z]) => { const [lat, lon] = toLL(x, z); return { lat, lon }; }) },
];
const result = await L.extractLidar({ lat0, lon0, size, elements, LazPerf, log: (m) => console.log('  ' + m),
  pointSource: async (visit) => { for (const p of points) visit(...p); } });
await writeFile('synthetic.json', JSON.stringify(result));
await writeFile('synthetic_elements.json', JSON.stringify({ elements }));
const sizeKB = (JSON.stringify(result).length / 1024).toFixed(1);
console.log(`synthetic: ${result.trees.count} trees, ${result.unmapped.cells} cells of unmapped building (80 there), packed result ${sizeKB} KB`);
