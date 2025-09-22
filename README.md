# Reto 2 - Procesamiento de Flujos con AWS
## Samuel Rendón Trujillo

Este documento describe paso a paso la construcción de **dos escenarios de pipelines en AWS** para procesar eventos en tiempo real.

### Link video: https://youtu.be/NxE4BOeJhA8

---

# Escenario 1: Server Logs → Kinesis Data Stream → AWS Lambda → DynamoDB

Este primer escenario implementa un pipeline básico de ingestión y persistencia de eventos en DynamoDB.

## 1. Preparación del entorno

### Variables de entorno

```bash
export REGION=us-east-1
export STREAM=http-requests
export TABLE=HttpRequests
export LAMBDA_FUNC=KinesisToDynamo
```

### Dependencias en CloudShell / EC2

```bash
sudo yum update -y
sudo yum install -y python3-pip jq
pip3 install boto3
```

---

## 2. Crear Kinesis Data Stream

```bash
aws kinesis create-stream   --region $REGION   --stream-name $STREAM   --shard-count 1
```

Verificar que está activo:

```bash
aws kinesis describe-stream   --region $REGION   --stream-name $STREAM   --query 'StreamDescription.StreamStatus'
```

---

## 3. Crear DynamoDB Table

```bash
aws dynamodb create-table   --region $REGION   --table-name $TABLE   --attribute-definitions       AttributeName=pk,AttributeType=S       AttributeName=sk,AttributeType=S   --key-schema       AttributeName=pk,KeyType=HASH       AttributeName=sk,KeyType=RANGE   --billing-mode PAY_PER_REQUEST
```

Confirmar estado:

```bash
aws dynamodb describe-table   --region $REGION   --table-name $TABLE   --query 'Table.{Status:TableStatus,Keys:KeySchema}'
```

---

## 4. Lambda Function

### Código (`lambda_function.py`)

```python
import os, json, base64, boto3
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["TABLE_NAME"])

def lambda_handler(event, context):
    for rec in event["Records"]:
        payload = base64.b64decode(rec["kinesis"]["data"])
        evt = json.loads(payload)
        pk = f"path#{evt['path']}"
        sk = evt["timestamp"]
        table.put_item(Item={"pk": pk, "sk": sk, **evt})
    return {"status": "ok"}
```

### Crear función

```bash
zip function.zip lambda_function.py
aws lambda create-function   --region $REGION   --function-name $LAMBDA_FUNC   --runtime python3.11   --role arn:aws:iam::<ACCOUNT_ID>:role/LabRole   --handler lambda_function.lambda_handler   --zip-file fileb://function.zip   --environment Variables="{TABLE_NAME=$TABLE}"
```

---

## 5. Conectar Kinesis con Lambda

```bash
# Obtener ARN del stream
export STREAM_ARN=$(aws kinesis describe-stream   --region $REGION   --stream-name $STREAM   --query 'StreamDescription.StreamARN'   --output text)

# Crear mapping
aws lambda create-event-source-mapping   --region $REGION   --function-name $LAMBDA_FUNC   --event-source $STREAM_ARN   --starting-position LATEST   --batch-size 1   --maximum-batching-window-in-seconds 0
```

Verificar mapping:

```bash
aws lambda list-event-source-mappings   --region $REGION   --function-name $LAMBDA_FUNC   --query 'EventSourceMappings[].{UUID:UUID,State:State,SourceArn:EventSourceArn}'   --output table
```

---

## 6. Productor en Python

Archivo `producer.py`:

```python
import os, json, random, time
import boto3
from datetime import datetime, timezone
from response_pool import get_pool

REGION = os.getenv("AWS_REGION", "us-east-1")
STREAM = os.getenv("KDS_STREAM", "http-requests")
kinesis = boto3.client("kinesis", region_name=REGION)

def main():
    pool = [opt.to_dict() for opt in get_pool()]
    while True:
        option = random.choice(pool)
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        event = {**option, "timestamp": timestamp}
        print(json.dumps(event))
        kinesis.put_record(
            StreamName=STREAM,
            Data=json.dumps(event).encode("utf-8"),
            PartitionKey=f"path#{event['path']}"
        )
        time.sleep(0.1)

if __name__ == "__main__":
    main()
```

Ejecutar:

```bash
export AWS_REGION=$REGION
export KDS_STREAM=$STREAM
python3 producer.py
```

---

## 7. Validación del pipeline

### Verificar en Kinesis

```bash
export SHARD_ID=$(aws kinesis list-shards   --region $REGION --stream-name $STREAM   --query 'Shards[0].ShardId' --output text)

export ITER=$(aws kinesis get-shard-iterator   --region $REGION   --stream-name $STREAM   --shard-id $SHARD_ID   --shard-iterator-type TRIM_HORIZON   --query 'ShardIterator' --output text)

aws kinesis get-records --region $REGION --shard-iterator $ITER --limit 5
```

### Verificar logs de Lambda

```bash
aws logs filter-log-events   --region $REGION   --log-group-name "/aws/lambda/$LAMBDA_FUNC"   --start-time $(($(date +%s) * 1000 - 5 * 60 * 1000))   --limit 20
```

### Verificar en DynamoDB

```bash
aws dynamodb scan   --region $REGION   --table-name $TABLE   --limit 5
```

---

## 8. Limpieza de recursos

```bash
aws dynamodb delete-table --region $REGION --table-name $TABLE
aws kinesis delete-stream --region $REGION --stream-name $STREAM --enforce-deletion
aws lambda delete-function --region $REGION --function-name $LAMBDA_FUNC
```

---

## 9. Diagrama del flujo

```
+------------------+         +---------------------+        +-------------------+
|  Python Producer |  --->   | Kinesis Data Stream | --->   | AWS Lambda (ETL)  |
| (Server Logs)    |         |  (http-requests)    |        | Writes to DynamoDB|
+------------------+         +---------------------+        +-------------------+
                                                                  |
                                                                  v
                                                        +-------------------+
                                                        | DynamoDB Table    |
                                                        |   HttpRequests    |
                                                        +-------------------+
```

---

# Escenario 2: Server Logs → Kinesis Data Stream → Kinesis Data Analytics (Flink/SQL) → Kinesis Data Stream → Lambda → SNS (SMS)

En este escenario se extiende el pipeline para implementar una aplicación **event-driven** que detecta inventario bajo y envía notificaciones vía **SNS (SMS)**.

## 1. Crear stream de salida (alertas)

```bash
export STREAM_OUT=alerts-low-inventory

aws kinesis create-stream   --region $REGION   --stream-name $STREAM_OUT   --shard-count 1

aws kinesis wait stream-exists --region $REGION --stream-name $STREAM_OUT
```

---

## 2. Crear aplicación de Kinesis Data Analytics

1. Ir a **Kinesis Data Analytics** en la consola.  
2. Crear aplicación tipo **SQL (Flink SQL)**.  
3. Fuente: stream de entrada (`http-requests`).  
4. Destino: stream de salida (`alerts-low-inventory`).  
5. Asignar un bucket de S3 para estado y un rol IAM (ej: `LabRole`).  

### SQL de ejemplo (inventario bajo)

```sql
CREATE OR REPLACE STREAM DESTINATION_SQL_STREAM (
  product_id VARCHAR(64),
  inventory INTEGER,
  ts TIMESTAMP,
  reason VARCHAR(32)
);

CREATE OR REPLACE PUMP STREAM_PUMP AS
INSERT INTO DESTINATION_SQL_STREAM
SELECT product_id, inventory, ts, 'LOW_STOCK'
FROM SOURCE_SQL_STREAM_001
WHERE type = 'inventory' AND inventory < 10;
```

---

## 3. Validar salida en el nuevo stream

```bash
export SHARD_ID=$(aws kinesis list-shards   --region $REGION --stream-name $STREAM_OUT   --query 'Shards[0].ShardId' --output text)

export ITER=$(aws kinesis get-shard-iterator   --region $REGION   --stream-name $STREAM_OUT   --shard-id $SHARD_ID   --shard-iterator-type TRIM_HORIZON   --query 'ShardIterator' --output text)

aws kinesis get-records --region $REGION --shard-iterator $ITER --limit 5
```

---

## 4. Conectar salida con Lambda y SNS

1. Crear **SNS Topic** y suscribir número de teléfono (SMS).  
2. Crear **Lambda** que consuma de `alerts-low-inventory` y publique en SNS:

```python
import os, json, base64, boto3
sns = boto3.client("sns")
TOPIC_ARN = os.environ["TOPIC_ARN"]

def lambda_handler(event, context):
    for rec in event["Records"]:
        payload = base64.b64decode(rec["kinesis"]["data"])
        evt = json.loads(payload)
        msg = f"ALERTA: {evt['product_id']} bajo inventario ({evt['inventory']})"
        sns.publish(TopicArn=TOPIC_ARN, Message=msg)
    return {"status": "ok"}
```

3. Asociar Lambda al stream `alerts-low-inventory` con un Event Source Mapping.  

---

## 5. Limpieza de recursos

```bash
aws kinesis delete-stream --region $REGION --stream-name $STREAM_OUT --enforce-deletion
# También eliminar aplicación de Kinesis Data Analytics, Lambda y SNS Topic creados
```

---

## 6. Diagrama del flujo

```
+------------------+        +---------------------+        +---------------------------+
|  Python Producer | -----> | Kinesis Data Stream | -----> | Kinesis Data Analytics    |
| (Server Logs)    |        |  (http-requests)    |        | (SQL/Flink - detecta low) |
+------------------+        +---------------------+        +---------------------------+
                                                                       |
                                                                       v
                                                         +----------------------------+
                                                         | Kinesis Data Stream (out)  |
                                                         |  alerts-low-inventory      |
                                                         +----------------------------+
                                                                       |
                                                                       v
                                                         +----------------------------+
                                                         | AWS Lambda → SNS (SMS)     |
                                                         +----------------------------+
```
