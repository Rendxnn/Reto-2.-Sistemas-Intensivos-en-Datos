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
BATCH_MAX = int(os.getenv("BATCH_MAX", "50"))   # hasta 500 soportado por API; 50 es cómodo
SLEEP_SEC = float(os.getenv("INTERVAL_SECS", "0.1"))  # 100 ms entre eventos

kinesis = boto3.client("kinesis", region_name=REGION)

_buffer: List[Dict[str, Any]] = []
_running = True

def _to_record(evt: Dict[str, Any]) -> Dict[str, Any]:
    body = json.dumps(evt, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # Particiona por path (puedes cambiar a "svc#<algo>" si quisieras)
    pk = f"path#{evt.get('path', '/')}"
    # Kinesis limita 1 MiB por registro; nuestros eventos son pequeños
    return {"Data": body, "PartitionKey": pk}

def _flush():
    """Envía el buffer a Kinesis con reintentos de fallidos."""
    global _buffer
    if not _buffer:
        return
    # Empaqueta
    try:
        resp = kinesis.put_records(StreamName=STREAM, Records=_buffer)
    except (BotoCoreError, ClientError) as e:
        print(f"[producer] put_records error: {e}", file=sys.stderr)
        return  # perdemos este lote; si te interesa, podrías loguearlo a disco

    failed = resp.get("FailedRecordCount", 0)
    if failed:
        retry = []
        for rec, res in zip(_buffer, resp["Records"]):
            if "ErrorCode" in res:
                retry.append(rec)
        if retry:
            # backoff simple y un intento adicional
            time.sleep(0.5)
            try:
                kinesis.put_records(StreamName=STREAM, Records=retry)
            except (BotoCoreError, ClientError) as e:
                print(f"[producer] retry error: {e}", file=sys.stderr)

    _buffer = []  # vacía buffer siempre al final

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
    """Hook: aquí enviamos al buffer y flush por lotes."""
    global _buffer
    _buffer.append(_to_record(option))
    if len(_buffer) >= BATCH_MAX:
        _flush()

def main() -> None:
    pool = [opt.to_dict() for opt in get_pool()]

    while _running:
        option = random.choice(pool)
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        event = {**option, "timestamp": timestamp}

        # (opcional) imprime para depurar
        print(json.dumps(event, ensure_ascii=False))
        sys.stdout.flush()

        process_option(event)
        time.sleep(SLEEP_SEC)

    # Si sale del loop, flush final
    _flush()

if __name__ == "__main__":
    main()
