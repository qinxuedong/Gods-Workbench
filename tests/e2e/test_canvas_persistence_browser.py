"""实际 Chrome 运行画布消费者；本地临时服务、账户与存储完全隔离。"""
from urllib.parse import urlsplit
from workflow_support import settings_browser
from gw.api import routes_god_canvas, routes_canvas_closure
from gw.canvas_closure.service import CanvasClosureService
from gw.god_canvas.service import GodCanvasService


def test_canvas_meta_lifecycle_restart_and_conflict_in_browser(monkeypatch,tmp_path):
    source=GodCanvasService(seed_golden_fixture=False,persistent=True)
    monkeypatch.setattr(routes_god_canvas,"default_god_canvas_service",source)
    monkeypatch.setattr(routes_canvas_closure,"closure",CanvasClosureService(canvas_service=source))
    with settings_browser(monkeypatch,tmp_path) as (page,base,errors,_):
        request=page.context.request
        headers={"Origin":base}
        project=request.post(base+'/api/asset-registry/projects',data={"name":"画布浏览器项目","project_type":"other"},headers=headers)
        assert project.status==201,project.text()
        pid=project.json()['project']['project_id']
        created=request.post(base+'/api/canvases',data={"project_id":pid,"title":"持久画布"},headers=headers)
        assert created.status==201,created.text()
        cid=created.json()['canvas']['canvas_id']
        page.goto(base+'/static/embeds/canvas-list.html?project_id='+pid,wait_until='networkidle')
        page.wait_for_function('(id)=>typeof canvases!=="undefined"&&canvases.some(c=>c.id===id)',arg=cid)
        console_errors=[]
        page.on('console',lambda message:console_errors.append(message.text) if message.type=='error' else None)
        assert page.evaluate('(id)=>canvasMetaWritePayload(canvases.find(c=>c.id===id),{}).expected_version',cid)==1
        # 运行真实页面动作与同一 HTTP 消费者，不用页面假响应。
        saved=page.evaluate('(id)=>persistMeta(id,{title:"实际保存"})',cid)
        assert saved is not None,console_errors
        assert request.get(base+'/api/canvases/'+cid).json()['version']==2
        page.evaluate('(id)=>archiveCanvas(id)',cid)
        page.wait_for_function('(id)=>typeof archivedCanvases!=="undefined"&&archivedCanvases.some(c=>(c.canvas_id||c.id)===id)',arg=cid)
        assert request.get(base+'/api/canvases/'+cid).json()['version']==3
        page.evaluate('(id)=>unarchiveCanvas(id)',cid)
        assert request.get(base+'/api/canvases/'+cid).json()['version']==4
        # 换服务对象并刷新；读回持久拓扑，不依赖旧页面或旧进程内字典。
        restarted=GodCanvasService(seed_golden_fixture=False,persistent=True)
        monkeypatch.setattr(routes_god_canvas,"default_god_canvas_service",restarted)
        monkeypatch.setattr(routes_canvas_closure,"closure",CanvasClosureService(canvas_service=restarted))
        page.reload(wait_until='networkidle')
        page.wait_for_function('(id)=>typeof canvases!=="undefined"&&canvases.some(c=>c.id===id&&c.version===4)',arg=cid)
        # 另一个写入者先推进版本；旧页面动作收到409，只刷新，不能自动重试。
        changed=request.post(base+f'/api/canvases/{cid}/meta',data={"title":"并发更新","expected_version":4},headers=headers)
        assert changed.status==200,changed.text()
        lifecycle_requests=[]
        page.on('request',lambda req:lifecycle_requests.append(req.url) if urlsplit(req.url).path.endswith('/touch') else None)
        with page.expect_response(lambda response:response.status==409 and urlsplit(response.url).path.endswith('/touch')):
            page.evaluate('(id)=>archiveCanvas(id)',cid)
        assert len(lifecycle_requests)==1
        assert request.get(base+f'/api/canvases/{cid}').json()['version']==5
        assert not restarted.ensure_lifecycle(cid)['archived_at']
        page.evaluate('(id)=>deleteCanvas(id)',cid)
        page.wait_for_function('(id)=>typeof deletedCanvases!=="undefined"&&deletedCanvases.some(c=>(c.canvas_id||c.id)===id)',arg=cid)
        assert request.get(base+f'/api/canvases/{cid}').json()['version']==6
        page.evaluate('(id)=>restoreCanvas(id)',cid)
        assert request.get(base+f'/api/canvases/{cid}').json()['version']==7
        page.evaluate('(id)=>deleteCanvas(id)',cid)
        page.wait_for_function('(id)=>typeof deletedCanvases!=="undefined"&&deletedCanvases.some(c=>(c.canvas_id||c.id)===id)',arg=cid)
        page.evaluate('(id)=>purgeCanvas(id)',cid)
        assert request.get(base+f'/api/canvases/{cid}').status==404
        assert errors==[]
