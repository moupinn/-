#!/usr/bin/env python3
"""e-Stat 定点データ自動取得スクリプト

パン市場・生活者変化スキャニングの定点データ（家計調査・CPI等）を
e-Stat API (v3.0) から取得し、リポジトリの data/ にCSVで保存する。

使い方:
  python scripts/fetch_estat.py discover "家計調査 品目分類"   # 統計表IDを探す
  python scripts/fetch_estat.py discover "品目分類" --stats-code 00200561
  python scripts/fetch_estat.py meta 0003348231               # 分類コード一覧を確認
  python scripts/fetch_estat.py fetch                          # targets.json の全対象を取得

環境変数:
  ESTAT_APP_ID : e-StatのアプリケーションID（必須）
                 https://www.e-stat.go.jp/api/ でユーザ登録して発行（無料・最大3つ）

API仕様: https://www.e-stat.go.jp/api/api-info/e-stat-manual3-0
クレジット表示義務: https://www.e-stat.go.jp/api/api-dev/faq （README参照）

注意:
  - 家計調査の品目分類は原則5年ごとに改定され、統計表IDが変わる
    （例: https://www.e-stat.go.jp/api/info-cat/news/kakei-info ）。
    改定時は targets.json の statsDataId を差し替えること。
  - 1リクエストの返却は最大10万件。超過分は NEXT_KEY でページング（実装済み）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

BASE = "https://api.e-stat.go.jp/rest/3.0/app/json"
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
META_DIR = DATA_DIR / "meta"
TARGETS_PATH = ROOT / "targets.json"

REQUEST_INTERVAL_SEC = 1.0  # APIへの礼儀。連続リクエストの間隔
TIMEOUT_SEC = 180


def app_id() -> str:
    v = os.environ.get("ESTAT_APP_ID", "").strip()
    if not v:
        sys.exit("ERROR: 環境変数 ESTAT_APP_ID が未設定です（GitHub ActionsではSecretsに設定）")
    return v


def api_get(path: str, params: dict) -> dict:
    """e-Stat APIを呼び、RESULT.STATUSを検証して返す。"""
    q = {"appId": app_id(), "lang": "J", **params}
    r = requests.get(f"{BASE}/{path}", params=q, timeout=TIMEOUT_SEC)
    r.raise_for_status()
    j = r.json()
    root_key = next(iter(j))  # GET_STATS_LIST / GET_META_INFO / GET_STATS_DATA
    result = j[root_key].get("RESULT", {})
    status = str(result.get("STATUS", ""))
    if status != "0":
        raise RuntimeError(f"e-Stat APIエラー STATUS={status}: {result.get('ERROR_MSG')}")
    return j[root_key]


# ---------------------------------------------------------------- discover --
def discover(search_word: str, stats_code: str | None) -> None:
    """統計表を検索して候補一覧をCSV保存。statsDataIdの確定に使う。"""
    params: dict = {"searchWord": search_word, "limit": 100}
    if stats_code:
        params["statsCode"] = stats_code
    res = api_get("getStatsList", params)
    tables = res.get("DATALIST_INF", {}).get("TABLE_INF", [])
    if isinstance(tables, dict):
        tables = [tables]
    rows = []
    for t in tables:
        rows.append({
            "statsDataId": t.get("@id"),
            "stat_name": _text(t.get("STAT_NAME")),
            "statistics_name": t.get("STATISTICS_NAME"),
            "title": _text(t.get("TITLE")),
            "cycle": t.get("CYCLE"),
            "survey_date": t.get("SURVEY_DATE"),
            "open_date": t.get("OPEN_DATE"),
            "updated_date": t.get("UPDATED_DATE"),
        })
    df = pd.DataFrame(rows)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = DATA_DIR / "discover_results.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    with pd.option_context("display.max_colwidth", 60, "display.width", 200):
        print(df.to_string(index=False))
    print(f"\n{len(df)}件 -> {out}")


# -------------------------------------------------------------------- meta --
def fetch_meta(stats_data_id: str) -> None:
    """指定統計表の分類事項（品目コード等）をCSV保存。絞り込みコードの確認に使う。"""
    res = api_get("getMetaInfo", {"statsDataId": stats_data_id})
    class_objs = res["METADATA_INF"]["CLASS_INF"]["CLASS_OBJ"]
    if isinstance(class_objs, dict):
        class_objs = [class_objs]
    rows = []
    for obj in class_objs:
        classes = obj.get("CLASS", [])
        if isinstance(classes, dict):
            classes = [classes]
        for c in classes:
            rows.append({
                "obj_id": obj.get("@id"),
                "obj_name": obj.get("@name"),
                "code": c.get("@code"),
                "name": c.get("@name"),
                "level": c.get("@level"),
                "unit": c.get("@unit"),
                "parent_code": c.get("@parentCode"),
            })
    df = pd.DataFrame(rows)
    META_DIR.mkdir(parents=True, exist_ok=True)
    out = META_DIR / f"{stats_data_id}_classes.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(df.head(40).to_string(index=False))
    print(f"\n{len(df)}行 -> {out}")


# ------------------------------------------------------------------- fetch --
def fetch_all() -> None:
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    for t in targets:
        if not t.get("enabled", True):
            print(f"skip (disabled): {t['name']}")
            continue
        print(f"fetch: {t['name']} (statsDataId={t['statsDataId']})")
        fetch_one(t)
        time.sleep(REQUEST_INTERVAL_SEC)


def fetch_one(target: dict) -> None:
    stats_data_id = target["statsDataId"]
    extra_params = target.get("params", {})  # 例: {"cdCat01": "010,011"} で品目絞り込み
    values: list[dict] = []
    class_maps: dict[str, dict] = {}
    table_inf: dict = {}
    start = 1
    page = 0
    while True:
        page += 1
        params = {
            "statsDataId": stats_data_id,
            "startPosition": start,
            "metaGetFlg": "Y" if page == 1 else "N",
            "explanationGetFlg": "N",
            "annotationGetFlg": "N",
            **extra_params,
        }
        res = api_get("getStatsData", params)
        sd = res["STATISTICAL_DATA"]
        if page == 1:
            table_inf = sd.get("TABLE_INF", {})
            class_maps = build_class_maps(sd.get("CLASS_INF", {}))
        chunk = sd.get("DATA_INF", {}).get("VALUE", [])
        if isinstance(chunk, dict):
            chunk = [chunk]
        values.extend(chunk)
        next_key = sd.get("RESULT_INF", {}).get("NEXT_KEY")
        print(f"  page {page}: +{len(chunk)}件 (累計 {len(values)})")
        if not next_key:
            break
        start = int(next_key)
        time.sleep(REQUEST_INTERVAL_SEC)

    if not values:
        raise RuntimeError(f"{target['name']}: データが0件でした。statsDataId/paramsを確認してください")

    df = pd.DataFrame(values)
    # 分類コード(@cat01等)に対応する名称列を付与
    for col in list(df.columns):
        if col.startswith("@"):
            obj_id = col[1:]
            if obj_id in class_maps:
                df[f"{obj_id}_name"] = df[col].map(class_maps[obj_id])
    df = df.rename(columns={"$": "value", "@unit": "unit"})
    df.columns = [c.lstrip("@") for c in df.columns]

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = DATA_DIR / f"{target['name']}.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")

    info = {
        "statsDataId": stats_data_id,
        "statistics_name": table_inf.get("STATISTICS_NAME"),
        "title": _text(table_inf.get("TITLE")),
        "updated_date_on_estat": table_inf.get("UPDATED_DATE"),
        "fetched_at_utc": pd.Timestamp.utcnow().isoformat(),
        "rows": len(df),
        "params": extra_params,
        "note": target.get("note", ""),
        "source": "政府統計の総合窓口(e-Stat) API機能",
    }
    (DATA_DIR / f"{target['name']}_info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  saved: {out} ({len(df)}行)")


def build_class_maps(class_inf: dict) -> dict[str, dict]:
    maps: dict[str, dict] = {}
    objs = class_inf.get("CLASS_OBJ", [])
    if isinstance(objs, dict):
        objs = [objs]
    for obj in objs:
        classes = obj.get("CLASS", [])
        if isinstance(classes, dict):
            classes = [classes]
        maps[obj.get("@id")] = {c.get("@code"): c.get("@name") for c in classes}
    return maps


def _text(v):
    """e-StatのJSONは要素が {'@code': .., '$': ..} 形式のことがあるので文字列化。"""
    if isinstance(v, dict):
        return v.get("$")
    return v


# -------------------------------------------------------------------- main --
def main() -> None:
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    cmd = args[0]
    if cmd == "discover":
        word = args[1] if len(args) > 1 else "家計調査 品目分類"
        stats_code = None
        if "--stats-code" in args:
            stats_code = args[args.index("--stats-code") + 1]
        discover(word, stats_code)
    elif cmd == "meta":
        if len(args) < 2:
            sys.exit("usage: fetch_estat.py meta <statsDataId>")
        fetch_meta(args[1])
    elif cmd == "fetch":
        fetch_all()
    else:
        sys.exit(f"unknown command: {cmd}\n{__doc__}")


if __name__ == "__main__":
    main()
