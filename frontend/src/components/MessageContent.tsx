import { Fragment, type ReactNode } from "react";

type MessageContentProps = {
  content: string;
};

const unorderedListPattern = /^\s*[-*]\s+(.+)$/;
const orderedListPattern = /^\s*\d+\.\s+(.+)$/;
const fencedCodePattern = /^```(\w+)?\s*$/;
const inlineTokenPattern =
  /(\[[^\]\n]+\]\((https?:\/\/[^\s)]+)\)|\*\*[^*\n]+?\*\*|`[^`\n]+?`|\*[^*\n]+?\*)/;

export function MessageContent({ content }: MessageContentProps) {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fencedCodeMatch = line.match(fencedCodePattern);
    if (fencedCodeMatch) {
      const language = fencedCodeMatch[1];
      const codeLines: string[] = [];
      index += 1;

      while (index < lines.length && !fencedCodePattern.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }

      if (index < lines.length) {
        index += 1;
      }

      blocks.push(
        <pre key={`code-${blocks.length}`}>
          <code className={language ? `language-${language}` : undefined}>{codeLines.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    const listPattern = unorderedListPattern.test(line) ? unorderedListPattern : orderedListPattern.test(line) ? orderedListPattern : null;
    if (listPattern) {
      const items: ReactNode[] = [];
      const ListTag = listPattern === orderedListPattern ? "ol" : "ul";

      while (index < lines.length) {
        const match = lines[index].match(listPattern);
        if (!match) {
          break;
        }

        items.push(<li key={`item-${blocks.length}-${items.length}`}>{renderInline(match[1].trim())}</li>);
        index += 1;
      }

      blocks.push(<ListTag key={`list-${blocks.length}`}>{items}</ListTag>);
      continue;
    }

    const paragraphLines: string[] = [];

    while (index < lines.length && !isBlockBoundary(lines[index])) {
      paragraphLines.push(lines[index].trimEnd());
      index += 1;
    }

    blocks.push(
      <p key={`paragraph-${blocks.length}`}>
        {paragraphLines.map((paragraphLine, paragraphIndex) => (
          <Fragment key={`line-${paragraphIndex}`}>
            {paragraphIndex > 0 ? <br /> : null}
            {renderInline(paragraphLine)}
          </Fragment>
        ))}
      </p>,
    );
  }

  return <div className="message-content">{blocks}</div>;
}

function isBlockBoundary(line: string) {
  return !line.trim() || fencedCodePattern.test(line) || unorderedListPattern.test(line) || orderedListPattern.test(line);
}

function renderInline(text: string) {
  const nodes: ReactNode[] = [];
  let remaining = text;
  let key = 0;

  while (remaining.length > 0) {
    const match = remaining.match(inlineTokenPattern);
    if (!match || match.index === undefined) {
      nodes.push(remaining);
      break;
    }

    if (match.index > 0) {
      nodes.push(remaining.slice(0, match.index));
    }

    const token = match[0];
    if (token.startsWith("[")) {
      const linkMatch = token.match(/^\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)$/);
      if (linkMatch) {
        nodes.push(
          <a key={`inline-${key}`} href={linkMatch[2]} target="_blank" rel="noreferrer">
            {renderInline(linkMatch[1])}
          </a>,
        );
      } else {
        nodes.push(token);
      }
    } else if (token.startsWith("**")) {
      nodes.push(<strong key={`inline-${key}`}>{renderInline(token.slice(2, -2))}</strong>);
    } else if (token.startsWith("`")) {
      nodes.push(<code key={`inline-${key}`}>{token.slice(1, -1)}</code>);
    } else {
      nodes.push(<em key={`inline-${key}`}>{renderInline(token.slice(1, -1))}</em>);
    }

    remaining = remaining.slice(match.index + token.length);
    key += 1;
  }

  return nodes;
}
