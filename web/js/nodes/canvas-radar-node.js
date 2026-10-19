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

/** C13-T3 `camera_radar` 画布节点与双向 Deep-Link。 */
(function (global) {
  'use strict';

  const NODE_TYPE = 'camera_radar';
  const radar = global.TopDownRadar || {};

  function text(value, fallback = '') {
    const result = String(value ?? '').trim();
    return result || fallback;
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[character]));
  }

  function nodeProjectId(node, options = {}) {
    return text(options.projectId || options.project_id || node?.project_id || node?.project || '');
  }

  function nodeShotId(node, options = {}) {
    return text(options.shotId || options.shot_id || node?.shot_id || node?.metadata?.shot_id || '');
  }

  function nodeCanvasId(node, options = {}) {
    return text(options.canvasId || options.canvas_id || node?.canvas_id || node?.metadata?.canvas_id || '');
  }

  function nodeEntityId(node, options = {}) {
    return text(options.nodeId || options.focusNode || node?.entity_id || node?.id || '');
  }

  function withQuery(path, params, options = {}) {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      const normalized = text(value);
      if (normalized) query.set(key, normalized);
    });
    const base = text(options.basePath, path);
    const origin = text(options.origin || global.location?.origin, '');
    if (origin) {
      const url = new URL(base, origin);
      url.search = query.toString();
      return `${url.pathname}${url.search}`;
    }
    return `${base}?${query.toString()}`;
  }

  function buildStoryboardReturnUrl(node, options = {}) {
    return withQuery('storyboard.html', {
      project_id: nodeProjectId(node, options),
      view: 'radar',
      shot_id: nodeShotId(node, options),
      canvas_id: nodeCanvasId(node, options),
      focus_node: nodeEntityId(node, options)
    }, { ...options, basePath: options.storyboardPath || 'storyboard.html' });
  }

  function buildCanvasFocusUrl(node, options = {}) {
    const returnUrl = options.returnUrl || buildStoryboardReturnUrl(node, options);
    return withQuery('canvas.html', {
      project_id: nodeProjectId(node, options),
      canvas_id: nodeCanvasId(node, options),
      focus_node: nodeEntityId(node, options),
      shot_id: nodeShotId(node, options),
      return_to: returnUrl
    }, { ...options, basePath: options.canvasPath || 'canvas.html' });
  }

  function createCameraRadarNode(input = {}) {
    const source = input && typeof input === 'object' ? input : {};
    const entityId = nodeEntityId(source) || `camera-radar-${nodeShotId(source) || 'node'}`;
    const camera = source.camera || source.metadata?.camera || source.camera_position || source;
    const subject = source.subject || source.metadata?.subject || { x: 0, y: 0, z: 0 };
    const model = typeof radar.createRadarModel === 'function'
      ? radar.createRadarModel(camera, {
        subject,
        previousCamera: source.previousCamera || source.previous_camera || source.metadata?.previous_camera,
        axisAngle: source.axisAngle ?? source.action_axis ?? source.metadata?.axis_angle ?? 0
      })
      : { camera, subject, extrinsics: source.extrinsics || {} };
    const shotId = nodeShotId(source);
    const projectId = nodeProjectId(source);
    const canvasId = nodeCanvasId(source);
    const metadata = {
      ...(source.metadata && typeof source.metadata === 'object' ? source.metadata : {}),
      camera: model.camera,
      subject: model.subject,
      previous_camera: model.previousCamera,
      extrinsics: model.extrinsics,
      axis_angle: model.axisAngle,
      axis_side: model.axisSide,
      crosses_axis: model.crossesAxis,
      shot_id: shotId,
      project_id: projectId,
      canvas_id: canvasId,
      return_url: buildStoryboardReturnUrl({entity_id: entityId, shot_id: shotId, project_id: projectId, canvas_id: canvasId})
    };
    return {
      ...source,
      entity_id: entityId,
      node_type: NODE_TYPE,
      title: text(source.title || source.name, shotId ? `机位雷达 · ${shotId}` : '机位雷达'),
      project_id: projectId,
      canvas_id: canvasId,
      shot_id: shotId,
      position: source.position && typeof source.position === 'object' ? {...source.position} : {x: 120, y: 120},
      in_ports: [
        {name: 'subject_entity', data_type: 'ENTITY'},
        {name: 'previous_camera', data_type: 'CAMERA'}
      ],
      out_ports: [
        {name: 'camera_extrinsics', data_type: 'CAMERA'},
        {name: 'motion_path', data_type: 'CAMERA'}
      ],
      camera: model.camera,
      subject: model.subject,
      extrinsics: model.extrinsics,
      metadata
    };
  }

  function findNodeElement(container, entityId) {
    if (!container || !entityId) return null;
    const nodes = container.querySelectorAll?.('[data-node-id]') || [];
    return [...nodes].find(element => element.dataset?.nodeId === entityId) || null;
  }

  function focusCanvasNode(entityId, options = {}) {
    const container = options.container || document;
    const element = findNodeElement(container, text(entityId));
    if (!element) return false;
    element.classList?.add('selected', 'camera-radar-focused');
    element.scrollIntoView?.({behavior: 'smooth', block: 'center', inline: 'center'});
    element.dispatchEvent?.(new CustomEvent('gw:camera-radar-focused', {detail: {entity_id: text(entityId)}}));
    return true;
  }

  function renderCameraRadarNode(nodeInput, container, options = {}) {
    const node = nodeInput?.node_type === NODE_TYPE ? nodeInput : createCameraRadarNode(nodeInput);
    if (!container) return node;
    const focusUrl = buildCanvasFocusUrl(node, options);
    const returnUrl = buildStoryboardReturnUrl(node, options);
    const model = typeof radar.createRadarModel === 'function'
      ? radar.createRadarModel(node.camera, {
        subject: node.subject,
        previousCamera: node.metadata?.previous_camera,
        axisAngle: node.metadata?.axis_angle
      })
      : null;
    container.innerHTML = `<article class="canvas-radar-node" data-node-type="${NODE_TYPE}" data-node-id="${escapeHtml(node.entity_id)}" data-focus-url="${escapeHtml(focusUrl)}">
  <header class="canvas-radar-node-header"><strong>${escapeHtml(node.title)}</strong><span class="canvas-radar-node-type">${NODE_TYPE}</span></header>
  <div class="canvas-radar-node-radar" data-radar-node-host></div>
  <div class="canvas-radar-node-actions">
    <a class="hw-btn hw-btn-sm" data-canvas-focus href="${escapeHtml(focusUrl)}">在画布中聚焦</a>
    <a class="hw-btn hw-btn-sm hw-btn-gold" data-storyboard-return href="${escapeHtml(returnUrl)}">返回分镜台</a>
  </div>
</article>`;
    const radarHost = container.querySelector?.('[data-radar-node-host]');
    if (radarHost && typeof radar.renderRadar === 'function' && model) {
      radar.renderRadar(radarHost, node.camera, {
        subject: node.subject,
        previousCamera: node.metadata?.previous_camera,
        axisAngle: node.metadata?.axis_angle
      });
    }
    return node;
  }

  global.CameraRadarNode = {
    NODE_TYPE,
    buildCanvasFocusUrl,
    buildStoryboardReturnUrl,
    createCameraRadarNode,
    focusCanvasNode,
    isCameraRadarNode: node => node?.node_type === NODE_TYPE,
    render: renderCameraRadarNode,
    renderCameraRadarNode
  };
})(window);
