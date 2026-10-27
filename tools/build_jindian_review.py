"""Build a local HTML review of all Jindian Stage B quality decisions.

Only the source colour-video topic is transcoded to small browser previews.
The MCAPs are never edited and the preview is not a cleaned video.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from mcap.reader import make_reader
from mcap_protobuf.decoder import DecoderFactory

VIDEO_TOPIC = "/raw/orbbec/color/video/compressed"
CLOCK_TOPIC = "/raw/marvin/joint_state"


def ffmpeg_binary() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def preview_one(row: dict, media: Path, ffmpeg: str) -> dict:
    index = int(row["episode_index"])
    source = Path(row["source"])
    stem = f"{index:03d}_{source.stem.removesuffix('.h264')}"
    video = media / f"{stem}.mp4"
    poster = media / f"{stem}.jpg"
    info_path = media / f"{stem}.json"
    if video.is_file() and poster.is_file() and info_path.is_file():
        return json.loads(info_path.read_text(encoding="utf-8"))

    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "h264",
        "-r", "30", "-i", "pipe:0", "-vf", "scale=-2:480",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-threads", "1", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(video),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    first_video = last_video = None
    frames = 0
    try:
        with source.open("rb") as stream:
            reader = make_reader(stream, decoder_factories=[DecoderFactory()])
            for _schema, _channel, message, decoded in reader.iter_decoded_messages(topics=[VIDEO_TOPIC]):
                if first_video is None:
                    first_video = int(message.log_time)
                last_video = int(message.log_time)
                process.stdin.write(decoded.data)
                frames += 1
    finally:
        process.stdin.close()
    error = process.stderr.read().decode("utf-8", errors="replace")
    if process.wait() != 0 or frames < 2:
        video.unlink(missing_ok=True)
        raise RuntimeError(f"{source.name}: preview encode failed ({frames} frames): {error[:300]}")

    first_joint = last_joint = None
    joint_count = 0
    with source.open("rb") as stream:
        for _schema, _channel, message in make_reader(stream).iter_messages(topics=[CLOCK_TOPIC]):
            if first_joint is None:
                first_joint = int(message.log_time)
            last_joint = int(message.log_time)
            joint_count += 1
    if joint_count < 2:
        raise RuntimeError(f"{source.name}: no robot clock")

    shot = subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.5",
        "-i", str(video), "-frames:v", "1", "-q:v", "3", str(poster),
    ], capture_output=True)
    if shot.returncode != 0 or not poster.is_file():
        raise RuntimeError(f"{source.name}: poster extraction failed: {shot.stderr[:200]}")

    metadata = {
        "episode_index": index,
        "source": str(source),
        "video": f"media/{video.name}",
        "poster": f"media/{poster.name}",
        "video_frames": frames,
        "video_start_s": (first_video - first_joint) / 1e9,
        "video_end_s": (last_video - first_joint) / 1e9,
        "episode_duration_s": (last_joint - first_joint) / 1e9 + 0.02,
        "clock_start_ns": first_joint,
        "source_mb": round(source.stat().st_size / 1e6, 2),
        "preview_mb": round(video.stat().st_size / 1e6, 2),
    }
    info_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def build_html(rows: list[dict], preview: dict[int, dict], errors: dict[int, str]) -> str:
    episodes = []
    for row in rows:
        index = int(row["episode_index"])
        item = {**row, **preview.get(index, {})}
        if index in errors:
            item["preview_error"] = errors[index]
        episodes.append(item)
    data = json.dumps(episodes, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    template = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>简智夹爪 · Stage B 逐条筛选审核</title>
<style>
:root{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Noto Sans SC",sans-serif;color:#e9eef4;background:#0c121a}
*{box-sizing:border-box}body{margin:0}header{position:sticky;top:0;z-index:10;background:#101923f2;border-bottom:1px solid #293743;backdrop-filter:blur(12px);padding:16px max(20px,calc((100vw - 1420px)/2))}
h1{font-size:22px;margin:0 0 6px;font-weight:700}header p{color:#9fb1be;margin:0 0 12px;font-size:13px}.toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
input,select,button{background:#182735;border:1px solid #344655;color:#e9eef4;border-radius:8px;padding:8px 11px;font:inherit;font-size:13px}
input{min-width:230px;flex:1}button{cursor:pointer}button.active{border-color:#63b6f7;background:#193e5c}
.stats{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 0}.stat{border:1px solid #314250;background:#172431;border-radius:9px;padding:7px 10px;font-size:12px;color:#b9c9d5}.stat strong{color:white;font-size:15px}
main{max-width:1440px;margin:auto;padding:20px}.note{border-left:3px solid #d7ae5c;background:#292519;color:#ecdcb8;padding:11px 14px;border-radius:5px;font-size:13px;line-height:1.5;margin-bottom:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr));gap:16px}.card{background:#14202b;border:1px solid #2d3d4a;border-radius:12px;overflow:hidden;min-width:0}
.card.issue{border-color:#7d4d46}.card.error{border-color:#806a42}.head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;padding:13px 14px 10px}.name{font-weight:700;font-size:14px;overflow-wrap:anywhere}.sub{font-size:11px;color:#91a8b7;margin-top:4px}.badge{font-size:11px;white-space:nowrap;padding:4px 7px;border-radius:6px;font-weight:700}.keep .badge{color:#78dec0;background:#123f3b}.partial .badge{color:#ffad9d;background:#4a282a}.discard .badge{color:#ff9a9a;background:#52282d}.error .badge{color:#f0d18a;background:#4c3d20}
.video-wrap{position:relative;background:#080c11;aspect-ratio:4/3}.video-wrap video{display:block;width:100%;height:100%;object-fit:contain}.video-wrap .play-label{position:absolute;left:9px;top:9px;font-size:11px;background:#101820d9;padding:5px 7px;border-radius:5px;pointer-events:none}.video-wrap .missing{display:grid;place-items:center;height:100%;color:#aab8c3}
.body{padding:13px 14px 16px}.bar{position:relative;height:26px;background:#52616c;border-radius:6px;overflow:hidden;cursor:pointer;border:1px solid #637180}.seg{position:absolute;top:0;height:100%;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;color:#082019;white-space:nowrap;overflow:hidden}.seg.good{background:#61c7a3}.seg.bad{background:#e57468;color:#2f0b0b}.seg.unknown{background:#b9a36f;color:#2b2412}.playhead{position:absolute;top:0;bottom:0;width:2px;background:white;box-shadow:0 0 6px #000;pointer-events:none;display:none}
.axis{display:flex;justify-content:space-between;color:#8fa5b5;font-size:11px;margin:5px 1px 9px}.ranges{font-size:12px;line-height:1.55;min-height:36px;color:#d6e0e8}.ranges .line{display:block}.ranges .badtext{color:#ffaaa0}.ranges .error-text{color:#f0d18a}.meta{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;color:#91a8b7;font-size:11px}.meta span{background:#1c2e3c;padding:4px 6px;border-radius:5px}.source{font-size:10px;color:#6e8596;overflow-wrap:anywhere;margin-top:9px}.empty{color:#9fb1be;text-align:center;padding:60px}footer{max-width:1440px;margin:auto;padding:20px;color:#7990a0;font-size:12px}
</style></head><body>
<header><h1>简智夹爪 · Stage B 逐条筛选审核</h1><p>88 条原始 MCAP · 视频为原始画面预览，红色区间表示当前规则标记剔除的时间。点击时间轴可跳转视频。</p>
<div class="toolbar"><input id="search" placeholder="搜索 episode 文件名或编号"><button data-filter="all" class="active">全部</button><button data-filter="partial">部分剔除</button><button data-filter="discard">整条剔除</button><button data-filter="error">未判定</button><button data-filter="keep">完整保留</button><select id="order"><option value="issue">问题优先</option><option value="index">原始顺序</option></select></div>
<div class="stats" id="stats"></div></header>
<main><div class="note">这是一份技术测试审核页。简智夹爪的阈值仍需人工校准；Stage 1/3 当前只检查关节与夹爪数值，尚未检查 TCP 位姿、状态有效标记和画面内容。3 条输入错误保持“未判定”，没有当作合格或不合格。视频预览经过缩放压缩，不是清洗后输出。</div><div id="grid" class="grid"></div><div id="empty" class="empty" hidden>没有符合当前筛选条件的 episode。</div></main><footer>本页基于 2026-10-27 全量质量判定表生成。视频预览为 480p H.264；原始 MCAP 未改动。</footer>
<script>
const episodes = __EPISODES__;
const $=s=>document.querySelector(s);
const escapeHtml=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sec=s=>{s=Math.max(0,Number(s)||0);return `${String(Math.floor(s/60)).padStart(2,'0')}:${(s%60).toFixed(2).padStart(5,'0')}`};
function type(e){if(e.error)return'error';if(e.discard_episode)return'discard';if(e.removed_ranges?.length)return'partial';return'keep'}
const names={keep:'完整保留',partial:'部分剔除',discard:'整条剔除',error:'未判定'};
function intervals(e){const d=e.episode_duration_s||0,base=e.clock_start_ns||0;if(e.error)return[{start:0,end:d,kind:'unknown',rule:'输入数据错误'}];let bad=(e.removed_ranges||[]).map(r=>({start:Math.max(0,(r.start_time_ns-base)/1e9),end:Math.min(d,(r.end_time_ns-base)/1e9),kind:'bad',rule:e.discard_episode?'S2':(e.metrics?.prefix_trigger||'筛选规则').toUpperCase()})).sort((a,b)=>a.start-b.start);let out=[],at=0;for(const b of bad){if(b.start>at+0.001)out.push({start:at,end:b.start,kind:'good',rule:'保留'});out.push(b);at=Math.max(at,b.end)}if(at<d-0.001)out.push({start:at,end:d,kind:'good',rule:'保留'});return out.length?out:[{start:0,end:d,kind:'good',rule:'保留'}]}
function renderCard(e){let t=type(e),d=e.episode_duration_s||0,parts=intervals(e),name=e.source.split('/').at(-1),ep=String(e.episode_index).padStart(3,'0');let bars=parts.map(p=>`<div class="seg ${p.kind}" style="left:${(p.start/d*100||0).toFixed(4)}%;width:${((p.end-p.start)/d*100||0).toFixed(4)}%" title="${escapeHtml(p.rule)} ${sec(p.start)}–${sec(p.end)}">${p.kind==='bad'?escapeHtml(p.rule):''}</div>`).join('');let lines=parts.map(p=>`<span class="line ${p.kind==='bad'?'badtext':p.kind==='unknown'?'error-text':''}">${p.kind==='good'?'保留':p.kind==='bad'?'剔除 · '+escapeHtml(p.rule):'未判定'} ${sec(p.start)}–${sec(p.end)}</span>`).join('');let sourceStatus=e.error?escapeHtml(e.error):e.reasons?.length?escapeHtml(e.reasons.join('；')):'';let media=e.video?`<video controls playsinline preload="none" poster="${escapeHtml(e.poster)}" src="${escapeHtml(e.video)}"></video><div class="play-label">原视频 · 当前时间 <span class="current">${sec(e.video_start_s)}</span> · <span class="current-status">${t==='error'?'未判定':'保留'}</span></div>`:`<div class="missing">视频预览生成失败：${escapeHtml(e.preview_error||'无视频')}</div>`;return `<article class="card ${t} ${t==='keep'?'':'issue'}" data-id="${e.episode_index}"><div class="head"><div><div class="name">EP ${ep} · ${escapeHtml(name)}</div><div class="sub">源视频 ${sec(d)} · 预览覆盖 ${sec(e.video_start_s)}–${sec(e.video_end_s)}</div></div><span class="badge">${names[t]}</span></div><div class="video-wrap">${media}</div><div class="body"><div class="bar" role="slider" aria-label="Episode 时间轴" tabindex="0">${bars}<div class="playhead"></div></div><div class="axis"><span>00:00.00</span><span>${sec(d)}</span></div><div class="ranges">${lines}${sourceStatus?`<span class="line ${t==='error'?'error-text':'badtext'}">原因：${sourceStatus}</span>`:''}</div><div class="meta"><span>质量帧 ${e.metrics?.num_frames??'未判定'}</span><span>S1 ${e.metrics?.stage1_flagged_frames??'—'}</span><span>S2 DA ${e.metrics?.stage2_da_mean?.toFixed(3)??'—'}</span><span>S3 ${e.metrics?.stage3_excluded_frames??'—'}</span><span>S4 ${e.metrics?.stage4_removed_frames??'—'}</span></div><div class="source">来源：${escapeHtml(e.source)}</div></div></article>`}
let filter='all';function render(){let q=$('#search').value.trim().toLowerCase();let shown=episodes.filter(e=>(filter==='all'||type(e)===filter)&&(!q||e.source.toLowerCase().includes(q)||String(e.episode_index).includes(q)));let rank={partial:0,discard:1,error:2,keep:3};if($('#order').value==='issue')shown.sort((a,b)=>rank[type(a)]-rank[type(b)]||a.episode_index-b.episode_index);else shown.sort((a,b)=>a.episode_index-b.episode_index);$('#grid').innerHTML=shown.map(renderCard).join('');$('#empty').hidden=shown.length>0;for(const card of document.querySelectorAll('.card')){let e=episodes.find(x=>x.episode_index===Number(card.dataset.id)),video=card.querySelector('video'),bar=card.querySelector('.bar'),head=card.querySelector('.playhead');if(!video)continue;video.addEventListener('timeupdate',()=>{let videoSpan=Math.max(.001,e.video_end_s-e.video_start_s),duration=Math.max(.001,video.duration-1/30),at=e.video_start_s+video.currentTime/duration*videoSpan;head.style.left=`${Math.max(0,Math.min(100,at/e.episode_duration_s*100))}%`;head.style.display='block';card.querySelector('.current').textContent=sec(at);let p=intervals(e).find(x=>at>=x.start&&at<x.end);card.querySelector('.current-status').textContent=p?.kind==='bad'?'剔除 · '+p.rule:p?.kind==='unknown'?'未判定':'保留'});bar.addEventListener('click',event=>{let at=(event.clientX-bar.getBoundingClientRect().left)/bar.clientWidth*e.episode_duration_s;let span=Math.max(.001,e.video_end_s-e.video_start_s);let seek=Math.max(0,Math.min(1,(at-e.video_start_s)/span));let go=()=>{video.currentTime=seek*Math.max(0,video.duration-1/30);video.play().catch(()=>{})};if(video.readyState>=1)go();else{video.addEventListener('loadedmetadata',go,{once:true});video.load()}})}}
$('#stats').innerHTML=[['总计',episodes.length],['完整保留',episodes.filter(e=>type(e)==='keep').length],['部分剔除',episodes.filter(e=>type(e)==='partial').length],['整条剔除',episodes.filter(e=>type(e)==='discard').length],['未判定',episodes.filter(e=>type(e)==='error').length],['标记剔除','60.68 秒']].map(([n,v])=>`<div class="stat">${n} <strong>${v}</strong></div>`).join('');
for(const button of document.querySelectorAll('button[data-filter]'))button.addEventListener('click',()=>{document.querySelectorAll('button[data-filter]').forEach(b=>b.classList.remove('active'));button.classList.add('active');filter=button.dataset.filter;render()});$('#search').addEventListener('input',render);$('#order').addEventListener('change',render);render();
</script></body></html>'''
    return template.replace("__EPISODES__", data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.decisions.read_text(encoding="utf-8").splitlines()]
    output = args.output.resolve()
    media = output / "media"
    media.mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_binary()
    results: dict[int, dict] = {}
    errors: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        tasks = {pool.submit(preview_one, row, media, ffmpeg): row for row in rows}
        for future in as_completed(tasks):
            row = tasks[future]
            index = int(row["episode_index"])
            try:
                results[index] = future.result()
                print(f"[{len(results)+len(errors)}/{len(rows)}] preview EP {index:03d}", flush=True)
            except Exception as exc:
                errors[index] = f"{type(exc).__name__}: {exc}"
                print(f"[{len(results)+len(errors)}/{len(rows)}] ERROR EP {index:03d}: {exc}", flush=True)
    index = output / "index.html"
    index.write_text(build_html(rows, results, errors), encoding="utf-8")
    (output / "preview_build_summary.json").write_text(json.dumps({
        "episodes": len(rows), "previews": len(results), "preview_errors": errors,
        "html": str(index),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"HTML {index} | previews={len(results)} errors={len(errors)}", flush=True)


if __name__ == "__main__":
    main()
