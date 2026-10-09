// 工作流资产沿用稳定长ID，兼容历史素材页数据，不改服务端标识。
export const workflowAssetId = item => item?.asset_id || item?.id || '';
export const workflowLibraryId = item => item?.library_id || item?.id || '';
export const workflowCategoryId = item => item?.category_id || item?.id || '';

export function workflowSourceId(item, origin = location.origin) {
    try {
        const url = new URL(item?.url || '', origin);
        if (url.origin !== origin || url.pathname !== '/static/pages/workflow.html') return '';
        const workflow_id = url.searchParams.get('id') || '';
        return /^[a-zA-Z0-9_-]{1,128}$/.test(workflow_id) ? workflow_id : '';
    } catch (_) { return ''; }
}

// ZIP存储模式不压缩，也不依赖外部库；先取得所有获准文档再生成完整包。
export function zipWorkflowFiles(files) {
    if (!files.length || files.length > 100) throw new Error('每次导出1～100个工作流');
    const encoder = new TextEncoder(), chunks = [], central = [];
    let offset = 0, total = 0;
    for (const file of files) {
        const name = encoder.encode(file.name);
        const data = file.content instanceof Uint8Array ? file.content : encoder.encode(file.content);
        total += data.length;
        if (!name.length || name.length > 65535 || total > 30 * 1024 * 1024) throw new Error('工作流导出包超过限制');
        let crc = 0xffffffff;
        for (const byte of data) {
            crc ^= byte;
            for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
        }
        crc = (crc ^ 0xffffffff) >>> 0;
        const header = new Uint8Array(30), view = new DataView(header.buffer);
        view.setUint32(0, 0x04034b50, true); view.setUint16(4, 20, true);
        view.setUint16(6, 0x800, true); view.setUint16(12, 33, true);
        view.setUint32(14, crc, true); view.setUint32(18, data.length, true);
        view.setUint32(22, data.length, true); view.setUint16(26, name.length, true);
        chunks.push(header, name, data);
        const directory = new Uint8Array(46), entry = new DataView(directory.buffer);
        entry.setUint32(0, 0x02014b50, true); entry.setUint16(4, 20, true);
        entry.setUint16(6, 20, true); entry.setUint16(8, 0x800, true);
        entry.setUint16(14, 33, true); entry.setUint32(16, crc, true);
        entry.setUint32(20, data.length, true); entry.setUint32(24, data.length, true);
        entry.setUint16(28, name.length, true); entry.setUint32(42, offset, true);
        central.push(directory, name);
        offset += header.length + name.length + data.length;
    }
    const centralLength = central.reduce((size, chunk) => size + chunk.length, 0);
    const tail = new Uint8Array(22), end = new DataView(tail.buffer);
    end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true);
    end.setUint16(10, files.length, true); end.setUint32(12, centralLength, true);
    end.setUint32(16, offset, true);
    return new Blob([...chunks, ...central, tail], {type:'application/zip'});
}
