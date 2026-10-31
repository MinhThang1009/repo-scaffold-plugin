<!-- repo-scaffold:pr-template=bugfix -->
<!-- repo-scaffold:pr-body-managed -->
<!-- This body is read by legacy template-based body-sync; the API-evidence body-sync does not read it. -->
<!-- Languages: [English](../../pr-body-template.md) | [Tiếng Việt](pr-body-template.md) -->

## Mục đích

Khắc phục các khoảng trống đã xác minh trong repository guardrail, mutation
cache, công cụ phát triển và bằng chứng end-to-end của pull request, đồng thời
giữ nguyên hợp đồng host và phân phối được hỗ trợ.

## Nguyên nhân gốc

Kiểm tra reminder có thể coi kết quả chưa xác định là hành động cần làm. Body
synchronization từ chối nhầm giới hạn hợp lệ 3.000 tệp, bỏ sót việc ghi nhận
revision đầy đủ của source và đọc bằng chứng remote trước khi áp dụng giới hạn
byte. Pin phát triển Coverage.py trực tiếp cũng chậm hơn release upstream hiện
hành.

## Thay đổi chính

- Từ chối an toàn trước khi tạo/cập nhật Issue reminder và giữ nguyên environment
  của mutation worker cùng phân công shard chính xác.
- Gắn mutation state và verdict có thể tái sử dụng với source, test, workflow,
  dependency, runtime, platform và bằng chứng shard đầy đủ, không chồng lấn;
  ghi shard plan theo cách atomic.
- Giới hạn số trang API, bằng chứng tổng hợp, cache metadata và workflow input
  trước khi decode; chấp nhận trần 3.000 tệp, gắn claim PR với source SHA đầy đủ,
  giữ nguyên phần do con người viết, xác minh trạng thái body remote và từ chối
  an toàn khi bằng chứng chưa đầy đủ hoặc nội dung tiếng Việt chưa được dịch.
- Đồng bộ Codex và Claude package asset, template tiếng Anh/tiếng Việt, tài liệu,
  validator và nguồn body thủ công cũ.
- Cập nhật Coverage.py lên 7.16.2 và tạo lại cả hai lockfile có hash mà không
  đổi phiên bản package đã resolve khác.

## Xác minh

- Toàn bộ test và coverage: chạy `python scripts/run_tests.py full`, ghi kết quả
  cuối cùng theo đúng source revision. Pytest thông thường và phản hồi focused
  không phải bằng chứng đạt full gate.
- Báo cáo coverage: 100.00% statement và branch coverage.
- Đã đạt kiểm tra repository, scaffold, workflow, Ruff, mypy, compileall,
  Markdownlint, pip check và Claude plugin strict. `pip-compile 7.6.0` đã tạo lại
  cả hai lockfile và cài đặt thành công dependency phát triển khóa bằng hash.
- Freshness audit và official-documentation audit mới nhất không phát hiện drift
  đầu vào bảo trì có phiên bản.
- Target cô lập tiếng Anh và tiếng Việt đã qua scaffold validation, body
  rendering và cả hai body preflight. Inventory chưa đầy đủ bị từ chối trước khi
  ghi output; nội dung README hiện có được giữ nguyên.
- Codex và Claude cài đặt thành công package cô lập. Digest và nội dung archive
  phát hành v1.10.14 khớp asset release trên GitHub; không tuyên bố đã hoàn tất
  mutation-quality đầy đủ vì đây là kết quả riêng theo lịch/thủ công.
- Khi render thủ công bằng `scripts/update_pr_body.py --template-file`,
  `{{HEAD_SHA}}` và `{{HEAD_REPOSITORY}}` được bind. Workflow template-based cũ
  có thể dùng tệp này; body-sync API-evidence được thêm bởi thay đổi đó không đọc
  tệp này.

## Checklist bắt buộc

<!-- repo-scaffold:required-checklist:start -->
- [x] Đã mô tả kiểu lỗi hoặc tác động đến người dùng ở trên
- [x] Thay đổi xử lý nguyên nhân đã xác nhận hoặc biện pháp giảm thiểu đã ghi lại
- [x] Đã ghi bằng chứng xác minh hoặc lý do không tự động hóa ở trên
- [x] Không có secret, thông tin xác thực, dữ liệu riêng tư hoặc scaffold marker chưa xử lý
<!-- repo-scaffold:required-checklist:end -->

## Nếu áp dụng

<!-- repo-scaffold:optional-checklist:start -->
- [x] Có test hồi quy tập trung cho kiểu lỗi này
- [x] Đã đánh giá ảnh hưởng đến tài liệu, migration, rollback, release hoặc bảo mật
<!-- repo-scaffold:optional-checklist:end -->

## Issue liên quan

Không áp dụng.
