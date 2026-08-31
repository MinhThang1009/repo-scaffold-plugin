<!-- repo-scaffold:pr-template=dependency-update -->

## Mục đích

Nêu dependency được cập nhật, lý do cập nhật và phạm vi bị ảnh hưởng.

## Thay đổi dependency

-

## Tác động đến compatibility và security

Nêu tác động đến API, runtime, license, supply chain, vulnerability, migration hoặc rollback.

## Cách kiểm thử

Liệt kê chính xác các kiểm tra cài đặt, dependency review, test và build hoặc runtime đã chạy.

## Danh sách bắt buộc

<!-- repo-scaffold:required-checklist:start -->
- [ ] Đã nêu dependency trực tiếp và gián tiếp được cập nhật, version và nguồn ở trên
- [ ] Đã đánh giá tác động đến compatibility, license, security advisory và supply chain
- [ ] Đã regenerate và review lockfile hoặc metadata dependency được tạo khi phù hợp
- [ ] Đã ghi lại bằng chứng verification tập trung ở trên
- [ ] Không có secret, credential, dữ liệu riêng tư hoặc scaffold marker chưa được thay thế
<!-- repo-scaffold:required-checklist:end -->

## Khi phù hợp

<!-- repo-scaffold:optional-checklist:start -->
- [ ] Đã cập nhật vulnerability advisory, release note, migration, rollback hoặc incident follow-up
- [ ] Đã đính kèm hoặc liên kết bằng chứng dependency review, SBOM, provenance hoặc deployment
<!-- repo-scaffold:optional-checklist:end -->

## Issue liên quan

Liên kết issue hoặc advisory khi phù hợp.
