/** RH fetch&parse 槽位契约：normalizeMatureWorkflow 两次归一化后连线槽位不得退化成行号。

后端 parser 对 API prompt 产出 connections {source, target, source_slot: String(输出槽),
target_slot: 输入名}；前端 apiFetch 与调用方会对同一份响应各归一化一次。若归一化把
数字 to_slot 置于命名 target_slot 之前，第二遍就会用 to_slot=0 覆盖 target_slot，
使所有连线都落在输入第 0 行。这里用真实合成 fixture 断言两次归一化保持命名槽位。
*/
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createContext, runInContext} from 'node:vm';

const controllerSource = readFileSync(
    new URL('../../web/js/controllers/workflow-controller.js', import.meta.url), 'utf8');
const promptFixture = JSON.parse(readFileSync(
    new URL('../contracts/fixtures/runninghub/workflow_2108837566177959938_api_prompt_synthetic.json',
        import.meta.url), 'utf8'));

/** 按花括号配对提取完整函数源码（字符串/模板串内的花括号不影响计数——控制器这几个辅助函数不含）。 */
function extractFunction(source, name) {
  const marker = `function ${name}(`;
  const start = source.indexOf(marker);
  assert.ok(start >= 0, `控制器中缺少函数 ${name}`);
  let depth = 0;
  for (let i = start; i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}') {
      depth--;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  assert.fail(`函数 ${name} 花括号不闭合`);
}

const context = createContext({});
for (const name of ['collapseNodePosition', 'normalizeMatureWorkflow', 'deriveActuatorParamsFromWidgets']) {
  runInContext(extractFunction(controllerSource, name), context);
}

/** 镜像后端 parser.parse_workflow 对 API prompt 的输出：
    每个取值为 [nodeId, slot] 的输入生成 {source, target, source_slot: String(slot), target_slot: 输入名}。 */
function backendDocumentFromApiPrompt(prompt) {
  const nodes = [];
  const connections = [];
  for (const [nodeId, node] of Object.entries(prompt)) {
    nodes.push({id: nodeId, class_type: node.class_type, inputs: node.inputs});
    for (const [inputName, value] of Object.entries(node.inputs || {})) {
      if (Array.isArray(value) && value.length >= 2 && !Array.isArray(value[0])) {
        connections.push({source: String(value[0]), target: nodeId,
                          source_slot: String(value[1]), target_slot: inputName});
      }
    }
  }
  return {name: 'rh-api-prompt', nodes, links: connections};
}

test('解析 RH API prompt 的连线在两次归一化后仍保持命名输入槽位', () => {
  const firstPass = context.normalizeMatureWorkflow(backendDocumentFromApiPrompt(promptFixture));
  const secondPass = context.normalizeMatureWorkflow(firstPass);
  assert.equal(JSON.stringify(secondPass.links), JSON.stringify(firstPass.links), '两次归一化必须幂等');

  const expectedTargets = new Map([
    ['484->3:positive', 'positive'], ['484->3:negative', 'negative'],
    ['482->3:latent_image', 'latent_image'], ['483->3:model', 'model'],
    ['476->484:vae', 'vae'], ['485->484:prompt', 'prompt'],
    ['477->484:clip', 'clip'], ['470->484:images.image_1', 'images.image_1'],
  ]);
  for (const [passIndex, document] of [[1, firstPass], [2, secondPass]]) {
    const links = new Map(document.links.map((link) => [`${link.source}->${link.target}:${link.target_slot}`, link]));
    assert.equal(document.links.length, 17, `第 ${passIndex} 遍应保留 fixture 全部连线`);
    for (const [key, inputName] of expectedTargets) {
      const link = links.get(key);
      assert.ok(link, `第 ${passIndex} 遍缺少连线 ${key}`);
      assert.equal(link.target_slot, inputName, `连线 ${key} 的 target_slot 必须保持输入名`);
      assert.equal(link.to_slot, 0, `命名槽位无法 parseInt 时 to_slot 只能回退 0（${key}）`);
    }
  }

  const outgoing484 = secondPass.links.filter((link) => link.source === '484')
      .sort((a, b) => Number(a.source_slot) - Number(b.source_slot));
  assert.deepEqual(outgoing484.map((link) => link.source_slot), ['0', '1', '2']);
  assert.deepEqual(outgoing484.map((link) => `${link.target}:${link.target_slot}`),
      ['3:positive', '3:negative', '482:on_true']);
});

test('旧字段形态（仅 from_slot/to_slot 数字）仍可归一化', () => {
  const document = context.normalizeMatureWorkflow({
    nodes: [{id: '1', class_type: 'Load'}, {id: '2', class_type: 'Save'}],
    links: [{id: 'l1', from_node: '1', to_node: '2', from_slot: 0, to_slot: 0}],
  });
  const link = document.links[0];
  assert.equal(link.source_slot, '0');
  assert.equal(link.target_slot, '0');
  assert.equal(link.from_slot, 0);
  assert.equal(link.to_slot, 0);
});
