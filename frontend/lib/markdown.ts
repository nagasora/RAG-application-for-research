/**
 * Normalise the math delimiters commonly emitted by chat models before handing
 * the text to remark-math.  remark-math deliberately accepts `$...$` and
 * `$$...$$`; ChatGPT-style `\\(...\\)` / `\\[...\\]` notation otherwise
 * remains ordinary text in the conversation.
 */
export function normalizeResearchMarkdown(content: string): string {
  const normalized = content.replace(/\r\n?/g, "\n");

  return normalized
    // A model occasionally serialises a delimiter twice while producing JSON.
    .replace(/\\\\([\[\]()])/g, "\\$1")
    // `\\nobreak` is a layout command, not mathematical content.  When its
    // leading slash is decoded as a JSON newline it breaks an otherwise valid
    // inline expression such as `$x(t)\\nobreak$`.
    .replace(/\nobreak(?=\$)/g, "")
    .replace(/\\\[([\s\S]*?)\\\]/g, (_match, expression: string) => {
      const trimmed = expression.trim();
      return trimmed ? `$$\n${trimmed}\n$$` : "";
    })
    .replace(/\\\(([\s\S]*?)\\\)/g, (_match, expression: string) => {
      const trimmed = expression.trim();
      return trimmed ? `$${trimmed}$` : "";
    });
}
