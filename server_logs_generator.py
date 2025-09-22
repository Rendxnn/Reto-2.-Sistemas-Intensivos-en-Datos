#!/usr/bin/env python3
import json
import os
import random
import sys
import time
import signal
from typing import Dict, Any, List

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from response_pool import get_pool
from datetime import datetime, timezone

# === Configuración por entorno ===
REGION = os.getenv("AWS_REGION", "us-east-1")
STREAM = os.getenv("KDS_STREAM", "http-requests")
BATCH_MAX = int(os.getenv("BATCH_MAX", "50"))              # hasta 500 soportado por API; 50 es cómodo
SLEEP_SEC = float(os.getenv("INTERVAL_SECS", "0.1"))       # 100 ms entre eventos

# —— NUEVO: parámetros para inventario ——
# Probabilidad de generar un evento de inventario en vez de HTTP (0.01 = 1%)
INV_PROB = float(os.getenv("INV_PROB", "0.02"))
# Rango de inventario simulado
INV_MIN = int(os.getenv("INV_MIN", "0"))
INV_MAX = int(os.getenv("INV_MAX", "50"))
# Catálogo de productos
PRODUCT_IDS = [p.strip() for p in os.getenv("PRODUCT_IDS", "P-001,P-002,P-003,P-004,P-005").split(",") if p.strip()]

kinesis = boto3.client("kinesis", region_name=REGION)

_buffer: List[Dict[str, Any]] = []
_running = True

def _now_iso_utc_z() -> str:
    """Timestamp ISO-8601 con milisegundos y sufijo Z (UTC)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def _to_record(evt: Dict[str, Any]) -> Dict[str, Any]:
    """Convierte el evento a record Kinesis (elige partición según tipo)."""
    body = json.dumps(evt, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    if evt.get("type") == "inventory":
        # Para inventario, particionamos por producto
        pk = f"product#{evt['product_id']}"
    else:
        # Para HTTP, particionamos por path (como tenías)
        pk = f"path#{evt.get('path', '/')}"

    return {"Data": body, "PartitionKey": pk}

def _flush():
    """Envía el buffer a Kinesis con reintento simple de fallidos."""
    global _buffer
    if not _buffer:
        return
    try:
        resp = kinesis.put_records(StreamName=STREAM, Records=_buffer)
    except (BotoCoreError, ClientError) as e:
        print(f"[producer] put_records error: {e}", file=sys.stderr)
        return

    failed = resp.get("FailedRecordCount", 0)
    if failed:
        retry = []
        for rec, res in zip(_buffer, resp["Records"]):
            if "ErrorCode" in res:
                retry.append(rec)
        if retry:
            time.sleep(0.5)
            try:
                kinesis.put_records(StreamName=STREAM, Records=retry)
            except (BotoCoreError, ClientError) as e:
                print(f"[producer] retry error: {e}", file=sys.stderr)

    _buffer = []

def _sigint_handler(signum, frame):
    global _running
    _running = False
    print("\n[producer] Señal recibida, haciendo flush final…")
    _flush()
    print("[producer] Listo. Saliendo.")
    sys.exit(0)

signal.signal(signal.SIGINT, _sigint_handler)
signal.signal(signal.SIGTERM, _sigint_handler)

def process_option(option: Dict[str, Any]) -> None:
    """Hook: envia al buffer y flush por lotes."""
    global _buffer
    _buffer.append(_to_record(option))
    if len(_buffer) >= BATCH_MAX:
        _flush()

# —— NUEVO: generadores de eventos ——
def _make_http_event(pool: List[Dict[str, Any]]) -> Dict[str, Any]:
    opt = random.choice(pool)  # item del response_pool
    return {**opt, "timestamp": _now_iso_utc_z()}

def _make_inventory_event() -> Dict[str, Any]:
    pid = random.choice(PRODUCT_IDS) if PRODUCT_IDS else "P-001"
    inv = random.randint(INV_MIN, INV_MAX)
    return {
        "type": "inventory",
        "product_id": pid,
        "inventory": inv,
        "ts": _now_iso_utc_z(),   # <- importante: campo 'ts' para Flink SQL
    }

def main() -> None:
    pool = [opt.to_dict() for opt in get_pool()]

    while _running:
        # Con probabilidad INV_PROB enviamos un evento de inventario,
        # en caso contrario, un log HTTP de tu pool.
        if random.random() < INV_PROB:
            event = _make_inventory_event()
        else:
            event = _make_http_event(pool)

        # (opcional) imprime para depurar
        print(json.dumps(event, ensure_ascii=False))
        sys.stdout.flush()

        process_option(event)
        time.sleep(SLEEP_SEC)

    _flush()

if __name__ == "__main__":
    main()
