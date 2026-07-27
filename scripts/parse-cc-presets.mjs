import fs from "node:fs";

function parseClaude(src) {
  const re =
    /name:\s*"([^"]+)",[\s\S]*?websiteUrl:\s*"([^"]+)",[\s\S]*?(?:isOfficial:\s*(true),)?[\s\S]*?settingsConfig:\s*\{([\s\S]*?)\n\s*\},/g;
  const out = [];
  let m;
  while ((m = re.exec(src))) {
    const [, name, website, official, config] = m;
    const base = (config.match(/ANTHROPIC_BASE_URL:\s*"([^"]+)"/) || [])[1] || "";
    const model = (config.match(/ANTHROPIC_MODEL:\s*"([^"]+)"/) || [])[1] || "";
    const small = (config.match(/ANTHROPIC_SMALL_FAST_MODEL:\s*"([^"]+)"/) || [])[1] || "";
    const authToken = /ANTHROPIC_AUTH_TOKEN/.test(config);
    const apiKey = /ANTHROPIC_API_KEY/.test(config);
    out.push({
      name,
      website,
      base_url: base.replace(/\/$/, ""),
      model,
      small_fast_model: small,
      auth_style: apiKey && !authToken ? "api_key" : "auth_token",
      official: official === "true",
    });
  }
  return out;
}

function parseCodex(src) {
  // Codex presets often use template strings with base_url inside config.toml blobs
  const chunks = src.split(/\n\s*\{\s*\n\s*name:\s*"/).slice(1);
  const out = [];
  for (const chunk of chunks) {
    const name = (chunk.match(/^([^"]+)/) || [])[1];
    const website = (chunk.match(/websiteUrl:\s*"([^"]+)"/) || [])[1] || "";
    const official = /isOfficial:\s*true/.test(chunk.slice(0, 500));
    const base =
      (chunk.match(/base_url\s*=\s*"([^"]+)"/) ||
        chunk.match(/ANTHROPIC_BASE_URL:\s*"([^"]+)"/) ||
        [])[1] || "";
    const model =
      (chunk.match(/model\s*=\s*"([^"]+)"/) || chunk.match(/model:\s*"([^"]+)"/) || [])[1] || "";
    const wire =
      (chunk.match(/wire_api\s*=\s*"([^"]+)"/) || [])[1] === "chat" ? "chat" : "responses";
    if (!name) continue;
    out.push({
      name,
      website,
      base_url: base.replace(/\/$/, ""),
      model,
      wire_api: wire,
      official,
    });
  }
  return out;
}

const temp = process.env.TEMP || "/tmp";
const claude = parseClaude(fs.readFileSync(`${temp}/claudePresets.ts`, "utf8"));
const codex = parseCodex(fs.readFileSync(`${temp}/codexPresets.ts`, "utf8"));
fs.writeFileSync(
  new URL("../ui/lib/cc-switch-raw.json", import.meta.url),
  JSON.stringify({ claude, codex }, null, 2),
);
console.log(`claude=${claude.length} codex=${codex.length}`);
console.log(
  "claude sample:",
  claude
    .filter((p) => p.base_url || p.official)
    .slice(0, 15)
    .map((p) => `${p.name} -> ${p.base_url || "(official)"} [${p.model}]`)
    .join("\n"),
);
console.log(
  "codex sample:",
  codex
    .slice(0, 15)
    .map((p) => `${p.name} -> ${p.base_url || "(?)"} [${p.model}]`)
    .join("\n"),
);
