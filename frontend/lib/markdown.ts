/**
 * Normalise the math delimiters commonly emitted by chat models before handing
 * the text to remark-math.  remark-math deliberately accepts `$...$` and
 * `$$...$$`; ChatGPT-style `\\(...\\)` / `\\[...\\]` notation otherwise
 * remains ordinary text in the conversation.
 */
export function normalizeResearchMarkdown(content: string): string {
  const normalized = content.replace(/\r\n?/g, "\n");

  const normalizeOutsideCode = (segment: string) => segment
    // A model occasionally serialises a delimiter twice while producing JSON.
    .replace(/\\\\(?=[\[\]()])/g, "\\")
    // `\\nobreak` is a layout command, not mathematical content.  Remove it
    // when it appears directly before a closing delimiter instead of letting
    // it become a visible, broken command in an inline expression.
    .replace(/\\nobreak(?=\$)/g, "")
    .replace(/\\\[([\s\S]*?)\\\]/g, (_match, expression: string) => {
      const trimmed = expression.trim();
      return trimmed ? `$$\n${trimmed}\n$$` : "";
    })
    .replace(/\\\(([\s\S]*?)\\\)/g, (_match, expression: string) => {
      const trimmed = expression.trim();
      return trimmed ? `$${trimmed}$` : "";
    })
    // Some models emit a bare LaTeX display environment rather than Markdown
    // delimiters.  remark-math only parses the latter, so make the expression
    // explicit while retaining its original LaTeX body for KaTeX.
    .replace(/\\begin\{(equation\*?|aligned|align\*?|gather\*?)\}([\s\S]*?)\\end\{\1\}/g, (_match, environment: string, body: string) => {
      const trimmed = body.trim();
      if (!trimmed) return "";
      // KaTeX accepts aligned/gathered inside display math, whereas LaTeX's
      // top-level align/gather/equation environments are not standalone KaTeX
      // functions.  Convert only the wrapper; the mathematical body is kept.
      if (environment.startsWith("equation")) return `$$\n${trimmed}\n$$`;
      const katexEnvironment = environment.startsWith("align") ? "aligned" : environment.startsWith("gather") ? "gathered" : environment;
      return `$$\n\\begin{${katexEnvironment}}\n${trimmed}\n\\end{${katexEnvironment}}\n$$`;
    });

  // Markdown code fences are literal source material.  Do not turn examples
  // such as `\\(x\\)` inside them into rendered math.
  return normalized.split(/(```[\s\S]*?```)/g).map((segment, index) => (
    index % 2 ? segment : normalizeOutsideCode(segment)
  )).join("");
}
