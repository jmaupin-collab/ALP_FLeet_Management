import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "src");
const pageDir = join(root, "pages");
let failed = false;
for (const file of readdirSync(pageDir)) {
  if (!file.endsWith(".js") && !file.endsWith(".jsx")) continue;
  const text = readFileSync(join(pageDir, file), "utf8");
  if (text.includes("mockData")) {
    console.error(`${file} still imports mock data. Production views must use the API.`);
    failed = true;
  }
}
if (failed) process.exit(1);
console.log("lint: pages do not depend on mockData");
