import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const schema = resolve(frontendRoot, "openapi/job-hunt.openapi.json");
const generated = resolve(frontendRoot, "lib/api-generated.ts");
const cli = resolve(frontendRoot, "node_modules/.bin/openapi-typescript");

if (!existsSync(schema) || !existsSync(generated)) {
  console.error(
    "Generated API contract is missing. Run npm run api:export and npm run api:generate.",
  );
  process.exit(1);
}

const temporaryDirectory = mkdtempSync(join(tmpdir(), "job-hunt-openapi-"));
const temporaryGenerated = join(temporaryDirectory, "api-generated.ts");
try {
  execFileSync(cli, [schema, "-o", temporaryGenerated], {
    cwd: frontendRoot,
    stdio: "inherit",
  });
  if (readFileSync(generated, "utf8") !== readFileSync(temporaryGenerated, "utf8")) {
    console.error("Generated TypeScript API contract is stale. Run npm run api:generate.");
    process.exitCode = 1;
  }
} finally {
  rmSync(temporaryDirectory, { recursive: true, force: true });
}
