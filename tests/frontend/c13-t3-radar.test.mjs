import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const read = path => readFileSync(path, 'utf8');

function loadRadar() {
  const context = { console, Math, Number, String, Array, Object, Set, Map, URL, URLSearchParams };
  context.window = context;
  vm.runInNewContext(read('web/js/modules/topdown-radar.js'), context, { filename: 'topdown-radar.js' });
  return context.TopDownRadar;
}

function loadNode(radar) {
  const context = { console, Math, Number, String, Array, Object, Set, Map, URL, URLSearchParams, CustomEvent, TopDownRadar: radar };
  context.window = context;
  vm.runInNewContext(read('web/js/nodes/canvas-radar-node.js'), context, { filename: 'canvas-radar-node.js' });
  return context.CameraRadarNode;
}

test('C13-AC3 规范化相机参数并输出 [R|T] 外参', () => {
  const radar = loadRadar();
  const model = radar.createRadarModel({
    x: 10,
    y: 20,
    z: 1.8,
    heading: 90,
    fov: 60,
    range: 100,
  }, { subject: { x: 40, y: 20, z: 0 } });

  assert.equal(model.camera.heading, 90);
  assert.equal(model.camera.fov, 60);
  assert.equal(model.fovPolygon.length, 3);
  assert.deepEqual([...model.extrinsics.translation], [10, 20, 1.8]);
  assert.equal(model.extrinsics.matrix.length, 3);
  assert.equal(model.extrinsics.matrix[0].length, 4);
  assert.equal(model.extrinsics.matrix[0][0], 0);
  assert.equal(model.extrinsics.matrix[1][0], 1);
});

test('C13-AC3 计算相邻镜头动作轴并检测 180 度越轴', () => {
  const radar = loadRadar();
  const subject = { x: 0, y: 0 };
  const previous = { x: -10, y: -5, heading: 0 };
  const sameSide = { x: -8, y: -4, heading: 5 };
  const oppositeSide = { x: 10, y: 5, heading: 180 };

  assert.equal(radar.axisSide(previous, subject, 0), -1);
  assert.equal(radar.axisSide(sameSide, subject, 0), -1);
  assert.equal(radar.axisSide(oppositeSide, subject, 0), 1);
  assert.equal(radar.detectAxisCrossing(previous, sameSide, { subject, axisAngle: 0 }), false);
  assert.equal(radar.detectAxisCrossing(previous, oppositeSide, { subject, axisAngle: 0 }), true);
});

test('C13-AC3 以纯 SVG 输出雷达视锥和红色越轴状态', () => {
  const radar = loadRadar();
  const host = { innerHTML: '' };
  const model = radar.renderRadar(host, {
    x: 10,
    y: 5,
    heading: 0,
    fov: 45,
    range: 80,
  }, {
    subject: { x: 0, y: 0 },
    previousCamera: { x: -10, y: -5, heading: 0 },
    axisAngle: 0,
  });

  assert.equal(model.crossesAxis, true);
  assert.match(host.innerHTML, /<svg[^>]+data-radar-type="topdown-camera-radar"/);
  assert.match(host.innerHTML, /data-radar-alert="axis-crossing"/);
  assert.match(host.innerHTML, /class="radar-fov radar-fov-alert"/);
  assert.match(host.innerHTML, /<path[^>]+data-radar-fov/);
});

test('C13-AC3 建立 camera_radar 节点与分镜/画布双向 Deep-Link', () => {
  const radar = loadRadar();
  const nodeApi = loadNode(radar);
  const node = nodeApi.createCameraRadarNode({
    entity_id: 'entity-camera-radar-001',
    shot_id: 'SC01_SH002',
    project_id: 'project-c13-001',
    canvas_id: 'canvas-c13-001',
    camera: { x: 10, y: 0, z: 1.6, heading: 180, fov: 50, range: 120 },
    subject: { x: 0, y: 0, z: 0 },
  });

  assert.equal(node.node_type, 'camera_radar');
  assert.equal(node.entity_id, 'entity-camera-radar-001');
  assert.deepEqual([...node.out_ports.map(port => port.name)], ['camera_extrinsics', 'motion_path']);
  assert.match(node.metadata.extrinsics.matrix.join(','), /,/);

  const focusUrl = nodeApi.buildCanvasFocusUrl(node, { origin: 'https://example.test' });
  const returnUrl = nodeApi.buildStoryboardReturnUrl(node, { origin: 'https://example.test' });
  assert.match(focusUrl, /canvas\.html\?project_id=project-c13-001/);
  assert.match(focusUrl, /focus_node=entity-camera-radar-001/);
  assert.match(returnUrl, /storyboard\.html\?project_id=project-c13-001/);
  assert.match(returnUrl, /view=radar/);
  assert.match(returnUrl, /shot_id=SC01_SH002/);
});

test('C13-AC3 页面接入原生雷达模块和 canvas.html 聚焦入口', () => {
  const storyboard = read('web/pages/storyboard.html');
  const canvas = read('web/pages/canvas.html');
  assert.match(storyboard, /topdown-radar\.js/);
  assert.match(storyboard, /canvas-radar-node\.js/);
  assert.match(storyboard, /data-radar-panel/);
  assert.match(storyboard, /canvas\.html\?project_id=/);
  assert.match(canvas, /id="canvasRadarWorkspace"/);
  assert.match(canvas, /focus_node/);
  assert.match(canvas, /camera-radar-node\.js|canvas-radar-node\.js/);
});
