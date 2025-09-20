import os
import json
import base64
import boto3
from datetime import datetime

TABLE_NAME = os.environ.get("TABLE_NAME", "HttpRequests")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)

def parse_iso(ts: str) -> datetime:
    """
    Acepta '2025-09-20T00:39:41.527Z' o variantes ISO.
    """
    if not ts:
        return datetime.utcnow()
    t = ts.strip().replace("Z", "+00:00")
    try:
        # Python 3.11: fromisoformat soporta offset
        return datetime.fromisoformat(t)
    except Exception:
        # fallback muy permisivo
        try:
            return datetime.strptime(ts.replace("Z",""), "%Y-%m-%dT%H:%M:%S.%f")
        except Exception:
            try:
                return datetime.strptime(ts.replace("Z",""), "%Y-%m-%dT%H:%M:%S")
            except Exception:
                return datetime.utcnow()

def status_family(code: int) -> str:
    try:
        c = int(code)
        return f"{c // 100}xx"
    except Exception:
        return "n/a"

def to_item(evt: dict) -> dict:
    """
    Mapear tu evento HTTP → item para DynamoDB.
    PK: path
    SK: timestamp exacto ISO (con milisegundos, UTC)
    Atributos derivados: status_family, is_error
    """
    method = evt.get("method") or "UNKNOWN"
    path = evt.get("path") or "/"
    status_code = evt.get("status_code")
    error_code = evt.get("error_code")
    message = evt.get("message")
    ts_str = evt.get("timestamp")

    dt = parse_iso(ts_str)
    # Normalizamos SK ISO a segundos.milisegundos + Z
    sk_iso = dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"  # milisegundos

    fam = status_family(status_code)
    is_err = (error_code is not None) or (isinstance(status_code, int) and status_code >= 500)

    # Item final
    item = {
        "pk": f"path#{path}",      # Partition Key
        "sk": f"{sk_iso}",         # Sort Key (orden cronológico exacto)
        "method": method,
        "status_code": status_code if status_code is not None else -1,
        "status_family": fam,
        "error_code": error_code,
        "is_error": bool(is_err),
        "message": message if message is not None else "",
        "event": evt               # Guardamos el raw completo
    }
    return item

def lambda_handler(event, context):
    put_requests = []

    for rec in event.get("Records", []):
        payload = base64.b64decode(rec["kinesis"]["data"])
        try:
            evt = json.loads(payload)
        except Exception:
            # Si no es JSON, guardar como raw en un registro mínimo
            txt = payload.decode("utf-8", errors="ignore")
            evt = {"raw": txt, "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
        item = to_item(evt)
        put_requests.append({"PutRequest": {"Item": item}})

    # Escribir en lotes de hasta 25 (límite BatchWriteItem)
    for i in range(0, len(put_requests), 25):
        batch = {"RequestItems": {TABLE_NAME: put_requests[i:i+25]}}
        dynamodb.meta.client.batch_write_item(**batch)

    return {"written": len(put_requests)}
