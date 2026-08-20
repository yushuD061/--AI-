from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from .. import auth, database, state
from ..database import connect
from . import alerts, phase2, quality
from .analytics import resolve_filters


def _version_hash() -> str:
    digest = hashlib.sha256()
    with database.DATABASE_PATH.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _previous_date(report_date: date) -> date | None:
    db = connect()
    try:
        row = db.execute("SELECT MAX(date_clean) FROM sales_clean WHERE date_clean < ?", (report_date.isoformat(),)).fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None
    finally:
        db.close()


def build_report(report_date: date, store_id: str | None = None) -> dict[str, Any]:
    current = resolve_filters(report_date, report_date, store_id)
    db = connect()
    try:
        sql, params = "SELECT 1 FROM sales_clean WHERE date_clean = ?", [report_date.isoformat()]
        if store_id:
            sql += " AND store_id = ?"; params.append(store_id)
        if db.execute(sql + " LIMIT 1", params).fetchone() is None:
            raise ValueError("指定日期没有有效销售数据")
    finally:
        db.close()
    q = quality.get_quality()
    if q["status"] == "critical":
        raise RuntimeError("DATA_QUALITY_CRITICAL")
    previous = _previous_date(report_date)
    previous_filters = resolve_filters(previous, previous, store_id) if previous else None
    summary = phase2._aggregate(current)
    comparison = phase2.compare(current, previous_filters) if previous_filters else {
        "current_period": current.model_dump(mode="json"), "previous_period": None,
        "metrics": {key: {"current": summary[key], "previous": 0, "absolute_change": summary[key], "change_rate": None}
                     for key in ("net_revenue", "order_count", "average_order_value", "quantity")},
        "daily": {"current": phase2._daily(current), "previous": []},
    }
    ranking = phase2.store_ranking(current, previous_filters or current, "net_revenue", 100)
    mix = phase2.product_mix(current, previous_filters or current)
    daily_alerts = alerts.get_alerts(current, limit=500)
    top_store = ranking["data"][0] if ranking["data"] else None
    top_product = mix["data"][0] if mix["data"] else None
    generated = datetime.now(timezone.utc)
    principal = auth.current_user()
    scope_store_ids = [store_id] if store_id else (list(principal.store_ids) if principal.store_ids is not None else None)
    scope_key = store_id or ("ALL" if scope_store_ids is None else "SCOPE:" + ",".join(scope_store_ids))
    identity = f"{report_date}|{store_id or 'ALL'}|{generated.timestamp()}"
    return {
        "report_id": f"dr_{generated.strftime('%Y%m%d%H%M%S')}_{hashlib.sha256(identity.encode()).hexdigest()[:10]}",
        "report_date": report_date.isoformat(), "store_id": store_id or "ALL",
        "scope_key": scope_key, "scope_store_ids": scope_store_ids, "created_by": principal.user_id,
        "generated_at": generated.isoformat(), "data_version": _version_hash(),
        "quality_status": q["status"], "quality": q, "source": "sales_clean.sqlite:sales_clean",
        "filters": {"report_date": report_date.isoformat(), "store_id": store_id},
        "summary": {key: summary[key] for key in ("net_revenue", "order_count", "average_order_value")},
        "comparison": comparison, "previous_date": previous.isoformat() if previous else None,
        "best_store": top_store, "best_product": top_product,
        "store_ranking": ranking["data"], "product_mix": mix["data"], "alerts": daily_alerts,
    }


def create_report(report_date: date, store_id: str | None = None) -> dict[str, Any]:
    return state.save_daily_report(build_report(report_date, store_id))


def _rows(snapshot: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = [["section", "field", "value", "detail"]]
    meta = [("report_date", snapshot["report_date"]), ("generated_at", snapshot["generated_at"]),
            ("version", snapshot.get("version", 0)), ("data_version", snapshot["data_version"]),
            ("quality_status", snapshot["quality_status"]), ("filters", json.dumps(snapshot["filters"], ensure_ascii=False)),
            ("source", snapshot["source"])]
    rows += [["metadata", key, value, ""] for key, value in meta]
    rows += [["kpi", key, value, ""] for key, value in snapshot["summary"].items()]
    for key, value in snapshot["comparison"]["metrics"].items():
        rows.append(["comparison", key, value.get("current"), json.dumps(value, ensure_ascii=False)])
    for item in snapshot["store_ranking"]:
        rows.append(["store_ranking", item["store_id"], item.get("value"), json.dumps(item, ensure_ascii=False)])
    for item in snapshot["product_mix"]:
        rows.append(["product_mix", item["product_id"], item.get("net_revenue"), json.dumps(item, ensure_ascii=False)])
    if not snapshot["alerts"]:
        rows.append(["alerts", "", "", "当日无异常"])
    for item in snapshot["alerts"]:
        rows.append(["alerts", item["alert_id"], item.get("severity"), json.dumps(item, ensure_ascii=False)])
    return rows


def csv_bytes(snapshot: dict[str, Any]) -> bytes:
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(_rows(snapshot))
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def _sheet_xml(rows: list[list[Any]]) -> str:
    body = []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{chr(65+c)}{r}"
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        body.append(f'<row r="{r}">{"".join(cells)}</row>')
    return f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(body)}</sheetData></worksheet>'


def _workbook_rows(snapshot: dict[str, Any]) -> list[tuple[str, list[list[Any]]]]:
    metadata = [["字段", "值"], *[[key, value] for key, value in (
        ("数据日期", snapshot["report_date"]), ("生成时间", snapshot["generated_at"]),
        ("日报版本", snapshot.get("version", 0)), ("数据版本", snapshot["data_version"]),
        ("质量状态", snapshot["quality_status"]), ("门店筛选", snapshot["store_id"]),
        ("数据来源", snapshot["source"]),
    )]]
    summary = [["指标", "当前值", "上一营业日", "绝对变化", "变化率"]]
    for key, label in (("net_revenue", "净营业额"), ("order_count", "订单数"), ("average_order_value", "平均客单价")):
        item = snapshot["comparison"]["metrics"][key]
        summary.append([label, item["current"], item["previous"], item["absolute_change"], item["change_rate"] if item["change_rate"] is not None else "不可比"])
    stores = [["排名", "门店ID", "门店", "净营业额", "订单数", "客单价", "变化率"]] + [[item["rank"], item["store_id"], item["store_name"], item["net_revenue"], item["order_count"], item["average_order_value"], item["change_rate"] if item["change_rate"] is not None else "不可比"] for item in snapshot["store_ranking"]]
    products = [["排名", "商品ID", "商品", "品类", "净营业额", "销量", "订单数", "营业额占比", "退款金额"]] + [[item["rank"], item["product_id"], item["product_name"], item["product_category"], item["net_revenue"], item["quantity"], item["order_count"], item["revenue_share"], item["refund_amount"]] for item in snapshot["product_mix"]]
    alert_rows = [["严重级别", "类型", "日期", "门店", "商品", "说明", "实际值", "基线", "变化率", "样本量"]] + [[item["severity"], item["type"], item["date"], item.get("store_id") or "全部", item.get("product_name") or "-", item["message"], item["actual_value"], item.get("baseline_value") or 0, item.get("change_rate") if item.get("change_rate") is not None else "不可比", item["sample_size"]] for item in snapshot["alerts"]]
    return [("元数据", metadata), ("日报摘要", summary), ("门店排名", stores), ("商品结构", products), ("异常列表", alert_rows)]


def xlsx_bytes(snapshot: dict[str, Any]) -> bytes:
    sheets = _workbook_rows(snapshot)
    overrides = ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets) + 1))
    content_types = f'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>{overrides}</Types>'
    sheet_entries = ''.join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, 1))
    workbook = f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{sheet_entries}</sheets></workbook>'
    rels = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    workbook_rels = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1)) + '</Relationships>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types); archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook); archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        for index, (_, rows) in enumerate(sheets, 1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(rows))
    return out.getvalue()


def pdf_bytes(snapshot: dict[str, Any]) -> bytes:
    revenue_change = snapshot["comparison"]["metrics"]["net_revenue"]["change_rate"]
    lines = [("F2", "MONEKI 门店运营日报"), ("F1", f"Report date: {snapshot['report_date']}"),
             ("F1", f"Report version: v{snapshot.get('version', 0)}"),
             ("F1", f"Generated at: {snapshot['generated_at']}"),
             ("F1", f"Data version: {snapshot['data_version'][:16]}"),
             ("F1", f"Store filter: {snapshot['store_id']}"),
             ("F1", f"Quality status: {snapshot['quality_status']}"),
             ("F1", f"Data source: {snapshot['source']}"),
             ("F1", f"Net revenue: {snapshot['summary']['net_revenue']}"),
             ("F1", f"Orders: {snapshot['summary']['order_count']}"),
             ("F1", f"Average order value: {snapshot['summary']['average_order_value']}"),
             ("F1", f"Revenue change: {'N/A' if revenue_change is None else f'{revenue_change:.2%}'}"),
             ("F1", f"Best store: {(snapshot.get('best_store') or {}).get('store_name', '-')}"),
             ("F2", f"最佳商品：{(snapshot.get('best_product') or {}).get('product_name', '-')}"),
             ("F1", f"Alert count: {len(snapshot['alerts'])}"), ("F1", "Page 1")]
    commands = ["BT 50 750 Td"]
    for index, (font, line) in enumerate(lines):
        if index: commands.append("0 -22 Td")
        commands.append(f"/{font} 12 Tf")
        commands.append(f"<{line.encode('utf-16-be').hex()}> Tj" if font == "F2" else f"({line.replace('(', '[').replace(')', ']')}) Tj")
    commands.append("ET")
    text = "\n".join(commands)
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>", "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R /F2 6 0 R >> >> /Contents 5 0 R >>", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", f"<< /Length {len(text.encode())} >>\nstream\n{text}\nendstream", "<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UCS2-H /DescendantFonts [7 0 R] >>", "<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light /CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> >>"]
    out = bytearray(b"%PDF-1.4\n"); offsets = [0]
    for index, obj in enumerate(objects, 1): offsets.append(len(out)); out.extend(f"{index} 0 obj\n{obj}\nendobj\n".encode())
    xref = len(out); out.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode()); out.extend("".join(f"{offset:010d} 00000 n \n" for offset in offsets[1:]).encode()); out.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()); return bytes(out)
