"""Local settings and audit status for optional automatic task code repair."""

import json
import html
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from module.script import auto_repair


repair_app = APIRouter(prefix="/tool/repair", tags=["tool"])


class RepairEnabled(BaseModel):
    enabled: bool


def _local_only(request: Request) -> None:
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="Only local OAS clients may use auto repair")
    origin = request.headers.get("origin")
    if origin and urlparse(origin).hostname not in ("localhost", "127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="Remote web origins cannot control auto repair")


def _latest_status() -> dict | None:
    try:
        files = sorted(auto_repair.STATE.glob("*.status.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return None
        record = json.loads(files[0].read_text(encoding="utf-8"))
        return {"id": files[0].name.removesuffix(".status.json"), **record}
    except (OSError, ValueError):
        return None


def _save_setting(key: str, value: bool) -> None:
    path = auto_repair.CONFIG
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not isinstance(current, dict):
            current = {}
        current[key] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Could not save diagnosis setting") from exc


@repair_app.get("/status")
async def repair_status(request: Request):
    _local_only(request)
    return {"enabled": auto_repair.enabled(),
            "share_game_evidence": auto_repair.model_evidence_enabled(),
            "latest": _latest_status(),
            "service": auto_repair.budget_status(auto_repair.STATE)}


@repair_app.get("/drafts")
async def repair_drafts(request: Request):
    _local_only(request)
    directory = auto_repair.STATE / "activity_drafts"
    records = []
    for manifest in sorted(directory.glob("*/manifest.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            records.append({key: data[key] for key in
                            ("task", "activity_name", "enabled", "registered",
                             "evidence", "confidence") if key in data})
        except (OSError, ValueError):
            continue
    return records


@repair_app.get('/dashboard', response_class=HTMLResponse)
async def repair_dashboard(request: Request):
    _local_only(request)
    service = auto_repair.budget_status(auto_repair.STATE)
    latest = _latest_status() or {}
    labels = {'network': '网络或路由故障', 'authentication': '登录验证失败',
              'timeout': '请求超时', 'generation': '修复生成失败', 'unavailable': '服务或证据不可用'}
    success = service.get('last_success_at')
    rows = [('自动修复', '开启' if auto_repair.enabled() else '关闭'),
            ('执行中', '是' if service.get('active') else '否'),
            ('待处理证据', service.get('pending_count', 0)),
            ('重试等待', f"{service.get('cooldown_seconds', 0)} 秒"),
            ('最后成功', datetime.fromtimestamp(success).strftime('%Y-%m-%d %H:%M:%S') if success else '尚无成功记录'),
            ('最近结果', latest.get('status', '暂无记录')),
            ('故障类型', labels.get(latest.get('error_category'), '')),
            ('原因', latest.get('error') or latest.get('reason') or latest.get('scan_stop_reason') or '')]
    content = ''.join(f'<dt>{html.escape(str(key))}</dt><dd>{html.escape(str(value)[:500])}</dd>' for key, value in rows)
    return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>OAS 修复状态</title>'
                        '<style>body{font:16px system-ui;max-width:720px;margin:40px auto;padding:0 20px}dt{color:#666;margin-top:20px}dd{margin:4px 0;overflow-wrap:anywhere}</style>'
                        '<h1>自动修复状态</h1><p>服务故障时保留本地证据，等待后重试。</p><dl>' + content + '</dl><p><a href="dashboard">刷新状态</a></p></html>')


@repair_app.put("/enabled")
async def repair_set_enabled(request: Request, data: RepairEnabled):
    _local_only(request)
    _save_setting("enabled", data.enabled)
    return {"enabled": auto_repair.enabled(),
            "share_game_evidence": auto_repair.model_evidence_enabled(),
            "latest": _latest_status()}


@repair_app.put("/evidence/enabled")
async def repair_set_evidence_enabled(request: Request, data: RepairEnabled):
    _local_only(request)
    _save_setting("share_game_evidence", data.enabled)
    return {"enabled": auto_repair.enabled(),
            "share_game_evidence": auto_repair.model_evidence_enabled(),
            "latest": _latest_status()}
