import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const selector = readFileSync(new URL("../components/generation-model-selector.tsx", import.meta.url), "utf8");

test("generation selector renders and selects only the server generation catalog", () => {
  assert.match(selector, /getGenerationSettings\(controller\.signal\)/);
  assert.match(selector, /settings\?\.options \?\? \[\]/);
  assert.match(selector, /options\.map\(option => <option key=\{`\$\{option\.provider\}:\$\{option\.model\}`\} value=\{`\$\{option\.provider\}:\$\{option\.model\}`\}/);
  assert.match(selector, /const option = options\.find\(item => `\$\{item\.provider\}:\$\{item\.model\}` === event\.target\.value\)/);
  assert.match(selector, /generation_provider: option\.provider, generation_model: option\.model/);
});

test("generation selector does not retain the retired OpenAI model name", () => {
  assert.doesNotMatch(selector, /gpt-5\.4-nano/);
});
