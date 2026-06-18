# -*- coding: utf-8 -*-
"""
LP 출자사업 공고 모니터링 — 빌드 스크립트
1) 5개 기관 사이트에서 공고를 수집한다.
2) state.json 으로 '신규(NEW)' 여부를 추적한다. (처음 발견된 날 기준 최근 N일이면 NEW)
3) 자체 완결형 index.html (데이터 내장) 과 data.json 을 생성한다.

실행: python build.py
"""
import json
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import scrapers
from scrapers import SCRAPERS, SOURCE_NAMES

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
DATA_FILE = ROOT / "data.json"
HTML_FILE = ROOT / "index.html"

NEW_DAYS = 3  # 처음 수집된 뒤 며칠 동안 NEW 배지를 유지할지

SOURCE_ORDER = ["kvic", "kgrowth", "kvca", "kfcc", "shinhan"]
SOURCE_COLORS = {
    "kvic": "#2563eb",
    "kgrowth": "#0891b2",
    "kvca": "#7c3aed",
    "kfcc": "#dc2626",
    "shinhan": "#ea580c",
}


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"first_seen": {}}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    today = date.today()
    today_str = today.isoformat()
    state = load_state()
    first_seen = state.setdefault("first_seen", {})

    all_items = []
    source_status = []

    for code in SOURCE_ORDER:
        fn = SCRAPERS[code]
        try:
            items = fn()
            source_status.append({"code": code, "name": SOURCE_NAMES[code],
                                  "count": len(items), "ok": True, "error": ""})
            all_items.extend(items)
            print(f"[OK] {code}: {len(items)}건")
        except Exception as e:
            source_status.append({"code": code, "name": SOURCE_NAMES[code],
                                  "count": 0, "ok": False, "error": str(e)})
            print(f"[FAIL] {code}: {e}")
            traceback.print_exc()

    # 신규 여부 계산
    new_count = 0
    cutoff = today - timedelta(days=NEW_DAYS)
    for it in all_items:
        uid = f"{it['source']}:{it['id']}"
        if uid not in first_seen:
            first_seen[uid] = today_str
        it["first_seen"] = first_seen[uid]
        try:
            fs = datetime.strptime(first_seen[uid], "%Y-%m-%d").date()
        except Exception:
            fs = today
        it["is_new"] = fs >= cutoff
        if it["is_new"]:
            new_count += 1
        it["source_name"] = SOURCE_NAMES[it["source"]]

    # 정렬: 등록일 내림차순, 그 다음 최초수집일 내림차순
    all_items.sort(key=lambda x: (x.get("date") or "", x.get("first_seen") or ""), reverse=True)

    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "today": today_str,
        "new_days": NEW_DAYS,
        "total": len(all_items),
        "new_count": new_count,
        "sources": source_status,
    }

    payload = {"meta": meta, "items": all_items}
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    save_state(state)

    HTML_FILE.write_text(render_html(payload), encoding="utf-8")
    print(f"\n총 {len(all_items)}건 (신규 {new_count}건) → index.html 생성 완료")


def render_html(payload):
    data_json = json.dumps(payload, ensure_ascii=False)
    colors_json = json.dumps(SOURCE_COLORS, ensure_ascii=False)
    order_json = json.dumps(SOURCE_ORDER, ensure_ascii=False)
    names_json = json.dumps(SOURCE_NAMES, ensure_ascii=False)
    return TEMPLATE.replace("/*__DATA__*/", data_json) \
                   .replace("/*__COLORS__*/", colors_json) \
                   .replace("/*__ORDER__*/", order_json) \
                   .replace("/*__NAMES__*/", names_json)


TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LP 출자사업 공고 모니터링</title>
<style>
  :root{
    --bg:#f4f6f9; --card:#ffffff; --line:#e6e9ef; --text:#1f2733;
    --muted:#6b7480; --accent:#2563eb; --new:#16a34a; --newbg:#e7f7ec;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
       font-family:"Malgun Gothic","Apple SD Gothic Neo",system-ui,sans-serif;
       -webkit-text-size-adjust:100%}
  header{background:linear-gradient(135deg,#1e3a8a,#2563eb);color:#fff;
         padding:18px 16px 14px}
  header h1{margin:0;font-size:19px;font-weight:700}
  header .sub{margin-top:4px;font-size:12px;opacity:.85}
  .wrap{max-width:980px;margin:0 auto;padding:14px 12px 60px}
  .toolbar{position:sticky;top:0;z-index:5;background:var(--bg);
           padding:10px 0 8px;margin-bottom:6px}
  .search{width:100%;padding:11px 13px;border:1px solid var(--line);
          border-radius:10px;font-size:15px;background:#fff;outline:none}
  .search:focus{border-color:var(--accent)}
  .chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px}
  .chip{border:1px solid var(--line);background:#fff;color:var(--muted);
        padding:6px 11px;border-radius:999px;font-size:13px;cursor:pointer;
        user-select:none;white-space:nowrap}
  .chip.active{color:#fff;border-color:transparent}
  .chip .cnt{font-size:11px;opacity:.8;margin-left:3px}
  .opts{display:flex;align-items:center;gap:14px;margin-top:9px;font-size:13px;
        color:var(--muted);flex-wrap:wrap}
  .opts label{display:flex;align-items:center;gap:5px;cursor:pointer}
  .summary{font-size:12px;color:var(--muted);margin:2px 2px 10px}
  .summary b{color:var(--new)}
  .list{display:flex;flex-direction:column;gap:8px}
  .item{display:block;background:var(--card);border:1px solid var(--line);
        border-radius:12px;padding:12px 14px;text-decoration:none;color:inherit;
        transition:.12s;position:relative}
  .item:hover{border-color:var(--accent);box-shadow:0 2px 10px rgba(37,99,235,.08)}
  .item.new{border-left:4px solid var(--new)}
  .meta-row{display:flex;align-items:center;gap:7px;margin-bottom:6px;flex-wrap:wrap}
  .badge{font-size:11px;font-weight:700;color:#fff;padding:2px 8px;border-radius:6px}
  .newtag{font-size:10px;font-weight:700;color:var(--new);background:var(--newbg);
          padding:2px 7px;border-radius:6px;border:1px solid #bfe9cb}
  .date{font-size:12px;color:var(--muted);margin-left:auto}
  .title{font-size:15px;font-weight:600;line-height:1.45;word-break:keep-all}
  .sub-row{margin-top:6px;font-size:12px;color:var(--muted);display:flex;gap:10px;flex-wrap:wrap}
  .sub-row .ddl{color:#b45309;font-weight:600}
  .empty{text-align:center;color:var(--muted);padding:50px 0;font-size:14px}
  .err{background:#fff4f4;border:1px solid #f3c9c9;color:#b42318;
       border-radius:10px;padding:9px 12px;font-size:12.5px;margin-bottom:10px}
  footer{text-align:center;color:var(--muted);font-size:11px;margin-top:24px}
  @media (max-width:520px){
    .title{font-size:14.5px}
    .date{margin-left:0;width:100%;order:9}
  }
</style>
</head>
<body>
<header>
  <h1>📋 LP 출자사업 공고 모니터링</h1>
  <div class="sub" id="hsub"></div>
</header>
<div class="wrap">
  <div class="toolbar">
    <input class="search" id="q" placeholder="제목·기관 검색…" autocomplete="off">
    <div class="chips" id="chips"></div>
    <div class="opts">
      <label><input type="checkbox" id="newonly"> 신규만 보기</label>
    </div>
  </div>
  <div class="summary" id="summary"></div>
  <div id="errors"></div>
  <div class="list" id="list"></div>
  <div class="empty" id="empty" style="display:none">조건에 맞는 공고가 없습니다.</div>
  <footer>매일 자동 수집됩니다 · 제목을 누르면 원문 공고 페이지로 이동합니다.</footer>
</div>

<script>
const PAYLOAD = /*__DATA__*/;
const COLORS  = /*__COLORS__*/;
const ORDER   = /*__ORDER__*/;
const NAMES   = /*__NAMES__*/;
const items = PAYLOAD.items, meta = PAYLOAD.meta;
let activeSource = "all", query = "", newOnly = false;

document.getElementById("hsub").textContent =
   `최종 업데이트 ${meta.generated_at} · 전체 ${meta.total}건 · 신규 ${meta.new_count}건`;

// 수집 실패 안내
const errBox = document.getElementById("errors");
meta.sources.filter(s=>!s.ok).forEach(s=>{
  const d=document.createElement("div"); d.className="err";
  d.textContent=`⚠ ${s.name} 수집 실패 (${s.error})`; errBox.appendChild(d);
});

// 칩(필터) 생성
const counts = {all: items.length};
ORDER.forEach(c=>counts[c]=items.filter(i=>i.source===c).length);
const chipBox = document.getElementById("chips");
function makeChip(code,label){
  const el=document.createElement("div"); el.className="chip"; el.dataset.code=code;
  el.innerHTML = `${label}<span class="cnt">${counts[code]||0}</span>`;
  if(code===activeSource){el.classList.add("active"); el.style.background=code==="all"?"#1f2733":COLORS[code];}
  el.onclick=()=>{activeSource=code; refreshChips(); render();};
  return el;
}
function refreshChips(){
  chipBox.innerHTML="";
  chipBox.appendChild(makeChip("all","전체"));
  ORDER.forEach(c=>chipBox.appendChild(makeChip(c,NAMES[c])));
}
refreshChips();

document.getElementById("q").addEventListener("input",e=>{query=e.target.value.trim().toLowerCase();render();});
document.getElementById("newonly").addEventListener("change",e=>{newOnly=e.target.checked;render();});

function esc(s){return (s||"").replace(/[&<>]/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[m]));}

function render(){
  let rows = items.filter(i=>{
    if(activeSource!=="all" && i.source!==activeSource) return false;
    if(newOnly && !i.is_new) return false;
    if(query){
      const hay=(i.title+" "+(i.org||"")+" "+i.source_name).toLowerCase();
      if(!hay.includes(query)) return false;
    }
    return true;
  });
  const list=document.getElementById("list");
  document.getElementById("empty").style.display = rows.length? "none":"block";
  document.getElementById("summary").innerHTML =
     `표시 ${rows.length}건` + (rows.filter(r=>r.is_new).length? ` · <b>신규 ${rows.filter(r=>r.is_new).length}건</b>`:"");
  list.innerHTML = rows.map(i=>{
    const c=COLORS[i.source]||"#666";
    return `<a class="item ${i.is_new?'new':''}" href="${i.url}" target="_blank" rel="noopener">
      <div class="meta-row">
        <span class="badge" style="background:${c}">${esc(i.source_name)}</span>
        ${i.is_new?'<span class="newtag">NEW</span>':''}
        <span class="date">${i.date||'-'}</span>
      </div>
      <div class="title">${esc(i.title)}</div>
      ${(i.org||i.deadline)?`<div class="sub-row">
          ${i.org?`<span>🏢 ${esc(i.org)}</span>`:''}
          ${i.deadline?`<span class="ddl">⏰ 마감 ${i.deadline}</span>`:''}
      </div>`:''}
    </a>`;
  }).join("");
}
render();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
