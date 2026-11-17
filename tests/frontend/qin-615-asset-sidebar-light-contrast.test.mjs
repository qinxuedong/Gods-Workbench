import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const css = readFileSync(new URL('../../web/css/pages/asset-manager.css', import.meta.url), 'utf8');
const embed = readFileSync(new URL('../../web/embeds/asset-manager.html', import.meta.url), 'utf8');
const inspectorBlock = css.match(/\/\* 3\. Right side inspector panel[\s\S]*?(?=\/\* 4\.)/)?.[0] ?? '';

function luminance(hex) {
  const rgb = hex.match(/[\da-f]{2}/gi).map(value => parseInt(value, 16) / 255);
  const linear = rgb.map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(foreground, background) {
  const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

test('QIN-615 AC1/AC2: loaded Asset Manager inspector uses hardware theme tokens', () => {
  assert.notEqual(inspectorBlock, '', 'QIN-615 inspector block exists');
  assert.match(embed, /href="\/static\/css\/pages\/asset-manager\.css/);
  assert.doesNotMatch(embed, /signal-flow\.css/, 'the iframe does not load the old override stylesheet');
  assert.match(inspectorBlock, /\.asset-detail-title h2[\s\S]*?color: var\(--text\);/);
  assert.match(inspectorBlock, /\.asset-detail-title p[\s\S]*?color: var\(--muted\);/);
  assert.match(inspectorBlock, /\.asset-detail-facts dt[\s\S]*?color: var\(--muted\);/);
  assert.match(inspectorBlock, /\.asset-detail-facts dd[\s\S]*?color: var\(--text\);/);
  assert.match(inspectorBlock, /\.asset-detail-actions a,[\s\S]*?background: var\(--soft\);/);
  assert.doesNotMatch(inspectorBlock, /#f0f3fa|#8a94a6|#8892a4|#e0e4f0|#11141c|#12151e/);
  assert.doesNotMatch(inspectorBlock, /!important/);

  const primaryTextContrast = contrastRatio('#25282e', '#ffffff');
  const mutedTextContrast = contrastRatio('#515d6f', '#e8eaee');
  const actionContrast = contrastRatio('#775617', '#ffffff');
  assert.ok(primaryTextContrast >= 4.5, `primary text contrast ${primaryTextContrast.toFixed(2)}:1`);
  assert.ok(mutedTextContrast >= 4.5, `muted text contrast ${mutedTextContrast.toFixed(2)}:1`);
  assert.ok(actionContrast >= 4.5, `action text contrast ${actionContrast.toFixed(2)}:1`);
});

test('QIN-615 AC2: tokenized inspector scope preserves dark theme and page isolation', () => {
  assert.match(inspectorBlock, /\.asset-detail-inspector/);
  assert.doesNotMatch(inspectorBlock, /\.asset-detail-media-canvas/);
  assert.doesNotMatch(inspectorBlock, /(?:^|\s)body\s*\{/);
  assert.doesNotMatch(inspectorBlock, /\.asset-detail-(?!inspector|title|facts|tags|versions|version-row|actions)/);
  assert.match(inspectorBlock, /var\(--panel\)|var\(--soft\)|var\(--text\)|var\(--muted\)/);
});
