import json

from sqlalchemy.orm import Session

from engine.db import DeliveryReceipt, Message, SystemEvent, now_bg


def acknowledge_delivery(
    db: Session,
    delivery_id: str,
    message_ids: list[int],
) -> dict:
    receipt = db.query(DeliveryReceipt).filter(DeliveryReceipt.delivery_id == delivery_id).first()
    if not receipt:
        return {"success": False, "reason": "unknown_delivery"}

    expected = set(json.loads(receipt.message_ids_json or "[]"))
    supplied = set(message_ids)
    if expected != supplied:
        return {"success": False, "reason": "message_ids_mismatch"}

    if receipt.status == "acknowledged":
        return {"success": True, "already_acknowledged": True}

    now = now_bg()
    messages = db.query(Message).filter(Message.id.in_(expected)).all() if expected else []
    for message in messages:
        if message.status == "active":
            message.status = "delivered"
            message.delivered_at = now

    receipt.status = "acknowledged"
    receipt.acknowledged_at = now
    db.add(SystemEvent(
        event_type="message_delivery_acknowledged",
        person_id=receipt.person_id,
        timestamp=now,
        metadata_json=json.dumps({
            "delivery_id": delivery_id,
            "message_ids": sorted(expected),
            "screen_id": receipt.screen_id,
            "zone_id": receipt.zone_id,
        }, ensure_ascii=False),
    ))
    db.commit()
    return {"success": True, "delivered_count": len(messages)}
