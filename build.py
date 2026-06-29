# -*- coding: utf-8 -*-
"""
LP 출자사업 공고 모니터링 — 빌드 스크립트
1) 5개 기관 사이트에서 공고를 수집한다.
2) state.json 으로 '신규(NEW)' 여부를 추적한다. (처음 발견된 날 기준 최근 N일이면 NEW)
3) 자체 완결형 index.html (데이터 내장) 과 data.json 을 생성한다.

실행: python build.py
"""
import json
import re
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import scrapers
from scrapers import SCRAPERS, SOURCE_NAMES, attachment_deadline

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
DATA_FILE = ROOT / "data.json"
HTML_FILE = ROOT / "index.html"

NEW_DAYS = 3  # 공고 게시일이 최근 며칠 이내면 NEW (게시 후 3일까지만 신규)

SOURCE_ORDER = ["kvic", "kgrowth", "kvca", "kfcc", "shinhan", "kofia",
                "nps", "mmaa", "ktcu", "kif"]
SOURCE_COLORS = {
    "kvic": "#1c3c63",     # 네이비 (Premier Partners 메인)
    "kgrowth": "#2f6fa5",  # 애저 블루
    "kvca": "#5a6bb0",     # 인디고
    "kfcc": "#2f8f7a",     # 틸 ([펀드]/[운용사] 소스)
    "shinhan": "#7d5ba6",  # 퍼플
    "kofia": "#b5524b",    # 테라코타 (집약 게시판)
    "nps": "#0e7490",      # 시안 (국민연금)
    "mmaa": "#4d7c0f",     # 올리브 (군인공제회)
    "ktcu": "#be185d",     # 마젠타 (교직원공제회)
    "kif": "#7c2d12",      # 브라운 (KIF)
}

# 명백히 펀드 출자와 무관한 공고(채용/포럼/시스템 등) — 모든 소스에 적용해 제외
GLOBAL_EXCLUDE = re.compile(r"채용|포럼|세미나|워크숍|행사\s*안내|시상|수상|보도자료|"
                            r"시스템\s*점검|점검\s*안내|개인정보|홈페이지\s*개편|연락처|설문조사|이벤트")


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"first_seen": {}}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _short_err(e):
    e = str(e)
    if any(k in e for k in ("timed out", "ConnectTimeout", "Max retries", "ConnectionError", "ReadTimeout")):
        return "연결 시간 초과"
    return e[:80]


def _doc_rank(title):
    """공고 종류 우선순위(낮을수록 대표). 같은 펀드의 본 공고를 서식/현황보다 우선."""
    if re.search(r"제출\s*서[류식]|서식|양식|FAQ|질의|Q\s*&\s*A", title):
        return 3
    if re.search(r"접수\s*현황|선정\s*결과|심사\s*결과|결과", title):
        return 2
    if re.search(r"선정\s*계획|출자\s*사업|위탁운용사|선정\s*공고|출자\s*공고|모집\s*공고|운용사\s*선정", title):
        return 0
    return 1


def _norm_fund(title):
    """제목 정규화: 【선정공고】·[기관] 태그/기호/공백 제거 → 같은 펀드 식별용."""
    t = re.sub(r"[【\[][^】\]]{0,25}[】\]]", "", title or "")
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", t)
    t = re.sub(r"(공고문?|공고안|계획|의건|공고)$", "", t)
    return t.lower()


def _dedup(items):
    """같은 펀드+같은 종류의 공고가 여러 소스에 중복되면 하나만(마감일 보유 > 소스 우선순위)."""
    prio = {c: i for i, c in enumerate(SOURCE_ORDER)}
    best, order = {}, []
    for it in items:
        fund = _norm_fund(it.get("title", ""))
        if len(fund) < 6:                      # 너무 짧으면 오병합 위험 → 중복판정 제외
            order.append(it)
            continue
        key = fund + ":" + str(_doc_rank(it.get("title", "")))
        cur = best.get(key)
        if cur is None:
            best[key] = it
            order.append(it)
        elif (bool(it.get("deadline")), -prio.get(it["source"], 99)) > \
             (bool(cur.get("deadline")), -prio.get(cur["source"], 99)):
            order[order.index(cur)] = it
            best[key] = it
    return order


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    KST = timezone(timedelta(hours=9))           # 한국 시간 기준(클라우드는 UTC라 보정 필요)
    now = datetime.now(KST)
    today = now.date()
    today_str = today.isoformat()
    state = load_state()
    first_seen = state.setdefault("first_seen", {})

    # 직전 수집 결과(이월용): 이번에 실패한 소스는 이전 데이터를 유지해 빈 화면 방지
    prev_by_source = {}
    if DATA_FILE.exists():
        try:
            for it in json.loads(DATA_FILE.read_text(encoding="utf-8")).get("items", []):
                prev_by_source.setdefault(it.get("source"), []).append(it)
        except Exception:
            pass

    all_items = []
    source_status = []

    for code in SOURCE_ORDER:
        fn = SCRAPERS[code]
        items, err = None, ""
        for attempt in range(3):                      # 소스 단위 재시도(HTTP 재시도와 별개)
            try:
                items = fn()
                break
            except Exception as e:
                err = str(e)
                print(f"[retry {attempt + 1}/3] {code}: {e}")
                time.sleep(3 * (attempt + 1))
        if items is not None:
            source_status.append({"code": code, "name": SOURCE_NAMES[code],
                                  "count": len(items), "ok": True, "error": "", "carried": 0})
            all_items.extend(items)
            print(f"[OK] {code}: {len(items)}건")
        else:
            carried = prev_by_source.get(code, [])    # 실패 → 직전 수집분 유지
            source_status.append({"code": code, "name": SOURCE_NAMES[code],
                                  "count": len(carried), "ok": False,
                                  "error": _short_err(err), "carried": len(carried)})
            all_items.extend(carried)
            print(f"[FAIL] {code}: {err} — 이전 {len(carried)}건 유지")

    # 펀드 출자와 무관한 공고 제거(채용/포럼 등) + 소스 간 중복 제거
    n0 = len(all_items)
    all_items = [it for it in all_items if not GLOBAL_EXCLUDE.search(it.get("title", ""))]
    n1 = len(all_items)
    all_items = _dedup(all_items)
    print(f"필터 제거 {n0 - n1}건 · 중복 제거 {n1 - len(all_items)}건 → {len(all_items)}건")

    # 첨부 PDF에서 마감일 보강 (K-Growth/신한). 결과는 캐시 → 매일 새 항목만 다운로드.
    dcache = state.setdefault("deadline_cache", {})
    tried = 0
    for it in all_items:
        if it.get("deadline") or it["source"] not in ("kgrowth", "shinhan"):
            continue
        uid = f"{it['source']}:{it['id']}"
        if uid in dcache:
            it["deadline"] = dcache[uid]
        else:
            it["deadline"] = dcache[uid] = attachment_deadline(it["source"], it["id"], it.get("date", ""))
            tried += 1
    if tried:
        print(f"첨부 마감일 추출 시도: {tried}건 (신규)")

    # 신규 여부 계산: '신규' = 공고 게시일(date)이 최근 NEW_DAYS일 이내(=3일 전까지). 그 외는 신규 아님.
    new_count = 0
    closed_count = 0
    cutoff = today - timedelta(days=NEW_DAYS)
    for it in all_items:
        uid = f"{it['source']}:{it['id']}"
        if uid not in first_seen:
            first_seen[uid] = today_str
        it["first_seen"] = first_seen[uid]
        try:
            pdate = datetime.strptime(it.get("date") or "", "%Y-%m-%d").date()
        except ValueError:
            pdate = None
        it["is_new"] = bool(pdate and pdate >= cutoff)   # 게시일 기준
        if it["is_new"]:
            new_count += 1
        # 마감일이 오늘(KST)보다 이전이면 '마감' 처리 (ISO 날짜라 문자열 비교 = 날짜 비교)
        dl = it.get("deadline") or ""
        it["is_closed"] = bool(dl and dl < today_str)
        if it["is_closed"]:
            closed_count += 1
        it["source_name"] = SOURCE_NAMES[it["source"]]

    # 정렬: 등록일 내림차순, 그 다음 최초수집일 내림차순
    all_items.sort(key=lambda x: (x.get("date") or "", x.get("first_seen") or ""), reverse=True)

    meta = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M") + " KST",
        "today": today_str,
        "new_days": NEW_DAYS,
        "total": len(all_items),
        "new_count": new_count,
        "closed_count": closed_count,
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
    --bg:#eef2f7; --card:#ffffff; --line:#dde4ee; --text:#1f2b3a;
    --muted:#67748a; --navy:#1c3c63; --accent:#2f6fa5;
    --new:#a9791f; --newbg:#f7eed6;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
       font-family:"Malgun Gothic","Apple SD Gothic Neo",system-ui,sans-serif;
       -webkit-text-size-adjust:100%}
  header{background:#ffffff;color:var(--navy);padding:13px 16px;
         border-bottom:2px solid var(--navy)}
  .brand-row{display:flex;align-items:center;gap:15px;max-width:980px;margin:0 auto}
  .logo{height:60px;width:auto;display:block;flex:none}
  .htitle{border-left:1px solid var(--line);padding-left:15px}
  header h1{margin:0;font-size:18px;font-weight:700;color:var(--navy);letter-spacing:-.2px}
  header .sub{margin-top:4px;font-size:12px;color:var(--muted)}
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
  .viewtoggle{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:#fff}
  .viewtoggle button{border:none;background:#fff;color:var(--muted);padding:6px 11px;
                     font-size:13px;cursor:pointer;font-family:inherit}
  .viewtoggle button.on{background:var(--navy);color:#fff;font-weight:600}
  .group{display:flex;flex-direction:column;gap:8px;margin-top:12px}
  .group:first-child{margin-top:2px}
  .group-head{display:flex;align-items:center;gap:9px;padding:9px 12px;border-radius:9px;
              background:#e7edf5;border-left:5px solid var(--navy)}
  .group-dot{width:11px;height:11px;border-radius:3px;flex:none}
  .group-title{font-size:15px;font-weight:700;color:var(--navy)}
  .group-count{font-size:12px;color:var(--muted);margin-left:auto}
  .summary{font-size:12px;color:var(--muted);margin:2px 2px 10px}
  .summary b{color:var(--new)}
  .list{display:flex;flex-direction:column;gap:8px}
  .item{display:block;background:var(--card);border:1px solid var(--line);
        border-radius:12px;padding:12px 14px;text-decoration:none;color:inherit;
        transition:.12s;position:relative}
  .item:hover{border-color:var(--accent);box-shadow:0 2px 10px rgba(37,99,235,.08)}
  .item.new{border-left:4px solid var(--new)}
  .item.closed{opacity:.45}
  .item.closed:hover{opacity:.9}
  .closedtag{font-size:10px;font-weight:700;color:#fff;background:#94a0ad;
             padding:2px 7px;border-radius:6px}
  .sub-row .ddl.done{color:#8a93a0;font-weight:600;text-decoration:line-through}
  .meta-row{display:flex;align-items:center;gap:7px;margin-bottom:6px;flex-wrap:wrap}
  .badge{font-size:11px;font-weight:700;color:#fff;padding:2px 8px;border-radius:6px}
  .newtag{font-size:10px;font-weight:700;color:var(--new);background:var(--newbg);
          padding:2px 7px;border-radius:6px;border:1px solid #e3cf9c}
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
    .logo{height:48px}
    header h1{font-size:16px}
    .htitle{padding-left:12px}
  }
</style>
</head>
<body>
<header>
  <div class="brand-row">
    <img class="logo" src="logo.jpg" alt="Premier Partners">
    <div class="htitle">
      <h1>LP 출자사업 공고 모니터링</h1>
      <div class="sub" id="hsub"></div>
    </div>
  </div>
</header>
<div class="wrap">
  <div class="toolbar">
    <input class="search" id="q" placeholder="제목·기관 검색…" autocomplete="off">
    <div class="chips" id="chips"></div>
    <div class="opts">
      <div class="viewtoggle" id="viewtoggle">
        <button data-view="inst" class="on">🏢 기관별</button>
        <button data-view="date">📅 날짜순</button>
      </div>
      <label><input type="checkbox" id="newonly"> 신규만 보기</label>
      <label><input type="checkbox" id="hideclosed"> 마감 제외</label>
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
let activeSource = "all", query = "", newOnly = false, hideClosed = false, groupBy = "inst";

document.getElementById("hsub").textContent =
   `최종 업데이트 ${meta.generated_at} · 전체 ${meta.total}건 · 신규 ${meta.new_count}건`;

// 수집 실패 안내
const errBox = document.getElementById("errors");
meta.sources.filter(s=>!s.ok).forEach(s=>{
  const d=document.createElement("div"); d.className="err";
  d.textContent = s.carried
    ? `⚠ ${s.name} 일시적 수집 실패 — 직전 수집분 ${s.carried}건 표시 중`
    : `⚠ ${s.name} 수집 실패 (${s.error})`;
  errBox.appendChild(d);
});

// 칩(필터) 생성
const counts = {all: items.length};
ORDER.forEach(c=>counts[c]=items.filter(i=>i.source===c).length);
const chipBox = document.getElementById("chips");
function makeChip(code,label){
  const el=document.createElement("div"); el.className="chip"; el.dataset.code=code;
  el.innerHTML = `${label}<span class="cnt">${counts[code]||0}</span>`;
  if(code===activeSource){el.classList.add("active"); el.style.background=code==="all"?"#1f3a5f":COLORS[code];}
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
document.getElementById("hideclosed").addEventListener("change",e=>{hideClosed=e.target.checked;render();});
document.querySelectorAll('#viewtoggle button').forEach(b=>{
  b.addEventListener("click",()=>{
    groupBy=b.dataset.view;
    document.querySelectorAll('#viewtoggle button').forEach(x=>x.classList.toggle("on", x===b));
    render();
  });
});

function esc(s){return (s||"").replace(/[&<>]/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[m]));}

function itemHTML(i){
  const c=COLORS[i.source]||"#666";
  return `<a class="item ${i.is_new?'new':''} ${i.is_closed?'closed':''}" href="${i.url}" target="_blank" rel="noopener">
      <div class="meta-row">
        <span class="badge" style="background:${c}">${esc(i.source_name)}</span>
        ${i.is_new?'<span class="newtag">NEW</span>':''}
        ${i.is_closed?'<span class="closedtag">마감</span>':''}
        <span class="date">${i.date||'-'}</span>
      </div>
      <div class="title">${esc(i.title)}</div>
      ${(i.org||i.deadline)?`<div class="sub-row">
          ${i.org?`<span>🏢 ${esc(i.org)}</span>`:''}
          ${i.deadline?`<span class="ddl ${i.is_closed?'done':''}">⏰ 마감 ${i.deadline}</span>`:''}
      </div>`:''}
    </a>`;
}

function render(){
  let rows = items.filter(i=>{
    if(activeSource!=="all" && i.source!==activeSource) return false;
    if(newOnly && !i.is_new) return false;
    if(hideClosed && i.is_closed) return false;
    if(query){
      const hay=(i.title+" "+(i.org||"")+" "+i.source_name).toLowerCase();
      if(!hay.includes(query)) return false;
    }
    return true;
  });
  const list=document.getElementById("list");
  document.getElementById("empty").style.display = rows.length? "none":"block";
  const nClosed = rows.filter(r=>r.is_closed).length;
  document.getElementById("summary").innerHTML =
     `표시 ${rows.length}건`
     + (rows.filter(r=>r.is_new).length? ` · <b>신규 ${rows.filter(r=>r.is_new).length}건</b>`:"")
     + (nClosed? ` · 마감 ${nClosed}건`:"");
  if(groupBy==="inst"){
    let html="";
    ORDER.forEach(code=>{
      const g=rows.filter(r=>r.source===code);
      if(!g.length) return;
      const col=COLORS[code]||"#666";
      const nn=g.filter(r=>r.is_new).length, nc=g.filter(r=>r.is_closed).length;
      html += `<div class="group">
        <div class="group-head" style="border-left-color:${col}">
          <span class="group-dot" style="background:${col}"></span>
          <span class="group-title">${NAMES[code]}</span>
          <span class="group-count">${g.length}건${nn?` · 신규 ${nn}`:""}${nc?` · 마감 ${nc}`:""}</span>
        </div>
        ${g.map(itemHTML).join("")}
      </div>`;
    });
    list.innerHTML = html;
  } else {
    list.innerHTML = rows.map(itemHTML).join("");
  }
}
render();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
