#!/usr/bin/env python3
import json
import os
import random
import sys
import time
import signal
from typing import Dict, Any, List
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from response_pool import get_pool

# === Configuración por entorno ===
REGION = os.getenv("AWS_REGION", "us-east-1")
STREAM = os.getenv("KDS_STREAM", "http-requests")
BATCH_MAX = int(os.getenv("BATCH_MAX", "50"))          # hasta 500 soportado por API
SLEEP_SEC = float(os.getenv("INTERVAL_SECS", "0.2"))   # 200 ms entre eventos

# Inventario
INV_PROB = float(os.getenv("INV_PROB", "0.05"))         # 5% inventario
INV_MIN = int(os.getenv("INV_MIN", "0"))
INV_MAX = int(os.getenv("INV_MAX", "50"))
PRODUCT_IDS = [p.strip() for p in os.getenv("PRODUCT_IDS", "P-001,P-002,P-003,P-004,P-005").split(",") if p.strip()]

kinesis = boto3.client("kinesis", region_name=REGION)

_buffer: List[Dict[str, Any]] = []
_running = True

def _now_epoch_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)

def _now_iso_z() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def _to_record(evt: Dict[str, Any]) -> Dict[str, Any]:
    body = json.dumps(evt, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # Particionamos por tipo
    if evt.get("type") == "inventory":
        pk = f"product#{evt['product_id']}"
    else:
        pk = f"path#{evt.get('path', '/')}"
    return {"Data": body, "PartitionKey": pk}

def _flush():
    global _buffer
    if not _buffer:
        return
    try:
        resp = kinesis.put_records(StreamName=STREAM, Records=_buffer)
        failed = resp.get("FailedRecordCount", 0)
        if failed:
            retry = [rec for rec, res in zip(_buffer, resp["Records"]) if "ErrorCode" in res]
            if retry:
                time.sleep(0.3)
                kinesis.put_records(StreamName=STREAM, Records=retry)
    except (BotoCoreError, ClientError) as e:
        print(f"[producer] put_records error: {e}", file=sys.stderr)
    finally:
        _buffer = []

def _sigint_handler(signum, frame):
    global _running
    _running = False
    print("\n[producer] Señal recibida, flush final…")
    _flush()
    print("[producer] Listo. Saliendo.")
    sys.exit(0)

signal.signal(signal.SIGINT, _sigint_handler)
signal.signal(signal.SIGTERM, _sigint_handler)

def process_option(option: Dict[str, Any]) -> None:
    global _buffer
    _buffer.append(_to_record(option))
    if len(_buffer) >= BATCH_MAX:
        _flush()

def _make_http_event(pool: List[Dict[str, Any]]) -> Dict[str, Any]:
    base = random.choice(pool).copy()
    # SIEMPRE añadimos ts_epoch y timestamp_iso
    base["ts_epoch"] = _now_epoch_ms()
    base["timestamp_iso"] = _now_iso_z()
    base["type"] = "http"
    return base

def _make_inventory_event() -> Dict[str, Any]:
    return {
        "type": "inventory",
        "product_id": random.choice(PRODUCT_IDS) if PRODUCT_IDS else "P-001",
        "inventory": random.randint(INV_MIN, INV_MAX),
        "ts_epoch": _now_epoch_ms(),       # SIEMPRE presente
        "timestamp_iso": _now_iso_z()
    }

def main() -> None:
    pool = [opt.to_dict() for opt in get_pool()]
    while _running:
        evt = _make_inventory_event() if random.random() < INV_PROB else _make_http_event(pool)
        print(json.dumps(evt, ensure_ascii=False))
        sys.stdout.flush()
        process_option(evt)
        time.sleep(SLEEP_SEC)
    _flush()

if __name__ == "__main__":
    main()
