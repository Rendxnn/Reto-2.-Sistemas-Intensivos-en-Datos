# Reto 2 - Pipeline Server Logs → Kinesis → Lambda → DynamoDB

Este documento describe paso a paso la construcción de un pipeline en AWS que procesa eventos de logs HTTP en tiempo real.  
El flujo implementado es:

```
Server Logs (Python producer) → Kinesis Data Stream → AWS Lambda → DynamoDB
```

---

## 1. Preparación del entorno

### Variables de entorno
Definir variables comunes para evitar repetir valores:

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

### Verificar en Kinesis (lectura de records):
```bash
export SHARD_ID=$(aws kinesis list-shards   --region $REGION --stream-name $STREAM   --query 'Shards[0].ShardId' --output text)

export ITER=$(aws kinesis get-shard-iterator   --region $REGION   --stream-name $STREAM   --shard-id $SHARD_ID   --shard-iterator-type TRIM_HORIZON   --query 'ShardIterator' --output text)

aws kinesis get-records --region $REGION --shard-iterator $ITER --limit 5
```

### Verificar logs de Lambda:
```bash
aws logs filter-log-events   --region $REGION   --log-group-name "/aws/lambda/$LAMBDA_FUNC"   --start-time $(($(date +%s) * 1000 - 5 * 60 * 1000))   --limit 20
```

### Verificar en DynamoDB:
```bash
aws dynamodb scan   --region $REGION   --table-name $TABLE   --limit 5
```

O consulta por rango de timestamp:

```bash
aws dynamodb query   --region $REGION   --table-name $TABLE   --key-condition-expression "pk = :p AND sk BETWEEN :a AND :b"   --expression-attribute-values '{
    ":p": {"S":"path#/static/logo.png"},
    ":a": {"S":"2025-09-20T00:00:00.000Z"},
    ":b": {"S":"2025-09-20T23:59:59.999Z"}
  }'
```

---

## 8. Limpieza de recursos

Cuando termines el laboratorio, elimina recursos para evitar costos:

```bash
aws dynamodb delete-table --region $REGION --table-name $TABLE
aws kinesis delete-stream --region $REGION --stream-name $STREAM --enforce-deletion
aws lambda delete-function --region $REGION --function-name $LAMBDA_FUNC
```

Si creaste una instancia EC2:
```bash
aws ec2 terminate-instances --region $REGION --instance-ids $INSTANCE_ID
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
