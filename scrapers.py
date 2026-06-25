# -*- coding: utf-8 -*-
"""
LP 출자사업 공고 수집기 (scrapers)
각 기관 사이트별로 공고 목록을 가져와 표준 형식의 dict 리스트로 반환한다.

표준 형식(dict):
  source       : 기관 코드 (kvic / kgrowth / kvca / kfcc / shinhan)
  source_name  : 기관 표시 이름 (한글)
  id           : 사이트 내부 글 번호 (문자열)
  title        : 공고 제목
  date         : 등록일 (YYYY-MM-DD)
  deadline     : 마감일 (YYYY-MM-DD) 또는 ""  (없으면 빈 문자열)
  org          : 주관/기관 (있으면) 또는 ""
  url          : 원문 공고 페이지 URL
"""
import re
import time
import requests
import urllib3
from bs4 import BeautifulSoup
from datetime import date, timedelta

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    import fitz  # PyMuPDF — 첨부 PDF에서 마감일 추출용
except Exception:
    fitz = None

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}
TIMEOUT = (10, 30)            # (connect, read) — 일부 한국 사이트가 해외 IP에 느림

# 연결 실패/타임아웃 시 자동 재시도(백오프). 클라우드(해외 IP)의 간헐적 타임아웃 완화.
from requests.adapters import HTTPAdapter
try:
    from urllib3.util.retry import Retry
    _RETRY = Retry(total=4, connect=4, read=3, backoff_factor=1.3,
                   status_forcelist=[429, 500, 502, 503, 504])
except Exception:
    _RETRY = None

SOURCE_NAMES = {
    "kvic": "한국벤처투자(모태펀드)",
    "kgrowth": "한국성장금융",
    "kvca": "벤처캐피탈협회",
    "kfcc": "새마을금고중앙회",
    "shinhan": "신한벤처투자",
    "kofia": "금융투자협회(KOFIA)",
}

# KOFIA(금융투자협회) 안내사항 게시판: 펀드 출자(사모/PE/VC 블라인드) 공고만 선별.
# 공모주식·채권 위탁/거래증권사 같은 공개시장 운용사 선정·채용·포럼 등은 제외.
_KOFIA_INC = re.compile(r"출자|블라인드|모펀드|모태|벤처|사모|PE|에쿼티|투자조합|세컨더리|"
                        r"프로젝트\s*펀드|업무집행조합원|신기술|임팩트|메자닌|그로스|바이아웃|코파|루키")
_KOFIA_EXC = re.compile(r"채용|포럼|세미나|워크숍|행사|시상|수상|보도|거래\s*증권사|증권사\s*선정|"
                        r"수탁|사무관리|국내\s*주식|해외\s*주식|주식형|채권|지분증권|대형주|중소형주|"
                        r"Active|패시브|인덱스|MMF|자문운용사|설명회|점검|일반사무")
_KOFIA_FUNDSEL = re.compile(r"위탁운용사|출자|운용사\s*선정|업무집행")


def _is_kofia_fund(title):
    """KOFIA 제목이 '펀드 출자(사모/PE/VC)' 공고인지. 공개시장 운용/채용/포럼 등은 제외."""
    if _KOFIA_EXC.search(title):
        return False
    if _KOFIA_INC.search(title):
        return True
    # 일반 '○○펀드 위탁운용사/출자/운용사 선정'도 포함 (공개시장형은 위 EXC에서 이미 제거)
    return bool(re.search(r"펀드", title) and _KOFIA_FUNDSEL.search(title))


def _new_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    if _RETRY is not None:
        ad = HTTPAdapter(max_retries=_RETRY)
        s.mount("https://", ad)
        s.mount("http://", ad)
    return s


_SESSION = _new_session()


def _get(url, **kw):
    kw.setdefault("timeout", TIMEOUT)
    r = _SESSION.get(url, **kw)
    r.raise_for_status()
    return r


def _norm_date(s):
    """'2026.04.24', '2026-04-24', '2026/04/24' -> '2026-04-24'"""
    if not s:
        return ""
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", s)
    if not m:
        return ""
    y, mo, d = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def _clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def _parse_kdate(s):
    """'2025년 05월 14일' / '2025.05.14' / '2025-5-14' -> '2025-05-14'"""
    m = re.search(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})", s)
    if not m:
        return ""
    y, mo, d = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


# 접수마감/접수기한/신청마감/제출기한/접수기간/신청기간/모집기간 등
_DDL_KW = re.compile(r"(접수\s*마감|접수\s*기한|신청\s*마감|신청\s*기한|제출\s*기한|"
                     r"제안서?\s*접수|접수\s*기간|신청\s*기간|모집\s*기간|마감\s*일시?)")


def extract_deadline(text):
    """본문 텍스트에서 '접수/신청 마감일'을 추출한다. 기간(A~B)이면 끝 날짜(B)를 마감으로 본다.
    명확히 못 찾으면 빈 문자열(잘못된 마감 표시 방지)."""
    text = re.sub(r"[ \t]+", " ", text or "")
    for m in _DDL_KW.finditer(text):
        seg = text[m.start():m.end() + 60]
        if "~" in seg or "∼" in seg or "～" in seg:           # 기간이면 끝 날짜 사용
            seg = re.split(r"[~∼～]", seg, 1)[1]
        d = _parse_kdate(seg)
        if d:
            return d
    return ""


# ----------------------------------------------------------------------------
# 1) 한국벤처투자 (KVIC) — 모태펀드 출자사업 공고
# ----------------------------------------------------------------------------
def scrape_kvic(pages=2):
    base = "https://www.kvic.or.kr/notice/kvic-notice/investment-business-notice"
    out = []
    seen = set()
    for p in range(1, pages + 1):
        r = _get(f"{base}?page={p}")
        r.encoding = r.apparent_encoding or "utf-8"
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.select("a[href*='board_view']"):
            m = re.search(r"board_view\((\d+)\)", a.get("href", ""))
            if not m:
                continue
            gid = m.group(1)
            if gid in seen:                 # ?page=2가 1페이지를 반복 반환 → 중복 방지
                continue
            seen.add(gid)
            tr = a.find_parent("tr")
            row_text = _clean(tr.get_text(" ", strip=True)) if tr else ""
            out.append({
                "source": "kvic",
                "id": gid,
                "title": _clean(a.get_text()),
                "date": _norm_date(row_text),
                "deadline": "",
                "org": "",
                "url": f"{base}?id={gid}",
            })
    return out


# ----------------------------------------------------------------------------
# 2) 한국성장금융 (K-Growth) — 공지사항
# ----------------------------------------------------------------------------
def scrape_kgrowth(pages=2):
    base = "https://www.kgrowth.or.kr/"
    out = []
    seen = set()
    for p in range(1, pages + 1):
        r = _get(f"{base}notice.asp?str_type=1&tab=1&page={p}")
        r.encoding = r.apparent_encoding or "euc-kr"
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.select("a[href*='notice_view']"):
            href = a.get("href", "")
            m = re.search(r"idx=(\d+)", href)
            if not m:
                continue
            gid = m.group(1)
            if gid in seen:
                continue
            seen.add(gid)
            tr = a.find_parent("tr")
            date = ""
            if tr:
                for td in tr.find_all("td"):
                    d = _norm_date(td.get_text())
                    if d:
                        date = d
                        break
            out.append({
                "source": "kgrowth",
                "id": gid,
                "title": _clean(a.get_text()),
                "date": date,
                "deadline": "",
                "org": "",
                "url": base + href.lstrip("/"),
            })
    return out


# ----------------------------------------------------------------------------
# 3) 벤처캐피탈협회 (KVCA) — 출자공고
# ----------------------------------------------------------------------------
def scrape_kvca(pages=2):
    base = "https://www.kvca.or.kr/Program/invest/"
    listurl = base + "list.html?a_gb=board&a_cd=8&a_item=0&sm=2_2_2"
    out = []
    seen = set()
    for p in range(1, pages + 1):
        r = _get(f"{listurl}&page={p}")
        r.encoding = r.apparent_encoding or "utf-8"
        soup = BeautifulSoup(r.text, "lxml")
        for tr in soup.select("tr"):
            a = tr.find("a", href=re.compile("po_no="))
            if not a:
                continue
            m = re.search(r"po_no=(\d+)", a.get("href", ""))
            if not m:
                continue
            gid = m.group(1)
            if gid in seen:
                continue
            seen.add(gid)
            tds = tr.find_all("td")
            org = _clean(tds[1].get_text()) if len(tds) > 1 else ""
            title = _clean(tds[2].get_text()) if len(tds) > 2 else _clean(a.get_text())
            date = _norm_date(tds[3].get_text()) if len(tds) > 3 else ""
            deadline = _norm_date(tds[4].get_text()) if len(tds) > 4 else ""
            out.append({
                "source": "kvca",
                "id": gid,
                "title": title,
                "date": date,
                "deadline": deadline,
                "org": org,
                "url": base + a.get("href", "").lstrip("/"),
            })
    return out


# ----------------------------------------------------------------------------
# 4) 새마을금고중앙회 (KFCC) — [펀드] / [운용사] 공고만
# ----------------------------------------------------------------------------
def scrape_kfcc(pages=6):
    base = "https://www.kfcc.co.kr/mgNotice/"
    out = []
    for p in range(1, pages + 1):
        r = _get(f"{base}mgNoticeList.do?pageNo={p}")
        r.encoding = r.apparent_encoding or "utf-8"
        soup = BeautifulSoup(r.text, "lxml")
        rows = [li for li in soup.select("ul.magazine > li")
                if "top" not in (li.get("class") or [])]
        for li in rows:
            a = li.select_one("a#subject, .info a")
            if not a:
                continue
            m = re.search(r"fnDetail\('?(\d+)'?\)", a.get("href", "") + str(a.get("onclick", "")))
            if not m:
                continue
            gid = m.group(1)
            cate = _clean(li.select_one(".cate").get_text()) if li.select_one(".cate") else ""
            title = _clean(a.get_text())
            blob = cate + " " + title
            # [펀드] 또는 [운용사] 포함 공고만 수집
            if ("펀드" not in blob) and ("운용사" not in blob):
                continue
            date = _norm_date(li.select_one(".date").get_text()) if li.select_one(".date") else ""
            full_title = (cate + " " + title).strip() if cate and cate not in title else title
            out.append({
                "source": "kfcc",
                "id": gid,
                "title": full_title,
                "date": date,
                "deadline": _kfcc_deadline(gid),   # 상세 페이지 본문에서 접수마감일 추출
                "org": "",
                "url": f"{base}mgNoticeDetail.do?no={gid}",
            })
    return out


def _kfcc_deadline(gid):
    """KFCC 상세 페이지 본문에서 접수마감일을 추출 (없으면 '')."""
    try:
        r = _get(f"https://www.kfcc.co.kr/mgNotice/mgNoticeDetail.do?no={gid}")
        r.encoding = r.apparent_encoding or "utf-8"
        soup = BeautifulSoup(r.text, "lxml")
        for t in soup(["script", "style"]):
            t.decompose()
        return extract_deadline(soup.get_text(" "))
    except Exception:
        return ""


# ----------------------------------------------------------------------------
# 5) 신한벤처투자 (Shinhan) — 공지사항 (JSON API)
# ----------------------------------------------------------------------------
def scrape_shinhan(pages=2):
    api = "https://www.shinhanfund.com/api/board/notice"
    view = "https://www.shinhanfund.com/ko/pc/board/noticeView?no="
    out = []
    seen = set()
    for p in range(1, pages + 1):
        r = _get(f"{api}?pageNo={p}")
        data = r.json()
        for it in data.get("items", []):
            gid = str(it.get("no"))
            if gid in seen:
                continue
            seen.add(gid)
            out.append({
                "source": "shinhan",
                "id": gid,
                "title": _clean(it.get("title")),
                "date": _norm_date(it.get("regDate") or it.get("FRONT_DATE")),
                "deadline": "",
                "org": "",
                "url": view + gid,
            })
    return out


# ============================================================================
# 첨부파일(PDF)에서 마감일(제안서 접수 마감) 추출
#   - K-Growth, 신한벤처투자: 공고문이 PDF (숫자 정상 추출됨)
#   - KVIC: 공고문 PDF의 숫자가 깨진 폰트로 추출 불가 → 시도하지 않음
#   - 잘못된 마감 표시를 막기 위해 '그럴듯한 날짜'(공고일~약5개월)만 채택
# ============================================================================
_DATE_RE = r"['‘’]?\s*\d{2,4}\s*[.년]\s*\d{1,2}\s*[.월]\s*\d{1,2}"
# 일정 표(선정일정/추진일정 등) 안에서 '제안서 접수' 행의 날짜만 채택 → 오탐 방지
_SCHED_ANCHOR = re.compile(r"선정\s*일정|추진\s*일정|향후\s*일정|주요\s*일정|평가\s*일정|진행\s*일정|모집\s*일정")
_SUBMIT_LABEL = re.compile(r"제안서\s*접수|서류\s*접수|접수\s*마감|제안서\s*제출|접수\s*기간")


def _flex_date(tok):
    """'26. 7. 8 / 2026. 7. 8 / 2026년 7월 8일 -> 2026-07-08"""
    m = re.search(r"['‘’]?\s*(\d{2,4})\s*[.년]\s*(\d{1,2})\s*[.월]\s*(\d{1,2})", tok)
    if not m:
        return ""
    y, mo, d = m.groups()
    y = int(y)
    y = y + 2000 if y < 100 else y
    try:
        date(y, int(mo), int(d))
    except ValueError:
        return ""
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def _plausible(d, ann_date):
    """마감일이 공고일(ann_date) 기준 -7일 ~ +150일 이내면 채택."""
    try:
        dd = date.fromisoformat(d)
    except ValueError:
        return False
    if not ann_date:
        return 2025 <= dd.year <= 2027
    try:
        aa = date.fromisoformat(ann_date)
    except ValueError:
        return 2025 <= dd.year <= 2027
    return aa - timedelta(days=7) <= dd <= aa + timedelta(days=150)


def _deadline_from_pdf_text(full, ann_date):
    """일정 표 안의 '제안서 접수' 행에서 마감일을 추출. (공고일/기준일 등 오탐 제외)"""
    full = re.sub(r"[ \t]+", " ", full or "")
    for am in _SCHED_ANCHOR.finditer(full):          # 일정 섹션만 탐색
        sec = full[am.start(): am.start() + 700]
        for lm in _SUBMIT_LABEL.finditer(sec):
            win = sec[max(0, lm.start() - 30): lm.end() + 30]
            for tok in re.findall(_DATE_RE, win):
                d = _flex_date(tok)
                if d and d != ann_date and _plausible(d, ann_date):
                    return d
    return ""


def _pdf_text(content):
    if not (fitz and content[:4] == b"%PDF"):
        return ""
    try:
        doc = fitz.open(stream=content, filetype="pdf")
        return "".join(pg.get_text() for pg in doc)
    except Exception:
        return ""


def _kgrowth_pdfs(idx):
    """K-Growth 첨부 다운로드(세션 필요 — 직접 접근 차단)."""
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get("https://www.kgrowth.or.kr/notice.asp?str_type=1&tab=1", timeout=TIMEOUT)
    ref = f"https://www.kgrowth.or.kr/notice_view.asp?idx={idx}&str_type=1&tab=1"
    s.get(ref, timeout=TIMEOUT)
    for sel in ("Notice1", "Notice2", "Notice3"):
        try:
            c = s.get(f"https://www.kgrowth.or.kr/down_file.asp?idx={idx}&SelType={sel}",
                      headers={"Referer": ref}, timeout=60).content
            if c[:4] == b"%PDF":
                yield c
        except Exception:
            continue


def _shinhan_pdfs(no):
    """신한벤처 상세 페이지의 /file/download/* 중 PDF 첨부."""
    try:
        html = _get(f"https://www.shinhanfund.com/ko/pc/board/noticeView?no={no}").text
    except Exception:
        return
    for fid in dict.fromkeys(re.findall(r"/file/download/(\d+)", html)):
        try:
            c = _get(f"https://www.shinhanfund.com/file/download/{fid}").content
            if c[:4] == b"%PDF":
                yield c
        except Exception:
            continue


def attachment_deadline(source, gid, ann_date=""):
    """공고 첨부(PDF)에서 마감일을 추출. 못 찾으면 '' (오탐 방지)."""
    if not fitz:
        return ""
    try:
        gens = {"kgrowth": _kgrowth_pdfs, "shinhan": _shinhan_pdfs}.get(source)
        if not gens:
            return ""
        for content in gens(gid):
            d = _deadline_from_pdf_text(_pdf_text(content), ann_date)
            if d:
                return d
    except Exception:
        return ""
    return ""


# ----------------------------------------------------------------------------
# 6) 금융투자협회 (KOFIA) — 안내사항 (여러 LP의 위탁운용사 선정/출자 공고 집약)
#    펀드 출자(사모/PE/VC) 공고만 선별. POST 아닌 풀 쿼리스트링 GET으로 페이징.
# ----------------------------------------------------------------------------
def scrape_kofia(pages=6):
    base = "https://www.kofia.or.kr:12443/brd/m_212/"
    qs = ("list.do?page=%d&srchFr=&srchTo=&srchWord=&srchTp="
          "&multi_itm_seq=0&itm_seq_1=0&itm_seq_2=0&company_cd=&company_nm=")
    out, seen, loaded = [], set(), 0
    for p in range(1, pages + 1):
        try:
            r = _get(base + (qs % p), verify=False)
        except Exception:
            continue
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "lxml")
        anchors = soup.select('a[href*="view.do"]')
        if anchors:
            loaded += 1
        for a in anchors:
            m = re.search(r"seq=(\d+)", a.get("href", ""))
            if not m:
                continue
            gid = m.group(1)
            title = _clean(a.get_text())
            if not title or gid in seen:
                continue
            # 펀드 출자 공고만 (공개시장 운용/채용/포럼 등 제외)
            if not _is_kofia_fund(title):
                continue
            seen.add(gid)
            tr = a.find_parent("tr")
            date = ""
            if tr:
                for td in tr.find_all("td"):
                    d = _norm_date(td.get_text())
                    if d:
                        date = d
                        break
            om = re.match(r"\[([^\]]+)\]", title)     # [기관명] 접두 → 주관기관
            out.append({
                "source": "kofia",
                "id": gid,
                "title": title,
                "date": date,
                "deadline": "",
                "org": om.group(1) if om else "",
                "url": base + "view.do?seq=" + gid,
            })
        time.sleep(1.0)                                # 호출 간격(차단 방지)
    if loaded == 0:                                    # 한 페이지도 못 읽음 → 차단/장애 → 이월 처리
        raise RuntimeError("KOFIA 접근 실패(차단/네트워크)")
    return out


SCRAPERS = {
    "kvic": scrape_kvic,
    "kgrowth": scrape_kgrowth,
    "kvca": scrape_kvca,
    "kfcc": scrape_kfcc,
    "shinhan": scrape_shinhan,
    "kofia": scrape_kofia,
}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for code, fn in SCRAPERS.items():
        try:
            items = fn()
            print(f"\n[{code}] {SOURCE_NAMES[code]} — {len(items)}건")
            for it in items[:5]:
                print(f"   {it['date']} | {it['title'][:55]} | {it['url']}")
        except Exception as e:
            print(f"\n[{code}] ERROR: {e}")
