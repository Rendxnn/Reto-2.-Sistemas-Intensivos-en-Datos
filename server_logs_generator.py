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
BATCH_MAX = int(os.getenv("BATCH_MAX", "50"))
SLEEP_SEC = float(os.getenv("INTERVAL_SECS", "0.5"))  # 0.5s para no saturar

kinesis = boto3.client("kinesis", region_name=REGION)

_buffer: List[Dict[str, Any]] = []
_running = True


def _to_record(evt: Dict[str, Any]) -> Dict[str, Any]:
    body = json.dumps(evt, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    pk = f"path#{evt.get('path', '/')}"
    return {"Data": body, "PartitionKey": pk}


def _flush():
    global _buffer
    if not _buffer:
        return
    try:
        kinesis.put_records(StreamName=STREAM, Records=_buffer)
    except (BotoCoreError, ClientError) as e:
        print(f"[producer] put_records error: {e}", file=sys.stderr)
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


def main() -> None:
    pool = [opt.to_dict() for opt in get_pool()]
    product_ids = [f"prod-{i}" for i in range(1, 6)]  # 5 productos simulados

    while _running:
        option = random.choice(pool)
        now = datetime.now(timezone.utc)
        ts_epoch = int(now.timestamp() * 1000)  # epoch en milisegundos

        event = {
            **option,
            "type": "inventory",
            "product_id": random.choice(product_ids),
            "inventory": random.randint(0, 50),  # inventario aleatorio
            "ts_epoch": ts_epoch,
        }

        print(json.dumps(event, ensure_ascii=False))
        sys.stdout.flush()

        process_option(event)
        time.sleep(SLEEP_SEC)

    _flush()

