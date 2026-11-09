/**
 * 本地工作区生成文件路径识别与 Markdown 预处理工具
 * 精准识别 AI 回复中的行内路径、Markdown 链接及裸文本生成路径，同时严格排除网页 URL、代码块与常规语法标识。
 */

export const AGENTHUB_FILE_SCHEME = '#agenthub-file=';

/** 高置信度的独立交付物扩展名（即使不带目录前缀也可直接识别） */
const STANDALONE_DELIVERABLE_EXTS = new Set([
  '.xlsx', '.xlsm', '.xls', '.csv', '.tsv',
  '.docx', '.doc', '.pptx', '.ppt', '.pdf',
  '.html', '.htm', '.md', '.markdown',
  '.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg',
  '.zip', '.7z', '.tar', '.gz',
]);

/** 完整支持直开的文件扩展名集合（需带目录/盘符/file:///，或属于独立交付物扩展名） */
const NAVIGABLE_FILE_EXTS = new Set([
  ...Array.from(STANDALONE_DELIVERABLE_EXTS),
  '.txt', '.log', '.rtf',
  '.json', '.jsonl', '.yaml', '.yml', '.toml', '.xml', '.ini', '.conf',
  '.py', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs',
  '.css', '.scss', '.less', '.sql',
  '.sh', '.bash', '.ps1', '.bat', '.cmd',
  '.java', '.go', '.rs', '.c', '.cpp', '.h', '.hpp', '.cs', '.php', '.rb',
  '.vue', '.svelte', '.mmd', '.mermaid',
  '.bmp', '.ico', '.mp3', '.wav', '.mp4', '.mov', '.webm',
  '.exe', '.msi', '.lnk',
]);

/** 常见技术名词或库名（防止误识别为本地文件名） */
const NON_FILE_TECH_TERMS = new Set([
  'node.js', 'next.js', 'react.js', 'vue.js', 'nuxt.js', 'three.js', 'd3.js',
  'chart.js', 'express.js', 'nest.js', 'electron.js', 'backbone.js', 'alpine.js',
  'ember.js', 'p5.js', 'pdf.js', 'reveal.js', 'highlight.js', 'echarts.js',
  'package.json', 'tsconfig.json', 'tailwind.config.ts', 'tailwind.config.js',
]);

const KNOWN_RELATIVE_PREFIX_RE =
  /^(?:\.\.?[/\\]|(?:output|reports|attachments|\.agenthub[/\\]attachments|dist|build|docs|src|scripts|skills|data|exports|artifacts|tmp|assets|public|pages|app|backend|frontend)[/\\])/i;

const WINDOWS_DRIVE_RE = /^[A-Za-z]:[/\\]/;

/**
 * 清洗并解包候选路径字符串（去掉外围引号、空白及行号锚点仅保留有效文件部分）
 */
export function cleanFilePathCandidate(raw: string): string {
  let text = (raw || '').trim();
  while (text.length >= 2 && text[0] === text[text.length - 1] && (text[0] === '`' || text[0] === '"' || text[0] === "'")) {
    text = text.slice(1, -1).trim();
  }
  return text;
}

/**
 * 剥离行号定位后缀 (#L10-L20 / :42) 以便判定真实扩展名
 */
function stripLineAnchorForExt(pathStr: string): string {
  let clean = cleanFilePathCandidate(pathStr);
  if (clean.toLowerCase().startsWith('file://')) {
    try {
      const parsed = new URL(clean);
      clean = decodeURIComponent(parsed.pathname || '');
    } catch {
      clean = clean.replace(/^file:\/\//i, '');
    }
  }
  clean = clean.replace(/#L?\d+(?:-L?\d+)?$/i, '');
  clean = clean.replace(/(?<=[^\\/:]):\d+(?::\d+)?$/, '');
  return clean.trim();
}

function getExtensionLower(pathStr: string): string {
  const base = pathStr.replace(/\\/g, '/').split('/').pop() || '';
  const dotIdx = base.lastIndexOf('.');
  if (dotIdx <= 0 || dotIdx === base.length - 1) return '';
  return base.slice(dotIdx).toLowerCase();
}

/**
 * 判断字符串是否为工作区内可安全直开/定位的本地文件路径。
 * 严格排除网页 URL、邮箱、CLI 命令字符串及常见框架专有名词。
 */
export function isNavigableLocalFilePath(raw: string): boolean {
  const cleaned = cleanFilePathCandidate(raw);
  if (!cleaned || cleaned.length < 3 || cleaned.length > 260) return false;
  if (/[\r\n\t<>|*]/.test(cleaned)) return false;

  const lower = cleaned.toLowerCase();

  // 严格放行外网 URL、自定义 URI Scheme (如 s:// / git://)、锚点、邮箱与注释风格路径
  if (/^(https?|mailto|tel|data|blob|javascript|ws|wss|ftp):/i.test(lower)) return false;
  if (/^[A-Za-z0-9+.-]+:\/\//.test(cleaned) && !lower.startsWith('file://')) return false;
  if (cleaned.startsWith('#') || cleaned.startsWith('//') || cleaned.startsWith('--')) return false;

  // 排除 Shell / 代码命令行语句特征（如 python foo.py --arg 或 cmd && cmd2）
  if (/(?:&&|\|\||;\s|\s--\w|\s-\w\s|=>|:=)/.test(cleaned)) return false;

  const pathForExt = stripLineAnchorForExt(cleaned);
  if (!pathForExt) return false;

  // file:// URI 且带具体路径即允许打开
  if (lower.startsWith('file:///') || lower.startsWith('file://')) {
    return pathForExt.length >= 3;
  }

  const ext = getExtensionLower(pathForExt);
  if (!ext || !NAVIGABLE_FILE_EXTS.has(ext)) return false;

  // 检查专有名词误匹配（仅当无目录分隔符时过滤常见 npm 库或泛指配置文件名）
  const hasDirSep = /[\\/]/.test(pathForExt);
  if (!hasDirSep && NON_FILE_TECH_TERMS.has(pathForExt.toLowerCase())) {
    return false;
  }

  // 带空格的场景限制：防止普通英文句子里出现 ".md 文件" 之类误命中，
  // 只有以明确盘符或工作区目录前缀开头时才允许文件名中存在空格。
  if (/\s/.test(pathForExt)) {
    const startsWithExplicitRoot =
      WINDOWS_DRIVE_RE.test(pathForExt) ||
      KNOWN_RELATIVE_PREFIX_RE.test(pathForExt);
    if (!startsWithExplicitRoot) return false;
    // 文件名里空格超过 5 个大概率是报错信息或自然语言段落
    if ((pathForExt.match(/\s/g) || []).length > 5) return false;
  }

  // 1. Windows 盘符绝对路径或 Unix 绝对路径
  if (WINDOWS_DRIVE_RE.test(pathForExt) || pathForExt.startsWith('/')) {
    return !/\s\/\s/.test(pathForExt);
  }

  // 2. 带有明确的相对路径分隔符（./foo.py, output/bar.xlsx 等）
  if (hasDirSep) {
    // 排除无协议纯域名如 github.com/user/repo/README.md 或 example.org/a.html
    const firstSeg = pathForExt.replace(/\\/g, '/').split('/')[0].toLowerCase();
    if (/^[a-z0-9-]+\.(?:com|org|net|io|cn|dev|app|ai|edu|gov|co|me|cc|tv|xyz)$/i.test(firstSeg)) {
      return false;
    }
    return !/[{}()[\]$]/.test(pathForExt.replace(/\([^)]*\)/g, ''));
  }

  // 3. 无路径分隔符时，仅接受明确的高置信度独立交付物文件名（如 report.xlsx / schedule.pdf）
  // 避免将 prose 里的 "main.py"、"utils.ts" 泛化成过于激进的芯片
  if (STANDALONE_DELIVERABLE_EXTS.has(ext)) {
    return !NON_FILE_TECH_TERMS.has(pathForExt.toLowerCase()) && !/^v?\d+\.\d+/i.test(pathForExt);
  }

  return false;
}

/** 匹配裸本地文件路径的正文正则（仅用于非代码块、非反引号、非 Markdown 链接区间） */
const BARE_PATH_EXT_GROUP =
  '(?:xlsm|xlsx?|csv|tsv|docx?|pptx?|pdf|html?|markdown|md|txt|jsonl?|ya?ml|xml|svg|png|jpe?g|webp|gif|py|tsx?|jsx?|sql|log|css|sh|ps1|bat|cmd|zip|7z)(?![A-Za-z0-9_])';

const HIGH_CONF_DELIVERABLE_EXT_GROUP =
  '(?:xlsm|xlsx?|csv|docx?|pptx?|pdf|html?|md|png|jpe?g|webp|gif|svg|zip)(?![A-Za-z0-9_])';

const BARE_LOCAL_PATH_REGEX = new RegExp(
  [
    // 1. file:/// URI
    'file:\\/\\/\\/[^\\s<>"\'`\\])\\}]+',
    // 2. Windows 盘符路径 (C:\... 或 D:/...)，禁止匹配协议如 https:// 中的 s://
    `(?<![A-Za-z0-9_])[A-Za-z]:[\\\\/](?![\\\\/])[^\\r\\n\\t,;!?，。；！？、"'<>\`\\])]+?\\.${BARE_PATH_EXT_GROUP}(?:#L?\\d+(?:-L?\\d+)?)?`,
    // 3. 明确前缀的相对路径 (./... 或 output/... 等)
    `(?:\\.{1,2}[\\\\/]|(?:output|reports|attachments|\\.agenthub[\\\\/]attachments|docs|src|scripts|skills|data|exports|artifacts|assets)[\\\\/])[^\\r\\n\\t\\s,;!?，。；！？、"'<>\`\\])]+?\\.${BARE_PATH_EXT_GROUP}(?:#L?\\d+(?:-L?\\d+)?)?`,
    // 4. 无 "." 开头的常见自定义子目录 + 高置信交付物（避开域名 example.com/... 误伤）
    `(?<![A-Za-z0-9_.:/\\\\-])[A-Za-z0-9_\\u4e00-\\u9fa5-]+(?:[\\\\/][A-Za-z0-9_\\u4e00-\\u9fa5().-]+)+\\.${HIGH_CONF_DELIVERABLE_EXT_GROUP}(?:#L?\\d+(?:-L?\\d+)?)?`,
  ].join('|'),
  'gi'
);

/**
 * 对普通 Markdown 文本段落做一次扫描：
 * 将非代码块、非行内反引号、非既有链接中的裸本地生成路径自动包装为 agenthub-file:// 链接，
 * 以便 ReactMarkdown 直接渲染为可点击直开芯片，同时 100% 保留代码块与网页 URL。
 */
export function linkifyBareFilePathsInMarkdown(markdown: string): string {
  if (!markdown) return '';

  const output: string[] = [];
  let i = 0;
  const n = markdown.length;
  let proseBuffer = '';

  const flushProse = () => {
    if (!proseBuffer) return;
    output.push(transformBarePathsInProse(proseBuffer));
    proseBuffer = '';
  };

  while (i < n) {
    // 1. 跳过围栏代码块 (``` 或 ~~~，要求在行首或仅前置空白)
    if (
      (i === 0 || markdown[i - 1] === '\n') &&
      (markdown.startsWith('```', i) || markdown.startsWith('~~~', i))
    ) {
      flushProse();
      const fence = markdown.startsWith('```', i) ? '```' : '~~~';
      const lineEnd = markdown.indexOf('\n', i);
      if (lineEnd === -1) {
        output.push(markdown.slice(i));
        i = n;
        break;
      }
      const closeIdx = markdown.indexOf(`\n${fence}`, lineEnd);
      if (closeIdx === -1) {
        // 流式未闭合代码块：整段原样保留
        output.push(markdown.slice(i));
        i = n;
        break;
      }
      const afterCloseLine = markdown.indexOf('\n', closeIdx + 1);
      const endPos = afterCloseLine === -1 ? n : afterCloseLine + 1;
      output.push(markdown.slice(i, endPos));
      i = endPos;
      continue;
    }

    // 2. 跳过行内反引号代码 `...` 或 ``...``
    if (markdown[i] === '`') {
      let tickLen = 0;
      while (i + tickLen < n && markdown[i + tickLen] === '`') tickLen++;
      const closer = '`'.repeat(tickLen);
      const closePos = markdown.indexOf(closer, i + tickLen);
      if (closePos !== -1) {
        flushProse();
        output.push(markdown.slice(i, closePos + tickLen));
        i = closePos + tickLen;
        continue;
      }
    }

    // 3. 跳过既有 Markdown 链接/图片 [label](href) 或 ![alt](src)
    if ((markdown[i] === '!' && markdown[i + 1] === '[') || markdown[i] === '[') {
      const startIdx = i;
      const bracketStart = markdown[i] === '!' ? i + 1 : i;
      const closeBracket = markdown.indexOf(']', bracketStart + 1);
      if (closeBracket !== -1 && markdown[closeBracket + 1] === '(') {
        const lineBreakInside = markdown.slice(startIdx, closeBracket).includes('\n');
        const closeParen = markdown.indexOf(')', closeBracket + 2);
        if (!lineBreakInside && closeParen !== -1 && !markdown.slice(closeBracket, closeParen).includes('\n')) {
          flushProse();
          output.push(markdown.slice(startIdx, closeParen + 1));
          i = closeParen + 1;
          continue;
        }
      }
    }

    proseBuffer += markdown[i];
    i += 1;
  }

  flushProse();
  return output.join('');
}

function transformBarePathsInProse(prose: string): string {
  return prose.replace(BARE_LOCAL_PATH_REGEX, (match, offset, fullStr) => {
    const prevChar = offset > 0 ? fullStr[offset - 1] : '';
    // 防止命中 URL 内部片段如 https://host/output/a.png 或更长相对路径中的中间段
    if (prevChar && /[A-Za-z0-9_.\\/-]/.test(prevChar)) {
      return match;
    }
    const prefixWindow = fullStr.slice(Math.max(0, offset - 60), offset);
    if (/https?:\/\/\S*$/i.test(prefixWindow)) {
      return match;
    }
    if (!isNavigableLocalFilePath(match)) {
      return match;
    }
    return `[${match}](${AGENTHUB_FILE_SCHEME}${encodeURIComponent(match)})`;
  });
}

/**
 * 归一化文件路径键以便跨引用格式去重（例如 `./output/a.xlsx` 与 `output/a.xlsx` 视为同一文件）
 */
function normalizeDedupeKey(filePath: string): string {
  let clean = cleanFilePathCandidate(filePath).replace(/\\/g, '/');
  if (clean.startsWith('./')) clean = clean.slice(2);
  return clean.toLowerCase();
}

/**
 * 从 AI 回复正文中抽取所有去重后的生成文件路径列表（跳过围栏代码块，避免把示例脚本路径当作结果）
 */
export function extractGeneratedFilePaths(markdown: string, limit = 8): string[] {
  if (!markdown) return [];

  // 剥离围栏代码块后再检索
  const withoutCodeBlocks = markdown
    .replace(/```[\s\S]*?(?:```|$)/g, '\n')
    .replace(/~~~[\s\S]*?(?:~~~|$)/g, '\n');

  const collected = new Map<string, string>();
  const tryCollect = (raw: string) => {
    const cleaned = cleanFilePathCandidate(raw);
    if (!isNavigableLocalFilePath(cleaned)) return;
    // 若与“只包含文件名”重复但当前带路径前缀，优先保留带路径的版本
    const key = normalizeDedupeKey(cleaned);
    if (!collected.has(key)) {
      collected.set(key, cleaned);
    }
  };

  // 1. 行内代码 `...`
  const inlineCodeRe = /`([^`\r\n]+)`/g;
  let m: RegExpExecArray | null;
  while ((m = inlineCodeRe.exec(withoutCodeBlocks)) !== null) {
    tryCollect(m[1]);
  }

  // 2. Markdown 链接 [text](target)（排除 ![img] 与外网链接）
  const mdLinkRe = /(?<!!)\[[^\]]*\]\(([^)\s]+)\)/g;
  while ((m = mdLinkRe.exec(withoutCodeBlocks)) !== null) {
    const href = m[1];
    if (href.startsWith(AGENTHUB_FILE_SCHEME)) {
      tryCollect(decodeURIComponent(href.slice(AGENTHUB_FILE_SCHEME.length)));
    } else {
      tryCollect(href);
    }
  }

  // 3. 裸文本生成文件路径（带上偏移防 URL 误截检查）
  const bareExecRe = new RegExp(BARE_LOCAL_PATH_REGEX.source, 'gi');
  while ((m = bareExecRe.exec(withoutCodeBlocks)) !== null) {
    const matchStr = m[0];
    const offset = m.index;
    const prevChar = offset > 0 ? withoutCodeBlocks[offset - 1] : '';
    if (prevChar && /[A-Za-z0-9_.\\/-]/.test(prevChar)) continue;
    const prefixWindow = withoutCodeBlocks.slice(Math.max(0, offset - 24), offset);
    if (/https?:\/\/\S*$/i.test(prefixWindow)) continue;
    tryCollect(matchStr);
  }

  // 二次去重：若已存在 ./output/foo.xlsx，则折叠掉裸文件名 foo.xlsx
  const entries = Array.from(collected.entries());
  const fullPathsWithDir = new Set(
    entries
      .filter(([, val]) => /[\\/]/.test(val))
      .map(([, val]) => (val.replace(/\\/g, '/').split('/').pop() || '').toLowerCase())
  );

  const finalPaths: string[] = [];
  for (const [, val] of entries) {
    const hasDir = /[\\/]/.test(val);
    if (!hasDir && fullPathsWithDir.has(val.toLowerCase())) {
      continue;
    }
    finalPaths.push(val);
    if (finalPaths.length >= limit) break;
  }
  return finalPaths;
}

/**
 * 提取文件名展示标签（完整路径保留在 title 提示里）
 */
export function extractFileBaseName(filePath: string): string {
  const clean = stripLineAnchorForExt(filePath).replace(/\\/g, '/');
  return clean.split('/').pop() || clean || filePath;
}
