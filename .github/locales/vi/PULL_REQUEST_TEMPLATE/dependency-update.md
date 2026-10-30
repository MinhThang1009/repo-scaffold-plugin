<!-- repo-scaffold:pr-template=dependency-update -->
<!-- repo-scaffold:pr-guidance: Describe the final PR scope, confirmed cause, material changes, and verification with its revision; keep acceptance and rollout details explicit. -->

**Ngôn ngữ:** [English](../../../PULL_REQUEST_TEMPLATE/dependency-update.md) | [Tiếng Việt](dependency-update.md)

## Mục đích

Mô tả cập nhật dependency, lý do cần cập nhật và phạm vi bị ảnh hưởng.

## Thay đổi dependency

-

## Ảnh hưởng đến tương thích và bảo mật

Mô tả ảnh hưởng đến API, runtime, giấy phép, chuỗi cung ứng, lỗ hổng, migration
hoặc rollback.

## Xác minh

Liệt kê chính xác các bước cài đặt, dependency review, test và build hoặc runtime
check đã chạy.

## Checklist bắt buộc

<!-- repo-scaffold:required-checklist:start -->
- [ ] Đã ghi lại dependency trực tiếp và bắc cầu, phiên bản cùng nguồn ở trên
- [ ] Đã đánh giá tương thích, giấy phép, cảnh báo bảo mật và chuỗi cung ứng
- [ ] Đã tạo lại và review lockfile hoặc metadata dependency được sinh ra nếu phù hợp
- [ ] Đã ghi bằng chứng xác minh tập trung ở trên
- [ ] Không có secret, thông tin xác thực, dữ liệu riêng tư hoặc scaffold marker chưa xử lý
<!-- repo-scaffold:required-checklist:end -->

## Nếu áp dụng

<!-- repo-scaffold:optional-checklist:start -->
- [ ] Đã cập nhật cảnh báo lỗ hổng, release note, migration, rollback hoặc theo dõi sự cố
- [ ] Đã đính kèm hoặc liên kết bằng chứng dependency review, SBOM, provenance hoặc deployment
<!-- repo-scaffold:optional-checklist:end -->

## Issue liên quan

Liên kết issue hoặc cảnh báo nếu phù hợp.

<!-- If applicable, use a standalone closing line: Fixes #<issue>, Closes #<issue>, or Resolves #<issue>. -->
