import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { remarkCitationLinks } from "../lib/remark-citations.mjs";

const askSource = readFileSync(new URL("../components/ask-workspace.tsx", import.meta.url), "utf8");
const layoutSource = readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8");
const markdownSource = readFileSync(new URL("../lib/markdown.ts", import.meta.url), "utf8");
const globalStyles = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
const packageJson = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));

test("research answers compile Markdown and LaTeX without enabling raw HTML", () => {
  assert.match(askSource, /<ReactMarkdown/);
  assert.match(askSource, /remarkPlugins=\{\[remarkGfm, remarkMath, remarkCitationLinks\]\}/);
  assert.match(askSource, /rehypeKatex/);
  assert.doesNotMatch(askSource, /rehypeRaw/);
  assert.match(layoutSource, /katex\/dist\/katex\.min\.css/);
  for (const dependency of ["react-markdown", "remark-gfm", "remark-math", "rehype-katex", "katex"]) {
    assert.equal(typeof packageJson.dependencies[dependency], "string");
  }
});

test("normalization accepts common LLM LaTex delimiters without touching fenced code", () => {
  assert.match(markdownSource, /split\(\/\(```\[\\s\\S\]\*\?```\)\/g\)/);
  assert.match(markdownSource, /replace\(\/\\\\\\\[\(\[\\s\\S\]\*\?\)\\\\\\\]\//);
  assert.match(markdownSource, /replace\(\/\\\\\\\(\(\[\\s\\S\]\*\?\)\\\\\\\)\//);
  assert.match(markdownSource, /begin\\\{\(equation/);
  assert.match(globalStyles, /research-markdown \.katex-display/);
});

test("citation links are created from Markdown text nodes without touching code or math", () => {
  const tree = { type: "root", children: [
    { type: "paragraph", children: [{ type: "text", value: "根拠 [1] です" }] },
    { type: "code", value: "array[1]" },
    { type: "inlineMath", value: "x_{[1]}" },
    { type: "link", url: "https://example.test", children: [{ type: "text", value: "資料 [2]" }] },
  ] };
  remarkCitationLinks()(tree);
  assert.equal(tree.children[0].children[1].type, "link");
  assert.equal(tree.children[0].children[1].url, "#paperpilot-citation-1");
  assert.equal(tree.children[1].value, "array[1]");
  assert.equal(tree.children[2].value, "x_{[1]}");
  assert.equal(tree.children[3].children[0].value, "資料 [2]");
});

test("untrusted answer images cannot trigger remote image requests", () => {
  assert.match(askSource, /img: \(\{ alt \}\) => <span/);
  assert.match(askSource, /画像は安全のため表示していません/);
});

test("normal non-audited LLM answers are not mislabeled as audit failures", () => {
  assert.match(askSource, /LLM使用 · 引用形式チェック済み/);
  assert.match(askSource, /lastMeta\.fallback_reason \? "LLM使用 · 根拠監査で保留"/);
});
