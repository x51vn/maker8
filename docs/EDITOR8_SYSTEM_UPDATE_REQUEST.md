# Yêu cầu cập nhật hệ thống Editor8

## Mục tiêu

Cập nhật hệ thống triển khai và giám sát của Editor8 để loại bỏ tình trạng:

- submit bài viết thành công nhưng job không được xử lý trong nhiều phút
- backend báo healthy nhưng pipeline thực tế không chạy
- tài liệu và health check không phản ánh đúng kiến trúc hiện tại

## Bối cảnh

Qua kiểm tra codebase hiện tại:

- API chỉ nhận request, tạo `Job` ở trạng thái `RECEIVED`, rồi publish message vào Kafka.
- Worker mới là tiến trình thực sự consume topic `editor8.input.v1` và chạy pipeline.
- API không còn tự chạy Kafka consumer trong lifecycle.
- Health check hiện tại chỉ phản ánh DB và Kafka producer của API, không phản ánh worker/consumer.
- README và quy trình vận hành hiện tại dễ khiến người triển khai hiểu nhầm rằng chỉ cần chạy backend API là đủ.

Hệ quả: nếu worker không chạy, chạy sai command, hoặc không consume được Kafka, job sẽ đứng ở `RECEIVED` mà hệ thống vẫn có thể trông như đang hoạt động bình thường.

## Các cập nhật bắt buộc

### 1. Cập nhật deployment để chạy worker riêng

Bắt buộc xác nhận môi trường chạy thực tế có tiến trình worker riêng với command:

```bash
python -m editor8.worker
```

Không được triển khai chỉ với:

```bash
python -m editor8.app
```

Yêu cầu:

- kiểm tra mọi deployment hiện tại của Editor8
- xác nhận service worker tồn tại và tự khởi động sau reboot
- xác nhận worker dùng đúng file env và đúng Kafka settings
- xác nhận worker có restart policy rõ ràng khi crash

### 2. Cập nhật health và observability

Health check hiện tại chưa đủ.

Cần bổ sung:

- trạng thái worker consumer
- thời điểm consume message gần nhất
- trạng thái kết nối Kafka consumer
- độ trễ hàng đợi cơ bản nếu đo được

Yêu cầu tối thiểu:

- API health không được báo “ổn” khi worker không chạy
- phải có cách phân biệt:
  - API healthy
  - worker healthy
  - Kafka producer healthy
  - Kafka consumer healthy

### 3. Cập nhật UI/ops để phát hiện pipeline bị kẹt

Trang ops cần hiển thị được các tín hiệu sau:

- số job đang ở `RECEIVED`
- số job bị kẹt quá ngưỡng thời gian, ví dụ `RECEIVED > 2 phút`
- worker status
- consumer lag hoặc ít nhất thời gian xử lý gần nhất

Nếu worker chết hoặc không consume được, UI phải cho thấy dấu hiệu rõ ràng thay vì chỉ hiển thị dashboard tổng quát.

### 4. Cập nhật tài liệu vận hành

Tài liệu phải phản ánh đúng kiến trúc hiện tại:

- API và worker là 2 process riêng
- local dev muốn pipeline chạy thì phải start cả worker
- production/deployment phải có worker service riêng
- submit thành công chỉ có nghĩa là message đã được publish, không có nghĩa pipeline đã bắt đầu xử lý

Cần cập nhật:

- quick start
- local development guide
- deployment notes
- troubleshooting guide

### 5. Cập nhật test coverage

Cần bổ sung test để tránh tái diễn hiểu nhầm và drift vận hành:

- test xác nhận API app không chạy consumer
- test xác nhận worker mới là tiến trình chạy consumer
- test health phản ánh đúng worker/consumer state
- test cho case worker không chạy nhưng API vẫn chạy
- test hoặc smoke check cho flow submit -> job chuyển từ `RECEIVED` sang `GENERATING`

## Thay đổi ưu tiên cao

Các thay đổi sau cần ưu tiên triển khai trước:

1. Đảm bảo worker được deploy và chạy đúng command.
2. Bổ sung health/monitoring cho worker consumer.
3. Cập nhật tài liệu để không ai còn deploy nhầm theo mô hình cũ.

## Tiêu chí nghiệm thu

Chỉ được coi là hoàn thành khi thỏa toàn bộ điều kiện sau:

- submit mới tạo job và job chuyển khỏi `RECEIVED` trong thời gian hợp lý
- khi worker dừng, hệ thống phát hiện được và lộ rõ trên health/ops
- deployment docs mô tả rõ API/worker split
- local dev docs hướng dẫn chạy cả API và worker
- không còn tình trạng backend có vẻ healthy nhưng pipeline thực tế không tiêu thụ job

## Đề xuất checklist triển khai

### Deployment

- xác minh service worker tồn tại
- xác minh command chạy là `python -m editor8.worker`
- xác minh worker dùng đúng env
- xác minh restart policy

### Runtime verification

- submit 1 job test
- kiểm tra job rời `RECEIVED`
- kiểm tra worker có log consume message
- kiểm tra pipeline bắt đầu chuyển `GENERATING`

### Observability

- health hiển thị worker status
- ops hiển thị backlog hoặc stuck jobs
- có cảnh báo khi worker không hoạt động

### Documentation

- cập nhật README
- cập nhật deployment guide
- cập nhật troubleshooting

## Lý do phải làm ngay

Đây không chỉ là vấn đề UI hay log.

Đây là vấn đề vận hành hệ thống:

- dễ triển khai sai
- khó phát hiện khi sai
- tạo cảm giác submit thành công nhưng thực tế không có xử lý nền
- làm mất độ tin cậy của hệ thống

Nếu không cập nhật, lỗi “job đứng yên sau khi submit” sẽ còn lặp lại bất cứ khi nào worker không được triển khai hoặc chết âm thầm.
