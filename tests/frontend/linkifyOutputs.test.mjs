import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
function load(name, imports = {}) {
  const source = readFileSync(new URL(`../../web/frontend/src/lib/${name}.ts`, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  runInNewContext(outputText, { exports, require: id => imports[id], window: { location: { pathname: '/' } } });
  return exports;
}
const api = load('api');
const { linkifyOutputs, rewriteLocalLinks } = load('linkifyOutputs', { './api': api });

test('absolute Markdown report links retain their label and open the content preview', () => {
  const input = '[查看复盘](/Users/example/Easel/outputs/示例项目/报告.md)';
  const expected = '[查看复盘](#/outputs/示例项目/报告.md)';
  const actual = linkifyOutputs(input);
  assert.equal(actual, expected.replace(/示例项目|报告.md/g, encodeURIComponent));
  assert.equal(linkifyOutputs(actual), actual);
});

test('images and angle-wrapped paths with spaces resolve through media or content routes', () => {
  assert.equal(linkifyOutputs('![图](/Users/example/Easel/outputs/a/cover.png)'), '![图](/api/media/a/cover.png)');
  assert.equal(linkifyOutputs('[报告](</Users/example/Easel/outputs/a b/report.md>)'), '[报告](#/outputs/a%20b/report.md)');
  assert.equal(linkifyOutputs('[报告](outputs/a/report.md)'), '[报告](#/outputs/a/report.md)');
});

test('external URLs, commands, converted links and traversal targets remain unchanged', () => {
  for (const input of [
    '[外部](https://example.com/outputs/report.md)',
    '[外部](//example.com/outputs/report.md)',
    '```\n[命令](/Users/example/Easel/outputs/a.md)\n```',
    '`cat outputs/a.md`',
    '[已处理](/api/media/a.md)',
    '[非法](outputs/../secret.md)',
    '[非法](outputs/%2e%2e/secret.md)',
    '[非法](outputs/a/./report.md)',
    '[损坏](outputs/%ZZ.md)',
    '[损坏](outputs/\uD800.md)',
  ]) assert.equal(linkifyOutputs(input), input);
});

test('Windows paths and encoded file names resolve without corrupting their destination', () => {
  assert.equal(linkifyOutputs('[报告](C:\\Easel\\outputs\\demo\\report.md)'), '[报告](#/outputs/demo/report.md)');
  assert.equal(linkifyOutputs('[报告](outputs/demo/a%20b.md)'), '[报告](#/outputs/demo/a%20b.md)');
  assert.equal(linkifyOutputs('[目录](outputs/demo/)'), '[目录](#/outputs/demo)');
});

test('multiple previewable Markdown output links resolve as media for gallery detection', () => {
  const input = '[卡片](outputs/cards/one.png) [网页](outputs/cards/two.html) [视频](outputs/cards/three.mp4)';
  assert.equal(linkifyOutputs(input),
    '[卡片](/api/media/cards/one.png) [网页](/api/media/cards/two.html) [视频](/api/media/cards/three.mp4)');
  assert.equal(linkifyOutputs('[PDF](</data/project/Easel/outputs/a b/card.pdf>)'),
    '[PDF](/api/media/a%20b/card.pdf)');
});

// 真实回归：agent 把产物目录写成 `/C:/Users/.../Easel/outputs/<项目>/assets`，
// 以前这种带前导斜杠 + 路径里带空格的形态没被识别，原样进 href，点开就是
// http://127.0.0.1:7860/C:/Users/... → {"detail":"Not Found"}。
const WIN = '/C:/Users/Administrator/WorkBuddy AI/2026-09-17-16-10-42/Easel/outputs';

test('leading-slash drive paths with spaces resolve instead of 404ing', () => {
  assert.equal(linkifyOutputs(`[分镜素材目录](<${WIN}/铁裤头奶爸图鉴/assets>)`),
    '[分镜素材目录](#/outputs/' + encodeURIComponent('铁裤头奶爸图鉴') + '/assets)');
  assert.equal(linkifyOutputs(`![封面](<${WIN}/a b/cover.png>)`), '![封面](/api/media/a%20b/cover.png)');
  assert.equal(linkifyOutputs(`[报告](<${WIN}/a b/report.md>)`), '[报告](#/outputs/a%20b/report.md)');
});

test('file:// URLs and bare drive paths resolve too', () => {
  assert.equal(linkifyOutputs('[图](file:///C:/a%20b/outputs/x/cover.png)'), '[图](/api/media/x/cover.png)');
  assert.equal(linkifyOutputs('[图](C:\\Users\\me\\MyWork\\outputs\\x\\cover.png)'), '[图](/api/media/x/cover.png)');
  // markdown 裸目标不允许空格：这种写法本来就解析不成链接，保持原样，不编坏链接
  const spaced = '[图](C:/Users/me/My Work/outputs/x/cover.png)';
  assert.equal(linkifyOutputs(spaced), spaced);
});

test('plain-text leading-slash drive paths become links, traversal still rejected', () => {
  assert.equal(linkifyOutputs('见 /C:/Users/me/Easel/outputs/demo/a.png 里'), '见 ![a.png](/api/media/demo/a.png) 里');
  assert.equal(linkifyOutputs(`[非法](<${WIN}/../secret.md>)`), `[非法](<${WIN}/../secret.md>)`);
});

test('rewriteLocalLinks fixes hrefs that survived markdown-level conversion', () => {
  const html = '<a href="/C:/Users/Administrator/WorkBuddy AI/Easel/outputs/a b/r.md" title="t">报告</a>'
    + '<img src="/C:/Users/Administrator/WorkBuddy AI/Easel/outputs/a b/c.png">';
  const out = rewriteLocalLinks(html);
  assert.ok(out.includes('href="#/outputs/a%20b/r.md"'), out);
  assert.ok(out.includes('src="/api/media/a%20b/c.png"'), out);
  // 已经是站内地址/外链的一律不动
  for (const keep of [
    '<a href="/api/media/a.png">x</a>',
    '<a href="https://example.com/outputs/a.md">x</a>',
    '<a href="#/outputs/a.md">x</a>',
  ]) assert.equal(rewriteLocalLinks(keep), keep);
});
