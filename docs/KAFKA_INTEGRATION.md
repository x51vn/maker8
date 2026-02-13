**Kafka2DB Integration Guide**

Mục đích: hướng dẫn cách tích hợp dịch vụ `kafka2db` với Kafka cluster (consumer), cấu hình các biến môi trường cần thiết, và các bước kiểm tra kết nối.

**Tệp tham khảo**:
- [kafka2db/.env.example](kafka2db/.env.example)
- [kafka2db/src/core/config.py](kafka2db/src/core/config.py)
- [kafka2db/src/core/kafka_consumer.py](kafka2db/src/core/kafka_consumer.py)
- [kafka2db/docker-compose.yml](kafka2db/docker-compose.yml)

**1. Yêu cầu trước khi tích hợp**
- Python 3.11+ và môi trường ảo (venv).
- Truy cập mạng tới Kafka bootstrap servers.
- Topic(s) đã tồn tại (ví dụ: `ai_service_output`).
- Quyền truy cập (SASL username/password) nếu Kafka bật auth.

**2. Biến môi trường (bắt buộc / khuyến nghị)**
(Những biến này được load bởi `ApplicationConfig` trong [src/core/config.py](kafka2db/src/core/config.py)).

- **KAFKA_SERVERS**: danh sách bootstrap servers, ví dụ `10.113.213.9:9094`.
- **KAFKA_USERNAME**: username SASL (ví dụ `client`).
- **KAFKA_PASSWORD**: password SASL (ví dụ `client-secret`).
- **KAFKA_SECURITY_PROTOCOL**: ví dụ `SASL_PLAINTEXT` hoặc `SASL_SSL`.
- **KAFKA_SASL_MECHANISM**: ví dụ `PLAIN`.
- **INPUT_TOPIC**: topic hoặc danh sách comma-separated (ví dụ `ai_service_output`).
- **CONSUMER_GROUP_ID**: group id cho consumer (ví dụ `kafka2db_consumer`).

Lưu ý an toàn: tránh commit `.env` chứa mật khẩu vào git. Luôn dùng secrets manager hoặc biến môi trường trên môi trường production.

**3. Ánh xạ tới cấu hình confluent-kafka**
`ApplicationConfig.get_kafka_consumer_configuration()` trả về map sau (tham khảo):

```
{
  "bootstrap.servers": KAFKA_SERVERS,
  "group.id": CONSUMER_GROUP_ID,
  "auto.offset.reset": "latest",
  "enable.auto.commit": True,
  "security.protocol": KAFKA_SECURITY_PROTOCOL,
  "sasl.mechanism": KAFKA_SASL_MECHANISM,
  "sasl.username": KAFKA_USERNAME,
  "sasl.password": KAFKA_PASSWORD,
  "session.timeout.ms": KAFKA_SESSION_TIMEOUT_MS,
  "max.poll.interval.ms": KAFKA_MAX_POLL_INTERVAL_MS,
}
```

**4. Cách chạy cục bộ (development)**
1. Sao chép `.env.example` -> `.env` và chỉnh thông tin phù hợp:

```bash
cp .env.example .env
# chỉnh KAFKA_SERVERS, KAFKA_USERNAME, KAFKA_PASSWORD, INPUT_TOPIC
```

2. Chạy service (virtualenv active):
```bash
pip install -r requirements.txt
python kafka2db.py
```

**5. Chạy trong Docker**
- `docker-compose.yml` có ví dụ environment variables cho container `kafka2db`.
- Lệnh:
```bash
docker-compose up -d --build
docker-compose logs -f kafka2db
```

**6. Kiểm tra kết nối Kafka (ví dụ nhanh)**
- Dùng `kcat` (trước kia kafkacat) để kiểm tra khả năng kết nối và đọc topic:
```bash
kcat -b ${KAFKA_SERVERS} -C -t ${INPUT_TOPIC} \
  -X security.protocol=${KAFKA_SECURITY_PROTOCOL} \
  -X sasl.mechanisms=${KAFKA_SASL_MECHANISM} \
  -X sasl.username=${KAFKA_USERNAME} \
  -X sasl.password=${KAFKA_PASSWORD}
```

**7. Ví dụ mã (khởi tạo consumer trong dịch vụ)**
Trong mã `kafka2db` sử dụng `ApplicationConfig` và `KafkaConsumerWrapper` (xem [src/core/config.py](kafka2db/src/core/config.py) và [src/core/kafka_consumer.py](kafka2db/src/core/kafka_consumer.py)):

```python
from src.core.config import ApplicationConfig
from src.core.kafka_consumer import KafkaConsumerWrapper

cfg = ApplicationConfig()  # đọc từ .env
consumer_conf = cfg.get_kafka_consumer_configuration()
kc = KafkaConsumerWrapper(cfg.input_topic, consumer_conf)
kc.connect()
msg = kc.poll_message(timeout_seconds=1.0)
if msg:
    # xử lý message (kafka2db có pipeline routing dựa trên prompt_id)
    pass
```

**8. Các lưu ý vận hành**
- Offset management: cấu hình hiện tại dùng `enable.auto.commit=True`. Nếu cần xử lý chính xác một lần, cân nhắc commit thủ công sau khi lưu DB thành công.
- Auto offset reset: mặc định là `latest` (cấu hình trong code). Khi deploy mới cần đảm bảo topic có dữ liệu.
- Consumer group: đặt `CONSUMER_GROUP_ID` khác nhau khi chạy nhiều instance để chia partition hoặc cùng ID để load-balance.
- Retry/Backoff: service đã có cơ chế xử lý lỗi, nhưng với lỗi kết nối Kafka hãy theo dõi logs và healthcheck trong `docker-compose.yml`.

**9. Vị trí thông tin credentials trong repo**
- Mẫu env: [kafka2db/.env.example](kafka2db/.env.example)
- Docker env: [kafka2db/docker-compose.yml](kafka2db/docker-compose.yml)

Nếu bạn muốn, tôi có thể: tạo scripts mẫu để inject secrets từ Vault/EnvFile, hoặc thêm phần hướng dẫn deploy Kubernetes Secret (YAML) cho `kafka2db`.

--
Tài liệu ngắn này dựa trên cấu trúc và config hiện có trong repository `kafka2db`.

**10. Collected Credentials (from repository)**

Below are credential values and host/port addresses found in the `kafka2db` repository. Treat these as sensitive: verify before re-using and do not commit to other repos.

- **Kafka bootstrap servers**: `10.113.213.9:9094` (also `10.113.213.1:9094` appears in the README)
- **Kafka username**: `client`
- **Kafka password**: `client-secret`
- **Kafka security protocol**: `SASL_PLAINTEXT`
- **Kafka SASL mechanism**: `PLAIN`
- **Input topics**: `ai_service_output` (also `ai_service_output,collect-companies,spider3-priceboard,proprietary-trades` in `.env.example`)
- **DB host**: `10.113.213.9`
- **DB port**: `5432`
- **DB name**: `maker8`
- **DB user**: `vubakninh` (appears in `docker-compose.yml` and psql examples)
- **DB password**: `123456`
- **Consumer group ids**: `kafka2db_consumer` (default), `kafka2db_consumer_1` (docker-compose)

**Sources**:
- [kafka2db/.env.example](kafka2db/.env.example)
- [kafka2db/docker-compose.yml](kafka2db/docker-compose.yml)
- [kafka2db/README.md](kafka2db/README.md)
- [kafka2db/BACKWARD_COMPATIBILITY_FIX_SUMMARY.md](kafka2db/BACKWARD_COMPATIBILITY_FIX_SUMMARY.md)

If you want these exported to a single `.env` file or CSV for import into another project, tell me the target filename and I will create it.
