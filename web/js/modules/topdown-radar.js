/**
 * Copyright 2026 Gods-Workbench Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/**
 * C13-T3 原生 SVG 2D 顶视机位雷达。
 *
 * 这个模块只依赖浏览器原生 DOM，不引入 Canvas、WebGL 或外部库。几何计算
 * 与 SVG 字符串生成保持纯函数，页面和画布节点可以复用同一份机位数据。
 */
(function (global) {
  'use strict';

  const RADAR_TYPE = 'topdown-camera-radar';
  const NODE_TYPE = 'camera_radar';
  const EPSILON = 1e-9;
  const DEFAULT_FOV = 60;
  const DEFAULT_RANGE = 120;
  const DEFAULT_VIEWBOX = { width: 640, height: 420, padding: 38 };

  function number(value, fallback = 0) {
    const result = Number(value);
    return Number.isFinite(result) ? result : fallback;
  }

  function positive(value, fallback) {
    const result = number(value, fallback);
    return result > 0 ? result : fallback;
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function round(value) {
    const result = Math.round(number(value) * 1000000) / 1000000;
    return Object.is(result, -0) ? 0 : result;
  }

  function degreesToRadians(value) {
    return number(value) * Math.PI / 180;
  }

  function normalizeAngle(value) {
    const result = number(value);
    return round(((result % 360) + 360) % 360);
  }

  function normalizePoint(point = {}, fallback = {}) {
    return {
      x: number(point.x ?? point.horizontal ?? fallback.x, 0),
      y: number(point.y ?? point.vertical ?? fallback.y, 0),
      z: number(point.z ?? point.height ?? fallback.z, 0)
    };
  }

  function normalizeCamera(input = {}) {
    const source = input && typeof input === 'object' ? input : {};
    const position = source.position && typeof source.position === 'object'
      ? source.position
      : source.camera_position && typeof source.camera_position === 'object'
        ? source.camera_position
        : source;
    const rotation = source.rotation && typeof source.rotation === 'object' ? source.rotation : {};
    const heading = source.heading ?? source.heading_deg ?? source.yaw ?? source.angle
      ?? rotation.heading ?? rotation.yaw ?? rotation.degrees ?? source.rotation;
    return {
      x: number(position.x ?? position.horizontal, 0),
      y: number(position.y ?? position.vertical, 0),
      z: number(position.z ?? position.height ?? source.height, 0),
      heading: normalizeAngle(heading),
      fov: clamp(number(source.fov ?? source.fov_deg ?? source.field_of_view, DEFAULT_FOV), 1, 179),
      range: positive(source.range ?? source.fov_range ?? source.distance, DEFAULT_RANGE),
      shot_id: String(source.shot_id || source.shotId || '').trim(),
      entity_id: String(source.entity_id || source.entityId || '').trim()
    };
  }

  function axisSide(point, subject = {}, axisAngle = 0) {
    const camera = normalizePoint(point);
    const target = normalizePoint(subject);
    const axis = degreesToRadians(axisAngle);
    const cross = Math.cos(axis) * (camera.y - target.y) - Math.sin(axis) * (camera.x - target.x);
    if (cross > EPSILON) return 1;
    if (cross < -EPSILON) return -1;
    return 0;
  }

  function detectAxisCrossing(previousCamera, currentCamera, options = {}) {
    if (!previousCamera || !currentCamera) return false;
    const subject = options.subject || {};
    const axisAngle = number(options.axisAngle ?? options.action_axis ?? options.actionAxis, 0);
    const previousSide = axisSide(previousCamera, subject, axisAngle);
    const currentSide = axisSide(currentCamera, subject, axisAngle);
    return previousSide !== 0 && currentSide !== 0 && previousSide !== currentSide;
  }

  function computeFovPolygon(cameraInput) {
    const camera = normalizeCamera(cameraInput);
    const heading = degreesToRadians(camera.heading);
    const halfFov = degreesToRadians(camera.fov / 2);
    const leftAngle = heading - halfFov;
    const rightAngle = heading + halfFov;
    const pointAt = angle => ({
      x: round(camera.x + Math.cos(angle) * camera.range),
      y: round(camera.y + Math.sin(angle) * camera.range),
      z: camera.z
    });
    return [
      { x: camera.x, y: camera.y, z: camera.z },
      pointAt(leftAngle),
      pointAt(rightAngle)
    ];
  }

  function computeExtrinsics(cameraInput) {
    const camera = normalizeCamera(cameraInput);
    const angle = degreesToRadians(camera.heading);
    const cosine = round(Math.cos(angle));
    const sine = round(Math.sin(angle));
    const translation = [camera.x, camera.y, camera.z];
    const matrix = [
      [cosine, round(-sine), 0, camera.x],
      [sine, cosine, 0, camera.y],
      [0, 0, 1, camera.z]
    ];
    return {
      rotation: [
        [cosine, round(-sine), 0],
        [sine, cosine, 0],
        [0, 0, 1]
      ],
      translation,
      matrix
    };
  }

  function createRadarModel(cameraInput = {}, options = {}) {
    const rawCamera = cameraInput && typeof cameraInput === 'object' ? cameraInput : {};
    const camera = normalizeCamera(rawCamera);
    const subject = normalizePoint(options.subject || rawCamera.subject || {});
    const previousCamera = options.previousCamera ? normalizeCamera(options.previousCamera) : null;
    const axisAngle = normalizeAngle(options.axisAngle ?? options.action_axis ?? options.actionAxis ?? 0);
    const previousSide = previousCamera ? axisSide(previousCamera, subject, axisAngle) : 0;
    const currentSide = axisSide(camera, subject, axisAngle);
    const crossesAxis = previousSide !== 0 && currentSide !== 0 && previousSide !== currentSide;
    return {
      type: RADAR_TYPE,
      node_type: NODE_TYPE,
      camera,
      subject,
      previousCamera,
      axisAngle,
      axisSide: currentSide,
      previousAxisSide: previousSide,
      crossesAxis,
      fovPolygon: computeFovPolygon(camera),
      extrinsics: computeExtrinsics(camera)
    };
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[character]));
  }

  function collectWorldPoints(model) {
    const points = [model.camera, model.subject, ...model.fovPolygon];
    const axis = degreesToRadians(model.axisAngle);
    const axisLength = model.camera.range * 1.4;
    points.push(
      { x: model.subject.x - Math.cos(axis) * axisLength, y: model.subject.y - Math.sin(axis) * axisLength },
      { x: model.subject.x + Math.cos(axis) * axisLength, y: model.subject.y + Math.sin(axis) * axisLength }
    );
    return points;
  }

  function createProjector(model, options = {}) {
    const width = positive(options.width, DEFAULT_VIEWBOX.width);
    const height = positive(options.height, DEFAULT_VIEWBOX.height);
    const padding = positive(options.padding, DEFAULT_VIEWBOX.padding);
    const points = collectWorldPoints(model);
    const xs = points.map(point => point.x);
    const ys = points.map(point => point.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const spanX = Math.max(maxX - minX, 1);
    const spanY = Math.max(maxY - minY, 1);
    const scale = Math.max(0.01, Math.min((width - padding * 2) / spanX, (height - padding * 2) / spanY));
    const drawWidth = spanX * scale;
    const drawHeight = spanY * scale;
    const offsetX = (width - drawWidth) / 2 - minX * scale;
    const offsetY = (height - drawHeight) / 2 + maxY * scale;
    return {
      width,
      height,
      project(point) {
        return {
          x: round(point.x * scale + offsetX),
          y: round(offsetY - point.y * scale)
        };
      },
      axisLength: scale * model.camera.range * 1.4
    };
  }

  function pointString(points, projector) {
    return points.map(point => {
      const projected = projector.project(point);
      return `${projected.x},${projected.y}`;
    }).join(' ');
  }

  function buildSvgMarkup(model, options = {}) {
    const projector = createProjector(model, options);
    const camera = projector.project(model.camera);
    const subject = projector.project(model.subject);
    const fovPoints = pointString(model.fovPolygon, projector);
    const fovProjected = model.fovPolygon.map(point => projector.project(point));
    const headingPoint = projector.project({
      x: model.camera.x + Math.cos(degreesToRadians(model.camera.heading)) * model.camera.range,
      y: model.camera.y + Math.sin(degreesToRadians(model.camera.heading)) * model.camera.range
    });
    const fovPath = `M ${fovProjected[0].x} ${fovProjected[0].y} L ${fovProjected[1].x} ${fovProjected[1].y} L ${fovProjected[2].x} ${fovProjected[2].y} Z`;
    const axis = degreesToRadians(model.axisAngle);
    const axisStart = projector.project({
      x: model.subject.x - Math.cos(axis) * model.camera.range * 1.4,
      y: model.subject.y - Math.sin(axis) * model.camera.range * 1.4
    });
    const axisEnd = projector.project({
      x: model.subject.x + Math.cos(axis) * model.camera.range * 1.4,
      y: model.subject.y + Math.sin(axis) * model.camera.range * 1.4
    });
    const alertClass = model.crossesAxis ? ' radar-fov-alert' : '';
    const axisColor = model.crossesAxis ? '#ef4444' : '#64748b';
    const alertLabel = model.crossesAxis ? '180° 动作轴越轴警示' : '动作轴安全';
    const shotLabel = model.camera.shot_id ? ` · ${escapeHtml(model.camera.shot_id)}` : '';
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${projector.width} ${projector.height}" role="img" aria-label="2D 顶视机位雷达${shotLabel}" data-radar-type="${RADAR_TYPE}" data-radar-alert="${model.crossesAxis ? 'axis-crossing' : 'safe'}">
  <defs>
    <radialGradient id="radar-grid-glow" cx="50%" cy="50%" r="65%"><stop offset="0%" stop-color="#dfc384" stop-opacity=".12"/><stop offset="100%" stop-color="#080a0e" stop-opacity="0"/></radialGradient>
  </defs>
  <rect class="radar-background" width="100%" height="100%" fill="#080a0e" rx="14" />
  <circle class="radar-grid-glow" cx="${projector.width / 2}" cy="${projector.height / 2}" r="${Math.min(projector.width, projector.height) * .45}" fill="url(#radar-grid-glow)" />
  <line class="radar-action-axis${model.crossesAxis ? ' radar-axis-alert' : ''}" data-radar-axis x1="${axisStart.x}" y1="${axisStart.y}" x2="${axisEnd.x}" y2="${axisEnd.y}" stroke="${axisColor}" stroke-width="${model.crossesAxis ? 2.5 : 1.25}" stroke-dasharray="${model.crossesAxis ? '7 5' : '3 6'}" />
  <path class="radar-fov${alertClass}" data-radar-fov d="${fovPath}" data-radar-points="${fovPoints}" fill="${model.crossesAxis ? '#ef4444' : '#dfc384'}" fill-opacity=".17" stroke="${model.crossesAxis ? '#ef4444' : '#dfc384'}" stroke-width="2" />
  <line class="radar-heading" data-radar-heading x1="${camera.x}" y1="${camera.y}" x2="${headingPoint.x}" y2="${headingPoint.y}" stroke="${model.crossesAxis ? '#fca5a5' : '#eddab3'}" stroke-width="2" />
  <circle class="radar-subject" data-radar-subject cx="${subject.x}" cy="${subject.y}" r="8" fill="#38bdf8" fill-opacity=".85" stroke="#dff6ff" stroke-width="1.5" />
  <circle class="radar-camera${alertClass}" data-radar-camera cx="${camera.x}" cy="${camera.y}" r="9" fill="${model.crossesAxis ? '#ef4444' : '#dfc384'}" stroke="#0b0d12" stroke-width="3" />
  <text class="radar-label" x="${camera.x + 13}" y="${camera.y - 12}" fill="#eddab3" style="font-size:var(--gw-type-label-md)">CAMERA · ${model.camera.heading}°</text>
  <text class="radar-label" x="${subject.x + 12}" y="${subject.y + 4}" fill="#7dd3fc" style="font-size:var(--gw-type-label-md)">SUBJECT</text>
  <text class="radar-alert-label${model.crossesAxis ? ' is-alert' : ''}" data-radar-alert-label x="18" y="${projector.height - 18}" fill="${model.crossesAxis ? '#fca5a5' : '#94a3b8'}" style="font-size:var(--gw-type-label-md)">${escapeHtml(alertLabel)}</text>
</svg>`;
  }

  function renderRadar(container, cameraInput, options = {}) {
    const model = createRadarModel(cameraInput, options);
    if (container && typeof container === 'object') {
      container.innerHTML = buildSvgMarkup(model, options);
      if (container.dataset) {
        container.dataset.radarType = RADAR_TYPE;
        container.dataset.radarAlert = model.crossesAxis ? 'axis-crossing' : 'safe';
      }
      if (typeof container.setAttribute === 'function') {
        container.setAttribute('role', 'img');
        container.setAttribute('aria-label', model.crossesAxis ? '180 度动作轴越轴警示' : '2D 顶视机位雷达');
      }
    }
    return model;
  }

  function mountRadar(container, options = {}) {
    if (!container) return null;
    return renderRadar(container, options.camera || {}, options);
  }

  global.TopDownRadar = {
    RADAR_TYPE,
    NODE_TYPE,
    axisSide,
    buildSvgMarkup,
    computeExtrinsics,
    computeFovPolygon,
    createRadarModel,
    detectAxisCrossing,
    mountRadar,
    normalizeAngle,
    normalizeCamera,
    renderRadar
  };
})(window);
