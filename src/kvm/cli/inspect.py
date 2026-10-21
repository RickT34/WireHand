"""Export an offline visual-debug gallery from a real screenshot."""
import argparse
import html
import io
import json
from pathlib import Path
import tempfile

from PIL import Image

from kvm.platforms.image import ImageBackend
from kvm.core.ownership import InputGate
from kvm.automation.inspection import Inspector


def export_gallery(source: Path, output: Path):
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='kvm-inspect-') as directory:
        backend = ImageBackend(source)
        service = Inspector(backend,InputGate(Path(directory)))
        first = service.inspect()
        snapshot = first.metadata['snapshot_id']
        overview = first if first.metadata['presentation']=='regions' else None
        if overview:
            first = service.region(snapshot,'FULL')
        pages = [first]
        for number in range(1,first.metadata['pages']):
            pages.append(service.region(snapshot,'FULL',page=number) if overview else service.inspect(snapshot,page=number))
        with Image.open(io.BytesIO(backend.screenshot(3840))) as captured:
            captured.convert('RGB').save(output/'original.png')
        all_targets = {}
        sections = []
        if overview:
            (output/'dense-overview.png').write_bytes(overview.images[0])
            (output/'dense-overview.json').write_text(json.dumps(overview.metadata,ensure_ascii=False,indent=2))
            with Image.open(io.BytesIO(overview.images[0])) as picture:
                dimensions = f'width="{picture.width}" height="{picture.height}"'
            sections.append(f'<section><h2>高密度画面 · MCP 首先返回区域总览</h2><img src="dense-overview.png" {dimensions}></section>')
        for page in pages:
            number = page.metadata['page']
            (output/f'page-{number}.json').write_text(json.dumps(page.metadata,ensure_ascii=False,indent=2))
            for identity,data in zip(page.metadata['image_regions'],page.images):
                filename = f'overview-{number}.png' if identity=='overview' else f'{identity}.png'
                (output/filename).write_bytes(data)
                with Image.open(io.BytesIO(data)) as picture:
                    dimensions = f'width="{picture.width}" height="{picture.height}"'
                targets = [t for t in page.metadata['targets'] if t['region']==identity]
                buttons = ''.join(f'<button data-target="{t["id"]}">{t["id"]}</button>' for t in targets)
                sections.append(f'<section><h2>{identity if identity!="overview" else "总览 · 第 "+str(number+1)+" 页"}</h2>'
                                f'<img src="{filename}" {dimensions} loading="lazy"><div class="targets">{buttons}</div></section>')
            for target in page.metadata['targets']:
                all_targets[target['id']] = target
        manifest = {'source':source.name,'size':first.metadata['size'],'pages':len(pages),
                    'regions':first.metadata['regions'],'targets':list(all_targets.values()),
                    'note':'Offline export. IDs are not valid in another MCP session.'}
        (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
        width,height = first.metadata['size']
        data = json.dumps(all_targets,ensure_ascii=False).replace('<','\\u003c')
        document = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>视觉定位检查</title>
<style>body{margin:0;background:#101820;color:#e4edf4;font:15px system-ui}main{max-width:1280px;margin:auto;padding:24px}h1{margin:0 0 8px}p{color:#aabbcc}section{border:1px solid #334552;border-radius:12px;margin:24px 0;padding:14px}h2{font-size:17px}img{max-width:100%;height:auto;display:block}button{background:#273e50;border:1px solid #648193;color:white;padding:7px 12px;border-radius:6px;cursor:pointer}.targets{display:flex;flex-wrap:wrap;gap:7px;margin-top:12px}.original{position:relative}.original svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}#chosen{color:#72ead5}a{color:#80cfff}</style>
<main><h1>视觉定位检查</h1><p>SOURCE · COUNT 个候选目标 · PAGES 页。选择编号，在原图中检查实际点击点。</p>
<section id="source"><h2 id="chosen">原始屏幕</h2><div class="original"><img src="original.png" width="WIDTH" height="HEIGHT"><svg viewBox="0 0 WIDTH HEIGHT"><rect id="box" fill="none" stroke="#ff4242" stroke-width="3"/><circle id="point" r="7" fill="#ff4242" stroke="white" stroke-width="2" visibility="hidden"/></svg></div></section>
SECTIONS
<p><a href="manifest.json">候选坐标与分组数据</a> · 检测与预览均在本地运行。</p></main>
<script>const targets=DATA;document.addEventListener('click',event=>{const id=event.target.dataset.target;if(!id)return;const t=targets[id],b=t.box,p=t.point,r=document.querySelector('#box'),c=document.querySelector('#point');for(const [k,v] of Object.entries({x:b[0],y:b[1],width:b[2]-b[0],height:b[3]-b[1]}))r.setAttribute(k,v);c.setAttribute('cx',p[0]);c.setAttribute('cy',p[1]);c.setAttribute('visibility','visible');document.querySelector('#chosen').textContent=id+' · 点击点 ('+p.join(', ')+')';document.querySelector('#source').scrollIntoView({behavior:'smooth'});});</script></html>'''
        replacements = {'SOURCE':html.escape(source.name),'COUNT':str(len(all_targets)),'PAGES':str(len(pages)),
                        'WIDTH':str(width),'HEIGHT':str(height),'SECTIONS':''.join(sections),'DATA':data}
        for key,value in replacements.items():
            document = document.replace(key,value)
        (output/'index.html').write_text(document)
        return manifest


def main():
    parser = argparse.ArgumentParser(description='Inspect saved screen images without a board or input device')
    parser.add_argument('image',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    manifest = export_gallery(args.image,args.output)
    print(json.dumps({'output':str(args.output.resolve()/'index.html'),'targets':len(manifest['targets']),
                      'regions':len(manifest['regions']),'pages':manifest['pages']},ensure_ascii=False))


if __name__ == '__main__':
    main()
