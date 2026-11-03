#!/usr/bin/env node
// Régénère les fonds de carte embarqués (frontend/src/geo/data/) :
//   npm --prefix frontend run build:geo
//
// Source : github.com/gregoiredavid/france-geojson, figée au commit SOURCE_COMMIT (version
// métropole « simplifiée », codes INSEE COG 2018, contours IGN Admin Express COG 2018,
// Licence Ouverte Etalab : citer la source, voir README « Fonds de carte »).
// L'empreinte SHA-256 garantit que l'entrée n'a pas changé ; la sortie est
// déterministe (même entrée, mêmes paramètres -> mêmes octets).
//
// Le script copie aussi data/departements.json (référentiel D-001) dans src/geo/data/ :
// l'étape Node du Dockerfile ne voit que frontend/, l'appli ne peut donc pas importer ../data.
// Un test vitest vérifie que la copie est identique à l'original.

import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { topology } from "topojson-server";
import { presimplify, simplify, quantile } from "topojson-simplify";
import { mergeArcs, quantize } from "topojson-client";

const SOURCE_COMMIT = "5d34ee6d0140c29f785fdb047d9329f1aab58833";
const BASE = `https://raw.githubusercontent.com/gregoiredavid/france-geojson/${SOURCE_COMMIT}`;

// Une seule couche source : les régions sont reconstruites à partir des départements
// (fusion par region_code du référentiel), pour que les deux couches partagent leurs arcs
// et que les frontières coïncident exactement à la superposition.
const SOURCE = {
  url: `${BASE}/departements-version-simplifiee.geojson`,
  sha256: "c08a1ad48299763b50ef649f05a911c1021500c94719a1aa22a9d06191e43fc3",
};
const OUT = "france.topo.json";

// Construction sur une grille fine (10^5, raccorde les frontières communes), simplification
// Visvalingam (part des points conservés), puis quantification finale (grille 10^4 sur
// l'emprise, ~100 m, arcs codés en deltas entiers) : réglés pour rester sous ~150 kB.
const BUILD_QUANTIZATION = 1e5;
const QUANTIZATION = 1e4;
const KEEP_FRACTION = 0.5;

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, "..", "src", "geo", "data");
const referential = join(here, "..", "..", "data", "departements.json");

async function download({ url, sha256 }) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  const bytes = Buffer.from(await res.arrayBuffer());
  const digest = createHash("sha256").update(bytes).digest("hex");
  if (digest !== sha256) {
    throw new Error(`${url}: SHA-256 ${digest}, attendu ${sha256}`);
  }
  return JSON.parse(bytes.toString("utf8"));
}

function build(geojson, departments) {
  // Ne garder que code (chaîne, jamais converti en nombre : « 2A », « 01 ») et nom.
  const features = geojson.features.map((f) => {
    const code = f.properties.code;
    if (typeof code !== "string") throw new Error(`departements: code non textuel ${code}`);
    return { type: "Feature", properties: { code, nom: f.properties.nom }, geometry: f.geometry };
  });
  let topo = topology({ departements: { type: "FeatureCollection", features } }, BUILD_QUANTIZATION);
  topo = presimplify(topo);
  topo = simplify(topo, quantile(topo, KEEP_FRACTION));
  topo = quantize(topo, QUANTIZATION);

  // Régions = fusion des départements par region_code (ordre du référentiel), sur les mêmes arcs.
  const byCode = new Map(topo.objects.departements.geometries.map((g) => [g.properties.code, g]));
  const regions = new Map();
  for (const d of departments) {
    const g = byCode.get(d.code);
    if (!g) throw new Error(`département ${d.code} absent du fond de carte`);
    if (!regions.has(d.region_code)) regions.set(d.region_code, { name: d.region_name, members: [] });
    regions.get(d.region_code).members.push(g);
  }
  if (byCode.size !== departments.length) {
    throw new Error(`${byCode.size} départements dans le fond de carte, ${departments.length} dans le référentiel`);
  }
  const geometries = [...regions].map(([code, { name, members }]) => ({
    ...mergeArcs(topo, members),
    properties: { code, nom: name },
  }));
  topo.objects.regions = { type: "GeometryCollection", geometries };
  return topo;
}

const referentialBytes = await readFile(referential);
const departments = JSON.parse(referentialBytes.toString("utf8"));
const topo = build(await download(SOURCE), departments);
const text = JSON.stringify(topo) + "\n";
const bytes = Buffer.byteLength(text, "utf8");

await mkdir(outDir, { recursive: true });
await writeFile(join(outDir, OUT), text);
for (const [name, object] of Object.entries(topo.objects)) {
  console.log(`${OUT} / ${name} : ${object.geometries.length} zones`);
}
console.log(`${OUT} : ${topo.arcs.length} arcs, ${bytes} octets`);

await writeFile(join(outDir, "departements.json"), referentialBytes);
console.log("departements.json copié depuis data/");
