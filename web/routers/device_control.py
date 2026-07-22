"""Managed device lifecycle API."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from web.database import get_db
from web.schemas import DeviceCommandAckRequest, DeviceEnrollRequest, DeviceHeartbeatRequest
from web.security import require_device
from web.services.device_control import (
    DeviceContext,
    acknowledge_command,
    device_config,
    enroll_device,
    pending_commands,
    update_heartbeat,
)


router = APIRouter(prefix="/api/devices", tags=["managed devices"])


@router.post("/enroll")
def enroll(
    request: DeviceEnrollRequest,
    x_enrollment_token: str = Header(min_length=20, max_length=200),
    db: Session = Depends(get_db),
):
    try:
        device, raw_key = enroll_device(
            db,
            enrollment_token=x_enrollment_token,
            identifier=request.identifier,
            name=request.name,
            device_type=request.device_type,
            capabilities=request.capabilities,
            software_version=request.software_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "device_id": device.identifier,
        "device_key": raw_key,
        "zone_id": device.zone_id,
        "screen_id": device.screen_id,
        "config_version": device.config_version,
        "notice": "Ключът се показва само веднъж. Запазете го в защитената конфигурация на устройството.",
    }


@router.post("/heartbeat")
def heartbeat(
    request: DeviceHeartbeatRequest,
    context: DeviceContext = Depends(require_device),
    db: Session = Depends(get_db),
):
    try:
        device = update_heartbeat(
            db,
            context,
            status=request.status,
            software_version=request.software_version,
            capabilities=request.capabilities,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "success": True,
        "device_id": device.identifier,
        "status": device.status,
        "config_version": device.config_version,
        "server_time": device.last_seen_at.isoformat(),
    }


@router.get("/config")
def get_config(
    context: DeviceContext = Depends(require_device),
    db: Session = Depends(get_db),
):
    return device_config(db, context)


@router.get("/commands/pending")
def get_pending_commands(
    context: DeviceContext = Depends(require_device),
    db: Session = Depends(get_db),
):
    return [
        {
            "id": item.id,
            "command": item.command,
            "payload": json.loads(item.payload_json or "{}"),
            "created_at": item.created_at.isoformat(),
        }
        for item in pending_commands(db, context)
    ]


@router.post("/commands/{command_id}/ack")
def command_ack(
    command_id: int,
    request: DeviceCommandAckRequest,
    context: DeviceContext = Depends(require_device),
    db: Session = Depends(get_db),
):
    try:
        item = acknowledge_command(
            db,
            context,
            command_id,
            success=request.success,
            result=request.result,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"success": True, "command_id": item.id, "status": item.status}
