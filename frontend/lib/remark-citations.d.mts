type MarkdownNode = {
  type: string;
  value?: string;
  url?: string;
  children?: MarkdownNode[];
};

export declare function remarkCitationLinks(): (tree: MarkdownNode) => void;
