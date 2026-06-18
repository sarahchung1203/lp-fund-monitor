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
import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}
TIMEOUT = 30

SOURCE_NAMES = {
    "kvic": "한국벤처투자(모태펀드)",
    "kgrowth": "한국성장금융",
    "kvca": "벤처캐피탈협회",
    "kfcc": "새마을금고중앙회",
    "shinhan": "신한벤처투자",
}


def _get(url, **kw):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kw)
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


# ----------------------------------------------------------------------------
# 1) 한국벤처투자 (KVIC) — 모태펀드 출자사업 공고
# ----------------------------------------------------------------------------
def scrape_kvic(pages=2):
    base = "https://www.kvic.or.kr/notice/kvic-notice/investment-business-notice"
    out = []
    for p in range(1, pages + 1):
        r = _get(f"{base}?page={p}")
        r.encoding = r.apparent_encoding or "utf-8"
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.select("a[href*='board_view']"):
            m = re.search(r"board_view\((\d+)\)", a.get("href", ""))
            if not m:
                continue
            gid = m.group(1)
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
                "deadline": "",
                "org": "",
                "url": f"{base}mgNoticeDetail.do?no={gid}",
            })
    return out


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


SCRAPERS = {
    "kvic": scrape_kvic,
    "kgrowth": scrape_kgrowth,
    "kvca": scrape_kvca,
    "kfcc": scrape_kfcc,
    "shinhan": scrape_shinhan,
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
