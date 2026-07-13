const CITATION_PATTERN = /(^|[\s。、,;；)])\[(\d+)\](?=$|[\s。、,;；)])/g;
const SKIP_NODES = new Set(["code", "inlineCode", "math", "inlineMath", "link", "linkReference"]);

function citationTextNodes(value) {
  const nodes = [];
  let cursor = 0;
  for (const match of value.matchAll(CITATION_PATTERN)) {
    const matchStart = match.index ?? 0;
    const citationStart = matchStart + match[1].length;
    if (citationStart > cursor) nodes.push({ type: "text", value: value.slice(cursor, citationStart) });
    nodes.push({
      type: "link",
      url: `#paperpilot-citation-${match[2]}`,
      children: [{ type: "text", value: `[${match[2]}]` }],
    });
    cursor = matchStart + match[0].length;
  }
  if (!nodes.length) return [{ type: "text", value }];
  if (cursor < value.length) nodes.push({ type: "text", value: value.slice(cursor) });
  return nodes;
}

export function remarkCitationLinks() {
  return tree => {
    const visit = node => {
      if (SKIP_NODES.has(node.type) || !node.children) return;
      node.children = node.children.flatMap(child => child.type === "text" && child.value
        ? citationTextNodes(child.value)
        : [child]);
      node.children.forEach(visit);
    };
    visit(tree);
  };
}
