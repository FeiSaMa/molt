/* Minimal, dependency-free Markdown renderer for chat messages.
 *
 * Input text is HTML-escaped before any formatting rule runs, and only a
 * fixed whitelist of tags is produced, so model or user output can never
 * inject markup. Links are limited to http(s), mailto and relative targets.
 *
 * Supported: headings, paragraphs, hard line breaks, bold / italic /
 * strikethrough, inline code, fenced code blocks, ordered / unordered lists
 * (nested), blockquotes, tables, horizontal rules, links and bare URLs.
 * Output is tolerant of partial input while a reply is still streaming. */
(function () {
  "use strict";

  var ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  var CODE_TOKEN = /\u0001(\d+)\u0002/g;
  var NUL = "\u0001", SOH = "\u0002";
  /* Bare URL in already-escaped text: stop at spaces, quotes, brackets and
   * CJK punctuation so trailing prose is not swallowed into the link. */
  var URL_IN_TEXT = /(^|[\s（(])(https?:\/\/[^\s<>()"'“”\u0001\u0002，。、；：！？）》」』】…]+)/g;

  function escapeHtml(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, function (ch) { return ESCAPES[ch]; });
  }

  function safeUrl(url) {
    var value = String(url == null ? "" : url).trim();
    if (!value) return "";
    if (/^(?:https?:|mailto:)/i.test(value)) return value;
    if (/^(?:\/|#)[^\s]*$/.test(value) && !/^\/\/\//.test(value)) return value;
    return "";
  }

  function link(href, label) {
    return '<a href="' + href + '" target="_blank" rel="noopener noreferrer">' + label + "</a>";
  }

  /* ---------- inline ---------- */
  function inline(text) {
    if (!text) return "";
    var codes = [];
    var out = escapeHtml(text);

    out = out.replace(/`([^`\n]+)`/g, function (match, code) {
      codes.push("<code>" + code + "</code>");
      return NUL + (codes.length - 1) + SOH;
    });

    out = out.replace(/!\[([^\]\n]*)\]\(((?:[^()\s]|\([^()\s]*\))+)\)/g, function (match, alt, url) {
      var href = safeUrl(url);
      return href ? link(href, "图片" + (alt ? "：" + alt : "")) : alt;
    });
    out = out.replace(/\[([^\]\n]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)/g, function (match, label, url) {
      var href = safeUrl(url);
      return href ? link(href, label) : label;
    });
    out = out.replace(/&lt;(https?:\/\/[^\s]+?)&gt;/g, function (match, url) { return link(url, url); });
    out = out.replace(URL_IN_TEXT, function (match, lead, url) {
      var body = url.split("&gt;")[0].split("&lt;")[0].split("&quot;")[0];
      var trimmed = body.replace(/[.,;:!?]+$/, "");
      var tail = url.slice(trimmed.length);
      return lead + link(trimmed, trimmed) + tail;
    });

    out = out.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/(^|[^\w*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    out = out.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
    out = out.replace(/ {2,}\n/g, "\n");
    out = out.replace(/\n/g, "<br>");
    out = out.replace(CODE_TOKEN, function (match, index) { return codes[Number(index)] || ""; });
    return out;
  }

  /* ---------- block helpers ---------- */
  var RE_FENCE = /^\s{0,3}(```+|~~~+)\s*([\w+#.-]*)\s*$/;
  var RE_HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
  var RE_HR = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
  var RE_QUOTE = /^\s{0,3}>\s?/;
  var RE_ITEM = /^([ \t]*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
  var RE_TABLE_SEP = /^\s*\|?\s*:?-{2,}:?(\s*\|\s*:?-{2,}:?)*\s*\|?\s*$/;

  function indentWidth(spaces) {
    return String(spaces == null ? "" : spaces).replace(/\t/g, "    ").length;
  }

  function splitRow(row) {
    var text = String(row).trim().replace(/^\|/, "").replace(/\|$/, "");
    return text.split("|").map(function (cell) { return cell.trim(); });
  }

  function isTableStart(lines, index) {
    if (index + 1 >= lines.length) return false;
    if (lines[index].indexOf("|") < 0) return false;
    if (!RE_TABLE_SEP.test(lines[index + 1])) return false;
    return splitRow(lines[index]).length === splitRow(lines[index + 1]).length;
  }

  function isBlockStart(lines, index) {
    var line = lines[index];
    if (RE_FENCE.test(line) || RE_HEADING.test(line) || RE_HR.test(line)) return true;
    if (RE_QUOTE.test(line) || RE_ITEM.test(line)) return true;
    return isTableStart(lines, index);
  }

  /* ---------- lists ---------- */
  function collectList(lines, start) {
    var items = [], index = start;
    while (index < lines.length) {
      var line = lines[index];
      if (/^\s*$/.test(line)) break;
      var item = line.match(RE_ITEM);
      if (item) {
        items.push({ indent: indentWidth(item[1]), ordered: /\d/.test(item[2]), text: [item[3]] });
        index++;
        continue;
      }
      if (!items.length || isBlockStart(lines, index)) break;
      items[items.length - 1].text.push(line.trim());
      index++;
    }
    return { items: items, next: index };
  }

  function listTree(items) {
    var root = { indent: -1, children: [] }, stack = [root];
    items.forEach(function (item) {
      while (stack.length > 1 && item.indent <= stack[stack.length - 1].indent) stack.pop();
      var node = { indent: item.indent, ordered: item.ordered, text: item.text.join("\n"), children: [] };
      stack[stack.length - 1].children.push(node);
      stack.push(node);
    });
    return root;
  }

  function renderListLevel(node) {
    var html = "", open = null;
    node.children.forEach(function (child) {
      var tag = child.ordered ? "ol" : "ul";
      if (open !== tag) {
        if (open) html += "</" + open + ">";
        html += "<" + tag + ">";
        open = tag;
      }
      html += "<li>" + inline(child.text);
      if (child.children.length) html += renderListLevel(child);
      html += "</li>";
    });
    if (open) html += "</" + open + ">";
    return html;
  }

  /* ---------- tables ---------- */
  function renderTable(lines, start) {
    var head = splitRow(lines[start]);
    var cols = head.length;
    var align = splitRow(lines[start + 1]).map(function (cell) {
      var left = /^:/.test(cell), right = /:$/.test(cell);
      if (left && right) return ' style="text-align:center"';
      if (right) return ' style="text-align:right"';
      return "";
    });
    var index = start + 2, rows = [];
    while (index < lines.length && lines[index].indexOf("|") >= 0 && !/^\s*$/.test(lines[index])) {
      var cells = splitRow(lines[index]);
      if (cells.length === 1 && !cells[0]) break;
      rows.push(cells);
      index++;
    }
    var html = "<table><thead><tr>";
    for (var c = 0; c < cols; c++) html += "<th" + (align[c] || "") + ">" + inline(head[c] || "") + "</th>";
    html += "</tr></thead><tbody>";
    rows.forEach(function (cells) {
      html += "<tr>";
      for (var i = 0; i < cols; i++) html += "<td" + (align[i] || "") + ">" + inline(cells[i] || "") + "</td>";
      html += "</tr>";
    });
    html += "</tbody></table>";
    return { html: html, next: index };
  }

  /* ---------- blocks ---------- */
  function blocks(source) {
    var lines = String(source == null ? "" : source).replace(/\r\n?/g, "\n").split("\n");
    var html = "", index = 0;

    while (index < lines.length) {
      var line = lines[index];
      if (/^\s*$/.test(line)) { index++; continue; }

      var fence = line.match(RE_FENCE);
      if (fence) {
        var marker = fence[1], lang = fence[2], code = [];
        index++;
        while (index < lines.length && !new RegExp("^\\s{0,3}" + marker.charAt(0) + "{" + marker.length + ",}\\s*$").test(lines[index])) {
          code.push(lines[index]);
          index++;
        }
        index++;
        var cls = lang ? ' class="lang-' + escapeHtml(lang) + '"' : "";
        html += "<pre><code" + cls + ">" + escapeHtml(code.join("\n")) + "</code></pre>";
        continue;
      }

      var heading = line.match(RE_HEADING);
      if (heading) {
        var level = heading[1].length;
        html += "<h" + level + ">" + inline(heading[2]) + "</h" + level + ">";
        index++;
        continue;
      }

      if (RE_HR.test(line)) { html += "<hr>"; index++; continue; }

      if (RE_QUOTE.test(line)) {
        var quoted = [];
        while (index < lines.length && RE_QUOTE.test(lines[index])) {
          quoted.push(lines[index].replace(RE_QUOTE, ""));
          index++;
        }
        html += "<blockquote>" + blocks(quoted.join("\n")) + "</blockquote>";
        continue;
      }

      if (isTableStart(lines, index)) {
        var table = renderTable(lines, index);
        html += table.html;
        index = table.next;
        continue;
      }

      if (RE_ITEM.test(line)) {
        var list = collectList(lines, index);
        html += renderListLevel(listTree(list.items));
        index = list.next;
        continue;
      }

      var paragraph = [line];
      index++;
      while (index < lines.length && !/^\s*$/.test(lines[index]) && !isBlockStart(lines, index)) {
        paragraph.push(lines[index]);
        index++;
      }
      html += "<p>" + inline(paragraph.join("\n")) + "</p>";
    }

    return html;
  }

  function render(source) {
    return blocks(source);
  }

  var api = { render: render, escapeHtml: escapeHtml };
  if (typeof window !== "undefined") window.MoltMarkdown = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
