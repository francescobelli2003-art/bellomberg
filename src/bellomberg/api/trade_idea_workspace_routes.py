"""Authenticated, version-bound research actions and exact-byte downloads."""
from hashlib import sha256
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from bellomberg.core.paths import MODELS_DIR, REPORT_DIR
from bellomberg.storage.memory_db import SQLITE_PATH
from bellomberg.storage.trade_idea_store import TradeIdeaStore, StorageNotReady
from bellomberg.valuation.trade_idea_workspace import ResearchWorkspace


class ResearchAction(BaseModel):
    model_config = ConfigDict(extra="forbid",strict=True)
    kind: str = Field(min_length=1,max_length=40)
    generation_id: str = Field(min_length=1,max_length=80)
    request_id: str = Field(min_length=1,max_length=80)
    data: dict[str,Any]


def _model_summary(model):
    return {key:model.get(key) for key in ("ticker","generation_id","snapshot_id","valuation_date","currency",
        "price","fair_value_bear","fair_value_base","fair_value_bull","market_quote","valuation_usability","workbook_sha256")}


def _public_event(event):
    def clean(value):
        if isinstance(value,list): return [clean(item) for item in value]
        if not isinstance(value,dict): return value
        return {key:_model_summary(item) if key=="model" and isinstance(item,dict) else clean(item)
            for key,item in value.items() if key not in ("path","manifest_path","acquisition_snapshot","previous_acquisition","request_sha256","source_catalog_path")}
    public = clean(event)
    data = event.get("data") or {}
    candidate = data.get("model") or data
    if candidate.get("path") and (data.get("status")=="ready" or (candidate.get("valuation_usability") or {}).get("usable")):
        public["artifact"] = {"name":Path(candidate["path"]).name,
            "kind":"xlsx" if data.get("model") else "zip",
            "sha256":candidate.get("workbook_sha256") or candidate.get("sha256"),
            "download_url":f"/trade-ideas/runs/{event['run_id']}/workspace/artifacts/{event['id']}"}
    return public


def install_trade_idea_workspace_routes(app, require_session, *, db_path=SQLITE_PATH, workspace=None):
    # 09/10 (B1, Opus 5.5): storage non pronto = stesso contratto delle altre rotte
    # Trade Idea (503 + error_code + storage), non un 503 generico senza diagnosi.
    from bellomberg.api.trade_idea_routes import StorageUnavailable, _storage_unavailable_response
    app.add_exception_handler(StorageUnavailable, _storage_unavailable_response)
    router = APIRouter(prefix="/trade-ideas/runs/{run_id}/workspace",dependencies=[Depends(require_session)])

    def service():
        if workspace is not None: return workspace
        try:
            return ResearchWorkspace(TradeIdeaStore(db_path),artifact_root=Path(REPORT_DIR)/"trade-idea-workspace",
                                     model_roots=[MODELS_DIR,REPORT_DIR])
        except StorageNotReady as exc:
            raise StorageUnavailable(exc) from exc
        except (FileNotFoundError,RuntimeError) as exc:
            raise HTTPException(503,"Research archive unavailable: "+str(exc)) from exc

    def perform(operation):
        try: return operation()
        except KeyError as exc: raise HTTPException(404,"Research or evidence not found") from exc
        except FileNotFoundError as exc: raise HTTPException(410,"Recorded artifact is no longer available") from exc
        except (ValueError,TypeError) as exc: raise HTTPException(422,str(exc)) from exc

    @router.get("")
    def read(run_id: str,generation_id: str | None = None):
        result = perform(lambda:service().view(run_id,generation_id))
        result["history"] = [_public_event(event) for event in result["history"]]
        return result

    @router.get("/costs")
    def costs(run_id: str):
        return perform(lambda:service().costs(run_id))

    @router.get("/export-preview")
    def preview(run_id: str):
        return perform(lambda:service().export_preview(run_id))

    def archive_only():
        raise HTTPException(409, {'code': 'excel_archived',
            'message': 'Workspace Excel in archivio: consultazione e download dei risultati salvati restano disponibili.'})

    @router.post("/actions", dependencies=[Depends(archive_only)])
    def action(run_id: str, body: ResearchAction):
        event = perform(lambda:service().act(run_id,body.kind,body.data,
            request_id=body.request_id,generation_id=body.generation_id))
        return _public_event(event)

    @router.post("/actions/{request_id}/recover", dependencies=[Depends(archive_only)])
    def recover(run_id: str, request_id: str):
        return _public_event(perform(lambda:service().recover(run_id,request_id)))

    @router.get("/artifacts/{event_id}")
    def artifact(run_id: str,event_id: int):
        current = service()
        event = perform(lambda:current.events.get(run_id,event_id))
        data = event["data"]
        candidate = data.get("model") or data
        if not candidate.get("path") or not (data.get("status")=="ready" or
                (candidate.get("valuation_usability") or {}).get("usable")):
            raise HTTPException(409,"This operation has no usable saved artifact")
        path = Path(candidate["path"]).resolve()
        if not path.is_relative_to(current.root):
            raise HTTPException(409,"Artifact is outside the research archive")
        blob = perform(path.read_bytes)
        digest = candidate.get("workbook_sha256") or candidate.get("sha256")
        if sha256(blob).hexdigest()!=digest:
            raise HTTPException(409,"Artifact changed after its immutable receipt")
        name = path.name.replace('"',"_").replace("\r","_").replace("\n","_")
        return Response(blob,media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if data.get("model") else "application/zip",
            headers={"Content-Disposition":f'attachment; filename="{name}"',"Cache-Control":"no-store",
                "X-Content-SHA256":digest,"X-Content-Type-Options":"nosniff"})

    app.include_router(router)
